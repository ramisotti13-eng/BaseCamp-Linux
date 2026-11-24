"""Control IPC for BaseCamp Linux (GitHub issue #20).

Splits the running app into a UI layer (the GUI process) and an action layer
that can be driven from outside it. The GUI hosts a small Unix-domain socket
server; any external program — a script reacting to a calendar event, an email
hook, the button-action daemon switching pages, … — connects and sends one
line of JSON to drive the keyboard/DisplayPad.

Wire protocol: one JSON object per line (newline-terminated, UTF-8). The server
replies with one JSON line, e.g. ``{"ok": true}`` or ``{"ok": false,
"error": "..."}``. Keep it line-oriented so any language can talk to it.

Example command objects::

    {"cmd": "rgb", "device": "everest60", "args": ["side-static", "255", "0", "0"]}
    {"cmd": "page", "page": "displaypad"}
    {"cmd": "dp_page", "page": "Editor"}
    {"cmd": "set_key", "button": 0, "type": "shell", "action": "kitty"}
    {"cmd": "image", "button": 2, "path": "/home/me/icon.png"}
    {"cmd": "ping"}

``page`` switches the GUI tab, ``dp_page`` switches the DisplayPad's own key
page (see docs/CONTROL_INTERFACE.md for the full list).

This module has no GUI dependencies so it is safe to import from the controller
daemon and from a thin CLI client.
"""
import json
import os
import socket
import threading


def socket_path():
    """Path of the control socket. Per-user, in the runtime dir when available."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return os.path.join(runtime, "basecamp-control.sock")
    return os.path.join("/tmp", f"basecamp-control-{os.getuid()}.sock")


def send_command(obj, timeout=2.0, path=None):
    """Send one command object to the running GUI and return its reply dict.

    Returns ``{"ok": False, "error": "..."}`` if the GUI isn't running or the
    exchange fails, so callers never have to catch socket errors themselves.
    """
    path = path or socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall((json.dumps(obj) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        line = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        return json.loads(line) if line else {"ok": True}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def is_running(path=None):
    """True if a control server is currently accepting connections at `path`
    (default: socket_path())."""
    return send_command({"cmd": "ping"}, path=path).get("ok", False)


class ControlServer:
    """Background Unix-socket server. `handler(obj) -> dict` runs per command.

    The handler is invoked on the server's own thread; a GUI handler should
    therefore marshal real work onto the Tk main thread (e.g. ``root.after``)
    and return quickly. Start with .start(); it is a daemon thread and dies
    with the process. Safe no-op if the socket can't be created.
    """

    def __init__(self, handler, path=None):
        self._handler = handler
        self._path = path or socket_path()
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        try:
            if os.path.exists(self._path):
                # Stale socket from a crashed instance, or another live one.
                if is_running(self._path):
                    raise OSError(f"control socket already in use: {self._path}")
                os.unlink(self._path)
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.bind(self._path)
            os.chmod(self._path, 0o600)
            self._sock.listen(8)
        except OSError as e:
            print(f"[Control] disabled: {e}")
            self._sock = None
            return False
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="control-server")
        self._thread.start()
        print(f"[Control] listening on {self._path}")
        return True

    def _serve(self):
        sock = self._sock
        while sock is not None and not self._stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn,),
                             daemon=True, name="control-conn").start()

    def _handle_conn(self, conn):
        with conn:
            conn.settimeout(5.0)
            try:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                line = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
                obj = json.loads(line)
            except (OSError, ValueError) as e:
                self._reply(conn, {"ok": False, "error": f"bad request: {e}"})
                return
            try:
                resp = self._handler(obj) or {"ok": True}
            except Exception as e:
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            self._reply(conn, resp)

    @staticmethod
    def _reply(conn, obj):
        try:
            conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        except OSError:
            pass

    def stop(self):
        """Stop accepting and remove the socket file. A command that is
        still running finishes on its own thread and replies if it can."""
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock:
            # close() alone does not wake an accept() blocked in another
            # thread on Linux; shutdown() does, so the thread can end.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(1.0)
        try:
            if os.path.exists(self._path):
                os.unlink(self._path)
        except OSError:
            pass
