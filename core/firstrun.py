"""The first start of 4.x on a configuration 3.x wrote: save it first.

4.x reads and writes the same files as 3.x and takes the 3.x profiles over
on its first start (core.profiles_migrate). Someone who comes by the in-app
update reads no release notes, so before the core starts the settings are
saved once to `mountain-time-sync-backup-before-4.0.zip` beside the
configuration folder (shared.config.export_backup: the small files, not the
picture libraries). 3.2.5's updater saves its own copy before it replaces the
AppImage; this one is for everyone who comes from an older 3.x or installs
the 4.x AppImage by hand.

A marker in the configuration folder says it was done (or that there was
nothing to save), so it happens once.
"""
import logging
import os

log = logging.getLogger("basecamp.core.firstrun")

MARKER = "v4_first_start"
BACKUP_NAME = "mountain-time-sync-backup-before-4.0.zip"


def backup_path(config_dir):
    return os.path.join(os.path.dirname(os.path.normpath(config_dir)), BACKUP_NAME)


def _has_settings(config_dir):
    """Any file a 3.x (or anyone) wrote. A fresh start makes only plugins/
    (the bundled plugins) and an empty profiles/."""
    for root, dirs, files in os.walk(config_dir):
        if os.path.relpath(root, config_dir) == ".":
            dirs[:] = [d for d in dirs if d != "plugins"]
            files = [f for f in files if f != MARKER]
        if files:
            return True
    return False


def backup_before_first_start(config_dir, export=None):
    """Save a 3.x configuration once before 4.x first touches it. Returns
    the backup's path when one was written, else None. Never raises: a
    backup that fails is logged and the application starts anyway."""
    marker = os.path.join(config_dir, MARKER)
    if os.path.exists(marker):
        return None
    written = None
    if _has_settings(config_dir):
        dest = backup_path(config_dir)
        if not os.path.exists(dest):
            try:
                if export is None:
                    from shared.config import export_backup as export
                export(dest)
                written = dest
                log.info("settings saved before the first 4.x start: %s", dest)
            except Exception as e:
                log.warning("could not save the settings before the first 4.x start: %s", e)
                return None         # no marker: tried again on the next start
    try:
        os.makedirs(config_dir, exist_ok=True)
        with open(marker, "w") as f:
            f.write("1")
    except OSError as e:
        log.warning("first start marker: %s", e)
    return written
