"""The tray icon of the web window through Qt's QSystemTrayIcon.

The window of the AppImage is pywebview's Qt backend, so a Qt application
runs in this process anyway. Its tray icon registers a StatusNotifierItem
over DBus where a StatusNotifierWatcher is there (KDE, Xfce, Cinnamon,
MATE, Budgie, GNOME with the AppIndicator extension; X11 and Wayland alike)
and docks by XEmbed where only an old X11 tray is. The tray helper
(tray_helper.py, pystray) in the AppImage can only dock by XEmbed, and on
Plasma 6 xembedsniproxy turns that into an icon of 1 x 1 pixel: none at all.

Threads. Every Qt call here runs on the Qt GUI thread, which is the main
thread (pywebview runs its toolkit there). start() is called from the
thread pywebview starts for webview.start(func=...), after it has built the
QApplication and just before the main thread enters the event loop. The way
across is a small QObject moved to the application's thread with a signal
connected to it as a queued connection: emitting the signal from any thread
posts the call to the GUI thread, where it runs once the loop takes it.

The menu is the helper's: Open, the DisplayPad pages, Quit. Open and Quit
run on a short thread of their own, as the helper's SIGUSR1 and SIGUSR2 do
(the window's show and destroy wait for pywebview's events, which must not
happen on the GUI thread). A click on the icon opens the window.

No notification area yet (a start at login, before the panel): Qt decides
once whether the DBus tray exists and keeps that answer for the life of the
process, so a look too early would get the XEmbed icon that cannot be seen.
The first look can wait for the StatusNotifierWatcher (`watcher_wait_s`;
the shell asks for that only on a --minimized start, the autostart at
login, so a desktop with an XEmbed tray only is not kept waiting on every
start), and a tray that Qt says is not there is asked for again every
POLL_S. running() is False until the icon is up, so closing the window
quits. Qt's first "no tray" calls `on_no_tray` once: a hidden window has
to come up then, or there would be no way to it.

The SNI's Id and Title are Qt's applicationName (measured with Qt 6.10:
the display name does not reach them). It is left as it is: Qt WebEngine
builds its profile paths from it.

Qt comes from qtpy (pywebview's Qt backend uses it, so it is there with
it); `qt` (a namespace with QtCore, QtGui, QtWidgets and optionally QtDBus)
replaces it in the tests, which have no display.
"""
import logging
import threading

from ui_web.qt_gui import load_qt, make_hop, on_gui_thread

log = logging.getLogger("basecamp.ui_web.qt_tray")

#: Seconds between two looks for a notification area that is not there.
POLL_S = 2.0
#: Seconds the first look waits for a StatusNotifierWatcher on the session bus.
WATCHER_WAIT_S = 120.0
#: The DBus name of the watcher (KDE's specification, used by every SNI host).
WATCHER = "org.kde.StatusNotifierWatcher"


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, name="qt-tray-action", daemon=True).start()


