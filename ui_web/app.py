"""The new interface in a pywebview window.

    python3 gui.py [--debug] [--minimized]      (the default interface since 4.0)
    python3 webui.py [--demo] [--debug] [--minimized]

gui.py (and the AppImage) come here unless --classic is given or the web
window cannot open on this machine (ui_web/launch.py decides, and the
classic interface starts in its place).

A frameless window (1440x900, at least 1100x700) shows ui_web/static, served
by pywebview's own HTTP server so the page's ES modules load; the page
reaches the core through Bridge.api (window.pywebview.api) and hears its
events as "bc:events" DOM events. Elements with the class
`pywebview-drag-region` move the window (the top bar).

One instance per session, as with gui.py: if the control socket answers, the
running one is asked to show its window and this one leaves. The running
one may be either interface. --demo skips that check (its socket is
private) and runs on stand-in devices (ui_web/demo.py).

Behaviour around the window (tray, close, geometry, start-up tasks, shutdown
order) is ui_web/shell.py. No splash screen here: the window paints its own
background at once and the page fills in as the core answers, so there is
nothing to cover (3.x needed one for the seconds Tk and CustomTkinter took to
build). The "splash" setting is the 3.x interface's and stays in the shared
configuration; the web settings page does not offer it.

pywebview is imported here and only when the window opens: it is not a
dependency of the CustomTkinter interface, and the tests run without it.
The backend it gets: Qt WebEngine in the AppImage (PySide6, bundled), the
distribution's WebKit2GTK or Qt WebEngine in a source install.
"""
import argparse
import os
import sys

TITLE = "BaseCamp Linux"
SIZE = (1440, 900)
MIN_SIZE = (1100, 700)
#: Shown until the page has painted: the page's own background.
BACKGROUND = "#0a0d14"

_INSTALL_HINT = ("The new interface needs pywebview and a web view for it: Qt WebEngine "
                 "or WebKit2GTK (README, Installation, From source). "
                 "gui.py --classic starts the classic interface.")


class WindowControls:
    """The page's window buttons on a pywebview window. `tray()` says
    whether a tray icon is up; close then only hides the window."""

    def __init__(self, window, tray=None, shell=None):
        self._w = window
        self._tray = tray or (lambda: False)
        self._shell = shell
        self.maximized = False
        events = getattr(window, "events", None)
        for name, handler in (("maximized", self._on_maximized),
                              ("restored", self._on_restored)):
            ev = getattr(events, name, None)
            if ev is not None:
                ev += handler

    def _on_maximized(self, *_a):
        self.maximized = True

    def _on_restored(self, *_a):
        self.maximized = False

    def minimize(self):
        self._w.minimize()

    def toggle_maximize(self):
        if self.maximized:
            self._w.restore()
            self.maximized = False
        else:
            self._w.maximize()
            self.maximized = True

    def close(self):
        if self._shell is not None:
            self._shell.close()
        elif self._tray():
            self._w.hide()
        else:
            self._w.destroy()

    def show(self):
        if self._shell is not None:
            self._shell.show()
            return
        self._w.show()
        try:
            self._w.restore()
        except Exception:
            pass


def file_picker(webview, window_ref):
    """(title, kind) -> path or None, through pywebview's native dialog."""
    def pick(title, kind, name=""):
        window = window_ref()
        if window is None:
            return None
        wanted = {"folder": "FOLDER", "save": "SAVE"}.get(kind, "OPEN")
        legacy = {"FOLDER": "FOLDER_DIALOG", "SAVE": "SAVE_DIALOG", "OPEN": "OPEN_DIALOG"}[wanted]
        dialog = getattr(getattr(webview, "FileDialog", None), wanted, None)
        if dialog is None:
            dialog = getattr(webview, legacy)
        types = (("Images (*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp)", "All files (*.*)")
                 if kind == "image" else ())
        extra = {"save_filename": name} if kind == "save" and name else {}
        result = window.create_file_dialog(dialog, allow_multiple=False, file_types=types, **extra)
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return result[0] if result else None
        return result
    return pick


def already_running(minimized=False):
    """True when a BaseCamp already answers on the control socket; its
    window is asked to come up unless this start is a minimised one."""
    try:
        from shared.ipc import send_command
        running = send_command({"cmd": "ping"}, timeout=1.5).get("ok", False)
        if running and not minimized:
            send_command({"cmd": "show"}, timeout=1.5)
    except Exception:
        running = False
    return bool(running)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="webui.py", description="BaseCamp Linux, the new interface.")
    ap.add_argument("--demo", action="store_true",
                    help="stand-in devices and a throw-away configuration (no hardware)")
    ap.add_argument("--debug", action="store_true", help="the web inspector")
    ap.add_argument("--minimized", action="store_true", help="start with the window hidden")
    ap.add_argument("--no-tray", action="store_true", help="no tray icon")
    from ui_web import demo_photos
    demo_photos.add_argument(ap)
    args, _unknown = ap.parse_known_args(argv)
    return args


