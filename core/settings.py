"""3.x profiles, backup and restore, autostart, splash, remembered directories.

``SettingsService`` (service ``settings``) is a thin layer over
shared/config.py, so an interface asks the core instead of importing the
configuration module, and a second interface sees the same operations. The
file formats and locations are unchanged.

shared/config.py creates the configuration directory when it is imported, so
it is imported on first use, not when this module is. Tests pass their own
``config`` object.

The profile commands here (``settings.profiles``, ``save_profile``,
``load_profile``, ``delete_profile`` and the control socket's 3.x names) are
the 3.x profiles: snapshots under CONFIG_DIR/profiles/. They stay for scripts
and act on that folder only, as before. The 4.0 device profiles are the
``profiles`` service (core/profiles.py), which takes the 3.x folders over
once without changing them.

Events: ``settings.profile_loaded {name, count}``, ``settings.profiles_changed``,
``settings.restored {count}``, ``settings.saved_colors_changed {colors}``. Loading a profile or restoring a backup does not
refresh live panels (it never did); the event lets an interface that wants to
do so.
"""
from core.commands import CommandError
from core.service import Service


class SettingsService(Service):
    name = "settings"

    def __init__(self, app, config=None):
        super().__init__(app)
        self._config = config

    @property
    def config(self):
        if self._config is None:
            import shared.config as cfg
            self._config = cfg
        return self._config

    # ── Profiles ───────────────────────────────────────────────────────────
    def profiles(self):
        return self.config.list_profiles()

    def active_profile(self):
        return self.config.get_active_profile()

    def save_profile(self, name):
        """Snapshot the configuration. Returns (safe_name, files_saved);
        ValueError for a name with nothing usable in it."""
        safe, count = self.config.save_profile(name)
        self.publish("profiles_changed")
        return safe, count

    def load_profile(self, name):
        """Restore a profile into the live files. Returns the file count;
        FileNotFoundError if there is no such profile."""
        count = self.config.load_profile(name)
        self.publish("profile_loaded", name=name, count=count)
        return count

    def delete_profile(self, name):
        done = self.config.delete_profile(name)
        if done:
            self.publish("profiles_changed")
        return done

    # ── Backup ─────────────────────────────────────────────────────────────
    def export_backup(self, path):
        return self.config.export_backup(path)

    def import_backup(self, path):
        count = self.config.import_backup(path)
        self.publish("restored", count=count)
        return count

    # ── Application ────────────────────────────────────────────────────────
    def autostart(self):
        return self.config.load_autostart_enabled()

    def set_autostart(self, enabled, ui=None):
        """`ui` "classic" or "web": which interface the entry starts (None:
        shared.config.AUTOSTART_UI, "web" since 4.0, "classic" in a start
        with --classic)."""
        if ui is None:
            self.config.save_autostart_enabled(bool(enabled))
        else:
            self.config.save_autostart_enabled(bool(enabled), ui=ui)

    def splash(self):
        return self.config.load_splash_enabled()

    def set_splash(self, enabled):
        self.config.save_splash_enabled(bool(enabled))

    def reset_last_dirs(self):
        self.config.reset_last_dirs()

    def last_dir(self, kind, default=None):
        return self.config._load_last_dir(kind, default=default)

    def remember_dir(self, kind, path):
        self.config._save_last_dir(kind, path)

    def saved_colors(self):
        return self.config.load_saved_colors()

    def set_saved_colors(self, colors):
        saved = self.config.save_saved_colors(colors)
        self.publish("saved_colors_changed", colors=saved)
        return saved

    def window_geometry(self):
        return self.config.load_window_geometry()

    def set_window_geometry(self, geo):
        self.config.save_window_geometry(geo)

    # ── Commands ───────────────────────────────────────────────────────────
    def commands(self):
        return {
            "profiles": self._cmd_profiles,
            "save_profile": self._cmd_save_profile,
            "load_profile": self._cmd_load_profile,
            "delete_profile": self._cmd_delete_profile,
            "export_backup": self._cmd_export_backup,
            "import_backup": self._cmd_import_backup,
            "set_autostart": self._cmd_set_autostart,
            "set_splash": self._cmd_set_splash,
            "get": self._cmd_get,
            "saved_colors": lambda: {"colors": self.saved_colors()},
            "set_saved_colors": lambda colors: {"colors": self.set_saved_colors(colors)},
        }

    def _cmd_profiles(self):
        return {"profiles": self.profiles(), "active": self.active_profile()}

    def _cmd_save_profile(self, profile):
        try:
            safe, count = self.save_profile(profile)
        except ValueError as e:
            raise CommandError(str(e))
        return {"name": safe, "count": count}

    def _cmd_load_profile(self, profile):
        try:
            return {"count": self.load_profile(profile)}
        except FileNotFoundError as e:
            raise CommandError(str(e))

    def _cmd_delete_profile(self, profile):
        return {"deleted": self.delete_profile(profile)}

    def _cmd_export_backup(self, path):
        return {"count": self.export_backup(path)}

    def _cmd_import_backup(self, path):
        try:
            return {"count": self.import_backup(path)}
        except Exception as e:
            raise CommandError(str(e))

    def _cmd_set_autostart(self, enabled):
        self.set_autostart(enabled)
        return {"autostart": self.autostart()}

    def _cmd_set_splash(self, enabled):
        self.set_splash(enabled)
        return {"splash": self.splash()}

    def _cmd_get(self):
        return {"autostart": self.autostart(), "splash": self.splash(),
                "profiles": self.profiles(), "active_profile": self.active_profile()}
