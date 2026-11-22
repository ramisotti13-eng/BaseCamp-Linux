"""Shared configuration paths and load/save helpers for BaseCamp Linux."""
import os
import re
import sys
import json
import pwd as _pwd
from PIL import Image


def shipped_path(name):
    """Path to a data file that the source overlay ships (lang/, presets).

    A frozen build has two candidate roots and picking the wrong one makes an
    update silently do nothing. `sys._MEIPASS` is the PyInstaller bundle and
    never changes; `__file__` moves with the source overlay, because that is
    where our modules are loaded from once an overlay is active. Anything
    build_source.sh puts in the tarball must therefore be looked up relative to
    `__file__`, or an overlay update can ship a new file that nothing ever
    reads. That is exactly what happened to the language files: the overlay
    carried them, the app kept reading the bundled copies, and every key added
    after the AppImage was built showed up in the interface as its own name.

    Files the overlay deliberately leaves out, resources/ above all, must keep
    using _MEIPASS: they only exist in the bundle.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(here, name)
    if os.path.exists(candidate) or not getattr(sys, "frozen", False):
        return candidate
    return os.path.join(getattr(sys, "_MEIPASS", here), name)


_JSON_WARNED_MTIMES = {}

def _warn_json(path, exc):
    """Print a console warning for a JSON config file that couldn't be
    parsed (malformed content, permission issues, etc.). A simply-missing
    file (FileNotFoundError) is normal on first run and is NOT warned about
    here -- callers that care about that distinction check it separately.

    Some callers (e.g. _load_all_displaypad_pages) get re-invoked very
    frequently (page-name lookups, action execution, GUI refresh, ...), so
    without dedup a single broken file would print the same warning dozens
    of times per second. Only warn once per distinct file content: track
    the file's mtime and stay silent on repeat reads of the same broken
    content, but warn again if the file changes (e.g. the user edited it,
    or a fresh write got interrupted again)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if path in _JSON_WARNED_MTIMES and _JSON_WARNED_MTIMES[path] == mtime:
        return
    _JSON_WARNED_MTIMES[path] = mtime
    print(f"[BaseCamp] warning: failed to parse JSON config '{path}': "
          f"{type(exc).__name__}: {exc}", file=sys.stderr)


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _warn_json(path, e)
        raise

# ── Path setup ─────────────────────────────────────────────────────────────────

def _resolve_real_user():
    """Return (home_dir, uid, gid) for the invoking user.

    When running under sudo we honour SUDO_USER, but only if it resolves to a
    real non-root account — protects against a poisoned env var that could
    redirect root's file writes into an arbitrary user's tree.
    """
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            pw = _pwd.getpwnam(sudo_user)
        except KeyError:
            pw = None
        if pw and pw.pw_uid != 0:
            return pw.pw_dir, pw.pw_uid, pw.pw_gid
    return os.path.expanduser("~"), None, None


_real_home, _real_uid, _real_gid = _resolve_real_user()


def _ensure_owned_dir(path):
    """Create a directory and, when running as root, chown it to the invoking user
    so the user can manage their own config outside the app."""
    os.makedirs(path, exist_ok=True)
    if _real_uid is not None and os.geteuid() == 0:
        try:
            st = os.stat(path)
            if st.st_uid != _real_uid:
                os.chown(path, _real_uid, _real_gid)
        except OSError:
            pass


CONFIG_DIR       = os.path.join(_real_home, ".config", "mountain-time-sync")
_ensure_owned_dir(CONFIG_DIR)

STYLE_FILE       = os.path.join(CONFIG_DIR, "style")
BUTTON_FILE      = os.path.join(CONFIG_DIR, "buttons.json")
OBS_FILE         = os.path.join(CONFIG_DIR, "obs.json")
OBS_BACKUP_FILE  = os.path.join(CONFIG_DIR, "obs_backup.json")
MAIN_MODE_FILE   = os.path.join(CONFIG_DIR, "main_display_mode")
AUTOSTART_FILE   = os.path.join(
    _real_home, ".config", "autostart", "basecamp-linux.desktop"
)
SPLASH_FILE      = os.path.join(CONFIG_DIR, "splash")
WINDOW_FILE      = os.path.join(CONFIG_DIR, "window")
ZONE_FILE        = os.path.join(CONFIG_DIR, "zone_colors.json")
RGB_FILE         = os.path.join(CONFIG_DIR, "rgb_settings.json")
PER_KEY_FILE     = os.path.join(CONFIG_DIR, "per_key_colors.json")
PRESET_FILE      = os.path.join(CONFIG_DIR, "rgb_presets.json")
ICON_LAST_FILE      = os.path.join(CONFIG_DIR, "icon_last.json")
ICON_LIBRARY_DIR    = os.path.join(CONFIG_DIR, "icon_library")
MAIN_LIBRARY_DIR    = os.path.join(CONFIG_DIR, "main_library")
MAKALU_LED_FILE     = os.path.join(CONFIG_DIR, "makalu_leds.json")
MAKALU_PRESET_FILE  = os.path.join(CONFIG_DIR, "makalu_presets.json")
MAKALU_DPI_FILE     = os.path.join(CONFIG_DIR, "makalu_dpi.json")
MAKALU_REMAP_FILE   = os.path.join(CONFIG_DIR, "makalu_remap.json")
DISPLAYPAD_LIBRARY_DIR     = os.path.join(CONFIG_DIR, "displaypad_library")
DISPLAYPAD_FS_LIBRARY_DIR  = os.path.join(CONFIG_DIR, "displaypad_fs_library")
DISPLAYPAD_BTN_FILE        = os.path.join(CONFIG_DIR, "displaypad_buttons.json")
DISPLAYPAD_FULLSCREEN_FILE = os.path.join(CONFIG_DIR, "displaypad_fullscreen.json")
DISPLAYPAD_ACTIONS_FILE    = os.path.join(CONFIG_DIR, "displaypad_actions.json")
DISPLAYPAD_PAGES_FILE      = os.path.join(CONFIG_DIR, "displaypad_pages.json")
DISPLAYPAD_PAGE_NAMES_FILE = os.path.join(CONFIG_DIR, "displaypad_page_names.json")
DISPLAYPAD_PAGES_DIR       = os.path.join(CONFIG_DIR, "displaypad_pages")
DISPLAYPAD_TIMEOUTS_FILE   = os.path.join(CONFIG_DIR, "displaypad_page_timeouts.json")
DISPLAYPAD_HINT_BOX_FILE   = os.path.join(CONFIG_DIR, "displaypad_hint_box.json")
DISPLAYPAD_ROTATION_FILE    = os.path.join(CONFIG_DIR, "displaypad_rotation")
DISPLAYPAD_BRIGHTNESS_FILE  = os.path.join(CONFIG_DIR, "displaypad_brightness")
DISPLAYPAD_DEBOUNCE_FILE    = os.path.join(CONFIG_DIR, "displaypad_debounce")
DISPLAYPAD_MIN_MS_FILE      = os.path.join(CONFIG_DIR, "displaypad_min_ms")
DISPLAYPAD_ACTIONS_DIALOG_SIZE_FILE = os.path.join(CONFIG_DIR, "displaypad_actions_dialog_size.json")
# MacroPad (PID 0x0008). Same chassis as the DisplayPad, but no screens, so
# there is nothing per-key to store except the action and the colour.
MACROPAD_ACTIONS_FILE       = os.path.join(CONFIG_DIR, "macropad_actions.json")
MACROPAD_RGB_FILE           = os.path.join(CONFIG_DIR, "macropad_rgb.json")