def main(argv=None, webview=None, build=None, tray=None, shell_options=None):
    """Run the window until it closes. Returns the exit code.

    `webview` (the pywebview module) and `build` (() -> a CoreApp, not
    started) are for tests; by default pywebview is imported and the core
    comes from core.bootstrap (or the demo). `tray` True/False overrides
    whether the tray helper starts (default: yes, except --demo and
    --no-tray); `shell_options` are more keyword arguments for
    ui_web.shell.Shell."""
    args = parse_args(sys.argv[1:] if argv is None else argv)

    home = None
    demo = None
    if args.demo:
        from ui_web import demo
        try:
            home = demo.prepare()
        except RuntimeError:
            print("[webui] --demo has to be started with webui.py: the configuration "
                  "is already loaded here.", file=sys.stderr)
            return 2

    toolkit = None
    if webview is None:
        # The toolkit's environment first (backend, X11 under Wayland, Qt
        # WebEngine's sandbox), then the backend itself: ui_web/launch.py.
        # gui.run() asked the same before choosing this interface.
        from ui_web import launch
        backend, why = launch.ready()
        if backend is None:
            print("[webui] %s. %s" % (why, _INSTALL_HINT), file=sys.stderr)
            if home:
                demo.cleanup(home)
            return 2
        toolkit = backend
        import webview

    if not args.demo:
        if already_running(args.minimized):
            if not args.minimized:
                print("[BaseCamp] already running, brought the existing window to the front",
                      flush=True)
            return 0
        # Before anything goes online (the plugin index): see shared/tls.py.
        from shared.tls import ensure_ca_bundle
        ensure_ca_bundle()

    from ui_web import STATIC_DIR, app_version
    from ui_web.bridge import Bridge
    from core.bootstrap import build_core, start_core

    if build is not None:
        app = build()
    elif args.demo:
        app, _pad = demo.build()
    else:
        app = build_core(version=app_version())
    from ui_web import demo_photos
    demo_photos.apply(args, say=lambda m: print("[webui] " + m, flush=True))

    from ui_web.shell import Shell
    holder = {}
    bridge = Bridge(app, picker=file_picker(webview, lambda: holder.get("window")))
    index = os.path.join(STATIC_DIR, "index.html")
    if tray is None:
        tray = not (args.demo or args.no_tray)
    # With the Qt window the tray icon is Qt's own (ui_web/qt_tray.py).
    title = TITLE + (" (demo)" if args.demo else "")
    options = {"tray": tray, "monitor": not args.demo, "toolkit": toolkit, "title": title}
    options.update(shell_options or {})
    shell = Shell(app, None, bridge, min_size=MIN_SIZE, size=SIZE, **options)
    # The size and position of the last run (3.x's file), never below the minimum.
    window = webview.create_window(
        title, url=index, js_api=bridge.api,
        min_size=MIN_SIZE, frameless=True,
        easy_drag=False, background_color=BACKGROUND, hidden=args.minimized,
        **shell.window_kwargs())
    holder["window"] = window
    shell.window = window
    controls = WindowControls(window, tray=shell.tray_running, shell=shell)
    bridge.attach_webview(window, controls)
    window.events.loaded += lambda *_a: bridge.page_loaded()
    # Virtual pads in windows of their own (ui_web/pad_windows.py).
    from shared.config import CONFIG_DIR
    from ui_web.pad_windows import PadWindows
    pad_windows = PadWindows(app, bridge, STATIC_DIR, webview, CONFIG_DIR,
                             lambda: shell.quitting, background=BACKGROUND)
    shell.pad_windows = pad_windows

    def closing(*_a):
        # A close that only hides the window keeps the page alive. One that
        # ends the application takes the pad windows along (the toolkit
        # would keep running while one is open); they stay marked open.
        keep = shell.on_closing()
        if keep is not False:
            bridge.page_gone()
            pad_windows.app_quitting()
        return keep
    window.events.closing += closing
    shell.bind_window()
    if args.minimized:
        shell.start_hidden()
    # What the control socket asks of the window (gui: _on_ui_request); the
    # page handles ui.page itself.
    shell.register()

    bridge.start()
    start_core(app)
    # Phones in the LAN (remote access): a server only while it is switched on.
    from ui_web.remote_server import RemoteHost
    remote_host = RemoteHost(app, bridge, STATIC_DIR).start()
    shell.start()
    collector = None
    if toolkit == "qt":
        # The QApplication pywebview will use, before anything makes one
        # (restore() may ask for the screens): it reports a Qt-wide quit to
        # the pad windows, and Python's garbage collector runs on its
        # thread only (ui_web/qt_gui.py).
        from ui_web import qt_gui
        _qapp, collector = qt_gui.make_application(pad_windows.system_quit)
    try:
        # The pad windows open at the last quit come back with the window
        # (also --minimized: they are windows of their own). After the core
        # runs: the virtual pads answer through its scheduler.
        pad_windows.restore()
        # func runs on a thread of its own once the toolkit is set up (the
        # QApplication exists), just before its loop: the Qt tray goes up.
        kwargs = {"func": shell.gui_started, "http_server": True, "debug": args.debug}
        icon = _icon()
        if icon:
            kwargs["icon"] = icon
        try:
            webview.start(**kwargs)
        except TypeError:           # a pywebview without icon=
            kwargs.pop("icon", None)
            webview.start(**kwargs)
    finally:
        if collector is not None:
            collector.stop()
        shell.save_geometry()
        pad_windows.stop()
        # Before the bridge stops (shell.shutdown): no phone calls in after it.
        remote_host.stop()
        shell.shutdown()
        if home:
            demo.cleanup(home)
    return 0


def _icon():
    from core import runtime
    for base in (runtime.RES, runtime.HERE):
        path = os.path.join(base, "resources", "app_icon_256.png")
        if os.path.isfile(path):
            return path
    return None
