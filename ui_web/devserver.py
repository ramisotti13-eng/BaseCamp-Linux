"""The web interface in a browser, for development: the page and the bridge
over HTTP on 127.0.0.1.

    python3 -m ui_web.devserver --demo            # stand-in devices, temp config
    python3 -m ui_web.devserver [--port 8770]     # the real devices (Linux)

then open http://127.0.0.1:8770/. bridge.js uses this when the page is not in
a pywebview window (window.pywebview is absent).

    GET  /                    ui_web/static/index.html, and the static files
    POST /api/call            {"cmd": "...", "params": {...}}  (or the
                              parameters beside "cmd") -> the dispatch reply
    GET  /api/events          server-sent events: one message per batch,
                              data = [{"topic", "payload"}, ...]
    GET  /api/image?path=&size=
                              {"url": "data:image/png;base64,..." | null};
                              with &raw=1 the PNG itself (404 when refused)
    POST /api/pick_file       {"title","kind","name"} -> the path the demo's stand-in
                              dialog answers, else null (no file dialog in a browser)
    POST /api/open            {"url"}: printed, not opened -> {"ok": bool}
    POST /api/win             {"action"}: no window here -> {"ok": false}
    POST /api/ready           {"ok": true}

It listens on 127.0.0.1 only and refuses what a web page elsewhere could
send it: a Host other than 127.0.0.1/localhost on its port (DNS rebinding),
an Origin other than its own, a POST that is not application/json (so a
foreign page's request needs a CORS preflight, which this server never
grants). The core can run any command it knows, so a stray page must not
reach it.
"""
import argparse
import json
import mimetypes
import os
import queue
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8770
HOST = "127.0.0.1"
#: Seconds between keep-alive comments on an idle event stream.
KEEPALIVE_S = 15.0

_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
    ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
    ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
}


# ── Pieces of a handler (the remote server uses them too) ─────────────────────

def send(handler, code, obj=None, body=None, ctype="application/json", headers=()):
    """Answer with `obj` as JSON, or `body` as `ctype`. A refusal (4xx/5xx)
    closes the connection. `headers`: more (name, value) pairs."""
    if body is None:
        body = json.dumps(obj, ensure_ascii=True).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    for name, value in headers:
        handler.send_header(name, value)
    if code >= 400:
        # A refused request's body may still be unread: it must not
        # be taken for the next request on this connection.
        handler.send_header("Connection", "close")
        handler.close_connection = True
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def read_json(handler):
    """The request's JSON body, or None when it has none. Raises ValueError
    (or UnicodeDecodeError) for one that is not JSON."""
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        n = 0
    raw = handler.rfile.read(n) if n > 0 else b""
    if not raw.strip():
        return None
    return json.loads(raw.decode("utf-8"))


def serve_static(handler, root, rel):
    """The file `rel` under `root` (a realpath), or 404 for anything that is
    not a file inside it. A directory gives its index.html."""
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
        return send(handler, 404, body=b"not found", ctype="text/plain; charset=utf-8")
    ext = os.path.splitext(full)[1].lower()
    ctype = _TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
    with open(full, "rb") as f:
        body = f.read()
    send(handler, 200, body=body, ctype=ctype)


def stream_events(handler, q, should_stop):
    """Server-sent events: each string taken from queue `q` is one message,
    until `should_stop()` is true, a None is taken or the client goes."""
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        handler.wfile.write(b"retry: 2000\n: connected\n\n")
        handler.wfile.flush()
        idle = 0.0
        while not should_stop():
            try:
                data = q.get(timeout=0.5)
            except queue.Empty:
                idle += 0.5
                if idle >= KEEPALIVE_S:
                    idle = 0.0
                    handler.wfile.write(b": keep-alive\n\n")
                    handler.wfile.flush()
                continue
            if data is None:
                break
            idle = 0.0
            handler.wfile.write(b"data: " + data.encode("ascii") + b"\n\n")
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        handler.close_connection = True


