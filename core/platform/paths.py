"""Where the application keeps its files.

The configuration directory keeps the name it has had since 1.x,
"mountain-time-sync", on every OS, so that a configuration copied from one
machine to another is found where the application looks for it:

    Linux    ~/.config/mountain-time-sync
    Windows  %APPDATA%\\mountain-time-sync
"""
import os

from core.platform import IS_WINDOWS

APP_DIR_NAME = "mountain-time-sync"


def config_dir(home):
    """The configuration directory for the user whose home is `home`."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, APP_DIR_NAME)
    return os.path.join(home, ".config", APP_DIR_NAME)


def runtime_dir():
    """A per-user directory for short-lived files that need not survive a
    reboot, or None where there is none we can trust.

    On Linux that is $XDG_RUNTIME_DIR, which is memory; config.py checks its
    ownership before using it. Windows has no equivalent we can count on to be
    memory-backed, so callers fall back to their own choice.
    """
    if IS_WINDOWS:
        return None
    run = os.environ.get("XDG_RUNTIME_DIR", "")
    return run if run and os.path.isabs(run) else None
