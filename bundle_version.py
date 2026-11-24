"""Build helper for the .spec files: the BUNDLED_VERSION data file.

Every bundle carries the APP_VERSION of gui.py it was built from, so that
_overlay_bootstrap.py can leave out a source overlay older than the bundle.

    sys.path.insert(0, SPECPATH)
    from bundle_version import bundle_version_data
    datas=[..., bundle_version_data(workpath)]
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))


def app_version():
    """APP_VERSION as gui.py states it (read as text, gui is not imported)."""
    with open(os.path.join(_HERE, "gui.py"), "r", encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("bundle_version: no APP_VERSION in gui.py")
    return m.group(1)


def bundle_version_data(workpath):
    """Writes BUNDLED_VERSION into PyInstaller's work directory and returns
    the (source, destination) pair for an Analysis' datas: the file lands
    beside the bundled modules (sys._MEIPASS)."""
    os.makedirs(workpath, exist_ok=True)
    path = os.path.join(workpath, "BUNDLED_VERSION")
    with open(path, "w", encoding="utf-8") as f:
        f.write(app_version() + "\n")
    return (path, ".")
