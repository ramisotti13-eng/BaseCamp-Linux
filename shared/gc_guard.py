"""Which thread Python's cyclic garbage collector runs on.

Python collects reference cycles on whatever thread happens to allocate
when a threshold is crossed. A cycle can hold a Qt object that Python owns:
pywebview's window (BrowserView, a QMainWindow) and its pywebview Window
point at each other, so a closed window is freed only by the collector.
Freeing a window that drew through OpenGL (Qt WebEngine does) off the GUI
thread aborts the process: "Cannot make QOpenGLContext current in a
different thread" (QRhi's destructor, measured with Qt 6.10).

With the Qt window, ui_web/qt_gui.py switches automatic collection off and
collects on the GUI thread instead, and installs itself here. Code that
wants a collection now (the DisplayPad, before it looks for the device
again) calls collect(): on the GUI thread then, waited for; without Qt,
gc.collect() as before.
"""
import gc

_owner = None


def install(owner):
    """`owner.collect(generation, timeout)` takes every collect() from now on."""
    global _owner
    _owner = owner


def uninstall(owner):
    global _owner
    if _owner is owner:
        _owner = None


def collect(generation=2, timeout=2.0):
    """A collection now: gc.collect() without an owner, else the owner's
    (on its thread; None when it did not happen within `timeout`)."""
    owner = _owner
    if owner is None:
        return gc.collect(generation)
    return owner.collect(generation, timeout)
