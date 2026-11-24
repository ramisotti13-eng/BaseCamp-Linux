"""What the Qt window needs from its GUI thread beyond pywebview.

Two things, both set up by make_application() on the main thread (the GUI
thread: pywebview runs Qt there) before webview.start():

The QApplication. pywebview takes the one that exists
(QApplication.instance() or a new one), so this one comes first: a
QApplication whose event() sees a Qt-wide quit (QEvent.Quit: Ctrl+C through
pywebview's SIGINT handler, the session manager at logout) before Qt closes
the windows, and calls on_quit(True) then; on_quit(False) when the quit is
refused (a window stayed open). The pad windows (ui_web/pad_windows.py)
then stay marked open instead of counting as closed by the user. Not an
event filter on the application: that would run Python for every event of
every object, and PySide 6.10 crashed in such a filter (pywebview's
easy_drag one, measured in a container).

The garbage collector (GuiCollector). A pywebview window freed by Python's
cyclic collector on some other thread aborts the process (shared/gc_guard.py
says why). Automatic collection is switched off; a timer on the GUI thread
collects the way the automatic collector would (by its thresholds), and
gc_guard.collect() from any thread is done there.

Qt comes from qtpy, as pywebview has it; `qt` (load_qt()) is replaced in
the tests.
"""
import gc
import logging
import sys
import threading
import types

from shared import gc_guard

log = logging.getLogger("basecamp.ui_web.qt_gui")

#: How often the GUI thread looks at the collector's counts.
GC_INTERVAL_MS = 500


def load_qt():
    """QtCore, QtGui, QtWidgets (and QtDBus when it loads) as one namespace,
    through qtpy as pywebview has them; None without Qt."""
    try:
        from qtpy import QtCore, QtGui, QtWidgets
    except Exception:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except Exception:
            return None
    ns = types.SimpleNamespace(QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets, QtDBus=None)
    try:
        from qtpy import QtDBus
        ns.QtDBus = QtDBus
    except Exception:
        try:
            from PySide6 import QtDBus
            ns.QtDBus = QtDBus
        except Exception:
            pass
    return ns


def make_hop(qt, app, what="a call on the GUI thread"):
    """A QObject in the application's thread whose signal `call(fn)` runs fn
    there. Created in the calling thread (Qt adopts a Python thread for it),
    then pushed over: a queued connection delivers in the receiver's
    thread."""
    QtCore = qt.QtCore

    class _Hop(QtCore.QObject):
        call = QtCore.Signal(object)

        def __init__(self):
            super().__init__()
            self.call.connect(self.run, QtCore.Qt.QueuedConnection)

        @QtCore.Slot(object)
        def run(self, fn):
            try:
                fn()
            except Exception:
                log.exception("%s failed", what)

    hop = _Hop()
    hop.moveToThread(app.thread())
    return hop


def on_gui_thread(qt, app):
    try:
        return qt.QtCore.QThread.currentThread() == app.thread()
    except Exception:
        return threading.current_thread() is threading.main_thread()


class GuiCollector:
    """Python's cyclic garbage collector on the GUI thread only.

    start() (on the GUI thread) switches automatic collection off and
    starts a timer that collects a generation whenever the automatic
    collector would have (gc.get_count() past gc.get_threshold()), and
    installs collect() in shared/gc_guard.py."""

    def __init__(self, qt, app, interval_ms=GC_INTERVAL_MS, gc_module=gc):
        self._qt = qt
        self._app = app
        self._interval = interval_ms
        self._gc = gc_module
        self._timer = None
        self._hop = None
        self._stopped = False
        self._was_enabled = None
        self.collections = 0

    def start(self):
        self._hop = make_hop(self._qt, self._app, "a garbage collection")
        timer = self._qt.QtCore.QTimer(self._app)
        timer.timeout.connect(self.check)
        timer.start(self._interval)
        self._timer = timer
        self._was_enabled = self._gc.isenabled()
        self._gc.disable()
        gc_guard.install(self)
        return self

    def check(self):
        """One look, on the GUI thread: the generations due, as the
        automatic collector takes them."""
        gc_ = self._gc
        try:
            counts = gc_.get_count()
            limits = gc_.get_threshold()
            if counts[0] <= limits[0]:
                return
            generation = 0
            for g in (1, 2):
                if g < len(counts) and g < len(limits) and limits[g] and counts[g] > limits[g]:
                    generation = g
                else:
                    break
            gc_.collect(generation)
            self.collections += 1
        except Exception:
            log.exception("collecting garbage failed")

    def collect(self, generation=2, timeout=2.0):
        """gc_guard.collect(): now on the GUI thread, else posted there and
        waited for up to `timeout` (None when it did not run; never on the
        calling thread)."""
        if on_gui_thread(self._qt, self._app):
            return self._gc.collect(generation)
        if self._stopped or self._hop is None:
            return None
        done = threading.Event()
        out = []

        def run():
            try:
                out.append(self._gc.collect(generation))
            finally:
                done.set()
        self._hop.call.emit(run)
        if not done.wait(timeout):
            return None
        return out[0] if out else None

    def stop(self):
        """The loop has ended (on the GUI thread): no more timer. Collection
        stays off: threads still running must not free Qt objects now."""
        self._stopped = True
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass


def application_class(qt, on_quit):
    """A QApplication subclass whose event() reports a Qt-wide quit:
    on_quit(True) before Qt closes the windows, on_quit(False) when the quit
    was refused."""
    QtCore, QtWidgets = qt.QtCore, qt.QtWidgets
    quit_type = getattr(getattr(QtCore.QEvent, "Type", QtCore.QEvent), "Quit")

    class App(QtWidgets.QApplication):
        def event(self, e):
            try:
                is_quit = e.type() == quit_type
            except Exception:
                is_quit = False
            if not is_quit:
                return super().event(e)
            _report(on_quit, True)
            try:
                return super().event(e)
            finally:
                try:
                    refused = not e.isAccepted()
                except Exception:
                    refused = False
                if refused:
                    _report(on_quit, False)
    return App


def _report(on_quit, value):
    try:
        on_quit(value)
    except Exception:
        log.exception("reporting a quit failed")


def make_application(on_quit, qt=None, argv=None, collector=True):
    """On the main thread, before webview.start(): the QApplication pywebview
    will use, and the GUI-thread garbage collector. Returns (app, collector);
    (None, None) without Qt. When a QApplication exists already, it is
    kept (no quit report then)."""
    if qt is None:
        try:
            # Qt WebEngine has to be loaded before the QApplication exists.
            from qtpy import QtWebEngineWidgets  # noqa: F401
        except Exception as e:
            log.warning("Qt WebEngine did not load: %s", e)
        qt = load_qt()
    if qt is None:
        return None, None
    app = qt.QtWidgets.QApplication.instance()
    if app is None:
        try:
            app = application_class(qt, on_quit)(list(sys.argv if argv is None else argv))
        except Exception:
            log.exception("making the Qt application failed")
            app = qt.QtWidgets.QApplication.instance()
            if app is None:
                return None, None
    else:
        log.warning("a Qt application exists already; a Qt-wide quit is not seen")
    gcol = None
    if collector:
        try:
            gcol = GuiCollector(qt, app).start()
        except Exception:
            log.exception("the garbage collector stays where it is")
            gcol = None
    return app, gcol
