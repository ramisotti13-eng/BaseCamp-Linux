"""The `remote` service: remote access settings, paired devices, decisions.

Settings (`remote.json`, 0600, written atomically):

    {"enabled": false, "port": 8780, "allow_shell": false, "allow_locked": false}

Off by default, and so are both boxes: "Phones may set shell commands" and
"Phones may press keys while the PC is locked".

Commands: settings, set_settings, pair, devices, rename_device,
remove_device. `remote.whoami` is in the allowlist but answered by the HTTP
server, the only one that knows which device asks (from its cookie).

Events: `remote.changed {}` (settings, devices, or a server error that
cleared), `remote.device_removed {id}` (the server ends that device's event
stream).

The HTTP server (ui_web/remote_server.py) asks:

    authorize(token)              -> the device, or None
    redeem(code, name)            -> a new device's token, or None
    check(cmd, params, image_ok)  -> why not, or None
    redact(obj)                   -> a reply or event payload fit for a phone
    image_ok(path)                -> a picture path a phone may name

The session lock is followed (core.platform.session) while remote access is
on and the lock box is off, and only then. The watcher is started and
stopped on the core scheduler, where its poll runs (set_settings posts it
there and waits). When it starts, the lock state is asked once (logind's
LockedHint, the watcher's query_locked or the `lock_query` given) on a
thread of its own, so a session that is locked already is; a failure, or
an event heard meanwhile, leaves what the events say. Where it cannot be
followed (no gdbus), `locked` stays False (presses pass) and
settings()["lock_watching"] is false, so the settings page can say so.

Switching remote access off also cancels the current pairing code.
"""
import logging
import os
import socket
import threading

from core.commands import CommandError
from core.remote import policy
from core.remote.pairing import Pairing, clean_name, load_json, save_json
from core.service import Service

log = logging.getLogger("basecamp.core.remote")

SETTINGS_FILE = "remote.json"
DEVICES_FILE = "remote_devices.json"
DEFAULTS = {"enabled": False, "port": 8780, "allow_shell": False, "allow_locked": False}
PORT_MIN, PORT_MAX = 1024, 65535
#: How long a settings change waits for the scheduler to start or stop the
#: lock watcher.
WATCH_WAIT_S = 3.0


#: Interfaces a phone in the LAN cannot reach: loopback, containers (Docker,
#: Podman, CNI, flannel, LXC/LXD), virtual machines' networks (libvirt,
#: VirtualBox, VMware) and the veth pairs between them.
VIRTUAL_PREFIXES = ("docker", "br-", "veth", "virbr", "podman", "cni", "flannel",
                    "lxcbr", "lxdbr", "vboxnet", "vmnet", "vnet")
ROUTE_FILE = "/proc/net/route"


def is_virtual(ifname):
    return ifname == "lo" or ifname.startswith(VIRTUAL_PREFIXES)


def default_interface(path=ROUTE_FILE):
    """The interface of the default route (up, lowest metric) from the
    kernel's route table, or None (no table: not Linux, or no route)."""
    best = None
    try:
        with open(path) as f:
            next(f, None)                       # the header
            for line in f:
                cols = line.split()
                if len(cols) < 7 or cols[1] != "00000000":
                    continue
                try:
                    flags, metric = int(cols[3], 16), int(cols[6])
                except ValueError:
                    continue
                if flags & 0x1 and (best is None or metric < best[0]):   # RTF_UP
                    best = (metric, cols[0])
    except OSError:
        return None
    return best[1] if best else None


def interface_addresses():
    """[(interface, IPv4)] for this machine's interfaces with an address
    (SIOCGIFADDR, the primary address of each); [] where that cannot be
    asked (not Linux)."""
    out = []
    try:
        import fcntl
        import struct
        names = [name for _idx, name in socket.if_nameindex()]
    except (ImportError, AttributeError, OSError):
        return out
    for name in names:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                packed = fcntl.ioctl(s.fileno(), 0x8915,       # SIOCGIFADDR
                                     struct.pack("256s", name[:15].encode()))
            out.append((name, socket.inet_ntoa(packed[20:24])))
        except OSError:
            pass                                # no IPv4 address on it
    return out