class QtTray:
    """One tray icon. Build it anywhere, start() it once the QApplication
    exists.

    labels       {"open", "quit", "pages"}: the menu's texts
    on_open      the window up (the helper's SIGUSR1)
    on_quit      the application leaves (the helper's SIGUSR2)
    list_pages   () -> ([(id, name), ...], current id or None); the pages
                 entry is left out while it gives none
    switch_page  (id) -> anything: the pad onto that page
    icon_paths   picture files of the icon, all sizes of one picture
    tooltip      the icon's tooltip
    on_no_tray   () -> anything: Qt's first answer was "no tray"
    qt           the Qt namespace (load_qt()), for tests
    spawn        (fn, *args): runs an action off the GUI thread, for tests
    say          one line for the console
    watcher_wait_s  seconds the first look waits for a StatusNotifierWatcher
    """

    def __init__(self, *, labels, on_open, on_quit, list_pages=None, switch_page=None,
                 icon_paths=(), tooltip="BaseCamp Linux", on_no_tray=None, qt=None, spawn=_spawn, say=None, poll_s=POLL_S,
                 watcher_wait_s=WATCHER_WAIT_S):
        self._labels = {"open": "Open", "quit": "Quit", "pages": "DisplayPad page"}
        self._labels.update(labels or {})
        self._on_open = on_open
        self._on_quit = on_quit
        self._list_pages = list_pages
        self._switch_page = switch_page
        self._icon_paths = [p for p in icon_paths if p]
        self._tooltip = tooltip
        self._on_no_tray = on_no_tray
        self._qt = qt
        self._spawn = spawn
        self._say = say or (lambda line: print(line, flush=True))
        self._poll_ms = int(poll_s * 1000)
        self._watcher_wait_s = watcher_wait_s
        self._waited_s = 0.0
        self._said_none = False
        self._app = None
        self._hop = None
        self._icon = None
        self._menu = None
        self._pages_menu = None
        self._open_action = self._quit_action = None
        self._stopped = False
        self._quit_on_close = None
        self.available = None           # None: not asked yet; then Qt's answer

    # ── From any thread ────────────────────────────────────────────────────
    def start(self):
        """Ask the GUI thread to put the icon up. False when there is no Qt
        application to put it into (then the helper is the way)."""
        if self._qt is None:
            self._qt = load_qt()
        if self._qt is None:
            return False
        app = self._qt.QtWidgets.QApplication.instance()
        if app is None:
            return False
        self._app = app
        self._stopped = False
        try:
            self._hop = self._make_hop()
        except Exception:
            log.exception("the tray cannot reach the Qt GUI thread")
            return False
        self._hop.call.emit(self._build)
        return True

    def running(self):
        """True while the icon is in a notification area: closing the
        window then only hides it."""
        icon = self._icon
        return bool(icon is not None and self.available and not self._stopped)

    def set_labels(self, labels):
        """The application's language changed: the menu follows."""
        self._labels.update(labels or {})
        self.on_gui(self._relabel)

    def stop(self, timeout=1.0):
        """The icon goes. On the GUI thread at once (the shutdown, after the
        loop has ended), else posted there and waited for up to `timeout`
        (an update's restart, while the loop still runs)."""
        self._stopped = True
        if self._hop is None:
            return
        if self._on_gui_thread():
            self._remove()
            return
        done = threading.Event()

        def run():
            try:
                self._remove()
            finally:
                done.set()
        self._hop.call.emit(run)
        done.wait(timeout)

    def on_gui(self, fn):
        """Run `fn` on the GUI thread: now when this is it, else posted."""
        if self._hop is None:
            return False
        if self._on_gui_thread():
            fn()
        else:
            self._hop.call.emit(fn)
        return True

    # ── The way to the GUI thread ──────────────────────────────────────────
    def _on_gui_thread(self):
        return on_gui_thread(self._qt, self._app)

    def _make_hop(self):
        """A QObject in the application's thread whose signal `call(fn)` runs
        fn there (ui_web/qt_gui.py make_hop)."""
        return make_hop(self._qt, self._app, "tray: a call on the GUI thread")

    # ── On the GUI thread ──────────────────────────────────────────────────
    def _later(self, fn):
        self._qt.QtCore.QTimer.singleShot(self._poll_ms, fn)

    def _watcher_present(self):
        """True or False when the session bus can say whether a
        StatusNotifierWatcher is there, None when it cannot (no QtDBus, no
        session bus)."""
        QtDBus = getattr(self._qt, "QtDBus", None)
        if QtDBus is None:
            return None
        try:
            bus = QtDBus.QDBusConnection.sessionBus()
            if not bus.isConnected():
                return None
            reply = bus.interface().isServiceRegistered(WATCHER)
            value = reply.value() if hasattr(reply, "value") else reply
            return bool(value)
        except Exception:
            return None

    def _build(self):
        if self._stopped or self._icon is not None:
            return
        W = self._qt.QtWidgets
        if (self._watcher_present() is False
                and self._waited_s < self._watcher_wait_s):
            # Not Qt's question yet: its answer about the DBus tray is
            # final, and a no now would be a no for good.
            if self._waited_s == 0:
                self._say("[Tray] waiting for the notification area (%s)" % WATCHER)
            self._waited_s += self._poll_ms / 1000.0
            self._later(self._build)
            return
        try:
            self.available = bool(W.QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:
            log.exception("asking Qt for a system tray failed")
            self.available = False
        if not self.available:
            if not self._said_none:
                self._said_none = True
                self._say("[Tray] no notification area here; closing the window quits "
                          "(looking again every %g s)" % (self._poll_ms / 1000.0))
                if self._on_no_tray is not None:
                    self._spawn(self._on_no_tray)
            self._later(self._build)
            return
        icon = W.QSystemTrayIcon(self._make_icon(), self._app)
        icon.setToolTip(self._tooltip)
        menu = W.QMenu()
        self._open_action = menu.addAction(self._labels["open"])
        self._open_action.triggered.connect(self._open)
        self._pages_menu = W.QMenu(self._labels["pages"], menu)
        menu.addMenu(self._pages_menu)
        self._quit_action = menu.addAction(self._labels["quit"])
        self._quit_action.triggered.connect(self._quit)
        menu.aboutToShow.connect(self._fill_pages)
        icon.setContextMenu(menu)
        icon.activated.connect(self._activated)
        self._menu = menu
        self._fill_pages()
        # Hidden window plus a menu that opens and closes: by Qt's default
        # that is the last window closed and the application would end.
        # pywebview ends the loop itself when its last window closes.
        try:
            self._quit_on_close = self._app.quitOnLastWindowClosed()
            self._app.setQuitOnLastWindowClosed(False)
        except Exception:
            self._quit_on_close = None
        icon.show()
        self._icon = icon
        self._say("[Tray] icon up (Qt)")

    def _make_icon(self):
        G = self._qt.QtGui
        icon = G.QIcon()
        for path in self._icon_paths:
            icon.addFile(path)
        return icon

    def _relabel(self):
        if self._open_action is not None:
            self._open_action.setText(self._labels["open"])
            self._quit_action.setText(self._labels["quit"])
            self._pages_menu.setTitle(self._labels["pages"])

    def _pages(self):
        if self._list_pages is None:
            return [], None
        try:
            pages, current = self._list_pages()
            return list(pages or ()), current
        except Exception as e:
            self._say("[Tray] page menu: %s: %s" % (type(e).__name__, e))
            return [], None

    def _fill_pages(self):
        """The pages are read when the menu opens (the root menu: its
        submenu is part of it): they are created and renamed while the
        application runs."""
        sub = self._pages_menu
        if sub is None:
            return
        pages, current = self._pages()
        sub.clear()
        for pid, name in pages:
            act = sub.addAction(str(name))
            act.setCheckable(True)
            act.setChecked(pid == current)
            act.triggered.connect(self._switcher(pid))
        sub.menuAction().setVisible(bool(pages))

    def _switcher(self, pid):
        def switch(*_a):
            if self._switch_page is not None:
                self._spawn(self._switch, pid)
        return switch

    def _switch(self, pid):
        try:
            self._switch_page(pid)
        except Exception as e:
            self._say("[Tray] page %s: %s: %s" % (pid, type(e).__name__, e))

    def _open(self, *_a):
        self._spawn(self._on_open)

    def _quit(self, *_a):
        self._spawn(self._on_quit)

    def _activated(self, reason=None):
        R = self._qt.QtWidgets.QSystemTrayIcon
        # A click (Trigger) or a double click opens; the context menu and
        # the middle button do not.
        if reason in (getattr(R, "Trigger", object()), getattr(R, "DoubleClick", object())):
            self._open()

    def _remove(self):
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.hide()
                icon.deleteLater()
            except Exception:
                log.exception("removing the tray icon failed")
        if self._menu is not None:
            try:
                self._menu.deleteLater()
            except Exception:
                pass
            self._menu = self._pages_menu = None
            self._open_action = self._quit_action = None
        if self._quit_on_close is not None:
            try:
                self._app.setQuitOnLastWindowClosed(self._quit_on_close)
            except Exception:
                pass
            self._quit_on_close = None
