"""Remote access: the page and a narrow bridge for paired phones in the LAN.

It decides nothing itself: who is paired, what a phone may call, what it may
see and which pictures it may name all come from the `remote` core service
(core/remote). Built from the development server's pieces (devserver.py).

    GET  /, /pair             remote.html (the phone page), without a cookie
    GET  /<file>              the other static files (public app code)
    POST /api/pair            {code, name} -> {ok}, and the cookie bc_remote
    POST /api/call            {"cmd", "params"} (or the parameters beside
                              "cmd"): only what remote.check() allows, the
                              reply through remote.redact(); remote.whoami
                              answers the device itself
    GET  /api/events          server-sent events: displaypad.* and vpads.*
                              only, payloads redacted; ends when the device
                              is removed or the server stops
    GET  /api/image?path=&size=[&raw=1]
                              DisplayPad key pictures, the DisplayPad
                              library and the virtual pads' pictures only

At most MAX_CONNECTIONS (32) connections are served at once; one more is
closed as soon as it is accepted. A POST body is at most 64 KiB, its Content-Length checked before anything
is read (411 without one, 413 above it). Every other /api/ request needs a paired device's cookie, else 401 {ok:
false, error: "not paired"}. A refusal by the guard or the allowlist is 403
with the reason in {ok: false, error} ("locked" while the PC is locked).

The guard: the Host header must be an address of this machine or its
hostname (also with .local) with the server's port (no DNS rebinding), an
Origin must be http://<that Host> (no foreign page), a POST must be
application/json (a foreign form needs a CORS preflight, never granted).

It listens on IPv4 only (0.0.0.0), so a bracketed IPv6 Host is never one of
its own. RemoteHost runs a server exactly while remote access is on.
"""
import errno
import json
import logging
import os
import queue
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.remote import policy
from core.remote.service import lan_addresses
from ui_web.devserver import read_json, send, serve_static, stream_events

log = logging.getLogger("basecamp.ui_web.remote")

COOKIE = "bc_remote"
COOKIE_MAX_AGE = 31536000
#: The phone page, for / and /pair.
PAGE = "remote.html"
WHOAMI = "remote.whoami"
PRESS = "displaypad.press_key"
#: A Host that is not among the known addresses makes them be looked up
#: again, at most this often (an address the DHCP server just handed out).
ADDRESSES_TTL_S = 2.0
#: A kept-alive connection that sends nothing for this long is closed.
IDLE_TIMEOUT_S = 60
NOT_PAIRED = {"ok": False, "error": "not paired"}
#: The largest request body taken (pairing is reachable without a cookie).
MAX_BODY = 64 * 1024
#: An open event stream asks again this often whether its device is still
#: paired (beside the remote.device_removed event, which ends it at once).
REAUTH_S = 15.0
#: Connections served at once; one more is closed at once (a phone page
#: needs a few: its event stream, a call, some pictures).
MAX_CONNECTIONS = 32


class _CappedServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that serves at most MAX_CONNECTIONS
    connections at once and closes any more as soon as it accepts them."""

    daemon_threads = True

    def __init__(self, address, handler, cap=MAX_CONNECTIONS):
        self._slots = threading.BoundedSemaphore(cap)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()       # no thread took it
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _Stream:
    """One device's event stream."""

    def __init__(self, device_id):
        self.device_id = device_id
        self.q = queue.Queue(maxsize=1000)
        self.closed = threading.Event()
        self.checked = time.monotonic()

    def end(self):
        self.closed.set()
        try:
            self.q.put_nowait(None)     # wakes the stream at once
        except queue.Full:
            pass                        # it is busy and sees `closed` next