MACROS_FILE                 = os.path.join(CONFIG_DIR, "macros.json")
MOUSE_RECORDINGS_DIR        = os.path.join(CONFIG_DIR, "mouse_recordings")
LAST_DIRS_FILE              = os.path.join(CONFIG_DIR, "last_dirs.json")
PLUGINS_DIR                 = os.path.join(CONFIG_DIR, "plugins")
PLUGINS_DISABLED_FILE       = os.path.join(CONFIG_DIR, "plugins_disabled.json")
_ensure_owned_dir(PLUGINS_DIR)


def _load_last_dir(kind, default=None):
    """Return the last directory used for a given file-picker context, or None.
    kind: free-form key like 'image', 'folder', 'app', 'gif' — caller decides.

    Lookup order:
      1. Saved last directory for this kind (last_dirs.json)
      2. Caller-supplied `default` (used by non-icon pickers like backup, so
         they don't fall through to the icon default below)
      3. $ICON_PATH environment variable, if set and valid
      4. /usr/share/icons as a sensible default for icon pickers
    """
    try:
        with open(LAST_DIRS_FILE) as f:
            data = json.load(f)
        path = data.get(kind)
        if path and os.path.isdir(path):
            return path
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        _warn_json(LAST_DIRS_FILE, e)
    if default and os.path.isdir(default):
        return default
    env_path = os.environ.get("ICON_PATH")
    if env_path and os.path.isdir(env_path):
        return env_path
    if os.path.isdir("/usr/share/icons"):
        return "/usr/share/icons"
    return None


