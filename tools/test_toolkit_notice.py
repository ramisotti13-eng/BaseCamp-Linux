#!/usr/bin/env python3
"""
Checks the notice a source install gets when its CustomTkinter is newer than
this version of the application handles (#116).

    python3 tools/test_toolkit_notice.py

It needs a display. A DisplayPad plugged into the machine is hidden from the
application, Monitor Mode is kept from starting, and the configuration goes to
a temporary directory, so no device and no setting of yours is touched.
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("no display, skipping the notice checks")
    sys.exit(0)

_TMP_HOME = tempfile.mkdtemp(prefix="basecamp-toolkit-test-")
os.environ["HOME"] = _TMP_HOME
_RUN = tempfile.mkdtemp(prefix="tk-", dir="/tmp")
os.environ["XDG_RUNTIME_DIR"] = _RUN

import customtkinter as ctk                           # noqa: E402

import gui                                            # noqa: E402
import shared.ui as UI                                # noqa: E402
from shared.ui import dropdown                        # noqa: E402
from devices.displaypad import panel as dpp           # noqa: E402
from devices.everest_max import panel as emp          # noqa: E402
from shared import hid_compat                         # noqa: E402
from shared.config import TOOLKIT_NOTICE_FILE         # noqa: E402

emp.EverestMaxPanel._start_cpu_auto = lambda self: None
emp.EverestMaxPanel._start_cpu_auto_clean = lambda self: None
emp.EverestMaxPanel._toggle_cpu = lambda self: None
_real_enum = hid_compat.enumerate
hid_compat.enumerate = lambda vid=0, pid=0, *a, **k: (
    [] if (vid, pid) == (dpp.VID, dpp.PID) else _real_enum(vid, pid, *a, **k))

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-62s %s" % (name, detail), flush=True)
    if not ok:
        failures.append(name)


app = gui.App()
app.geometry("1000x700")


def pump(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


shown = []
real_dialog = UI.ConfirmDialog


def recorder(parent, title, message, ok_text, cancel_text=None, danger=False,
             detail=None):
    shown.append({"title": title, "message": message, "detail": detail})


def checks():
    ver = ctk.__version__
    major_minor = ".".join((ver.split(".") + ["0"])[:2])
    UI.ConfirmDialog = recorder

    check("the application's own start said nothing", shown == [], shown)

    dropdown.DECLINED = ()
    app._check_toolkit()
    check("a CustomTkinter the list fits: no notice", shown == [], shown)

    dropdown.DECLINED = ("frobnicate",)
    app._check_toolkit()
    check("one the list does not fit: a notice", len(shown) == 1, shown)
    n = shown[0] if shown else {"title": "", "message": "", "detail": ""}
    check("naming the installed version", ver in n["message"], n["message"])
    check("and saying how to get out of it",
          "git pull" in n["detail"]
          and 'pip install "customtkinter<%s"' % major_minor in n["detail"],
          n["detail"])

    app._check_toolkit()
    check("once, not on every start", len(shown) == 1, len(shown))
    check("remembered in the configuration", os.path.isfile(TOOLKIT_NOTICE_FILE))

    real_ver = ctk.__version__
    ctk.__version__ = "9.9.9"
    app._check_toolkit()
    ctk.__version__ = real_ver
    check("another CustomTkinter is told about again", len(shown) == 2, len(shown))

    os.remove(TOOLKIT_NOTICE_FILE)
    gui._FROZEN = True
    app._check_toolkit()
    gui._FROZEN = False
    check("the AppImage, which carries its own, says nothing", len(shown) == 2,
          len(shown))

    # The real window, closed by its button.
    UI.ConfirmDialog = real_dialog
    seen = {}

    def close_it():
        for w in app.winfo_children():
            if isinstance(w, real_dialog):
                seen["texts"] = texts(w)
                w._ok()
                return
        app.after(100, close_it)

    app.after(300, close_it)
    app._check_toolkit()
    got = seen.get("texts", [])
    check("the window shows the version and the commands",
          any(ver in t for t in got)
          and any('customtkinter<%s' % major_minor in t for t in got), got)
    dropdown.DECLINED = ()


def texts(widget):
    out = []
    for child in widget.winfo_children():
        try:
            out.append(str(child.cget("text")))
        except Exception:
            pass
        out.extend(texts(child))
    return out


def run():
    try:
        checks()
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("raised, see the traceback above")
    finally:
        app.after(50, app.destroy)


app.after(2500, run)        # after the application's own check at 1.5 s
app.mainloop()
shutil.rmtree(_TMP_HOME, ignore_errors=True)
shutil.rmtree(_RUN, ignore_errors=True)
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