class RemoteServer:
    """The phone page and the bridge for paired devices, on `host`:`port`.
    Raises OSError when the port cannot be had (errno EADDRINUSE: taken)."""

    def __init__(self, bridge, static_dir, remote, port, host="0.0.0.0"):
        self.bridge = bridge
        self.remote = remote
        self.static_dir = os.path.realpath(static_dir)
        self.closing = threading.Event()
        self._lock = threading.Lock()
        self._streams = set()
        self._conns = set()
        self._addrs = frozenset()
        self._addrs_at = None
        self._stopped = False
        self.httpd = _CappedServer((host, port), _handler_for(self))
        self.port = self.httpd.server_address[1]
        self.host = host
        self._remove_sink = bridge.events.add_sink(self._broadcast)
        # Straight from the bus, not through the bridge's batches: a burst
        # past the bridge's bounded queue must not drop a removal.
        self._unsubscribe = remote.bus.subscribe("remote.device_removed",
                                                 self._device_removed)
        self._thread = None

    @property
    def url(self):
        host = self.host
        if host in ("", "0.0.0.0"):
            found = lan_addresses()
            host = found[0] if found else "127.0.0.1"
        return "http://%s:%d/" % (host, self.port)

    # ── What a request may name ───────────────────────────────────────────────
    def _names(self, refresh=False):
        now = time.monotonic()
        with self._lock:
            stale = self._addrs_at is None or (refresh and now - self._addrs_at >= ADDRESSES_TTL_S)
        if stale:
            names = {"127.0.0.1"}
            try:
                names.update(lan_addresses())
            except Exception:
                log.exception("listing this machine's addresses failed")
            hostname = (socket.gethostname() or "").strip().lower().rstrip(".")
            for h in (hostname, hostname.split(".")[0]):
                if h:
                    names.update((h, h + ".local"))
            with self._lock:
                self._addrs = frozenset(names)
                self._addrs_at = now
        with self._lock:
            return self._addrs

    def host_ok(self, host):
        """Is `host` (the Host header, lower case) <own address or
        hostname>:<port>?"""
        suffix = ":%d" % self.port
        if not host.endswith(suffix):
            return False
        name = host[:-len(suffix)]
        if not name or name.startswith("["):
            return False                # IPv6: not listened on
        return name in self._names() or name in self._names(refresh=True)

    def picture_ok(self, path):
        """A picture a phone may see or name: the DisplayPad library or a
        virtual pad's folder (remote.image_ok), or one a DisplayPad key or a
        virtual pad's key carries now."""
        try:
            return bool(self.remote.image_ok(path) or self.bridge.key_picture(path))
        except Exception:
            log.exception("checking a picture for a phone failed")
            return False

    # ── Event streams ─────────────────────────────────────────────────────────
    def _device_removed(self, _topic, payload):
        self.end_device({payload.get("id")})

    def _broadcast(self, batch):
        events = [ev for ev in batch
                  if str(ev.get("topic", "")).startswith(policy.REMOTE_TOPIC_PREFIXES)]
        if not events:
            return
        data = json.dumps(self.remote.redact(events), ensure_ascii=True)
        with self._lock:
            streams = list(self._streams)
        for st in streams:
            if st.closed.is_set():
                continue
            try:
                st.q.put_nowait(data)
            except queue.Full:
                pass            # that phone is not reading: it loses events

    def open_stream(self, device_id):
        st = _Stream(device_id)
        with self._lock:
            self._streams.add(st)
        return st

    def close_stream(self, st):
        with self._lock:
            self._streams.discard(st)

    def end_device(self, device_ids):
        """End the event streams of these devices."""
        with self._lock:
            streams = [st for st in self._streams if st.device_id in device_ids]
        for st in streams:
            st.end()

    @property
    def streams(self):
        with self._lock:
            return len(self._streams)

    # ── Connections ───────────────────────────────────────────────────────────
    def _track(self, conn):
        with self._lock:
            self._conns.add(conn)

    def _untrack(self, conn):
        with self._lock:
            self._conns.discard(conn)

    # ── Running ───────────────────────────────────────────────────────────────
    def start(self):
        """Serve on a thread of its own."""
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        kwargs={"poll_interval": 0.25},
                                        daemon=True, name="remote-server")
        self._thread.start()
        return self

    def stop(self):
        """Stop serving: every stream ends, every connection is cut, the
        port is free."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            streams = list(self._streams)
        self.closing.set()
        self._remove_sink()
        self._unsubscribe()
        for st in streams:
            st.end()
        if self._thread is not None:
            self.httpd.shutdown()
        self.httpd.server_close()
        with self._lock:
            conns = list(self._conns)
        for conn in conns:
            # A kept-alive connection must not outlive the server: its next
            # request would still reach the core.
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(2.0)


def _name_from_agent(agent):
    """A device name from a User-Agent, when the page sent none."""
    agent = agent or ""
    for needle, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                         ("Windows", "Windows PC"), ("Macintosh", "Mac"), ("Linux", "Linux")):
        if needle in agent:
            return name
    return "Device"


def _handler_for(server):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = IDLE_TIMEOUT_S
        srv = server

        def log_message(self, fmt, *args):
            pass

        def setup(self):
            super().setup()
            self.srv._track(self.connection)

        def finish(self):
            try:
                super().finish()
            finally:
                self.srv._untrack(self.connection)

        # ── Guards ─────────────────────────────────────────────────────────
        def _guard(self, post=False):
            if self.srv.closing.is_set():
                send(self, 503, {"ok": False, "error": "remote access is stopping"})
                return False
            host = (self.headers.get("Host") or "").strip().lower()
            if not self.srv.host_ok(host):
                send(self, 403, {"ok": False, "error": "wrong host"})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin.strip().rstrip("/").lower() != "http://" + host:
                send(self, 403, {"ok": False, "error": "foreign origin"})
                return False
            if post:
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    send(self, 415, {"ok": False, "error": "application/json only"})
                    return False
            return True

        def _token(self):
            for header in self.headers.get_all("Cookie") or []:
                for part in header.split(";"):
                    name, sep, value = part.strip().partition("=")
                    if sep and name.strip() == COOKIE:
                        return value.strip().strip('"')
            return None

        def _device(self):
            """The paired device of the cookie, or None after answering 401."""
            token = self._token()
            dev = None
            if token:
                try:
                    dev = self.srv.remote.authorize(token)
                except Exception:
                    log.exception("authorizing a device failed")
            if dev is None:
                send(self, 401, NOT_PAIRED)
            return dev

        def _reply(self, obj):
            send(self, 200, self.srv.remote.redact(obj))

        # ── GET ────────────────────────────────────────────────────────────
        def do_GET(self):
            if not self._guard():
                return
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                dev = self._device()
                if dev is None:
                    return
                if path == "/api/events":
                    return self._events(dev)
                if path == "/api/image":
                    return self._image(urllib.parse.parse_qs(parts.query))
                return send(self, 404, {"ok": False, "error": "no such endpoint"})
            return self._static(path)

        def do_HEAD(self):
            if not self._guard():
                return
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/api/"):
                if self._device() is None:
                    return
                return send(self, 405, {"ok": False, "error": "not here"})
            return self._static(path)

        def _static(self, path):
            if path in ("/", "/pair"):
                return serve_static(self, self.srv.static_dir, PAGE)
            serve_static(self, self.srv.static_dir, urllib.parse.unquote(path).lstrip("/"))

        def _image(self, query):
            path = (query.get("path") or [""])[0]
            size = (query.get("size") or [None])[0]
            if not self.srv.picture_ok(path):
                return send(self, 403, {"ok": False,
                                        "error": "that picture cannot be used from a phone"})
            url = self.srv.bridge.image(path, size)
            if (query.get("raw") or ["0"])[0] in ("1", "true"):
                if url is None:
                    return send(self, 404, body=b"", ctype="image/png")
                import base64
                return send(self, 200, body=base64.b64decode(url.split(",", 1)[1]),
                            ctype="image/png")
            self._reply({"url": url})

        def _events(self, dev):
            st = self.srv.open_stream(dev["id"])
            try:
                # Removed between the cookie check and now: the removal's
                # event may already have gone by.
                try:
                    still = self.srv.remote.authorize(self._token())
                except Exception:
                    still = None
                if still is None:
                    st.end()
                stream_events(self, st.q, lambda: self._stream_over(st))
            finally:
                self.srv.close_stream(st)

        def _stream_over(self, st):
            """Should stream `st` end? Its device is asked for again every
            REAUTH_S seconds."""
            if self.srv.closing.is_set() or st.closed.is_set():
                return True
            now = time.monotonic()
            if now - st.checked >= REAUTH_S:
                st.checked = now
                try:
                    still = self.srv.remote.authorize(self._token())
                except Exception:
                    still = None
                if still is None or still.get("id") != st.device_id:
                    st.end()
                    return True
            return False

        # ── POST ───────────────────────────────────────────────────────────
        def do_POST(self):
            if not self._guard(post=True):
                return
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/pair":
                body = self._json()
                if body is not False:
                    self._pair(body)
                return
            dev = self._device()
            if dev is None:
                return
            if path != "/api/call":
                return send(self, 404, {"ok": False, "error": "no such endpoint"})
            body = self._json()
            if body is not False:
                self._call(dev, body)

        def _json(self):
            """The JSON body, or False after answering 400, 411 or 413. The
            length is checked before anything is read: no Content-Length is
            411, one that is not a number 400, more than MAX_BODY 413."""
            given = self.headers.get("Content-Length")
            if given is None:
                send(self, 411, {"ok": False, "error": "Content-Length needed"})
                return False
            given = given.strip()
            if not given.isdigit() or not given.isascii():
                send(self, 400, {"ok": False, "error": "bad Content-Length"})
                return False
            if len(given) > 12 or int(given) > MAX_BODY:
                send(self, 413, {"ok": False, "error": "the request is too large"})
                return False
            try:
                return read_json(self)
            except (ValueError, UnicodeDecodeError) as e:
                send(self, 400, {"ok": False, "error": "bad JSON: %s" % e})
                return False

        def _pair(self, body):
            if not isinstance(body, dict):
                return send(self, 400, {"ok": False, "error": "send {code, name}"})
            name = body.get("name")
            if not isinstance(name, str) or not name.strip():
                name = _name_from_agent(self.headers.get("User-Agent"))
            try:
                token = self.srv.remote.redeem(body.get("code"), name)
            except Exception:
                log.exception("redeeming a pairing code failed")
                token = None
            if token is None:
                return send(self, 403, {"ok": False, "error": "wrong or expired code"})
            cookie = "%s=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (
                COOKIE, token, COOKIE_MAX_AGE)
            send(self, 200, {"ok": True}, headers=[("Set-Cookie", cookie)])

        def _call(self, dev, body):
            if not isinstance(body, dict):
                return send(self, 400, {"ok": False, "error": "a command must be a JSON object"})
            cmd = body.get("cmd")
            params = body.get("params")
            if not isinstance(params, dict):
                params = {k: v for k, v in body.items() if k != "cmd"}
            if not isinstance(cmd, str) or not cmd:
                return send(self, 400, {"ok": False, "error": "missing \"cmd\""})
            remote = self.srv.remote
            try:
                why = remote.check(cmd, params, self.srv.picture_ok)
            except Exception:
                log.exception("checking %s for a phone failed", cmd)
                why = "not allowed from a phone: %s" % cmd
            if why is not None:
                return send(self, 403, {"ok": False, "error": why})
            if cmd == WHOAMI:
                return self._reply({"ok": True, "device": {"id": dev["id"], "name": dev["name"]}})
            if cmd == PRESS:
                params = dict(params, remote=True)
            self._reply(self.srv.bridge.call(cmd, params))

    return Handler


class RemoteHost:
    """Runs a RemoteServer exactly while remote access is on, on the port
    the settings name, and moves it when the port changes. Follows
    `remote.changed` on a thread of its own (a bus handler must return at
    once). A port that cannot be had is published as `remote.error {code:
    "port_in_use" | "cannot_listen", port, error}` and kept on the service
    (remote.set_server_error, in settings()["server_error"]) until a server
    runs or access is switched off, so a settings page opened later shows
    it too."""

    def __init__(self, app, bridge, static_dir):
        self.app = app
        self.bridge = bridge
        self.static_dir = static_dir
        self.remote = app.service("remote")
        self.server = None
        self.error = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._unsubscribe = None
        self._thread = None

    def start(self):
        if self.remote is None or self._thread is not None:
            return self
        self._unsubscribe = self.app.bus.subscribe("remote.changed",
                                                   lambda _t, _p: self._wake.set())
        self._thread = threading.Thread(target=self._loop, daemon=True, name="remote-host")
        self._thread.start()
        self._wake.set()
        return self

    def _loop(self):
        while True:
            self._wake.wait()
            if self._quit.is_set():
                return
            self._wake.clear()
            try:
                self.reconcile()
            except Exception:
                log.exception("following the remote access settings failed")

    def reconcile(self):
        """Start, stop or move the server to match the settings now."""
        with self._lock:
            if self._quit.is_set():
                return
            s = self.remote.settings()
            port = s["port"]
            cur = self.server
            if cur is not None and (not s["enabled"] or cur.port != port):
                cur.stop()              # raises: kept, the next reconcile tries again
                self.server = None
            if not s["enabled"] or self.server is not None:
                # Off, or already serving: nothing has failed now.
                self.error = None
                if s["server_error"]:
                    self.remote.set_server_error(None)
                return
            try:
                srv = RemoteServer(self.bridge, self.static_dir, self.remote, port)
            except OSError as e:
                code = "port_in_use" if e.errno == errno.EADDRINUSE else "cannot_listen"
                log.warning("remote access cannot listen on port %d: %s", port, e)
                self.error = {"code": code, "port": port, "error": str(e)}
                # Kept on the service first, so a page reading settings() on the event sees it.
                self.remote.set_server_error(self.error)
                self.app.bus.publish("remote.error", **self.error)
                return
            self.server = srv.start()
            self.error = None
            self.remote.set_server_error(None)
            log.info("remote access on %s", srv.url)

    def stop(self):
        """Stop following the settings and stop the server. Never raises."""
        self._quit.set()
        unsub, self._unsubscribe = self._unsubscribe, None
        if unsub is not None:
            unsub()
        self._wake.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(3.0)
        with self._lock:
            cur, self.server = self.server, None
        if cur is not None:
            try:
                cur.stop()
            except Exception:
                log.exception("stopping the remote server failed")
