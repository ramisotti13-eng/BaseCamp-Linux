"""How this process runs: frozen or from source, and where its helpers are.

Moved out of gui.py so that any interface (and the core itself) builds the
command line of a controller the same way. gui.py keeps ``_FROZEN``, ``_BIN``,
``_cmd`` and ``_cmd_for_device`` as names that delegate here, because tests
patch ``gui._FROZEN``; for that, ``controller_cmd`` takes the frozen flag as
an optional argument.
"""
import os
import sys

# The directory that holds gui.py and core/. In the source overlay of an
# updated AppImage core/ travels beside gui.py, so this is right there too.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Deliberately PyInstaller only. A Nuitka build sets __compiled__ instead, but
# counting it as frozen here would send the code below into sys._MEIPASS, which
# only PyInstaller defines, and the app would not import at all. Nuitka is
# handled where it actually differs: the interpreter it names does not exist
# (see interpreter()) and the helper it would start was never shipped (#77).
FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    BIN = os.path.dirname(sys.executable)
    RES = getattr(sys, "_MEIPASS", BIN)
    PYTHON = None
else:
    BIN = HERE
    RES = HERE
    PYTHON = sys.executable

# device id -> (frozen binary name, script path relative to HERE)
_CONTROLLERS = {
    "everest_max": ("basecamp-controller", "emax_controller.py"),
    "makalu67": ("makalu-controller",
                 os.path.join("devices", "makalu67", "controller.py")),
    "everest60": ("everest60-controller",
                  os.path.join("devices", "everest60", "controller.py")),
}


def interpreter():
    """A python that actually exists, or None.

    sys.executable is normally right, but a compiled build can name an
    interpreter that was never shipped: Nuitka reports <dist>/python3, and
    starting a helper with it fails with FileNotFoundError. Fall back to
    whatever python3 is on PATH before giving up (#77).
    """
    exe = sys.executable
    if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
        return exe
    import shutil
    return shutil.which("python3") or shutil.which("python")


def controller_cmd(device_id, *args, frozen=None):
    """The command line that runs a device's controller with `args`.

    Ids other than makalu67 and everest60 get the Everest Max controller,
    as they always did. `frozen` overrides FROZEN (a test patches it).
    """
    if frozen is None:
        frozen = FROZEN
    binary, script = _CONTROLLERS.get(device_id, _CONTROLLERS["everest_max"])
    if frozen:
        return [os.path.join(BIN, binary)] + list(args)
    return [PYTHON, os.path.join(HERE, script)] + list(args)
