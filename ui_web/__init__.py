"""The new interface: HTML/CSS/JS in a pywebview window, over the core.

    static/      the page (index.html, css/, js/, fonts/, img/), served as it
                 is in git: no build step
    bridge.py    Bridge: core commands and events for the page, pictures,
                 the file picker, links, window controls
    app.py       the pywebview window (webui.py, gui.py --web)
    devserver.py the same bridge over HTTP for development in a browser
    demo.py      --demo: the whole core against stand-in devices in a
                 throw-away configuration directory

Nothing in here is imported by the CustomTkinter interface, and pywebview is
imported only by app.py, when the window opens.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
REPO = os.path.dirname(HERE)

_FALLBACK_VERSION = "4.0.0-beta.1"


def app_version():
    """The application's version: gui.py's APP_VERSION literal, read as text
    (importing gui.py would import Tk; build_source.sh greps the same line).

    When gui.py has been imported already, its value: the AppImage starts
    through `import gui` (appentry.py) and carries gui.py only compiled, so
    there is no text to read, and the update check would compare against
    the fallback below."""
    for name in ("gui", "__main__"):
        ver = getattr(sys.modules.get(name), "APP_VERSION", None)
        if isinstance(ver, str) and ver:
            return ver
    try:
        with open(os.path.join(REPO, "gui.py"), encoding="utf-8") as f:
            m = re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return _FALLBACK_VERSION