class DevServer:
    """The bridge and a static directory over HTTP. `port` 0 picks a free one."""

    def __init__(self, bridge, static_dir, port=DEFAULT_PORT, host=HOST, log=None):
        self.bridge = bridge
        self.static_dir = os.path.realpath(static_dir)
        self._log = log or (lambda msg: print("[devserver] " + msg, flush=True))
        self._clients = set()
        self._clients_lock = threading.Lock()
        self.closing = threading.Event()
        self.httpd = ThreadingHTTPServer((host, port), _handler_for(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.host = host
        self._remove_sink = bridge.events.add_sink(self._broadcast)
        self._thread = None

    @property
    def url(self):
        return "http://%s:%d/" % (self.host, self.port)

    # ── Event streams ──────────────────────────────────────────────────────
    def _broadcast(self, batch):
        data = json.dumps(batch, ensure_ascii=True)
        with self._clients_lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass            # that page is not reading: it loses events

    def open_stream(self):
        q = queue.Queue(maxsize=1000)
        with self._clients_lock:
            self._clients.add(q)
        return q

    def close_stream(self, q):
        with self._clients_lock:
            self._clients.discard(q)

    @property
    def streams(self):
        with self._clients_lock:
            return len(self._clients)

    # ── Running ────────────────────────────────────────────────────────────
    def serve_forever(self):
        self.httpd.serve_forever(poll_interval=0.25)

    def start(self):
        """Serve on a thread of its own."""
        self._thread = threading.Thread(target=self.serve_forever, daemon=True,
                                        name="devserver")
        self._thread.start()
        return self

    def stop(self):
        self.closing.set()
        self._remove_sink()
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(2.0)

    def log(self, msg):
        self._log(msg)


def _handler_for(server):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        srv = server

        def log_message(self, fmt, *args):     # quiet: errors are logged below
            pass

        # ── Guards ─────────────────────────────────────────────────────────
        def _own_hosts(self):
            port = self.srv.port
            return {"127.0.0.1:%d" % port, "localhost:%d" % port}

        def _guard(self, post=False):
            host = (self.headers.get("Host") or "").strip().lower()
            if host not in self._own_hosts():
                self._send(403, {"ok": False, "error": "wrong host"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin.rstrip("/").lower() not in {
                    "http://" + h for h in self._own_hosts()}:
                self._send(403, {"ok": False, "error": "foreign origin"})
                return False
            if post:
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    self._send(415, {"ok": False, "error": "application/json only"})
                    return False
            return True

        # ── Replies ────────────────────────────────────────────────────────
        def _send(self, code, obj=None, body=None, ctype="application/json"):
            send(self, code, obj, body, ctype)

        def _body(self):
            return read_json(self)

        # ── GET ────────────────────────────────────────────────────────────
        def do_GET(self):
            if not self._guard():
                return
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            if path == "/api/events":
                return self._events()
            if path == "/api/image":
                return self._image(urllib.parse.parse_qs(parts.query))
            if path.startswith("/api/"):
                return self._send(404, {"ok": False, "error": "no such endpoint"})
            return self._static(path)

        do_HEAD = do_GET

        def _static(self, path):
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            serve_static(self, self.srv.static_dir, rel)

        def _image(self, query):
            path = (query.get("path") or [""])[0]
            size = (query.get("size") or [None])[0]
            url = self.srv.bridge.image(path, size)
            if (query.get("raw") or ["0"])[0] in ("1", "true"):
                if url is None:
                    return self._send(404, body=b"", ctype="image/png")
                import base64
                return self._send(200, body=base64.b64decode(url.split(",", 1)[1]),
                                  ctype="image/png")
            self._send(200, {"url": url})

        def _events(self):
            q = self.srv.open_stream()
            try:
                stream_events(self, q, self.srv.closing.is_set)
            finally:
                self.srv.close_stream(q)

        # ── POST ───────────────────────────────────────────────────────────
        def do_POST(self):
            if not self._guard(post=True):
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                body = self._body()
            except (ValueError, UnicodeDecodeError) as e:
                return self._send(400, {"ok": False, "error": "bad JSON: %s" % e})
            bridge = self.srv.bridge
            if path == "/api/call":
                if not isinstance(body, dict):
                    return self._send(400, {"ok": False, "error": "a command must be a JSON object"})
                cmd = body.get("cmd")
                params = body.get("params")
                if not isinstance(params, dict):
                    params = {k: v for k, v in body.items() if k != "cmd"}
                return self._send(200, bridge.call(cmd, params))
            if path == "/api/pick_file":
                return self._send(200, bridge.pick_file(body if isinstance(body, dict) else {}))
            if path == "/api/open":
                url = body.get("url") if isinstance(body, dict) else body
                ok = _valid_url(url)
                self.srv.log("would open %r%s" % (url, "" if ok else " (refused)"))
                return self._send(200, {"ok": ok})
            if path == "/api/win":
                action = body.get("action") if isinstance(body, dict) else body
                self.srv.log("window %r: there is no window in a browser" % (action,))
                return self._send(200, {"ok": False})
            if path == "/api/ready":
                return self._send(200, {"ok": True})
            return self._send(404, {"ok": False, "error": "no such endpoint"})

    return Handler


def _valid_url(url):
    if not isinstance(url, str):
        return False
    parts = urllib.parse.urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m ui_web.devserver",
                                 description="The web interface in a browser, for development.")
    ap.add_argument("--demo", action="store_true",
                    help="stand-in devices and a throw-away configuration (no hardware)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--static", default=None, help="the page's directory (default: ui_web/static)")
    from ui_web import demo_photos
    demo_photos.add_argument(ap)
    args = ap.parse_args(argv)

    home = None
    if args.demo:
        # Before anything imports shared.config, which fixes the
        # configuration directory when it is imported.
        from ui_web import demo
        home = demo.prepare()
        print("[devserver] demo mode, configuration in %s" % home, flush=True)

    from ui_web import STATIC_DIR, app_version
    from ui_web.bridge import Bridge
    from core.bootstrap import build_core, start_core

    if args.demo:
        app, _pad = demo.build()
    else:
        app = build_core(version=app_version())
    demo_photos.apply(args, say=lambda m: print("[devserver] " + m, flush=True))
    bridge = Bridge(app, picker=demo.picker(home) if args.demo else None,
                    opener=lambda url: None).start()
    server = DevServer(bridge, args.static or STATIC_DIR, port=args.port)
    start_core(app)
    print("[devserver] %s  (Ctrl+C stops it)" % server.url, flush=True)
    if not os.path.isfile(os.path.join(server.static_dir, "index.html")):
        print("[devserver] note: %s has no index.html yet" % server.static_dir, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.closing.set()
        server.httpd.server_close()
        bridge.stop()
        app.stop()
        if home:
            demo.cleanup(home)
    return 0


if __name__ == "__main__":
    sys.exit(main())
