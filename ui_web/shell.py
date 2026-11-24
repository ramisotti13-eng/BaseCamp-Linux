"""What makes the web window behave like the 3.x application (gui.py).

app.py builds the pywebview window and the core; this module is everything
around them that the CustomTkinter application does in App.__init__, run()
and destroy():

    tray            starts tray_helper.py (the frozen basecamp-tray) the way
                    gui._setup_tray does; its Open entry sends SIGUSR1, its
                    Quit entry SIGUSR2
    close           the window's close button and the window manager's close
                    hide the window while the tray runs and quit without it
    ui.*            ui.show brings the window up, ui.quit leaves, ui.page asks
                    the page for a screen (the page handles that one)
    geometry        size and position are remembered in the file 3.x uses
                    (shared.config.load/save_window_geometry, "WxH+X+Y")
    start-up tasks  Monitor Mode through the Everest Max service 0.5 s after
                    the start, the update check 2 s after it
    update restart  the tray helper goes before the process is replaced
    shutdown        gui.destroy()'s order

The page learns whether it is on screen from the bus event
`ui.visible {visible}` and the command `shell.state`: a plugin update that
turns up while the window sits in the tray is announced when it comes back
(gui: _announce_plugin_updates waits for the window).

Nothing here imports pywebview; the window is whatever app.py passes in.
"""
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading

log = logging.getLogger("basecamp.ui_web.shell")

#: ms after the start (gui: after(500, _start_cpu_auto_clean) and
#: after(2000, _check_for_update)).
MONITOR_DELAY_MS = 500
UPDATE_DELAY_MS = 2000
#: Seconds the size and position must stay unchanged before they are saved.
GEOMETRY_DEBOUNCE_S = 0.5
#: gui.destroy(): a moment for the HID threads to close their devices.
SETTLE_S = 0.4

_GEOMETRY = re.compile(r"^(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?$")


def parse_geometry(text, min_size=(0, 0)):
    """"WxH+X+Y" (3.x's file) as (w, h, x, y); x and y are None when the
    string has none. None for anything else. The size is raised to
    `min_size`."""
    m = _GEOMETRY.match(str(text or "").strip())
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    x = y = None
    if m.group(3) is not None:
        x, y = int(m.group(3).replace("+", "")), int(m.group(4).replace("+", ""))
    return max(w, min_size[0]), max(h, min_size[1]), x, y


def format_geometry(w, h, x=None, y=None):
    """The inverse: "WxH+X+Y" the way Tk writes it."""
    out = "%dx%d" % (w, h)
    if x is not None and y is not None:
        out += "%s%d%s%d" % ("+" if x >= 0 else "-", abs(x), "+" if y >= 0 else "-", abs(y))
    return out


def tray_command(lang_file, *, pid=None, environ=None, frozen=None, helper=None,
                 interpreter=None):
    """(command line, environment) of the tray helper, as gui._setup_tray
    builds it (including the `sudo -u` of a start under sudo). The command
    is empty when no python can be found."""
    from core import runtime
    environ = dict(os.environ if environ is None else environ)
    frozen = runtime.FROZEN if frozen is None else frozen
    if helper is None:
        helper = (os.path.join(runtime.BIN, "basecamp-tray") if frozen
                  else os.path.join(runtime.HERE, "tray_helper.py"))
    pid = os.getpid() if pid is None else pid
    env = dict(environ)
    if frozen:
        cmd = [helper, str(pid), lang_file]
    else:
        py = interpreter if interpreter is not None else runtime.interpreter()
        cmd = [py, helper, str(pid), lang_file] if py else []
    user = environ.get("SUDO_USER")
    if user and cmd:
        import pwd
        uid = pwd.getpwnam(user).pw_uid
        env["DISPLAY"] = environ.get("DISPLAY", ":0")
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/%d/bus" % uid
        env["XDG_RUNTIME_DIR"] = "/run/user/%d" % uid
        cmd = ["sudo", "-u", user, "-E"] + cmd
    return cmd, env


