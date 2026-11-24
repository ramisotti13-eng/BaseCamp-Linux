"""The system's keyboard layout, as an XKB layout name ("de", "gb", "us"...).

Linux has several places that say it; they are tried from the most direct to
the most general and the first answer wins:

1. the XKB_DEFAULT_LAYOUT environment variable,
2. `localectl status` (X11 Layout),
3. `setxkbmap -query` (layout:),
4. GNOME: `gsettings get org.gnome.desktop.input-sources sources`,
5. KDE: LayoutList in ~/.config/kxkbrc.

Windows is not done yet: it returns None, and the caller falls back to the
interface language. Every outside call goes through `run`, so a test can
stand in for the system.
"""
import os
import re
import subprocess

from core.platform import IS_WINDOWS


def _run(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _first(layouts):
    """The first of a comma separated list ("de,us" -> "de")."""
    for part in (layouts or "").split(","):
        part = part.strip()
        if part:
            return part
    return None


def _kxkb(home):
    try:
        with open(os.path.join(home, ".config", "kxkbrc"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("LayoutList="):
                    return _first(line.split("=", 1)[1])
    except OSError:
        pass
    return None


def system_layout(environ=None, run=None, home=None):
    """The system's first keyboard layout as an XKB name, or None when unknown."""
    if IS_WINDOWS and environ is None and run is None:
        return None
    environ = os.environ if environ is None else environ
    run = run or _run
    home = home or os.path.expanduser("~")
    code = _first(environ.get("XKB_DEFAULT_LAYOUT"))
    if code:
        return code
    m = re.search(r"X11 Layout:\s*(\S+)", run(["localectl", "status"]) or "")
    if m and m.group(1) != "n/a":
        return _first(m.group(1))
    m = re.search(r"^layout:\s*(\S+)", run(["setxkbmap", "-query"]) or "", re.M)
    if m:
        return _first(m.group(1))
    out = run(["gsettings", "get", "org.gnome.desktop.input-sources", "sources"]) or ""
    m = re.search(r"\('xkb',\s*'([^']+)'\)", out)
    if m:
        return m.group(1).split("+")[0]
    return _kxkb(home)
