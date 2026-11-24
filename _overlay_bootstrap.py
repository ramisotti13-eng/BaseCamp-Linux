"""PyInstaller runtime hook: runs BEFORE the entry script.

If a source-overlay directory with a VERSION file exists under
~/.local/share/basecamp-linux/source-overlay/, prepend it to sys.path so
that user modules (gui, shared, devices, plugins, ...) resolve to the
overlay's .py files instead of the bundled .pyc copies in _internal/.

An overlay older than this bundle is left out: a new AppImage beside the
overlay an older one downloaded (a 4.0 AppImage on a machine that runs 3.x)
would otherwise start the old code. The bundle's own version is the
BUNDLED_VERSION file the .spec files put next to the bundled modules
(bundle_version.py); a bundle without it uses any overlay, as before.

Native deps (customtkinter, PIL, hid, ...) stay bundled; the overlay
only ever contains pure-Python code, so this can't break the runtime.
A broken/empty overlay is silently ignored.
"""
import os
import sys


_PRE_RANK = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "rc": 3}


def _version_key(text):
    """'4.0.0-beta.2' -> ((4, 0, 0, 0), 0, 2, 2); a release ranks above its
    pre-releases, and those rank dev < alpha < beta < rc, then by number
    (as core.updates.version_tuple). None when it is not a version."""
    main, dash, pre = text.strip().partition("-")
    parts = main.split(".")
    if not 1 <= len(parts) <= 4 or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts] + [0] * (4 - len(parts))
    if not dash:
        return tuple(nums), 1, 0, 0
    word = pre.rstrip("0123456789").rstrip(".-_")
    num = pre[len(pre.rstrip("0123456789")):]
    return tuple(nums), 0, _PRE_RANK.get(word.lower(), 0), int(num) if num else 0


def _bundled_version():
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    try:
        with open(os.path.join(base, "BUNDLED_VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


try:
    _overlay = os.path.join(os.path.expanduser("~"),
                            ".local", "share", "basecamp-linux",
                            "source-overlay")
    _version = os.path.join(_overlay, "VERSION")
    if os.path.isdir(_overlay) and os.path.isfile(_version):
        with open(_version, "r", encoding="utf-8") as _f:
            _ver = _f.read().strip()
        # Sanity: a 'gui.py' must be present, otherwise the overlay is
        # incomplete / mid-extraction and we'd crash on import.
        if _ver and os.path.isfile(os.path.join(_overlay, "gui.py")):
            _bundled = _bundled_version()
            _bkey = _version_key(_bundled) if _bundled else None
            _okey = _version_key(_ver)
            if _bkey is not None and (_okey is None or _okey < _bkey):
                # Said once: the helpers the application starts (controllers,
                # tray) inherit the variable and stay quiet, so the line does
                # not end up in every controller's stderr.
                if os.environ.get("BASECAMP_OVERLAY_SKIPPED") != _ver:
                    print("[BaseCamp] source overlay %s is %s this build (%s); not used"
                          % (_ver, "older than" if _okey else "not comparable with", _bundled),
                          file=sys.stderr, flush=True)
                os.environ["BASECAMP_OVERLAY_SKIPPED"] = _ver
            else:
                sys.path.insert(0, _overlay)
                os.environ["BASECAMP_OVERLAY_VERSION"] = _ver
except Exception:
    pass