class SignalListener:
    """SIGUSR1 / SIGUSR2 (the tray helper's Open and Quit) delivered on a
    thread of their own.

    A Python signal handler only runs when the main thread executes Python
    code, and the main thread of a pywebview window sits in the toolkit's
    main loop. The interpreter's wake-up pipe does not depend on that: the C
    handler writes the signal number to it at once and the thread reading it
    calls the action. No-op where the signals do not exist (Windows) or off
    the main thread.

    The Python handler acts as well. GTK's main loop, as PyGObject runs it,
    puts its own wake-up pipe in place of this one for as long as it runs,
    and runs the Python handlers from there instead; the pipe above then
    never hears a thing. Where both paths work (Qt) one signal reaches both,
    so a signal acts once per COALESCE_S."""

    #: Seconds within which a second delivery of the same signal is the same one.
    COALESCE_S = 0.3

    def __init__(self, actions):
        self._actions = actions          # {signal number: callable}
        self._rsock = self._wsock = None
        self._thread = None
        self._old = {}
        self._old_fd = None
        self._lock = threading.Lock()
        self._last = {}

    def start(self):
        if not self._actions or threading.current_thread() is not threading.main_thread():
            return False
        try:
            self._rsock, self._wsock = socket.socketpair()
            self._wsock.setblocking(False)
            for sig in self._actions:
                self._old[sig] = signal.signal(sig, self._on_signal)
            self._old_fd = signal.set_wakeup_fd(self._wsock.fileno(), warn_on_full_buffer=False)
        except (ValueError, OSError, AttributeError):
            self.stop()
            return False
        self._thread = threading.Thread(target=self._loop, name="tray-signals", daemon=True)
        self._thread.start()
        return True

    def _on_signal(self, num, _frame):
        # On the main thread, maybe inside the toolkit's loop: the action
        # (which may wait for that loop) goes to a thread of its own.
        if self._due(num):
            threading.Thread(target=self._call, args=(num,), name="tray-signal",
                             daemon=True).start()

    def _due(self, num):
        import time
        with self._lock:
            now = time.monotonic()
            if now - self._last.get(num, float("-inf")) < self.COALESCE_S:
                return False
            self._last[num] = now
            return True

    def _call(self, num):
        fn = self._actions.get(num)
        if fn is not None:
            try:
                fn()
            except Exception:
                log.exception("signal %d handler failed", num)

    def _loop(self):
        sock = self._rsock
        while True:
            try:
                data = sock.recv(64)
            except OSError:
                return
            if not data:
                return
            for num in data:
                if num in self._actions and self._due(num):
                    self._call(num)

    def stop(self):
        if self._old_fd is not None or self._old:
            try:
                signal.set_wakeup_fd(-1 if self._old_fd is None else self._old_fd)
            except (ValueError, OSError):
                pass
            for sig, old in self._old.items():
                try:
                    signal.signal(sig, old if old is not None else signal.SIG_DFL)
                except (ValueError, OSError):
                    pass
        self._old, self._old_fd = {}, None
        for s in (self._wsock, self._rsock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._rsock = self._wsock = None


class Shell:
    """Tray, show/hide/quit, geometry and start-up tasks of one web window.

    app          the CoreApp (not necessarily started)
    window       the pywebview window (or a stand-in)
    bridge       the ui_web Bridge (stopped first on the way out)
    min_size     (w, h) the window never gets smaller than
    tray         False: no tray helper (demo, tests, --no-tray)
    monitor      start Monitor Mode when an Everest Max is there
    update_check ask for a newer release after UPDATE_DELAY_MS
    popen        subprocess.Popen, replaceable
    """

    def __init__(self, app, window, bridge=None, *, min_size=(1100, 700), tray=True,
                 monitor=True, update_check=True, popen=subprocess.Popen,
                 signals=True, debounce=GEOMETRY_DEBOUNCE_S, settle=SETTLE_S,
                 size=(1440, 900), tray_cmd=None):
        self.app = app
        self.window = window
        self.bridge = bridge
        self.min_size = min_size
        self._want_tray = tray
        self._monitor = monitor
        self._update_check = update_check
        self._popen = popen
        self._debounce = debounce
        self._settle = settle
        self._tray_cmd = tray_cmd or tray_command
        self.tray_proc = None
        self.visible = True
        self.quitting = False
        self.maximized = False
        self._size = tuple(size)
        self._pos = (None, None)
        self._timer = None
        self._timer_lock = threading.Lock()
        self._handles = []
        self._shut = False
        self._signals = None
        if signals and hasattr(signal, "SIGUSR1"):
            self._signals = SignalListener({signal.SIGUSR1: self.show,
                                            signal.SIGUSR2: self.quit})

    # ── Geometry ───────────────────────────────────────────────────────────
    def window_kwargs(self):
        """create_window()'s size and position: the remembered ones, else
        the defaults. Also primes the state the next save starts from."""
        from shared.config import load_window_geometry
        geo = parse_geometry(load_window_geometry(), self.min_size)
        if geo is None:
            return {"width": self._size[0], "height": self._size[1]}
        w, h, x, y = geo
        self._size, self._pos = (w, h), (x, y)
        kw = {"width": w, "height": h}
        if x is not None:
            kw["x"], kw["y"] = x, y
        return kw

    def bind_window(self):
        """Follow the window: size, position, maximised state, closing."""
        events = getattr(self.window, "events", None)

        def add(name, fn):
            ev = getattr(events, name, None)
            if ev is not None:
                ev += fn
        add("resized", self._on_resized)
        add("moved", self._on_moved)
        add("maximized", self._on_maximized)
        add("restored", self._on_restored)

    def _on_resized(self, width=None, height=None, *_a):
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            self._size = (width, height)
            self._schedule_save()

    def _on_moved(self, x=None, y=None, *_a):
        if isinstance(x, int) and isinstance(y, int):
            self._pos = (x, y)
            self._schedule_save()

    def _on_maximized(self, *_a):
        self.maximized = True

    def _on_restored(self, *_a):
        self.maximized = False

    def _schedule_save(self):
        """Save once the window stops changing (gui: _on_window_configure)."""
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            if self._shut:
                return
            self._timer = threading.Timer(self._debounce, self.save_geometry)
            self._timer.daemon = True
            self._timer.start()

    def save_geometry(self):
        """Write the geometry, unless the window is hidden or maximised: that
        size is not the one to come back to (gui: _save_window_geometry)."""
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if not self.visible or self.maximized:
            return False
        w = max(self._size[0], self.min_size[0])
        h = max(self._size[1], self.min_size[1])
        geo = format_geometry(w, h, *self._pos)
        settings = self.app.service("settings")
        try:
            if settings is not None:
                settings.set_window_geometry(geo)
            else:
                from shared.config import save_window_geometry
                save_window_geometry(geo)
        except Exception:
            log.exception("saving the window geometry failed")
            return False
        return True

    # ── Tray ───────────────────────────────────────────────────────────────
    def tray_running(self):
        p = self.tray_proc
        return p is not None and p.poll() is None

    def start_tray(self):
        """Start the helper. Not having one is not an error: the application
        runs without a tray icon (gui #77)."""
        if not self._want_tray:
            return False
        i18n = self.app.service("i18n")
        code = getattr(i18n, "code", "en") or "en"
        from shared.config import shipped_path
        lang_file = os.path.join(shipped_path("lang"), "%s.json" % code)
        try:
            cmd, env = self._tray_cmd(lang_file)
            if not cmd or not cmd[0]:
                raise FileNotFoundError("no python interpreter found")
            self.tray_proc = self._popen(cmd, env=env)
        except Exception as e:
            print("[Tray] not started (%s: %s); the app runs without a tray icon"
                  % (type(e).__name__, e), flush=True)
            self.tray_proc = None
            return False
        return True

    def stop_tray(self):
        p, self.tray_proc = self.tray_proc, None
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except Exception:
                    p.kill()
        except Exception:
            log.exception("stopping the tray helper failed")

    # ── The window ─────────────────────────────────────────────────────────
    def _set_visible(self, visible):
        if self.visible != visible:
            self.visible = visible
        self.app.bus.publish("ui.visible", visible=bool(visible))

    def show(self):
        """ui.show, the tray's Open, a second start: window up, in front."""
        try:
            self.window.show()
            try:
                self.window.restore()
            except Exception:
                pass
        except Exception:
            log.exception("showing the window failed")
        self._set_visible(True)

    def hide(self):
        try:
            self.window.hide()
        except Exception:
            log.exception("hiding the window failed")
        self._set_visible(False)

    def start_hidden(self):
        """--minimized: the window was created hidden."""
        self.visible = False
        self.app.bus.publish("ui.visible", visible=False)

    def close(self):
        """The page's close button: into the tray when there is one, else
        away (gui: WM_DELETE_WINDOW is _hide_window; without a tray there
        was no way back, so here the window leaves instead)."""
        if self.tray_running():
            self.hide()
        else:
            self.quit()

    def on_closing(self, *_a):
        """The window is about to close (pywebview's `closing`; a handler
        returning False keeps it open). With the tray this only hides."""
        if self.quitting:
            return True
        if self.tray_running():
            self.hide()
            return False
        self.quitting = True
        return True

    def quit(self):
        """ui.quit, the tray's Quit: leave, run() then shuts down."""
        self.quitting = True
        try:
            self.window.destroy()
        except Exception:
            log.exception("closing the window failed")

    def screens(self):
        """What `page` accepts: the device screens and the ones under the gear."""
        return {"home", "displaypad", "makalu67", "everest_max", "everest60", "macropad",
                "settings", "macros", "obs", "plugins"}

    # ── Commands ───────────────────────────────────────────────────────────
    def register(self):
        cmds = self.app.commands
        cmds.register("shell.state", lambda: {"visible": self.visible,
                                              "tray": self.tray_running()}, replace=True)
        if self.app.service("update") is not None and cmds.has("update.restart"):
            cmds.register("update.restart", self._restart_after_update, replace=True)
        control = self.app.service("control")
        if control is not None:
            control.attach_ui(screens=self.screens)
        self._bus_off = [
            self.app.bus.subscribe("ui.show", lambda _t, _p: self.show()),
            self.app.bus.subscribe("ui.quit", lambda _t, _p: self.quit()),
        ]

    def _restart_after_update(self):
        """gui.restart_after_update: the tray helper goes first (execve keeps
        the pid, the helper would sit on the new process); if the process
        was not replaced, the window closes."""
        self.stop_tray()
        updates = self.app.service("update")
        try:
            updates.restart()
        finally:
            self.quit()
        return {"restarting": False}

    # ── Start-up ───────────────────────────────────────────────────────────
    def start(self):
        """Tray, signals and the delayed start-up tasks. After the core runs."""
        self.start_tray()
        if self._signals is not None:
            self._signals.start()
        sched = self.app.scheduler
        if self._monitor:
            self._handles.append(sched.call_later(MONITOR_DELAY_MS, self.start_monitor))
        if self._update_check:
            self._handles.append(sched.call_later(UPDATE_DELAY_MS, self.check_for_update))

    def start_monitor(self):
        """Monitor Mode at start-up, through the Everest Max service whenever
        an Everest Max is plugged in (gui: _start_cpu_auto_clean)."""
        svc = self.app.service("everest_max")
        reg = self.app.service("devices")
        if svc is None or reg is None or not reg.present.get("everest_max"):
            return False
        try:
            svc.start_monitor(clean=True)
        except Exception:
            log.exception("starting Monitor Mode failed")
            return False
        return True

    def check_for_update(self):
        """The answer comes back as update.available."""
        svc = self.app.service("update")
        if svc is None:
            return False
        try:
            svc.check_async()
        except Exception:
            log.exception("the update check failed to start")
            return False
        return True

    # ── Shutdown ───────────────────────────────────────────────────────────
    def shutdown(self):
        """gui.destroy()'s order: the page's door, the plugins, the core (the
        device services stop their workers), the control socket, the tray
        helper, then the wait that proves no thread is inside libusb."""
        if self._shut:
            return
        self._shut = True
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        for h in self._handles:
            try:
                self.app.scheduler.cancel(h)
            except Exception:
                pass
        for off in getattr(self, "_bus_off", ()):
            try:
                off()
            except Exception:
                pass
        if self._signals is not None:
            self._signals.stop()
        if self.bridge is not None:
            self.bridge.stop()
        plugins = self.app.service("plugins")
        manager = getattr(plugins, "manager", None)
        if manager is not None:
            try:
                manager.shutdown()
            except Exception:
                log.exception("stopping the plugins failed")
        self.app.stop()
        control = self.app.service("control")
        if control is not None:
            try:
                control.stop_server()
            except Exception:
                log.exception("closing the control socket failed")
        self.stop_tray()
        if self._settle:
            import time
            time.sleep(self._settle)
        pad = self.app.service("displaypad")
        lock = getattr(pad, "usb_lock", None)
        if lock is not None and lock.acquire(timeout=3.0):
            lock.release()