def reset_last_dirs():
    """Wipe the remembered last-used directory for every file-picker kind.
    Next pickers will fall back to ICON_PATH / /usr/share/icons."""
    try:
        os.remove(LAST_DIRS_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        try:
            with open(LAST_DIRS_FILE, "w") as f:
                json.dump({}, f)
        except OSError:
            pass


def _save_last_dir(kind, path):
    """Remember the directory of a chosen file/folder for next time."""
    if not path:
        return
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    if not directory or not os.path.isdir(directory):
        return
    try:
        with open(LAST_DIRS_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except (json.JSONDecodeError, OSError) as e:
        _warn_json(LAST_DIRS_FILE, e)
        data = {}
    data[kind] = directory
    try:
        with open(LAST_DIRS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass

# ── Auto-copy bundled plugins on first run ───────────────────────────────────

def _bundled_plugin_version(path):
    """Read version string from plugin.json in `path`, or '' on any error."""
    plugin_json = os.path.join(path, "plugin.json")
    try:
        with open(plugin_json) as f:
            return str(json.load(f).get("version", ""))
    except FileNotFoundError:
        return ""
    except Exception as e:
        _warn_json(plugin_json, e)
        return ""


def _plugin_version_tuple(s):
    parts = []
    for p in str(s or "").lstrip("v").split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _copy_bundled_plugins():
    """Copy bundled example plugins to user plugins dir.

    On first run, all bundled plugins are installed. On subsequent app
    upgrades, any bundled plugin whose version is newer than the installed
    copy is refreshed in place — config.json and other user-state files in
    the plugin dir are preserved, only the plugin's own source files are
    overwritten.
    """
    import shutil
    # Resolve the source dir from our own __file__ so it naturally tracks
    # the source-overlay: if this config.py came from the overlay, the
    # plugins/ it ships sit right next to it and we pick those up. When no
    # overlay is active __file__ points into _internal/, which is exactly
    # where PyInstaller bundles plugins via the datas=[('plugins',...)]
    # entry, so the fallback to _MEIPASS is just defense-in-depth.
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(os.path.join(base, "plugins")) \
            and getattr(sys, "frozen", False):
        base = sys._MEIPASS
    bundled = os.path.join(base, "plugins")
    if not os.path.isdir(bundled):
        return
    for name in os.listdir(bundled):
        src = os.path.join(bundled, name)
        dst = os.path.join(PLUGINS_DIR, name)
        if not os.path.isdir(src):
            continue
        if not os.path.exists(dst):
            shutil.copytree(src, dst)
            print(f"[Plugin] Installed bundled plugin: {name}")
            continue
        src_ver = _bundled_plugin_version(src)
        dst_ver = _bundled_plugin_version(dst)
        if not src_ver or _plugin_version_tuple(src_ver) <= _plugin_version_tuple(dst_ver):
            continue
        # Newer bundled version — overwrite source files but keep user state
        # (config.json, *.json that aren't plugin.json, any *.cache files etc.)
        for src_name in os.listdir(src):
            src_item = os.path.join(src, src_name)
            dst_item = os.path.join(dst, src_name)
            try:
                if os.path.isdir(src_item):
                    if os.path.isdir(dst_item):
                        shutil.rmtree(dst_item)
                    shutil.copytree(src_item, dst_item)
                else:
                    shutil.copy2(src_item, dst_item)
            except Exception as e:
                print(f"[Plugin] Failed to refresh {name}/{src_name}: {e}")
        print(f"[Plugin] Refreshed bundled plugin: {name} {dst_ver} → {src_ver}")

_copy_bundled_plugins()


# ── Profiles (named action / display config snapshots) ──────────────────────

PROFILES_DIR        = os.path.join(CONFIG_DIR, "profiles")
ACTIVE_PROFILE_FILE = os.path.join(CONFIG_DIR, "active_profile")
_ensure_owned_dir(PROFILES_DIR)

# Files that get snapshotted into a profile — keyboard buttons + DisplayPad
# layout. Library / cache dirs are kept global on purpose so users don't have
# to re-upload the same icons per profile.
_PROFILE_FILES = [
    "buttons.json",
    "obs.json",
    "displaypad_buttons.json",
    "displaypad_fullscreen.json",
    "displaypad_actions.json",
    "displaypad_pages.json",
    "displaypad_rotation",
    "macros.json",
]


def _safe_profile_name(name):
    """Sanitise a profile name: keep alnum, dash, underscore, space."""
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    return cleaned[:48]


def list_profiles():
    """Return sorted list of saved profile names."""
    if not os.path.isdir(PROFILES_DIR):
        return []
    return sorted(
        d for d in os.listdir(PROFILES_DIR)
        if os.path.isdir(os.path.join(PROFILES_DIR, d))
    )


def get_active_profile():
    try:
        with open(ACTIVE_PROFILE_FILE) as f:
            name = f.read().strip()
        if name and os.path.isdir(os.path.join(PROFILES_DIR, name)):
            return name
    except OSError:
        pass
    return ""


def set_active_profile(name):
    try:
        with open(ACTIVE_PROFILE_FILE, "w") as f:
            f.write(name or "")
        if _real_uid is not None and os.geteuid() == 0:
            try:
                os.chown(ACTIVE_PROFILE_FILE, _real_uid, _real_gid)
            except OSError:
                pass
    except OSError:
        pass


def save_profile(name):
    """Snapshot the current configuration into profiles/<name>/."""
    import shutil
    safe = _safe_profile_name(name)
    if not safe:
        raise ValueError("Invalid profile name")
    target = os.path.join(PROFILES_DIR, safe)
    _ensure_owned_dir(target)
    saved = 0
    for fname in _PROFILE_FILES:
        src = os.path.join(CONFIG_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target, fname))
            saved += 1
    set_active_profile(safe)
    return safe, saved


def load_profile(name):
    """Restore a saved profile back into the live config files."""
    import shutil
    safe = _safe_profile_name(name)
    src_dir = os.path.join(PROFILES_DIR, safe)
    if not os.path.isdir(src_dir):
        raise FileNotFoundError(f"Profile not found: {safe}")
    restored = 0
    for fname in _PROFILE_FILES:
        src = os.path.join(src_dir, fname)
        if os.path.exists(src):
            dst = os.path.join(CONFIG_DIR, fname)
            shutil.copy2(src, dst)
            if _real_uid is not None and os.geteuid() == 0:
                try:
                    os.chown(dst, _real_uid, _real_gid)
                except OSError:
                    pass
            restored += 1
    set_active_profile(safe)
    return restored


def delete_profile(name):
    import shutil
    safe = _safe_profile_name(name)
    target = os.path.join(PROFILES_DIR, safe)
    if os.path.isdir(target):
        shutil.rmtree(target)
        if get_active_profile() == safe:
            set_active_profile("")
        return True
    return False


# ── Backup / Restore ─────────────────────────────────────────────────────────

# Folders inside CONFIG_DIR that are user-replaceable caches / generated assets
# — excluded from backups to keep ZIPs small. Plugins are also excluded so we
# don't pull in arbitrary third-party code via a backup file.
_BACKUP_EXCLUDE_DIRS = {
    "icon_library", "main_library",
    "displaypad_library", "displaypad_fs_library",
    "mouse_recordings", "plugins",
}


def export_backup(zip_path):
    """Write a ZIP archive containing all small JSON/state config files.
    Large media libraries and plugins are excluded — re-add them manually."""
    import zipfile
    written = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(CONFIG_DIR):
            rel = os.path.relpath(root, CONFIG_DIR)
            # Skip excluded top-level dirs in-place so os.walk doesn't recurse
            parts = [] if rel == "." else rel.split(os.sep)
            if parts and parts[0] in _BACKUP_EXCLUDE_DIRS:
                dirs[:] = []
                continue
            for name in files:
                src = os.path.join(root, name)
                arcname = os.path.relpath(src, CONFIG_DIR)
                try:
                    zf.write(src, arcname)
                    written += 1
                except OSError:
                    pass
    return written


def import_backup(zip_path):
    """Extract a backup ZIP into CONFIG_DIR, overwriting any existing files.
    Refuses paths with .. or absolute components — defends against malicious ZIPs."""
    import zipfile
    restored = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            # Reject path traversal
            if (os.path.isabs(name)
                    or ".." in name.replace("\\", "/").split("/")):
                continue
            target = os.path.join(CONFIG_DIR, name)
            target_dir = os.path.dirname(target)
            # Final guard: make sure resolved target lives under CONFIG_DIR
            if not os.path.realpath(target).startswith(
                    os.path.realpath(CONFIG_DIR) + os.sep):
                continue
            if name.endswith("/"):
                _ensure_owned_dir(target)
                continue
            if target_dir:
                _ensure_owned_dir(target_dir)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            if _real_uid is not None and os.geteuid() == 0:
                try:
                    os.chown(target, _real_uid, _real_gid)
                except OSError:
                    pass
            restored += 1
    return restored


# Keep these for backward compatibility in code that imports them by old names
RGB_PRESETS_FILE = PRESET_FILE


# ── OBS internal order ────────────────────────────────────────────────────────

OBS_INTERNAL_ORDER = ["none", "scene", "record", "stream"]

# ── Style ──────────────────────────────────────────────────────────────────────

def load_config():
    """Load style string. Returns 'analog' if not set."""
    try:
        with open(STYLE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "analog"


def save_config(style_arg):
    with open(STYLE_FILE, "w") as f:
        f.write(style_arg)


# Keep old names used throughout gui.py
load_style = load_config
save_style = save_config


# ── Buttons ────────────────────────────────────────────────────────────────────

def load_buttons():
    default = [{"icon": 7, "action": "", "type": "shell"} for _ in range(4)]
    try:
        with open(BUTTON_FILE) as f:
            data = json.load(f)
        for i in range(4):
            if i < len(data):
                default[i].update(data[i])
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as e:
        _warn_json(BUTTON_FILE, e)
    return default


def save_buttons(buttons):
    with open(BUTTON_FILE, "w") as f:
        json.dump(buttons, f, indent=2)


# ── OBS ────────────────────────────────────────────────────────────────────────

def load_obs_config():
    default = {
        "host": "localhost",
        "port": 4455,
        "password": "",
        "buttons": [{"type": "none", "scene": ""} for _ in range(4)],
    }
    try:
        with open(OBS_FILE) as f:
            data = json.load(f)
        for k in ("host", "port", "password"):
            if k in data:
                default[k] = data[k]
        for i in range(4):
            if i < len(data.get("buttons", [])):
                default["buttons"][i].update(data["buttons"][i])
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as e:
        _warn_json(OBS_FILE, e)
    return default


def save_obs_config(cfg):
    with open(OBS_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Autostart ──────────────────────────────────────────────────────────────────

def _autostart_exec():
    _FROZEN = getattr(sys, "frozen", False)
    if _FROZEN:
        p = os.environ.get("APPIMAGE", sys.executable)
        return f'"{p}" --minimized'
    # __file__ would refer to this module; we need the gui entry point.
    # Callers that know the gui path can override; fall back to gui.py sibling.
    gui_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gui.py")
    return f'"{sys.executable}" "{gui_path}" --minimized'


def load_autostart_enabled():
    return os.path.exists(AUTOSTART_FILE)


def save_autostart_enabled(val):
    if val:
        os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
        with open(AUTOSTART_FILE, "w") as f:
            f.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=BaseCamp Linux\n"
                "Comment=Mountain Everest Max display control\n"
                f"Exec={_autostart_exec()}\n"
                "Icon=basecamp-linux\n"
                "Hidden=false\n"
                "NoDisplay=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
    else:
        try:
            os.remove(AUTOSTART_FILE)
        except FileNotFoundError:
            pass


# ── Splash ─────────────────────────────────────────────────────────────────────

def load_splash_enabled():
    try:
        with open(SPLASH_FILE) as f:
            return f.read().strip() != "0"
    except FileNotFoundError:
        return True


def save_splash_enabled(val):
    with open(SPLASH_FILE, "w") as f:
        f.write("1" if val else "0")


# ── RGB zone colors ────────────────────────────────────────────────────────────

def load_zone_colors(defaults):
    """Load zone color dict and brightness from ZONE_FILE.
    Returns (colors_dict, brightness_int)."""
    try:
        data = _read_json(ZONE_FILE)
        colors = dict(defaults)
        for k in colors:
            if k in data and len(data[k]) == 3:
                colors[k] = tuple(data[k])
        brightness = int(data.get("brightness", 100))
        return colors, brightness
    except Exception:
        return dict(defaults), 100


# Keep old name used in gui.py
load_zone_config = load_zone_colors


def save_zone_colors(colors, brightness):
    data = {k: list(v) for k, v in colors.items()}
    data["brightness"] = brightness
    with open(ZONE_FILE, "w") as f:
        f.write(json.dumps(data, indent=2))


# Keep old name used in gui.py
save_zone_config = save_zone_colors


# ── RGB effect settings ────────────────────────────────────────────────────────

def load_rgb_settings():
    try:
        return _read_json(RGB_FILE)
    except Exception:
        return {}


# Keep old name used in gui.py
load_rgb_config = load_rgb_settings


def save_rgb_settings(data):
    with open(RGB_FILE, "w") as f:
        f.write(json.dumps(data, indent=2))


# Keep old name used in gui.py
save_rgb_config = save_rgb_settings


# ── Per-key RGB ────────────────────────────────────────────────────────────────

_SIDE_ZONE_INDICES = [
    [13, 14, 15, 7, 6, 5, 4, 3, 2, 1, 0],             # main top   (11)
    [9, 8, 10, 11],                                     # main right  (4)
    [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 12],  # main bottom(12)
    [16, 17, 18, 19],                                   # main left   (4)
    [31, 44, 43, 42],                                   # np top      (4)
    [41, 40, 39],                                       # np right    (3)
    [35, 36, 37, 38],                                   # np bottom   (4)
    [32, 33, 34],                                       # np left     (3)
]


def _load_per_key():
    default_side = [(255, 255, 255)] * 45
    try:
        d = _read_json(PER_KEY_FILE)
        leds = [tuple(c) for c in d.get("leds", [])]
        leds = (leds + [(20, 20, 20)] * 126)[:126]
        raw = d.get("side", [])
        if isinstance(raw, list) and len(raw) == 45:
            side = [tuple(c) for c in raw]
        elif isinstance(raw, dict):
            # backward compat: zone dict → expand to 45
            side = list(default_side)
            zone_map = {
                "Top":    _SIDE_ZONE_INDICES[0], "Right":  _SIDE_ZONE_INDICES[1],
                "Bottom": _SIDE_ZONE_INDICES[2], "Left":   _SIDE_ZONE_INDICES[3],
                "NP": _SIDE_ZONE_INDICES[4] + _SIDE_ZONE_INDICES[5] +
                      _SIDE_ZONE_INDICES[6] + _SIDE_ZONE_INDICES[7],
            }
            for z, idxs in zone_map.items():
                c = tuple(raw.get(z, (255, 255, 255)))
                for i in idxs:
                    side[i] = c
        else:
            side = list(default_side)
        bri = int(d.get("brightness", 100))
        return leds, side, bri
    except Exception:
        return [(20, 20, 20)] * 126, list(default_side), 100


def _save_per_key(leds, side, bri):
    with open(PER_KEY_FILE, "w") as f:
        f.write(json.dumps({
            "leds": [list(c) for c in leds],
            "side": [list(c) for c in side],
            "brightness": bri,
        }, indent=2))


# ── Presets ────────────────────────────────────────────────────────────────────

def _load_presets():
    defaults = {}
    _default_file = shipped_path("default_presets.json")
    try:
        defaults = _read_json(_default_file)
    except Exception:
        pass
    try:
        user = _read_json(PRESET_FILE)
        defaults.update(user)
    except Exception:
        pass
    return defaults


def _save_presets(presets):
    with open(PRESET_FILE, "w") as f:
        f.write(json.dumps(presets, indent=2))


# ── Everest 60 per-key storage ─────────────────────────────────────────────────

PER_KEY_60_FILE = os.path.join(CONFIG_DIR, "per_key_60_colors.json")
PRESET_60_FILE  = os.path.join(CONFIG_DIR, "rgb60_presets.json")


def _load_per_key_60():
    try:
        d = _read_json(PER_KEY_60_FILE)
        leds = [tuple(c) for c in d.get("leds", [])]
        leds = (leds + [(20, 20, 20)] * 64)[:64]
        # Side ring: 44 LEDs (hw 126..169). Persisted so the per-LED ring editor
        # (#4) keeps its state across sessions.
        side = [tuple(c) for c in d.get("side", [])]
        side = (side + [(20, 20, 20)] * 44)[:44]
        bri  = int(d.get("brightness", 100))
        return leds, side, bri
    except Exception:
        return [(20, 20, 20)] * 64, [(20, 20, 20)] * 44, 100


def _save_per_key_60(leds, side, bri):
    with open(PER_KEY_60_FILE, "w") as f:
        f.write(json.dumps({
            "leds": [list(c) for c in leds],
            "side": [list(c) for c in (side or [])],
            "brightness": bri,
        }, indent=2))


def _load_presets_60():
    defaults = {}
    try:
        defaults = _read_json(shipped_path("default_presets_60.json"))
    except Exception:
        pass
    try:
        user = _read_json(PRESET_60_FILE)
        defaults.update(user)
    except Exception:
        pass
    return defaults


def _save_presets_60(presets):
    with open(PRESET_60_FILE, "w") as f:
        f.write(json.dumps(presets, indent=2))


# ── MacroPad storage ────────────────────────────────────────────────────────────

_MACROPAD_KEYS = 12


def _load_macropad_actions():
    """The 12 key actions, as [{"type": ..., "action": ...}] for M1 to M12.

    A missing or half written file is not worth an error on a screen the
    person may only be opening to change the lighting, so it degrades to
    "nothing bound" the way the DisplayPad does. Type and value are forced to
    strings for the same reason the colours are coerced above: this screen is
    built before the window appears.
    """
    actions = [{"type": "none", "action": ""} for _ in range(_MACROPAD_KEYS)]
    try:
        data = _read_json(MACROPAD_ACTIONS_FILE).get("actions", [])
        for i in range(_MACROPAD_KEYS):
            if i < len(data) and isinstance(data[i], dict):
                actions[i]["type"] = str(data[i].get("type", "none") or "none")
                actions[i]["action"] = str(data[i].get("action", "") or "")
    except Exception:
        pass
    return actions


def _save_macropad_actions(actions):
    with open(MACROPAD_ACTIONS_FILE, "w") as f:
        f.write(json.dumps({"actions": actions}, indent=2))


def _macropad_int(value, fallback, low=0, high=100):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _macropad_color(value, fallback=(255, 255, 255)):
    """One RGB triple, whatever the file happened to contain."""
    try:
        r, g, b = value
    except (TypeError, ValueError):
        return list(fallback)
    return [_macropad_int(c, f, 0, 255) for c, f in zip((r, g, b), fallback)]


def _load_macropad_rgb():
    """Lighting state: effect id, brightness, speed, the two effect colours,
    direction, and the 12 per key colours the Custom effect sends.

    Every field is coerced rather than trusted. The MacroPad screen is built
    at startup, not on first visit, so a hand edited or truncated file here
    would otherwise take the whole application down before the window appears.
    """
    default = {
        "effect": 0, "brightness": 60, "speed": 60,
        "color1": [255, 0, 0], "color2": [0, 0, 255], "direction": 0,
        "colors": [[255, 255, 255] for _ in range(_MACROPAD_KEYS)],
    }
    try:
        stored = _read_json(MACROPAD_RGB_FILE)
    except Exception:
        return default
    if not isinstance(stored, dict):
        return default

    out = dict(default)
    out["effect"]     = _macropad_int(stored.get("effect"), 0, 0, 255)
    out["brightness"] = _macropad_int(stored.get("brightness"), 60)
    out["speed"]      = _macropad_int(stored.get("speed"), 60)
    out["direction"]  = _macropad_int(stored.get("direction"), 0, 0, 255)
    out["color1"]     = _macropad_color(stored.get("color1"), (255, 0, 0))
    out["color2"]     = _macropad_color(stored.get("color2"), (0, 0, 255))
    colors = stored.get("colors")
    if not isinstance(colors, list):
        colors = []
    colors = [_macropad_color(c) for c in colors[:_MACROPAD_KEYS]]
    out["colors"] = colors + [[255, 255, 255]] * (_MACROPAD_KEYS - len(colors))
    return out


def _save_macropad_rgb(cfg):
    with open(MACROPAD_RGB_FILE, "w") as f:
        f.write(json.dumps(cfg, indent=2))


# ── Makalu 67 LED storage ───────────────────────────────────────────────────────

def _load_makalu_leds():
    try:
        d = _read_json(MAKALU_LED_FILE)
        leds = [tuple(c) for c in d.get("leds", [])]
        leds = (leds + [(255, 255, 255)] * 8)[:8]
        bri    = int(d.get("brightness", 100))
        preset = d.get("preset", "")
        return leds, bri, preset
    except Exception:
        return [(255, 255, 255)] * 8, 100, ""


def _save_makalu_leds(leds, bri, preset=""):
    with open(MAKALU_LED_FILE, "w") as f:
        f.write(json.dumps({"leds": [list(c) for c in leds], "brightness": bri, "preset": preset}))


def _load_makalu_presets():
    defaults = {}
    try:
        defaults = _read_json(shipped_path("default_makalu_presets.json"))
    except Exception:
        pass
    try:
        user = _read_json(MAKALU_PRESET_FILE)
        defaults.update(user)
    except Exception:
        pass
    return defaults


def _save_makalu_presets(presets):
    with open(MAKALU_PRESET_FILE, "w") as f:
        f.write(json.dumps(presets, indent=2))


DPI_DEFAULTS = [400, 800, 1600, 3200, 6400]


def _load_makalu_dpi():
    try:
        d = _read_json(MAKALU_DPI_FILE)
        values = [int(v) for v in d.get("levels", DPI_DEFAULTS)]
        if len(values) == 5:
            return values
    except Exception:
        pass
    return list(DPI_DEFAULTS)


def _save_makalu_dpi(levels):
    with open(MAKALU_DPI_FILE, "w") as f:
        f.write(json.dumps({"levels": levels}))


REMAP_DEFAULTS = {"1": "left", "2": "right", "3": "middle",
                  "4": "back", "5": "forward", "6": "dpi+"}

REMAP_DEFAULTS_MAX = {"1": "left", "2": "right", "3": "middle",
                      "4": "dpi+", "5": "disabled", "6": "disabled",
                      "7": "forward", "8": "back"}


def _load_makalu_remap(defaults=None):
    if defaults is None:
        defaults = REMAP_DEFAULTS
    try:
        d = _read_json(MAKALU_REMAP_FILE)
        result = dict(defaults)
        result.update({k: v for k, v in d.items() if k in defaults})
        return result
    except Exception:
        return dict(defaults)


def _save_makalu_remap(assignments):
    with open(MAKALU_REMAP_FILE, "w") as f:
        f.write(json.dumps(assignments))


# ── DisplayPad config ────────────────────────────────────────────────────────

# ── DisplayPad config ────────────────────────────────────────────────────────
#
# Every page (including page 0 / Main) lives in its own file under
# DISPLAYPAD_PAGES_DIR, named after the page (#53) -- e.g.
# "displaypad_pages/Monitoring__3.json". The numeric id is kept as a
# filename suffix purely as a collision guard (two pages could otherwise be
# renamed to the same thing); it is NOT used as the primary reference
# anywhere in the app anymore -- actions, chain steps, and timeouts all
# refer to a page by its name (see panel.py's _page_target()). The
# functions below keep their historical signatures so callers don't need to
# change; only the on-disk representation changed.

_PAGE_FILENAME_UNSAFE = re.compile(r'[^A-Za-z0-9 _.\-]+')

_EMPTY_ACTIONS = [{"type": "none", "action": ""} for _ in range(12)]


def _page_dir():
    os.makedirs(DISPLAYPAD_PAGES_DIR, exist_ok=True)
    return DISPLAYPAD_PAGES_DIR


def _page_filename(page_id, name):
    safe = _PAGE_FILENAME_UNSAFE.sub("_", (name or "").strip()) or "page"
    safe = safe.strip("_.") or "page"
    return f"{safe}__{int(page_id)}.json"


def _find_page_file(page_id):
    """Locate an existing page's file by id -- its name (and therefore
    filename) may have changed since it was last written, so this scans
    rather than guessing the current filename."""
    page_id = int(page_id)
    suffix = f"__{page_id}.json"
    try:
        entries = os.listdir(_page_dir())
    except OSError:
        return None
    for fn in entries:
        if fn.endswith(suffix):
            return os.path.join(_page_dir(), fn)
    return None


_PAGES_CACHE = None  # dict[page_id -> record], or None when not (yet) loaded

def _invalidate_pages_cache():
    global _PAGES_CACHE
    _PAGES_CACHE = None


def _copy_tree(obj):
    """A copy of parsed JSON down to the last dict and list.

    What the page loaders hand out is the caller's to change. A shallow copy
    of the cache shared every record with it, so a caller that kept a page's
    button map and wrote into it was writing into the cache: the DisplayPad
    panel kept Main's map that way and the widget plugins write their frames
    into it, after which the cache answered "which icon does this key carry"
    with a widget frame, and the next save put that frame in the page file as
    the key's icon (#104, the frames FransM's Main__0.json carries). Pages
    are small, so a full copy costs well under a millisecond.
    """
    if isinstance(obj, dict):
        return {k: _copy_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_copy_tree(v) for v in obj]
    return obj


def _load_all_displaypad_pages():
    """Return {page_id: {"id","name","v","actions","buttons","fullscreen",
    "timeout"}} by reading every page's own file. Migrates the old combined
    files into this layout the first time it's called on an existing
    install (see _migrate_legacy_displaypad_pages).

    This is called very frequently (page-name lookups, action execution on
    every button press, GUI refreshes, ...), so the parsed result is cached
    in memory and only rebuilt when something actually changed. The only
    writer of these files is this same process (the tray helper is a
    separate, much smaller process that only talks to the GUI over Unix
    signals and never touches DisplayPad page files), so it's enough to
    invalidate the cache from the handful of functions that write here --
    no need to re-stat the directory on every call."""
    global _PAGES_CACHE
    # Bind once: plugin threads and the control socket read pages while the
    # GUI thread may be invalidating the cache, and checking the global and
    # then copying it separately can hit dict(None) in between.
    cached = _PAGES_CACHE
    if cached is not None:
        return _copy_tree(cached)

    _migrate_legacy_displaypad_pages()
    out = {}
    try:
        entries = os.listdir(_page_dir())
    except OSError:
        entries = []
    for fn in entries:
        if not fn.endswith(".json"):
            continue
        try:
            data = _read_json(os.path.join(_page_dir(), fn))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        try:
            pid = int(data.get("id"))
        except (TypeError, ValueError):
            continue
        out[pid] = data

    _PAGES_CACHE = out
    return _copy_tree(out)


def _save_displaypad_page_record(page_id, record):
    """Write one page's complete record to its own file, renaming the file
    if the page's name changed since it was last saved."""
    page_id = int(page_id)
    record = dict(record)
    record["id"] = page_id
    record.setdefault("name", "Main" if page_id == 0 else f"Page {page_id}")
    old_path = _find_page_file(page_id)
    new_path = os.path.join(_page_dir(), _page_filename(page_id, record["name"]))
    if old_path and old_path != new_path and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass
    with open(new_path, "w") as f:
        json.dump(record, f, indent=2)
    _invalidate_pages_cache()


def _delete_displaypad_page_record(page_id):
    path = _find_page_file(page_id)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    _invalidate_pages_cache()


def _migrate_legacy_displaypad_pages():
    """One-time upgrade (#53): fold the old separate files (displaypad_
    actions.json, displaypad_buttons.json, displaypad_fullscreen.json,
    displaypad_pages.json, displaypad_page_timeouts.json, displaypad_
    page_names.json) into one file per page. Skipped once the per-page
    directory has anything in it -- the old files are left on disk
    untouched (unused, but nothing is deleted)."""
    try:
        if os.listdir(_page_dir()):
            return
    except OSError:
        pass

    names = {}
    try:
        raw = _read_json(DISPLAYPAD_PAGE_NAMES_FILE)
        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    names[int(k)] = str(v)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    timeouts = {}
    try:
        raw = _read_json(DISPLAYPAD_TIMEOUTS_FILE)
        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    timeouts[int(k)] = v
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    records = {}

    try:
        main_actions = _read_json(DISPLAYPAD_ACTIONS_FILE)
    except Exception:
        main_actions = list(_EMPTY_ACTIONS)
    try:
        main_buttons = _read_json(DISPLAYPAD_BTN_FILE)
    except Exception:
        main_buttons = {}
    try:
        main_fullscreen = _read_json(DISPLAYPAD_FULLSCREEN_FILE).get("gif_path")
    except Exception:
        main_fullscreen = None
    records[0] = {
        "id": 0, "name": names.get(0, "Main"), "v": 2,
        "actions": main_actions, "buttons": main_buttons,
        "fullscreen": main_fullscreen, "timeout": timeouts.get(0),
    }

    try:
        raw_pages = _read_json(DISPLAYPAD_PAGES_FILE)
    except Exception:
        raw_pages = {}
    if isinstance(raw_pages, dict):
        for ps, pdata in raw_pages.items():
            try:
                pid = int(ps)
            except (TypeError, ValueError):
                continue
            if not isinstance(pdata, dict):
                continue
            records[pid] = {
                "id": pid, "name": names.get(pid, f"Page {pid}"),
                "v": pdata.get("v", 1),
                "actions": pdata.get("actions", list(_EMPTY_ACTIONS)),
                "buttons": pdata.get("buttons", {}),
                "fullscreen": pdata.get("fullscreen"),
                "timeout": timeouts.get(pid),
            }

    # A page that only ever got a *name* registered (created ahead of time,
    # #52) but never made it into the old combined-pages file.
    for pid, nm in names.items():
        if pid not in records:
            records[pid] = {
                "id": pid, "name": nm, "v": 2,
                "actions": list(_EMPTY_ACTIONS), "buttons": {},
                "fullscreen": None, "timeout": timeouts.get(pid),
            }

    for pid, record in records.items():
        _save_displaypad_page_record(pid, record)


def _load_displaypad_buttons():
    """Return dict {str(key_idx): image_path} for Main's button images."""
    rec = _load_all_displaypad_pages().get(0)
    return (rec or {}).get("buttons") or {}


def _save_displaypad_buttons(data):
    recs = _load_all_displaypad_pages()
    rec = recs.get(0, {"id": 0, "name": "Main", "v": 2, "actions": list(_EMPTY_ACTIONS),
                        "fullscreen": None, "timeout": None})
    rec["buttons"] = data
    _save_displaypad_page_record(0, rec)


def _load_displaypad_fullscreen():
    rec = _load_all_displaypad_pages().get(0)
    return (rec or {}).get("fullscreen")


def _save_displaypad_fullscreen(path):
    recs = _load_all_displaypad_pages()
    rec = recs.get(0, {"id": 0, "name": "Main", "v": 2, "actions": list(_EMPTY_ACTIONS),
                        "buttons": {}, "timeout": None})
    rec["fullscreen"] = path
    _save_displaypad_page_record(0, rec)


def _clear_displaypad_fullscreen():
    _save_displaypad_fullscreen(None)


def _load_displaypad_actions(page=0):
    """Return list of 12 dicts with 'type' and 'action' keys for the given
    DisplayPad page (0 = main page, matching self._current_page in the
    DisplayPad panel). Sub-page actions are stored separately (see
    _load_displaypad_pages) so plugins that only ever asked for the main
    page's actions used to miss anything assigned on a sub-page."""
    default = [{"type": "none", "action": ""} for _ in range(12)]
    try:
        if page:
            data = _load_displaypad_pages().get(str(page), {}).get("actions", [])
        else:
            # Main (page 0) actions live in the per-page file written by
            # _save_displaypad_actions() (issue #53's per-page-file migration).
            # DISPLAYPAD_ACTIONS_FILE is the pre-#53 legacy location and is no
            # longer written to, so reading it here silently dropped every
            # save on the next restart -- both primary and any "also on
            # press"/double-click chain riding along with it.
            data = _load_all_displaypad_pages().get(0, {}).get("actions", [])
        for i in range(12):
            if i < len(data):
                default[i].update(data[i])
    except Exception:
        pass
    return default


def _save_displaypad_actions(actions):
    """Save Main's (page 0's) actions."""
    recs = _load_all_displaypad_pages()
    rec = recs.get(0, {"id": 0, "name": "Main", "v": 2, "buttons": {},
                        "fullscreen": None, "timeout": None})
    rec["actions"] = actions
    _save_displaypad_page_record(0, rec)


def _load_displaypad_pages():
    """Return {str(page_id): {"v","buttons","actions","fullscreen"}} for
    every page EXCEPT page 0/Main (which has its own _load/_save_displaypad_
    actions/buttons/fullscreen functions) -- matches the historical
    contract used by panel.py's page-loading and _save_sub_pages()."""
    out = {}
    for pid, rec in _load_all_displaypad_pages().items():
        if pid == 0:
            continue
        out[str(pid)] = {
            "v": rec.get("v", 2),
            "buttons": rec.get("buttons", {}),
            "actions": rec.get("actions", list(_EMPTY_ACTIONS)),
            "fullscreen": rec.get("fullscreen"),
        }
    return out


def _save_displaypad_pages(data):
    """data: {str(page_id): {"v","buttons","actions","fullscreen"}} for
    every non-main page (the exact shape panel.py's _save_sub_pages()
    builds). Each page is written to its own file; a page that was on disk
    before but is missing from `data` now (e.g. deleted) has its file
    removed too -- the same "whole state overwrite" semantics the old
    single combined-file format had."""
    recs = _load_all_displaypad_pages()
    seen = set()
    for ps, pdata in (data or {}).items():
        try:
            pid = int(ps)
        except (TypeError, ValueError):
            continue
        if pid == 0:
            continue
        seen.add(pid)
        rec = recs.get(pid, {})
        rec.update({
            "id": pid,
            "name": rec.get("name") or f"Page {pid}",
            "v": pdata.get("v", 2),
            "buttons": pdata.get("buttons", {}),
            "actions": pdata.get("actions", list(_EMPTY_ACTIONS)),
            "fullscreen": pdata.get("fullscreen"),
            "timeout": rec.get("timeout"),
        })
        _save_displaypad_page_record(pid, rec)
    for pid in list(recs.keys()):
        if pid != 0 and pid not in seen:
            _delete_displaypad_page_record(pid)


def _load_displaypad_page_names():
    """Return {int page_id: str name} for every page that exists. This is
    the authoritative registry of which pages *exist* -- independent of
    whether any button currently targets them, so a page can be created
    ahead of time and won't vanish for being "unused". Page 0 (the page the
    app happens to open on) is just another entry here; if it has no
    stored name yet, the caller is responsible for filling in a translated
    default (kept out of this low-level function since it doesn't have
    access to translations)."""
    return {pid: rec.get("name", f"Page {pid}")
            for pid, rec in _load_all_displaypad_pages().items()}


def _save_displaypad_page_names(names):
    """names: {int page_id: str name}. Kept for callers that batch-update
    the whole name map at once; updates just the 'name' field (and
    therefore filename) of each affected page's own file."""
    recs = _load_all_displaypad_pages()
    for pid, name in (names or {}).items():
        pid = int(pid)
        rec = recs.get(pid, {"id": pid, "v": 2, "actions": list(_EMPTY_ACTIONS),
                              "buttons": {}, "fullscreen": None, "timeout": None})
        rec["name"] = name
        _save_displaypad_page_record(pid, rec)


def _create_displaypad_page(name, existing_ids=None):
    """Register a brand-new, currently-unused page with the given name and
    return its id. `existing_ids` lets a caller fold in ids it already
    knows about from elsewhere (legacy configs where a page id was only
    ever implied by a button's stored target) so the new id can't collide
    with those either."""
    known = set(_load_all_displaypad_pages().keys()) | {0}
    if existing_ids:
        known.update(int(i) for i in existing_ids)
    new_id = max(known) + 1
    record = {
        "id": new_id, "name": name, "v": 2,
        "actions": list(_EMPTY_ACTIONS), "buttons": {},
        "fullscreen": None, "timeout": None,
    }
    _save_displaypad_page_record(new_id, record)
    return new_id


def _rename_displaypad_page(page_id, name):
    page_id = int(page_id)
    rec = _load_all_displaypad_pages().get(
        page_id, {"id": page_id, "v": 2, "actions": list(_EMPTY_ACTIONS),
                  "buttons": {}, "fullscreen": None, "timeout": None})
    rec["name"] = name
    _save_displaypad_page_record(page_id, rec)


def _delete_displaypad_page(page_id):
    """Remove a page entirely. Page 0 can't be deleted -- it always
    exists, it's just the page the app happens to open on."""
    page_id = int(page_id)
    if page_id == 0:
        return False
    if page_id not in _load_all_displaypad_pages():
        return False
    _delete_displaypad_page_record(page_id)
    return True


def _load_displaypad_page_hints(page_id):
    """A page's note, its per-key hints and whether its box is shown (#93).

    Kept in the page's own record, beside its actions rather than in them:
    an action is rebuilt whenever it is edited, and a hint stored inside it
    would vanish the first time its key was given another action."""
    rec = _load_all_displaypad_pages().get(int(page_id)) or {}
    keys = rec.get("key_hints") or {}
    return {
        "note": str(rec.get("note") or ""),
        "keys": {str(k): str(v) for k, v in keys.items() if str(v).strip()},
        "show": rec.get("hint_box", True) is not False,
    }


def _save_displaypad_page_hints(page_id, note=None, keys=None, show=None):
    """Change any of a page's note, key hints (a full {key: text} map) or
    box switch; what is passed as None is left as it is."""
    page_id = int(page_id)
    recs = _load_all_displaypad_pages()
    rec = recs.get(page_id)
    if rec is None:
        rec = {"id": page_id, "v": 2, "actions": list(_EMPTY_ACTIONS),
               "buttons": {}, "fullscreen": None, "timeout": None}
    if note is not None:
        rec["note"] = str(note)
    if keys is not None:
        rec["key_hints"] = {str(k): str(v) for k, v in keys.items() if str(v).strip()}
    if show is not None:
        rec["hint_box"] = bool(show)
    _save_displaypad_page_record(page_id, rec)


def _load_hint_box_settings():
    """How the page box behaves: seconds until it closes (0: it stays), and
    where it was last left (None: the bottom right corner)."""
    out = {"timeout": 0, "x": None, "y": None}
    try:
        data = _read_json(DISPLAYPAD_HINT_BOX_FILE)
        if isinstance(data, dict):
            out["timeout"] = max(0, int(data.get("timeout", 0) or 0))
            for k in ("x", "y"):
                if isinstance(data.get(k), int):
                    out[k] = data[k]
    except Exception:
        pass
    return out


def _save_hint_box_settings(**changes):
    data = _load_hint_box_settings()
    data.update(changes)
    with open(DISPLAYPAD_HINT_BOX_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_displaypad_page_timeouts():
    """Per-page auto-timeout config (issue #45).

    Returns {int(page): {"mode": "off"|"after"|"idle", "seconds": int,
    "target": str|"prev"}}. 'after' fires N seconds after the page is
    shown; 'idle' fires N seconds after the last keypress on that page.
    'target' is a page NAME (#52), resolved against the page registry by
    the caller -- it is deliberately NOT int()-coerced here, since a named
    target is a string on purpose, not a legacy numeric id."""
    out = {}
    for pid, rec in _load_all_displaypad_pages().items():
        v = rec.get("timeout")
        if not isinstance(v, dict) or v.get("mode", "off") == "off":
            continue
        tgt = v.get("target", 0)
        if tgt != "prev" and not isinstance(tgt, str):
            try:
                tgt = int(tgt)
            except (TypeError, ValueError):
                tgt = 0
        out[pid] = {
            "mode": v.get("mode", "off"),
            "seconds": int(v.get("seconds", 0) or 0),
            "target": tgt,
        }
    return out


def _save_displaypad_page_timeouts(data):
    """data: {page_id: {"mode","seconds","target"} or None to clear}."""
    recs = _load_all_displaypad_pages()
    all_ids = set(recs.keys()) | {int(k) for k in (data or {}).keys()}
    for pid in all_ids:
        v = (data or {}).get(pid, (data or {}).get(str(pid)))
        to = None
        if isinstance(v, dict) and v.get("mode", "off") != "off" and int(v.get("seconds", 0) or 0) > 0:
            to = v
        rec = recs.get(pid)
        if rec is None:
            if to is None:
                continue  # nothing to persist, and the page doesn't exist yet
            rec = {"id": pid, "v": 2, "actions": list(_EMPTY_ACTIONS),
                   "buttons": {}, "fullscreen": None}
        rec["timeout"] = to
        _save_displaypad_page_record(pid, rec)


def _load_displaypad_rotation():
    try:
        with open(DISPLAYPAD_ROTATION_FILE) as f:
            v = int(f.read().strip())
        return v if v in (0, 90, 180, 270) else 0
    except Exception:
        return 0


def _save_displaypad_rotation(deg):
    with open(DISPLAYPAD_ROTATION_FILE, "w") as f:
        f.write(str(deg))


def _load_displaypad_brightness():
    try:
        with open(DISPLAYPAD_BRIGHTNESS_FILE) as f:
            v = int(f.read().strip())
        return v if v in (0, 25, 50, 75, 100) else 100
    except Exception:
        return 100


def _save_displaypad_brightness(val):
    with open(DISPLAYPAD_BRIGHTNESS_FILE, "w") as f:
        f.write(str(val))


_DEBOUNCE_VALUES = [0.2, 0.4, 0.6, 0.8, 1.0]

def _load_displaypad_debounce():
    try:
        with open(DISPLAYPAD_DEBOUNCE_FILE) as f:
            v = float(f.read().strip())
        return v if v in _DEBOUNCE_VALUES else 0.8
    except Exception:
        return 0.8


def _save_displaypad_debounce(val):
    with open(DISPLAYPAD_DEBOUNCE_FILE, "w") as f:
        f.write(str(val))


# Slowest allowed GIF frame rate is one frame per second; below 10 ms the pad
# cannot keep up with the upload anyway.
_MIN_MS_RANGE = (10, 1000)


def _load_displaypad_min_ms():
    """Minimum milliseconds per GIF frame. The box for this was on screen from
    the start but the value never outlived the process (#73)."""
    try:
        with open(DISPLAYPAD_MIN_MS_FILE) as f:
            v = int(float(f.read().strip()))
        lo, hi = _MIN_MS_RANGE
        return v if lo <= v <= hi else 50
    except Exception:
        return 50


def _save_displaypad_min_ms(val):
    try:
        v = int(float(val))
    except (TypeError, ValueError):
        return
    lo, hi = _MIN_MS_RANGE
    if not (lo <= v <= hi):
        return
    with open(DISPLAYPAD_MIN_MS_FILE, "w") as f:
        f.write(str(v))


_ACTIONS_DIALOG_MIN_W, _ACTIONS_DIALOG_MIN_H = 400, 400
_ACTIONS_DIALOG_MAX_W, _ACTIONS_DIALOG_MAX_H = 2000, 2000

def _load_displaypad_actions_dialog_size():
    """Return (width, height) for the 'Configure buttons' dialog, or None if
    nothing was saved yet (let the dialog use its natural default size)."""
    try:
        with open(DISPLAYPAD_ACTIONS_DIALOG_SIZE_FILE) as f:
            data = json.load(f)
        w, h = int(data["width"]), int(data["height"])
        if (_ACTIONS_DIALOG_MIN_W <= w <= _ACTIONS_DIALOG_MAX_W
                and _ACTIONS_DIALOG_MIN_H <= h <= _ACTIONS_DIALOG_MAX_H):
            return w, h
    except FileNotFoundError:
        pass
    except Exception as e:
        _warn_json(DISPLAYPAD_ACTIONS_DIALOG_SIZE_FILE, e)
    return None


def _save_displaypad_actions_dialog_size(width, height):
    try:
        w = max(_ACTIONS_DIALOG_MIN_W, min(_ACTIONS_DIALOG_MAX_W, int(width)))
        h = max(_ACTIONS_DIALOG_MIN_H, min(_ACTIONS_DIALOG_MAX_H, int(height)))
        with open(DISPLAYPAD_ACTIONS_DIALOG_SIZE_FILE, "w") as f:
            json.dump({"width": w, "height": h}, f)
    except Exception:
        pass


# ── DisplayPad library helpers ────────────────────────────────────────────────

def _save_to_dp_library(path, gif_frame=0):
    """Resize image to 102×102, save as PNG by content-hash. Returns filename or None."""
    import hashlib
    try:
        img = Image.open(path)
        try:
            img.seek(gif_frame)
        except Exception:
            pass
        img = img.convert("RGB").resize((102, 102), Image.LANCZOS)
        buf = img.tobytes()
        h = hashlib.md5(buf).hexdigest()[:16]
        os.makedirs(DISPLAYPAD_LIBRARY_DIR, exist_ok=True)
        out = os.path.join(DISPLAYPAD_LIBRARY_DIR, f"{h}.png")
        if not os.path.exists(out):
            img.save(out, "PNG")
        return f"{h}.png"
    except Exception:
        return None


def _list_dp_library():
    """Return sorted list of PNG filenames in the DisplayPad library."""
    try:
        return sorted(f for f in os.listdir(DISPLAYPAD_LIBRARY_DIR) if f.endswith(".png"))
    except FileNotFoundError:
        return []


def _compute_dp_lib_hash(path, gif_frame=0):
    """Return the library filename (hash.png) for an image without saving it."""
    import hashlib
    try:
        img = Image.open(path)
        try:
            img.seek(gif_frame)
        except Exception:
            pass
        img = img.convert("RGB").resize((102, 102), Image.LANCZOS)
        h = hashlib.md5(img.tobytes()).hexdigest()[:16]
        return f"{h}.png"
    except Exception:
        return None


def _save_to_dp_fs_library(path):
    """Save fullscreen image/GIF to the DisplayPad fullscreen library. Returns filename or None."""
    import hashlib, shutil
    try:
        os.makedirs(DISPLAYPAD_FS_LIBRARY_DIR, exist_ok=True)
        with open(path, "rb") as f:
            h = hashlib.md5(f.read()).hexdigest()[:16]
        ext = os.path.splitext(path)[1].lower() or ".png"
        out = os.path.join(DISPLAYPAD_FS_LIBRARY_DIR, f"{h}{ext}")
        if not os.path.exists(out):
            shutil.copy2(path, out)
        return f"{h}{ext}"
    except Exception:
        return None


def _list_dp_fs_library():
    """Return sorted list of image filenames in the DisplayPad fullscreen library."""
    try:
        return sorted(f for f in os.listdir(DISPLAYPAD_FS_LIBRARY_DIR)
                       if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")))
    except FileNotFoundError:
        return []


# ── Icon library helpers ────────────────────────────────────────────────────────

def _load_icon_last():
    """Return dict {slot_str: thumb_filename} for last uploaded image per slot."""
    try:
        return _read_json(ICON_LAST_FILE)
    except Exception:
        return {}


def _save_icon_last(slot, filename):
    data = _load_icon_last()
    data[str(slot)] = filename
    with open(ICON_LAST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _save_to_library(path, gif_frame=0):
    """Resize image to 72×72, save as PNG by content-hash. Returns filename or None."""
    import hashlib
    try:
        img = Image.open(path)
        try:
            img.seek(gif_frame)
        except Exception:
            pass
        img = img.convert("RGB").resize((72, 72), Image.LANCZOS)
        buf = img.tobytes()
        h = hashlib.md5(buf).hexdigest()[:16]
        os.makedirs(ICON_LIBRARY_DIR, exist_ok=True)
        out = os.path.join(ICON_LIBRARY_DIR, f"{h}.png")
        if not os.path.exists(out):
            img.save(out, "PNG")
        return f"{h}.png"
    except Exception:
        return None


def _save_to_main_library(path, gif_frame=0):
    """Save 96×82 thumbnail of main display image to main_library. Returns filename or None."""
    import hashlib
    try:
        img = Image.open(path)
        try:
            img.seek(gif_frame)
        except Exception:
            pass
        img = img.convert("RGB").resize((96, 82), Image.LANCZOS)
        buf = img.tobytes()
        h = hashlib.md5(buf).hexdigest()[:16]
        os.makedirs(MAIN_LIBRARY_DIR, exist_ok=True)
        out = os.path.join(MAIN_LIBRARY_DIR, f"{h}.png")
        if not os.path.exists(out):
            img.save(out, "PNG")
        return f"{h}.png"
    except Exception:
        return None


def _compute_lib_hash(path, gif_frame=0):
    """Return the library filename (hash.png) for an image without saving it."""
    import hashlib
    try:
        img = Image.open(path)
        try:
            img.seek(gif_frame)
        except Exception:
            pass
        img = img.convert("RGB").resize((72, 72), Image.LANCZOS)
        h = hashlib.md5(img.tobytes()).hexdigest()[:16]
        return f"{h}.png"
    except Exception:
        return None


def _compute_main_lib_hash(path, gif_frame=0):
    import hashlib
    try:
        img = Image.open(path)
        try:
            img.seek(gif_frame)
        except Exception:
            pass
        img = img.convert("RGB").resize((96, 82), Image.LANCZOS)
        h = hashlib.md5(img.tobytes()).hexdigest()[:16]
        return f"{h}.png"
    except Exception:
        return None


def _list_library():
    """Return sorted list of PNG filenames in the icon library."""
    try:
        return sorted(f for f in os.listdir(ICON_LIBRARY_DIR) if f.endswith(".png"))
    except FileNotFoundError:
        return []


def _list_main_library():
    try:
        return sorted(f for f in os.listdir(MAIN_LIBRARY_DIR) if f.endswith(".png"))
    except FileNotFoundError:
        return []


# ── Macros ─────────────────────────────────────────────────────────────────────

def load_macros():
    """Return full macros dict: {"macros": {uuid: {name, actions, repeat_mode, repeat_count}}}."""
    try:
        return _read_json(MACROS_FILE)
    except Exception:
        return {"macros": {}}


def save_macros(data):
    with open(MACROS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def macro_names():
    """{uuid: name} of every saved macro.

    The panels that let a key run a macro used to ask the Macros screen for
    this. Since 3.0 that screen is only built when it is first opened, so
    before then there were no names at all and a key that runs a macro showed
    the empty dropdown instead of the macro it is set to."""
    macros = (load_macros() or {}).get("macros") or {}
    return {uid: (m or {}).get("name", uid) for uid, m in macros.items()}


# ── Window geometry ────────────────────────────────────────────────────────────

def load_window_geometry():
    """Remembered "WxH+X+Y" of the main window, or None on first run.

    Kept as a plain string because that is exactly what Tk hands out and takes
    back. Anything unparsable is treated as absent rather than as an error:
    a broken geometry file must never stop the app from opening.
    """
    try:
        with open(WINDOW_FILE) as f:
            geo = f.read().strip()
    except OSError:
        return None
    return geo if re.match(r"^\d+x\d+([+-]-?\d+[+-]-?\d+)?$", geo) else None


def save_window_geometry(geo):
    try:
        with open(WINDOW_FILE, "w") as f:
            f.write(geo)
    except OSError:
        pass