def route_source():
    """The address this machine sends from on its default route, or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))         # TEST-NET: nothing is sent
            return s.getsockname()[0]
    except OSError:
        return None


def host_addresses():
    """The IPv4 addresses the host name resolves to."""
    return [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]


def lan_addresses(interfaces=interface_addresses, default_iface=default_interface,
                  source=route_source, host_addresses=host_addresses):
    """This machine's IPv4 addresses a phone in the LAN can reach, the one
    of the default route first (the settings page shows the first as its QR
    code). Container and virtual interfaces are left out, except the default
    route's own. Without an interface list (not Linux): the default route's
    source address, then what the host name resolves to. Each way of asking
    may fail on its own; all failing gives []."""
    def ask(fn, empty):
        try:
            got = fn()
        except Exception:
            return empty
        return empty if got is None else got

    found = []

    def add(ip):
        if (isinstance(ip, str) and ip.count(".") == 3 and not ip.startswith("127.")
                and ip != "0.0.0.0" and ip not in found):
            found.append(ip)

    pairs = list(ask(interfaces, []))
    default = ask(default_iface, None)
    src = ask(source, None)
    if pairs:
        usable = [(n, ip) for n, ip in pairs if n == default or not is_virtual(n)]
        for n, ip in usable:
            if n == default:
                add(ip)
        if not found and any(ip == src for _n, ip in usable):
            add(src)
        for _n, ip in usable:
            add(ip)
    else:
        add(src)
        for ip in ask(host_addresses, []):
            add(ip)
    return found


def _bool(value, what):
    if not isinstance(value, bool):
        raise CommandError("%s must be true or false" % what)
    return value


def _port(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CommandError("the port must be a number")
    try:
        port = int(value)
    except ValueError:
        raise CommandError("the port must be a number") from None
    if not PORT_MIN <= port <= PORT_MAX:
        raise CommandError("the port must be between %d and %d" % (PORT_MIN, PORT_MAX))
    return port


class RemoteService(Service):
    name = "remote"

    def __init__(self, app, config_dir, session_events=None, *, addresses=None,
                 hostname=None, lock_query=None):
        super().__init__(app)
        self.config_dir = config_dir
        self.settings_path = os.path.join(config_dir, SETTINGS_FILE)
        self.pairing = Pairing(os.path.join(config_dir, DEVICES_FILE))
        self._events_factory = session_events or self._default_events
        self._lock_query = lock_query     # () -> True/False/None; else the watcher's
        self._addresses = addresses or lan_addresses
        self._hostname = hostname or socket.gethostname
        self._lock = threading.RLock()
        self._settings = self._load()
        self.started = False
        self.watcher = None
        self.watching = False
        self.locked = False
        self._heard_lock = False        # the current watcher has said something
        self.probe = None               # the thread asking the lock state at the start
        self.server_error = None        # RemoteHost's last failure to listen, or None

    def _default_events(self):
        from core.platform.session import session_events
        return session_events(self.app.scheduler)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _load(self):
        raw = load_json(self.settings_path, {})
        out = dict(DEFAULTS)
        if isinstance(raw, dict):
            for k in ("enabled", "allow_shell", "allow_locked"):
                if isinstance(raw.get(k), bool):
                    out[k] = raw[k]
            try:
                out["port"] = _port(raw.get("port", DEFAULTS["port"]))
            except CommandError:
                pass
        return out

    def settings(self):
        """The stored settings, `lock_watching`: whether the session lock is
        being followed now (false when nothing watches or the watcher could
        not start), so Settings can say it cannot be, and `server_error`:
        why the server could not listen ({code, port, error}, see
        set_server_error), or None."""
        with self._lock:
            out = dict(self._settings)
            out["lock_watching"] = bool(self.watching)
            out["server_error"] = dict(self.server_error) if self.server_error else None
            return out

    def set_server_error(self, error):
        """RemoteHost (ui_web/remote_server.py) reports its last failure to
        listen ({code: "port_in_use" | "cannot_listen", port, error}), or
        None once a server runs or access is off. Kept here, not stored, so a
        settings page opened later still shows it. Clearing a set error
        publishes remote.changed (the page drops it); a new error is
        published by RemoteHost as remote.error."""
        with self._lock:
            was = self.server_error
            self.server_error = dict(error) if error else None
        if was and not error:
            self.publish("changed")

    def set_settings(self, enabled=None, port=None, allow_shell=None, allow_locked=None):
        # One lock over read, check, save: two overlapping calls (switch off,
        # change the port) must not undo each other.
        with self._lock:
            new = dict(self._settings)
            if enabled is not None:
                new["enabled"] = _bool(enabled, "enabled")
            if port is not None:
                new["port"] = _port(port)
            if allow_shell is not None:
                new["allow_shell"] = _bool(allow_shell, "allow_shell")
            if allow_locked is not None:
                new["allow_locked"] = _bool(allow_locked, "allow_locked")
            try:
                save_json(self.settings_path, new)
            except OSError as e:
                raise CommandError("the settings could not be saved: %s" % e) from None
            self._settings = new
            if not new["enabled"]:
                self.pairing.cancel()           # a code shown before must not pair later
            if enabled is not None or port is not None:
                self.server_error = None        # a new attempt; RemoteHost says how it went
        self._sync_watch()
        out = self.settings()
        self.publish("changed")
        return out

    # ── Pairing and devices ───────────────────────────────────────────────────

    def pair(self):
        """A new one-time code and the addresses a phone opens with it."""
        got = self.pairing.start_code()
        port = self.settings()["port"]
        code = got["code"]
        urls = ["http://%s:%d/pair#%s" % (ip, port, code) for ip in self._addresses()]
        host = (self._hostname() or "").split(".")[0]
        if host:
            urls.append("http://%s.local:%d/pair#%s" % (host, port, code))
        return {"code": code, "expires": got["expires"], "urls": urls}

    def redeem(self, code, name):
        """A new device's token for the current code, or None (also while
        remote access is off)."""
        if not self.settings()["enabled"]:
            return None
        token = self.pairing.redeem(code, name)
        if token is not None:
            self.publish("changed")
        return token

    def devices(self):
        return {"devices": self.pairing.devices()}

    def rename_device(self, id, name):
        if not clean_name(name):
            raise CommandError("a name is needed")
        if not self._try(self.pairing.rename, id, name):
            raise CommandError("no such device")
        self.publish("changed")

    def remove_device(self, id):
        if not self._try(self.pairing.remove, id):
            raise CommandError("no such device")
        self.publish("device_removed", id=id)
        self.publish("changed")

    @staticmethod
    def _try(fn, *args):
        try:
            return fn(*args)
        except OSError as e:
            raise CommandError("the devices could not be saved: %s" % e) from None

    def commands(self):
        return {
            "settings": self.settings,
            "set_settings": self.set_settings,
            "pair": self.pair,
            "devices": self.devices,
            "rename_device": self.rename_device,
            "remove_device": self.remove_device,
        }

    # ── What the server asks ──────────────────────────────────────────────────

    def authorize(self, token):
        """The paired device of `token`, or None (always None while remote
        access is off)."""
        if not self.settings()["enabled"]:
            return None
        return self.pairing.device_for(token)

    def check(self, cmd, params, image_ok):
        s = self.settings()
        return policy.check_call(cmd, params, allow_shell=s["allow_shell"],
                                 allow_locked=s["allow_locked"], locked=self.locked,
                                 image_ok=image_ok)

    def redact(self, obj):
        return policy.redact(obj, self.settings()["allow_shell"])

    def _image_roots(self):
        from core.devices.displaypad.library import default_dir
        return [os.path.realpath(default_dir()),
                os.path.realpath(os.path.join(self.config_dir, "virtual_pads"))]

    def image_ok(self, path):
        """True for a path inside the DisplayPad library or the virtual pads'
        folders, after resolving `..` and symlinks."""
        if not isinstance(path, str) or not path or "\0" in path:
            return False
        real = os.path.realpath(path)
        return any(real.startswith(root + os.sep) for root in self._image_roots())

    # ── The session lock ──────────────────────────────────────────────────────

    def start(self):
        with self._lock:
            self.started = True
        self._sync_watch()

    def stop(self):
        with self._lock:
            self.started = False
        self._sync_watch(inline_on_timeout=True)

    def _sync_watch(self, inline_on_timeout=False):
        """Bring the watcher in line with the settings, on the scheduler:
        at once when called there, else posted and waited for (up to
        WATCH_WAIT_S; it then still runs when the scheduler gets to it). The
        watcher's poll runs on the scheduler and reads what stop() closes.
        Never called with self._lock held (the scheduler takes it).
        `inline_on_timeout`: stop() when the scheduler does not answer (it
        no longer runs, so nothing polls): done here instead."""
        sched = self.app.scheduler
        if sched.in_scheduler_thread():
            self._watch()
            return
        done = threading.Event()

        def run():
            try:
                self._watch()
            finally:
                done.set()
        sched.call_soon(run)
        if not done.wait(WATCH_WAIT_S):
            log.warning("the scheduler did not take the lock watcher in time")
            if inline_on_timeout:
                self._watch()

    def _watch(self):
        """Follow the lock while remote access is on and the lock box is
        off, and only then. Runs on the scheduler (see _sync_watch)."""
        with self._lock:
            s = self._settings
            want = self.started and s["enabled"] and not s["allow_locked"]
            if want and self.watcher is None:
                self._heard_lock = False
                try:
                    w = self._events_factory()
                    self.watcher = w
                    self.watching = bool(w.start(lambda kind, active: self._heard(w, kind, active)))
                except Exception as e:
                    log.warning("cannot follow the session lock: %s", e)
                    self.watching = False
                if not self.watching:
                    log.warning("the session lock cannot be followed here (no gdbus?): "
                                "phones can press keys while the PC is locked")
                else:
                    self._ask_locked(w)
            elif not want and self.watcher is not None:
                self._unwatch()

    def _ask_locked(self, watcher):
        """Ask once, on a thread of its own, whether the session is locked
        already: the events only say what changes. An answer counts only
        while `watcher` is the current one and has said nothing yet."""
        query = self._lock_query or getattr(watcher, "query_locked", None)
        if query is None:
            return

        def run():
            try:
                got = query()
            except Exception as e:
                log.info("cannot ask whether the session is locked: %s", e)
                got = None
            if got is None:
                return
            with self._lock:
                if watcher is self.watcher and not self._heard_lock:
                    self.locked = bool(got)
        t = threading.Thread(target=run, daemon=True, name="remote-lock-state")
        self.probe = t
        t.start()

    def _unwatch(self):
        w, self.watcher = self.watcher, None
        self.watching = False
        self.locked = False
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass

    def _heard(self, watcher, kind, active):
        """notify() of a watcher: any thread. Only the current watcher counts."""
        if kind != "lock":
            return
        with self._lock:
            if watcher is self.watcher:
                self._heard_lock = True
                self.locked = bool(active)
