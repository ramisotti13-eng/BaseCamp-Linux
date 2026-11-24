"""Device profiles: software-only profiles per device, as in Base Camp.

Each device that takes part (the Everest Max, the Makalu 67, the DisplayPad)
has its own list of profiles under

    CONFIG_DIR/device_profiles/<device>/index.json
    CONFIG_DIR/device_profiles/<device>/<id>/...     one folder per profile

A profile folder is laid out like CONFIG_DIR: every file has the name of its
live file. The live files always belong to the active profile; a profile
folder is refreshed when the user switches away from it, duplicates or
exports it, and when the application quits. The keyboard and the mouse stay
on their hardware profile 1; a switch writes the new profile's settings to
the device.

``ProfileStore`` holds the files (no device, no threads). ``ProfileService``
(service ``profiles``) owns the index and the switch, runs every change on
one worker thread, exports and imports ``.bcprofile`` files and watches for
programs that switch a profile. The 3.x ``profiles/<name>/`` folders are
taken over once by core/profiles_migrate.py and never written.

Each device service implements the adapter: ``profile_files()``,
``profile_picture_refs(folder)``, ``capture_profile_state()``,
``factory_profile_state()`` and ``apply_profile(state, previous_folder)``.
A device whose service has no adapter is left out.

Commands (all take ``device``):

    profiles.list                -> {"device", "profiles": [{"id", "name",
                                     "picture_url" (absolute path) | None,
                                     "program" | None}], "active", "limit"}
    profiles.activate  id | name -> {"id", "name"}
    profiles.create    [name]    -> {"id"}          (becomes active)
    profiles.duplicate id        -> {"id"}          (becomes active)
    profiles.rename    id, name
    profiles.delete    id
    profiles.reset     id
    profiles.set_program id, path | None
    profiles.set_picture id, path | None
    profiles.export    id, path  -> {"path"}
    profiles.import    path      -> {"id", "device"} for a .bcprofile file,
                                    {"ids", "profiles"} for a 3.x folder
                                    (``device`` optional here)

Refusals are CommandErrors with a ``code``: unknown_device, no_adapter,
not_found, limit, last_profile, bad_name, bad_picture, bad_file,
wrong_device, no_data, snapshot_failed, busy.

Events: ``profiles.changed {device}``, ``profiles.activated {device, id,
name}``, ``profiles.migrated {device, created, skipped}``.
"""
import fnmatch
import hashlib
import json
import logging
import os
import queue
import re
import secrets
import shlex
import shutil
import sys
import threading
import time
import zipfile
from concurrent.futures import Future
from concurrent.futures import TimeoutError as _FutureTimeout

from core.commands import CommandError
from core.service import Service

log = logging.getLogger("basecamp.core.profiles")

DEVICES = ("everest_max", "makalu", "displaypad")
LIMITS = {"everest_max": 5, "makalu": 5, "displaypad": None}

ROOT_NAME = "device_profiles"
INDEX_FILE = "index.json"
PICTURE_FILE = "picture.png"
PICTURE_MAX = 256
STANDARD_NAME = "Standard Profil"
NEW_NAME = "Profil %d"
COPY_SUFFIX = " Kopie"
MAX_NAME = 64
EXPORT_EXT = ".bcprofile"
EXPORT_FORMAT = 1
#: Where a deleted or reset profile's files are kept until the next one, so
#: the adapter can read the outgoing key bindings (and nothing is lost at once).
PREVIOUS_DIR = ".previous"
#: Live files a device's profile_files() lists by globbing CONFIG_DIR: the
#: store also restores those a profile folder holds that the list (made from
#: the outgoing profile's live files) does not name.
EXTRA_FILE_PATTERNS = {"displaypad": ("dp_label_*.png", "dp_folder_*.png", "dp_blank.png")}
#: Picture libraries that are named by content: an imported picture that came
#: from one of them goes back into it under the same name.
LIBRARY_DIRS = ("icon_library", "main_library", "main_sources",
                "displaypad_library", "displaypad_fs_library")
PICTURES_DIR = "profile_pictures"
#: Import limits for a .bcprofile file.
MAX_IMPORT_BYTES = 256 * 1024 * 1024
#: A JSON file in an import is read whole (and parsed): kept small.
MAX_IMPORT_JSON_BYTES = 16 * 1024 * 1024
MAX_IMPORT_FILES = 5000

_ID_RE = re.compile(r"^[0-9a-f]{6}$")
_TMP = ".profiles-tmp"
_ADAPTER_METHODS = ("profile_files", "capture_profile_state",
                    "factory_profile_state", "apply_profile")


def state_name(device):
    """The name of a profile's state file (not a live file)."""
    return "%s_state.json" % device


def empty_index():
    return {"version": 1, "active": None, "profiles": []}


def _err(code, message=None, **params):
    return CommandError(message or code, code=code, **params)


# ── File helpers ─────────────────────────────────────────────────────────────

def _atomic_write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + _TMP
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        _remove_quietly(tmp)
        raise


def _atomic_write_json(path, data):
    _atomic_write_bytes(path, json.dumps(data, indent=2).encode("utf-8"))


def _read_json_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _copy_file_atomic(src, dst):
    """Copy `src` over `dst` so that `dst` is never half written."""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + _TMP
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        _remove_quietly(tmp)
        raise


def _remove_quietly(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def _recover_dir(dst):
    """Tidy what an interrupted swap of `dst` left: put "<dst>.old" back if
    `dst` is gone, drop a stale "<dst>.new" and "<dst>.old"."""
    old, new = dst + ".old", dst + ".new"
    if not os.path.exists(dst) and os.path.isdir(old):
        try:
            os.rename(old, dst)
        except OSError:
            log.exception("could not put %s back", old)
    for leftover in (new, old):
        if os.path.exists(leftover):
            shutil.rmtree(leftover, ignore_errors=True)


def _swap_dir_in(src, dst):
    """Replace the directory `dst` with a copy of `src`: copy to "<dst>.new",
    rename `dst` to "<dst>.old", rename the new one in, remove the old one.
    `dst` is whole at every moment except between the two renames, and
    _recover_dir() repairs that."""
    _recover_dir(dst)
    new, old = dst + ".new", dst + ".old"
    shutil.copytree(src, new, symlinks=True)
    had = os.path.lexists(dst)
    if had:
        os.rename(dst, old)
    try:
        os.rename(new, dst)
    except BaseException:
        if had:
            try:
                os.rename(old, dst)
            except OSError:
                log.exception("could not put %s back", old)
        shutil.rmtree(new, ignore_errors=True)
        raise
    if had:
        shutil.rmtree(old, ignore_errors=True)


def _remove_dir(dst):
    """Remove the directory `dst` at once (a rename), then its content."""
    _recover_dir(dst)
    if not os.path.lexists(dst):
        return
    old = dst + ".old"
    os.rename(dst, old)
    shutil.rmtree(old, ignore_errors=True)


def _clean_rel(name):
    """A profile file name as (relative path, is_dir), or None if it would
    leave the directory it is relative to."""
    if not isinstance(name, str) or not name or "\x00" in name:
        return None
    is_dir = name.endswith("/")
    rel = name.rstrip("/")
    if not rel or os.path.isabs(rel) or rel.startswith(("/", "\\")):
        return None
    norm = os.path.normpath(rel)
    parts = norm.replace("\\", "/").split("/")
    if norm in (".", "..") or ".." in parts or (len(rel) > 1 and rel[1] == ":"):
        return None
    return norm, is_dir


def _file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Store ────────────────────────────────────────────────────────────────────

class ProfileStore:
    """The profile files of every device; no device access, no threads.

    `owner(path)` is called for every file and directory the store creates
    (the configuration chowns them to the real user when run as root).
    """

    def __init__(self, config_dir, owner=None):
        self.config_dir = config_dir
        self.root = os.path.join(config_dir, ROOT_NAME)
        self._owner = owner

    def _own(self, path):
        if self._owner is not None:
            try:
                self._owner(path)
            except Exception:
                pass

    # ── Index ────────────────────────────────────────────────────────────────
    def device_dir(self, device):
        if device not in DEVICES:
            raise ValueError("unknown device %r" % (device,))
        return os.path.join(self.root, device)

    def index_path(self, device):
        return os.path.join(self.device_dir(device), INDEX_FILE)

    def has_index(self, device):
        return os.path.isfile(self.index_path(device))

    def index(self, device):
        """The device's index; an empty one if there is none (nothing is
        created). A broken index is set aside and rebuilt from the folders."""
        path = self.index_path(device)
        if not os.path.isfile(path):
            return empty_index()
        try:
            raw = _read_json_file(path)
            if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
                raise ValueError("not an index")
        except (OSError, ValueError, UnicodeDecodeError) as e:
            log.error("profile index %s cannot be read (%s); rebuilding it", path, e)
            return self._rebuild_index(device, path)
        return self._normalise(device, raw)

    def _normalise(self, device, raw):
        idx = dict(raw)
        idx["version"] = 1
        seen = set()
        profiles = []
        for p in raw.get("profiles") or []:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if not isinstance(pid, str) or not _ID_RE.match(pid) or pid in seen:
                continue
            seen.add(pid)
            entry = dict(p)
            name = entry.get("name")
            entry["name"] = name if isinstance(name, str) and name.strip() else pid
            if not isinstance(entry.get("picture"), str):
                entry["picture"] = None
            if not isinstance(entry.get("program"), str) or not entry.get("program"):
                entry["program"] = None
            profiles.append(entry)
        idx["profiles"] = profiles
        if idx.get("switching") not in seen:
            idx.pop("switching", None)
        if idx.get("active") not in seen:
            idx["active"] = idx.get("switching") or (profiles[0]["id"] if profiles else None)
        return idx

    def _rebuild_index(self, device, path):
        try:
            os.replace(path, "%s.broken-%d" % (path, int(time.time())))
        except OSError:
            pass
        idx = empty_index()
        try:
            names = sorted(os.listdir(self.device_dir(device)))
        except OSError:
            names = []
        for n, pid in enumerate((x for x in names if _ID_RE.match(x)
                                 and os.path.isdir(os.path.join(self.device_dir(device), x))), 1):
            idx["profiles"].append({"id": pid, "name": NEW_NAME % n,
                                    "picture": PICTURE_FILE if os.path.isfile(
                                        os.path.join(self.device_dir(device), pid, PICTURE_FILE)) else None,
                                    "program": None})
        if idx["profiles"]:
            idx["active"] = idx["profiles"][0]["id"]
            # The live files may be any of them; nothing better is known.
            idx["migrated_3x"] = True
            self.save_index(device, idx)
        return idx

    def save_index(self, device, idx):
        """Write the index atomically (a temporary file, then a rename)."""
        d = self.device_dir(device)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            self._own(self.root)
            self._own(d)
        path = self.index_path(device)
        _atomic_write_json(path, idx)
        self._own(path)

    # ── Folders ──────────────────────────────────────────────────────────────
    def folder(self, device, pid):
        if not isinstance(pid, str) or not _ID_RE.match(pid):
            raise ValueError("bad profile id %r" % (pid,))
        return os.path.join(self.device_dir(device), pid)

    def make_folder(self, device, pid):
        path = self.folder(device, pid)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            self._own(self.root)
            self._own(self.device_dir(device))
            self._own(path)
        return path

    def new_id(self, device):
        """6 random hex digits, used by no profile and no folder."""
        used = {p["id"] for p in self.index(device)["profiles"]}
        while True:
            pid = secrets.token_hex(3)
            if pid not in used and not os.path.exists(self.folder(device, pid)):
                return pid

    def previous_dir(self, device):
        return os.path.join(self.device_dir(device), PREVIOUS_DIR)

    def _extra(self, device, rel):
        return "/" not in rel.replace("\\", "/") and any(
            fnmatch.fnmatchcase(rel, pat) for pat in EXTRA_FILE_PATTERNS.get(device, ()))

    def is_profile_name(self, device, rel, names):
        """Is `rel` (a path inside a profile folder) one of the device's
        profile files: named by `names` (or inside a directory it names), or
        one of the device's generated files?"""
        rel = rel.replace("\\", "/")
        for n in names:
            c = _clean_rel(n)
            if c is None:
                continue
            norm, is_dir = c
            norm = norm.replace("\\", "/")
            if rel == norm or (is_dir and rel.startswith(norm + "/")):
                return True
        return self._extra(device, rel)

    def snapshot(self, device, pid, names):
        """Copy the live files `names` into the profile's folder. A name ending
        in "/" is a directory, copied as a whole. A live file that does not
        exist is removed from the folder. Raises OSError (after trying every
        name) if a file could not be copied."""
        dst = self.make_folder(device, pid)
        self.snapshot_to(device, dst, names)

    def snapshot_to(self, device, dst, names):
        os.makedirs(dst, exist_ok=True)
        failed = []
        for name in names:
            c = _clean_rel(name)
            if c is None:
                log.warning("profile file name %r refused", name)
                continue
            rel, is_dir = c
            live = os.path.join(self.config_dir, rel)
            mine = os.path.join(dst, rel)
            try:
                if is_dir:
                    _recover_dir(live)
                    if os.path.isdir(live):
                        _swap_dir_in(live, mine)
                    else:
                        _remove_dir(mine)
                elif os.path.isfile(live):
                    _copy_file_atomic(live, mine)
                elif os.path.lexists(mine):
                    os.remove(mine)
            except OSError as e:
                log.error("snapshot of %s into %s failed: %s", rel, dst, e)
                failed.append(rel)
        # Generated files the folder has and the live directory no longer has.
        try:
            entries = os.listdir(dst)
        except OSError:
            entries = []
        for entry in entries:
            if self._extra(device, entry) and not os.path.exists(
                    os.path.join(self.config_dir, entry)):
                _remove_quietly(os.path.join(dst, entry))
        if failed:
            raise OSError("could not save %s" % ", ".join(failed))

    def restore(self, device, pid, names):
        """Copy the profile's folder into the live files: every top-level file
        and directory it holds (but its state and picture). Files one by one
        (a temporary file, then a rename); a directory as a whole ("<dir>.new",
        "<dir>.old", two renames). A name the folder lacks is removed from the
        live files, so the device's default applies. A failure is logged and
        the rest goes on. Returns the names that failed."""
        src = self.folder(device, pid)
        todo = []
        seen = set()
        for name in names:
            c = _clean_rel(name)
            if c is None:
                log.warning("profile file name %r refused", name)
                continue
            if c[0] not in seen:
                seen.add(c[0])
                todo.append(c)
        try:
            entries = sorted(os.listdir(src))
        except OSError:
            entries = []
        # Everything else the folder holds is the profile's too (the
        # DisplayPad's generated captions, which profile_files() lists from
        # the live directory, i.e. from the outgoing profile).
        for entry in entries:
            if entry in seen or entry in (state_name(device), PICTURE_FILE) \
                    or entry.endswith((_TMP, ".new", ".old", ".import", ".migrate")):
                continue
            seen.add(entry)
            todo.append((entry, os.path.isdir(os.path.join(src, entry))))
        failed = []
        for rel, is_dir in todo:
            mine = os.path.join(src, rel)
            live = os.path.join(self.config_dir, rel)
            try:
                if is_dir:
                    if os.path.isdir(mine):
                        _swap_dir_in(mine, live)
                        self._own(live)
                    else:
                        _remove_dir(live)
                elif os.path.isfile(mine):
                    _copy_file_atomic(mine, live)
                    self._own(live)
                elif os.path.lexists(live) and not os.path.isdir(live):
                    os.remove(live)
            except OSError as e:
                log.error("restoring %s from profile %s/%s failed: %s", rel, device, pid, e)
                failed.append(rel)
        return failed

    def clear_files(self, device, pid):
        """Remove the profile's files, keeping its picture and nothing else."""
        folder = self.folder(device, pid)
        try:
            entries = os.listdir(folder)
        except OSError:
            return
        for entry in entries:
            if entry != PICTURE_FILE:
                _remove_quietly(os.path.join(folder, entry))

    def copy_profile(self, device, src_pid, dst_pid, picture=True):
        """A new folder `dst_pid` holding a copy of `src_pid`'s."""
        src = self.folder(device, src_pid)
        dst = self.folder(device, dst_pid)
        tmp = dst + ".new"
        _remove_quietly(tmp)
        ignore = None if picture else (
            lambda d, names: [PICTURE_FILE] if os.path.normpath(d) == os.path.normpath(src) else [])
        if os.path.isdir(src):
            shutil.copytree(src, tmp, symlinks=True, ignore=ignore)
        else:
            os.makedirs(tmp)
        os.rename(tmp, dst)
        self._own(dst)
        return dst

    def retire(self, device, pid):
        """Move the profile's folder to the device's ".previous" (replacing what
        was there) and return that path."""
        prev = self.previous_dir(device)
        _remove_quietly(prev)
        src = self.folder(device, pid)
        if os.path.isdir(src):
            os.rename(src, prev)
        else:
            os.makedirs(prev, exist_ok=True)
        return prev

    # ── State ────────────────────────────────────────────────────────────────
    def read_state(self, device, pid):
        try:
            data = _read_json_file(os.path.join(self.folder(device, pid), state_name(device)))
        except (OSError, ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_state(self, device, pid, state):
        path = os.path.join(self.make_folder(device, pid), state_name(device))
        _atomic_write_json(path, state if isinstance(state, dict) else {})
        self._own(path)


# ── Adapter helpers (shared with core/profiles_migrate.py) ───────────────────

def adapter_names(adapter):
    """The adapter's profile_files() as a list of strings."""
    names = adapter.profile_files()
    return [n for n in (names or []) if isinstance(n, str)]


def capture_state(adapter):
    """capture_profile_state(), or None if it failed."""
    try:
        state = adapter.capture_profile_state()
    except Exception:
        log.exception("capture_profile_state failed")
        return None
    return state if isinstance(state, dict) else None


def picture_refs(adapter, folder):
    fn = getattr(adapter, "profile_picture_refs", None)
    if fn is None:
        return []
    try:
        refs = fn(folder) or []
    except Exception:
        log.exception("profile_picture_refs failed")
        return []
    out = []
    for r in refs:
        if isinstance(r, str) and os.path.isabs(r) and os.path.isfile(r) and r not in out:
            out.append(r)
    return out


def ensure_index(store, device, adapter, capture=True):
    """Give the device an index if it has none: one "Standard Profil" made
    from the live files, active. Returns True if one was made. With
    `capture` False only the index and an empty state are written; the
    caller snapshots later (fill_standard())."""
    if store.has_index(device):
        return False
    pid = store.new_id(device)
    store.make_folder(device, pid)
    store.write_state(device, pid, {})
    idx = empty_index()
    idx["profiles"].append({"id": pid, "name": STANDARD_NAME, "picture": None,
                            "program": None})
    idx["active"] = pid
    store.save_index(device, idx)
    if capture:
        fill_standard(store, device, adapter, pid)
    return True


def fill_standard(store, device, adapter, pid):
    """Snapshot the live files and the device's state into profile `pid`."""
    state = capture_state(adapter)
    store.snapshot(device, pid, adapter_names(adapter))
    if state is not None:
        store.write_state(device, pid, state)


# ── Program watcher ──────────────────────────────────────────────────────────

_INTERPRETERS = ("python", "perl", "ruby", "bash", "sh", "node", "env", "java")


def _resolve(path):
    if not path:
        return None
    if not os.path.isabs(path):
        found = shutil.which(path)
        if not found:
            return None
        path = found
    return os.path.realpath(path)


# Linux: a Flatpak app runs in a sandbox (bwrap, its own /app paths), and no
# "flatpak" process is left once it runs. It is known by its app id, which the
# kernel's cgroup of every one of its processes names
# (".../app-flatpak-<app id>-<n>.scope"). Its target is "flatpak:<app id>".
_FLATPAK_ID = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}$")
_FLATPAK_CGROUP = re.compile(r"app-flatpak-([A-Za-z0-9_.-]+?)-\d+\.scope")


def _flatpak_run_id(words):
    """The app id of a `flatpak run [options] <app id> ...` command line."""
    if not words or os.path.basename(words[0]) != "flatpak" or "run" not in words:
        return None
    for w in words[words.index("run") + 1:]:
        if not w.startswith("-") and _FLATPAK_ID.match(w):
            return w
    return None


def _flatpak_export_id(path):
    """The app id of a Flatpak export (.../flatpak/exports/bin/<app id>)."""
    if os.sep + os.path.join("flatpak", "exports", "bin") + os.sep in path:
        name = os.path.basename(path)
        return name if _FLATPAK_ID.match(name) else None
    return None


def desktop_exec(path):
    """The program a .desktop file starts: its Exec= line's first word that is
    not "env" or VAR=value, resolved with shutil.which. A Flatpak app's
    launcher gives "flatpak:<app id>". None if there is none."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    section = None
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section not in (None, "[Desktop Entry]"):
            continue
        if s.startswith("X-Flatpak=") and _FLATPAK_ID.match(s[10:].strip()):
            return "flatpak:" + s[10:].strip()
    section = None
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section not in (None, "[Desktop Entry]") or not s.startswith("Exec="):
            continue
        try:
            words = shlex.split(s[5:])
        except ValueError:
            words = s[5:].split()
        words = [w for w in words if not (w == "env" or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w))]
        app_id = _flatpak_run_id(words)
        if app_id:
            return "flatpak:" + app_id
        return _resolve(words[0]) if words else None
    return None


def program_target(program):
    """The resolved executable a profile's program setting stands for."""
    if not isinstance(program, str) or not program:
        return None
    if program.endswith(".desktop"):
        return desktop_exec(program)
    path = os.path.expanduser(program)
    app_id = _flatpak_export_id(path)
    if app_id:
        return "flatpak:" + app_id
    if _LINUX and program.lower().endswith(".exe"):
        # A Windows game under Wine or Proton: its process is the Wine loader,
        # and argv[0] the Windows path of the .exe ("Z:\\...\\Game.exe").
        return "winexe:" + _win_basename(program)
    return _resolve(path)


_LINUX = sys.platform.startswith("linux")


def _win_basename(path):
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _linux_processes():
    """The process list read from /proc: a few ms where psutil takes about
    40 (no psutil call per process). create_time is the start in clock ticks
    since boot, which orders the processes as well."""
    out = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for d in names:
        if not d.isdigit():
            continue
        base = "/proc/" + d + "/"
        try:
            with open(base + "stat", "rb") as f:
                st = f.read()
            start = int(st[st.rfind(b")") + 2:].split()[19])
        except (OSError, ValueError, IndexError):
            continue
        try:
            exe = os.readlink(base + "exe")
        except OSError:
            exe = None
        try:
            with open(base + "cmdline", "rb") as f:
                cmd = [c.decode("utf-8", "replace") for c in f.read().split(b"\0") if c]
        except OSError:
            cmd = []
        flatpak = None
        try:
            with open(base + "cgroup", "rb") as f:
                m = _FLATPAK_CGROUP.search(f.read().decode("utf-8", "replace"))
            flatpak = m.group(1) if m else None
        except OSError:
            pass
        out.append({"pid": int(d), "exe": exe, "cmdline": cmd, "create_time": start,
                    "flatpak": flatpak})
    return out


def _default_processes():
    if _LINUX and os.path.isdir("/proc/self"):
        return _linux_processes()
    return _psutil_processes()


def _psutil_processes():
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for p in psutil.process_iter(["pid", "exe", "cmdline", "create_time"]):
        info = getattr(p, "info", None)
        if isinstance(info, dict):
            out.append(info)
    return out


def _process_paths(proc):
    """Resolved executables a process stands for: its exe, its argv[0] and,
    for an interpreter, the script it runs. On Linux also "flatpak:<app id>"
    for a process of a Flatpak app and "winexe:<name>" for a Windows program
    under Wine."""
    paths = []
    if proc.get("flatpak"):
        paths.append("flatpak:" + proc["flatpak"])
    exe = proc.get("exe")
    if exe:
        try:
            paths.append(os.path.realpath(exe))
        except (OSError, ValueError):
            pass
    cmd = proc.get("cmdline") or []
    if cmd and _LINUX and isinstance(cmd[0], str) and cmd[0].lower().endswith(".exe"):
        paths.append("winexe:" + _win_basename(cmd[0]))
    if cmd:
        r = _resolve(cmd[0]) if isinstance(cmd[0], str) else None
        if r:
            paths.append(r)
        base = os.path.basename(exe or cmd[0] or "")
        if len(cmd) > 1 and isinstance(cmd[1], str) and base.startswith(_INTERPRETERS) \
                and os.path.isabs(cmd[1]):
            paths.append(os.path.realpath(cmd[1]))
    return paths


class ProgramWatcher:
    """Switches a device's profile while a program set on it runs.

    Polls the process list every `poll_s` seconds while some profile has a
    program. When a matched program starts, its profile becomes active and
    the profile active before is remembered; when no matched program of the
    device runs any more, the remembered one comes back, unless the user
    switched by hand in between (hand_switched()). If several run, the last
    one started wins. `lister()` returns dicts with "exe", "cmdline",
    "create_time" (psutil's names) and on Linux "flatpak" (the app id or
    None); tests pass their own.
    """

    def __init__(self, service, lister=None, poll_s=2.0):
        self._svc = service
        self._lister = lister or _default_processes
        self.poll_s = poll_s
        self._lock = threading.Lock()
        self._return_to = {}        # device -> profile id to go back to
        self._return_loaded = False  # read from RETURN_FILE on the first tick
        self._last = {}             # device -> profile id the programs asked for
        self._first_seen = {}       # process key -> tick number
        self._tick_no = 0
        self._wake = threading.Event()
        self._stopping = False
        self._thread = None

    # ── Thread ───────────────────────────────────────────────────────────────
    def start(self):
        if self._thread is not None:
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name="profiles-watcher",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout=3.0):
        self._stopping = True
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    def wake(self):
        self._wake.set()

    def _loop(self):
        while not self._stopping:
            active = False
            try:
                active = self.tick()
            except Exception:
                log.exception("program watcher failed")
            if self._stopping:
                break
            self._wake.wait(self.poll_s if active else None)
            self._wake.clear()

    # ── Rules ────────────────────────────────────────────────────────────────
    # The profile to go back to is kept in a file too: the app may be quit
    # (or the PC restarted) while the program runs, and the next start must
    # still go back when the program ends.
    RETURN_FILE = "program_return.json"

    def _return_path(self):
        store = getattr(self._svc, "store", None)
        root = getattr(store, "root", None)
        return os.path.join(root, self.RETURN_FILE) if root else None

    def _load_return(self):
        if self._return_loaded:
            return
        self._return_loaded = True
        path = self._return_path()
        try:
            data = _read_json_file(path) if path and os.path.exists(path) else {}
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            with self._lock:
                for device, pid in data.items():
                    if isinstance(device, str) and isinstance(pid, str):
                        self._return_to.setdefault(device, pid)
                        # Not seen yet this run: the first tick decides.
                        self._last.setdefault(device, "")

    def _save_return(self):
        path = self._return_path()
        if not path:
            return
        with self._lock:
            data = dict(self._return_to)
        try:
            if data:
                _atomic_write_json(path, data)
            else:
                _remove_quietly(path)
        except OSError as e:
            log.warning("program watcher: %s", e)

    def hand_switched(self, device):
        with self._lock:
            had = self._return_to.pop(device, None) is not None
        if had:
            self._save_return()

    def forget(self, device, pid):
        """Profile `pid` is gone: do not go back to it."""
        with self._lock:
            had = self._return_to.get(device) == pid
            if had:
                self._return_to.pop(device, None)
        if had:
            self._save_return()

    def tick(self):
        """One poll. Returns True if some profile has a program."""
        self._load_return()
        programs = self._svc.programs()     # {device: {pid: program}}
        if not any(programs.values()):
            with self._lock:
                self._last.clear()
                had = bool(self._return_to)
                self._return_to.clear()     # no program set: nothing to go back from
            if had:
                self._save_return()
            return False
        targets = {}
        for device, by_pid in programs.items():
            for pid, program in by_pid.items():
                t = program_target(program)
                if t:
                    targets.setdefault(device, []).append((t, pid))
        self._tick_no += 1
        procs = list(self._lister() or [])
        seen_keys = set()
        best = {}                           # device -> (start, pid)
        for proc in procs:
            paths = _process_paths(proc)
            if not paths:
                continue
            key = (proc.get("pid"), proc.get("create_time"), tuple(paths))
            seen_keys.add(key)
            order = self._first_seen.setdefault(key, self._tick_no)
            started = proc.get("create_time")
            started = (started, order) if isinstance(started, (int, float)) else (0, order)
            for device, pairs in targets.items():
                for target, pid in pairs:
                    if target in paths and (device not in best or started > best[device][0]):
                        best[device] = (started, pid)
        for key in list(self._first_seen):
            if key not in seen_keys:
                del self._first_seen[key]
        for device in set(programs) | set(self._last):
            want = best[device][1] if device in best else None
            with self._lock:
                if self._last.get(device) == want:
                    continue
                self._last[device] = want
            if self._act(device, want) is False:
                with self._lock:            # tried again on the next poll
                    self._last.pop(device, None)
        return True

    def _act(self, device, want):
        active = self._svc.active_id(device)
        if want is not None:
            if want == active:
                return
            with self._lock:
                new = device not in self._return_to
                self._return_to.setdefault(device, active)
            if new:
                self._save_return()
            return self._svc.activate_for_program(device, want)
        with self._lock:
            back = self._return_to.pop(device, None)
        if back is not None:
            self._save_return()
        if back and back != active and self._svc.has_profile(device, back):
            if self._svc.activate_for_program(device, back) is False:
                with self._lock:
                    self._return_to.setdefault(device, back)
                self._save_return()
                return False


# ── Worker ───────────────────────────────────────────────────────────────────

class _Worker:
    """One thread that runs jobs one at a time."""

    def __init__(self, name="profiles-worker"):
        self._name = name
        self._q = queue.Queue()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def in_worker(self):
        return self._thread is not None and threading.current_thread() is self._thread

    def start(self):
        if self.running:
            return
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread.start()

    def post(self, fn, *args, **kw):
        fut = Future()
        self._q.put((fut, fn, args, kw))
        return fut

    def stop(self, timeout=5.0):
        t = self._thread
        if t is None:
            return True
        self._q.put(None)
        if t is not threading.current_thread():
            t.join(timeout)
        alive = t.is_alive()
        if not alive:
            self._thread = None
        return not alive

    def _loop(self):
        while True:
            job = self._q.get()
            if job is None:
                return
            fut, fn, args, kw = job
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args, **kw))
            except BaseException as e:
                fut.set_exception(e)


# ── Service ──────────────────────────────────────────────────────────────────

class ProfileService(Service):
    name = "profiles"

    def __init__(self, app, config=None, config_dir=None, version="",
                 process_lister=None, poll_s=2.0, watch=True, call_timeout=120.0,
                 migrate=True):
        super().__init__(app)
        self._config = config
        self._config_dir = config_dir
        self._store = None
        self.version = version or ""
        self._lock = threading.RLock()
        self._worker = _Worker()
        self._call_timeout = call_timeout
        self._watch = watch
        self._migrate = migrate
        self.watcher = ProgramWatcher(self, lister=process_lister, poll_s=poll_s)
        self._started = False
        #: Set once the start-up job (Standard profiles, migration) ran.
        self.ready = threading.Event()

    # ── Wiring ───────────────────────────────────────────────────────────────
    @property
    def config(self):
        if self._config is None:
            import shared.config as cfg
            self._config = cfg
        return self._config

    @property
    def store(self):
        if self._store is None:
            cdir = self._config_dir or self.config.CONFIG_DIR
            self._store = ProfileStore(cdir, owner=self._owner_fn())
        return self._store

    def _owner_fn(self):
        cfg = self._config
        uid = getattr(cfg, "_real_uid", None) if cfg is not None else None
        gid = getattr(cfg, "_real_gid", None) if cfg is not None else None
        if uid is None or not hasattr(os, "geteuid") or os.geteuid() != 0:
            return None

        def own(path):
            try:
                os.chown(path, uid, gid)
            except OSError:
                pass
        return own

    def adapter(self, device):
        """The device service if it implements the adapter, else None."""
        svc = self.app.service(device) if device in DEVICES else None
        if svc is None:
            return None
        for m in _ADAPTER_METHODS:
            if not callable(getattr(svc, m, None)):
                return None
        return svc

    def adapters(self):
        return {d: a for d in DEVICES for a in [self.adapter(d)] if a is not None}

    def _need(self, device):
        if device not in DEVICES:
            raise _err("unknown_device", "unknown device: %s" % (device,), device=device)
        a = self.adapter(device)
        if a is None:
            raise _err("no_adapter", "%s has no profiles" % device, device=device)
        return a

    def _run(self, fn, *args, **kw):
        """Run `fn` on the worker (inline when already there or not started)
        and return its result."""
        if not self._worker.running or self._worker.in_worker():
            with self._lock:
                return fn(*args, **kw)
        fut = self._worker.post(self._locked, fn, *args, **kw)
        try:
            return fut.result(timeout=self._call_timeout)
        except _FutureTimeout:
            raise _err("busy", "the profiles are busy") from None

    def _locked(self, fn, *args, **kw):
        with self._lock:
            return fn(*args, **kw)

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        if self._started:
            return
        self._started = True
        self.ready.clear()
        fresh = []
        with self._lock:
            for device, adapter in self.adapters().items():
                try:
                    if ensure_index(self.store, device, adapter, capture=False):
                        fresh.append(device)
                except Exception:
                    log.exception("could not create the profile index of %s", device)
        self._worker.start()
        self._worker.post(self._locked, self._startup, fresh)
        if self._watch:
            self.watcher.start()

    def stop(self):
        if not self._started:
            return
        self._started = False
        self.watcher.stop()
        if self._worker.running:
            self._worker.post(self._locked, self._final_snapshot)
            if not self._worker.stop(5.0):
                log.warning("the profiles worker did not stop within 5 s")

    def _startup(self, fresh):
        try:
            for device, adapter in self.adapters().items():
                try:
                    self._recover(device, adapter)
                except Exception:
                    log.exception("recovering the profiles of %s failed", device)
            for device in fresh:
                adapter = self.adapter(device)
                idx = self.store.index(device)
                if adapter is None or not idx.get("active"):
                    continue
                try:
                    fill_standard(self.store, device, adapter, idx["active"])
                except Exception:
                    log.exception("saving the standard profile of %s failed", device)
            if self._migrate:
                self._run_migration()
            for device in fresh:
                self.publish("changed", device=device)
        finally:
            self.ready.set()
            self.watcher.wake()

    def _run_migration(self):
        from core import profiles_migrate
        try:
            results = profiles_migrate.migrate(
                self.store, self.adapters(), self.config,
                macros=self.app.service("macros"))
        except Exception:
            log.exception("migrating the 3.x profiles failed")
            return
        for device, res in results.items():
            self.publish("migrated", device=device, created=list(res.get("created", [])),
                         skipped=list(res.get("skipped", [])))
            if res.get("created"):
                self.publish("changed", device=device)

    def _recover(self, device, adapter):
        """A switch that was cut off (the index still says "switching"):
        copy the target's files in again and make it active."""
        idx = self.store.index(device)
        target = idx.get("switching")
        if not target:
            return
        log.warning("finishing an interrupted switch of %s to %s", device, target)
        names = adapter_names(adapter)
        self.store.restore(device, target, names)
        self._apply(adapter, self.store.read_state(device, target), None)
        idx["active"] = target
        idx.pop("switching", None)
        self.store.save_index(device, idx)

    def _final_snapshot(self):
        """Quit: refresh each active profile's folder from the live files."""
        for device, adapter in self.adapters().items():
            try:
                idx = self.store.index(device)
                if idx.get("active") and not idx.get("switching"):
                    self.store.snapshot(device, idx["active"], adapter_names(adapter))
            except Exception:
                log.exception("saving the active profile of %s on quit failed", device)

    # ── Queries ──────────────────────────────────────────────────────────────
    def _entry(self, idx, pid):
        for p in idx["profiles"]:
            if p["id"] == pid:
                return p
        return None

    def _need_entry(self, device, idx, pid):
        p = self._entry(idx, pid) if isinstance(pid, str) else None
        if p is None:
            raise _err("not_found", "no such profile: %s" % (pid,), device=device, id=pid)
        return p

    # The queries read the index without the lock: it is written atomically,
    # and a switch can hold the lock for seconds (capture_profile_state).
    def active_id(self, device):
        return self.store.index(device).get("active")

    def has_profile(self, device, pid):
        return self._entry(self.store.index(device), pid) is not None

    def programs(self):
        """{device: {id: program}} of every profile that has a program."""
        out = {}
        for device in self.adapters():
            progs = {p["id"]: p["program"] for p in self.store.index(device)["profiles"]
                     if p.get("program")}
            if progs:
                out[device] = progs
        return out

    def list(self, device):
        self._need(device)
        idx = self.store.index(device)
        rows = []
        for p in idx["profiles"]:
            pic = None
            if p.get("picture"):
                path = os.path.join(self.store.folder(device, p["id"]), p["picture"])
                pic = path if os.path.isfile(path) else None
            rows.append({"id": p["id"], "name": p["name"], "picture_url": pic,
                         "program": p.get("program")})
        return {"device": device, "profiles": rows, "active": idx.get("active"),
                "limit": LIMITS[device]}

    # ── Activation ───────────────────────────────────────────────────────────
    def _apply(self, adapter, state, previous_folder):
        try:
            adapter.apply_profile(state, previous_folder)
        except Exception:
            log.exception("apply_profile failed")

    def _switch(self, device, adapter, idx, pid, previous_folder=None, save_outgoing=True):
        """Spec "Switching" 1-4: save the outgoing profile, copy the new one
        in, apply it, write the index, publish."""
        names = adapter_names(adapter)
        old = idx.get("active")
        if idx.get("switching"):
            # A switch was cut off: the live files are nobody's, do not save them.
            save_outgoing = False
        if save_outgoing and old and self._entry(idx, old) is not None and old != pid:
            state = capture_state(adapter)                                  # 1
            try:
                self.store.snapshot(device, old, names)
            except OSError as e:
                raise _err("snapshot_failed", "the active profile could not be saved: %s" % e,
                           device=device, id=old) from None
            if state is not None:
                self.store.write_state(device, old, state)
            previous_folder = self.store.folder(device, old)
        idx["switching"] = pid
        self.store.save_index(device, idx)
        failed = self.store.restore(device, pid, names)                     # 2
        if failed:
            log.warning("profile %s/%s: could not restore %s", device, pid, ", ".join(failed))
        self._apply(adapter, self.store.read_state(device, pid), previous_folder)  # 3
        idx["active"] = pid                                                 # 4
        idx.pop("switching", None)
        self.store.save_index(device, idx)
        entry = self._entry(idx, pid)
        self.publish("activated", device=device, id=pid, name=entry["name"] if entry else "")

    def _activate(self, device, pid=None, name=None, by_program=False):
        adapter = self._need(device)
        idx = self.store.index(device)
        if pid is None and name is not None:
            entry = next((p for p in idx["profiles"] if p["name"] == name), None) or next(
                (p for p in idx["profiles"] if p["name"].lower() == str(name).lower()), None)
            if entry is None:
                raise _err("not_found", "no such profile: %s" % (name,), device=device, name=name)
        else:
            entry = self._need_entry(device, idx, pid)
        if not by_program:
            self.watcher.hand_switched(device)
        if idx.get("active") != entry["id"] or idx.get("switching"):
            self._switch(device, adapter, idx, entry["id"])
        return {"id": entry["id"], "name": entry["name"]}

    def activate(self, device, id=None, name=None):
        if id is None and name is None:
            raise _err("not_found", "activate needs an id or a name", device=device)
        return self._run(self._activate, device, id, name)

    def activate_for_program(self, device, pid):
        """The program watcher's switch (does not count as a hand switch)."""
        try:
            self._run(self._activate, device, pid, None, True)
            return True
        except Exception:
            log.exception("switching %s to %s for a program failed", device, pid)
            return False

    # ── Create, duplicate, rename, delete, reset ─────────────────────────────
    def _check_limit(self, device, idx):
        limit = LIMITS[device]
        if limit is not None and len(idx["profiles"]) >= limit:
            raise _err("limit", "%s holds at most %d profiles" % (device, limit),
                       device=device, limit=limit)

    @staticmethod
    def _clean_name(name):
        if not isinstance(name, str):
            raise _err("bad_name", "a profile needs a name")
        name = " ".join(name.split())[:MAX_NAME]
        if not name:
            raise _err("bad_name", "a profile needs a name")
        return name

    @staticmethod
    def _unique(idx, base):
        names = {p["name"] for p in idx["profiles"]}
        if base not in names:
            return base
        n = 2
        while "%s %d" % (base, n) in names:
            n += 1
        return "%s %d" % (base, n)

    def _create(self, device, name=None):
        adapter = self._need(device)
        idx = self.store.index(device)
        self._check_limit(device, idx)
        if name is None:
            n = len(idx["profiles"]) + 1
            names = {p["name"] for p in idx["profiles"]}
            while NEW_NAME % n in names:
                n += 1
            name = NEW_NAME % n
        else:
            name = self._clean_name(name)
        try:
            state = adapter.factory_profile_state()
        except Exception:
            log.exception("factory_profile_state failed")
            state = {}
        pid = self.store.new_id(device)
        self.store.make_folder(device, pid)
        self.store.write_state(device, pid, state if isinstance(state, dict) else {})
        idx["profiles"].append({"id": pid, "name": name, "picture": None, "program": None})
        self.store.save_index(device, idx)
        self.publish("changed", device=device)
        self.watcher.hand_switched(device)
        self._switch(device, adapter, idx, pid)
        return {"id": pid}

    def create(self, device, name=None):
        return self._run(self._create, device, name)

    def _refresh_active(self, device, adapter, idx):
        """Save the active profile's live files and state into its folder."""
        old = idx.get("active")
        if not old or idx.get("switching"):
            return
        state = capture_state(adapter)
        try:
            self.store.snapshot(device, old, adapter_names(adapter))
        except OSError as e:
            raise _err("snapshot_failed", "the active profile could not be saved: %s" % e,
                       device=device, id=old) from None
        if state is not None:
            self.store.write_state(device, old, state)

    def _duplicate(self, device, pid):
        adapter = self._need(device)
        idx = self.store.index(device)
        src = self._need_entry(device, idx, pid)
        self._check_limit(device, idx)
        is_active = idx.get("active") == pid
        if is_active:
            self._refresh_active(device, adapter, idx)
        new = self.store.new_id(device)
        self.store.copy_profile(device, pid, new)
        idx["profiles"].append({"id": new, "name": self._unique(idx, (src["name"] + COPY_SUFFIX)[:MAX_NAME]),
                                "picture": src.get("picture"), "program": None})
        self.watcher.hand_switched(device)
        if is_active:
            # The live files already are the copy's: no swap, no device write.
            idx["active"] = new
            self.store.save_index(device, idx)
            self.publish("changed", device=device)
            self.publish("activated", device=device, id=new, name=idx["profiles"][-1]["name"])
        else:
            self.store.save_index(device, idx)
            self.publish("changed", device=device)
            self._switch(device, adapter, idx, new)
        return {"id": new}

    def duplicate(self, device, id):
        return self._run(self._duplicate, device, id)

    def _rename(self, device, pid, name):
        self._need(device)
        idx = self.store.index(device)
        entry = self._need_entry(device, idx, pid)
        entry["name"] = self._clean_name(name)
        self.store.save_index(device, idx)
        self.publish("changed", device=device)
        return {"id": pid, "name": entry["name"]}

    def rename(self, device, id, name):
        return self._run(self._rename, device, id, name)

    def _delete(self, device, pid):
        adapter = self._need(device)
        idx = self.store.index(device)
        self._need_entry(device, idx, pid)
        if len(idx["profiles"]) <= 1:
            raise _err("last_profile", "the last profile cannot be deleted", device=device)
        was_active = idx.get("active") == pid
        idx["profiles"] = [p for p in idx["profiles"] if p["id"] != pid]
        self.watcher.forget(device, pid)
        if not was_active:
            self.store.save_index(device, idx)
            self.store.retire(device, pid)
            self.publish("changed", device=device)
            return {"deleted": pid}
        new = idx["profiles"][0]["id"]
        # The index says "switching" before the folder goes, so a cut-off
        # delete never takes the live files for another profile's.
        idx["switching"] = new
        self.store.save_index(device, idx)
        prev = self.store.retire(device, pid)
        self.publish("changed", device=device)
        self._switch(device, adapter, idx, new, previous_folder=prev, save_outgoing=False)
        return {"deleted": pid, "active": new}

    def delete(self, device, id):
        return self._run(self._delete, device, id)

    def _reset(self, device, pid):
        adapter = self._need(device)
        idx = self.store.index(device)
        self._need_entry(device, idx, pid)
        try:
            state = adapter.factory_profile_state()
        except Exception:
            log.exception("factory_profile_state failed")
            state = {}
        state = state if isinstance(state, dict) else {}
        if idx.get("active") != pid:
            self.store.clear_files(device, pid)
            self.store.write_state(device, pid, state)
            self.publish("changed", device=device)
            return {"id": pid}
        # Keep what the profile was in ".previous" (the adapter reads the
        # outgoing bindings there), then empty it and apply the factory state.
        prev = self.store.previous_dir(device)
        _remove_quietly(prev)
        try:
            self.store.snapshot_to(device, prev, adapter_names(adapter))
        except OSError as e:
            raise _err("snapshot_failed", "the active profile could not be saved: %s" % e,
                       device=device, id=pid) from None
        self.store.clear_files(device, pid)
        self.store.write_state(device, pid, state)
        self._switch(device, adapter, idx, pid, previous_folder=prev, save_outgoing=False)
        self.publish("changed", device=device)
        return {"id": pid}

    def reset(self, device, id):
        return self._run(self._reset, device, id)

    # ── Program, picture ─────────────────────────────────────────────────────
    def _set_program(self, device, pid, path):
        self._need(device)
        idx = self.store.index(device)
        entry = self._need_entry(device, idx, pid)
        if path in (None, ""):
            entry["program"] = None
        elif isinstance(path, str) and len(path) <= 4096 and "\x00" not in path:
            entry["program"] = path.strip() or None
        else:
            raise _err("bad_file", "not a program path")
        self.store.save_index(device, idx)
        self.publish("changed", device=device)
        self.watcher.wake()
        return {"id": pid, "program": entry["program"]}

    def set_program(self, device, id, path=None):
        return self._run(self._set_program, device, id, path)

    def _set_picture(self, device, pid, path):
        self._need(device)
        idx = self.store.index(device)
        entry = self._need_entry(device, idx, pid)
        target = os.path.join(self.store.make_folder(device, pid), PICTURE_FILE)
        if path in (None, ""):
            _remove_quietly(target)
            entry["picture"] = None
        else:
            try:
                from PIL import Image
                import io
                with Image.open(path) as img:
                    img.seek(0)
                    pic = img.convert("RGBA")
                pic.thumbnail((PICTURE_MAX, PICTURE_MAX))
                buf = io.BytesIO()
                pic.save(buf, "PNG")
            except Exception as e:
                raise _err("bad_picture", "not a picture: %s" % e, path=str(path)) from None
            _atomic_write_bytes(target, buf.getvalue())
            entry["picture"] = PICTURE_FILE
        self.store.save_index(device, idx)
        self.publish("changed", device=device)
        return {"id": pid, "picture_url": target if entry["picture"] else None}

    def set_picture(self, device, id, path=None):
        return self._run(self._set_picture, device, id, path)

    # ── Export ───────────────────────────────────────────────────────────────
    def _export(self, device, pid, path):
        adapter = self._need(device)
        idx = self.store.index(device)
        entry = self._need_entry(device, idx, pid)
        if not isinstance(path, str) or not path:
            raise _err("bad_file", "no file to export to")
        if not path.endswith(EXPORT_EXT):
            path += EXPORT_EXT
        if idx.get("active") == pid:
            self._refresh_active(device, adapter, idx)
        folder = self.store.folder(device, pid)
        meta = {"format": EXPORT_FORMAT, "device": device, "name": entry["name"],
                "program": entry.get("program"), "app_version": self.version,
                "config_dir": self.store.config_dir}
        tmp = path + _TMP
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("profile.json", json.dumps(meta, indent=2))
                for root, dirs, files in os.walk(folder):
                    dirs.sort()
                    for fn in sorted(files):
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, folder).replace(os.sep, "/")
                        z.write(full, "files/" + rel)
                rewrite = {}
                used = set()
                for ref in picture_refs(adapter, folder):
                    base = os.path.basename(ref)
                    arc = "pictures/" + base
                    n = 1
                    while arc in used:
                        arc = "pictures/%d_%s" % (n, base)
                        n += 1
                    used.add(arc)
                    z.write(ref, arc)
                    rewrite[ref] = arc
                z.writestr("rewrite.json", json.dumps(rewrite, indent=2))
            os.replace(tmp, path)
        except Exception as e:
            _remove_quietly(tmp)
            raise _err("bad_file", "could not write %s: %s" % (path, e), path=path) from None
        return {"path": path}

    def export(self, device, id, path):
        return self._run(self._export, device, id, path)

    # ── Import ───────────────────────────────────────────────────────────────
    def _import(self, path, device=None):
        if not isinstance(path, str) or not path:
            raise _err("bad_file", "no file to import")
        if os.path.isdir(path):
            return self._import_3x(path, device)
        return self._import_bcprofile(path, device)

    def import_(self, path, device=None):
        return self._run(self._import, path, device)

    def _import_3x(self, path, device=None):
        from core import profiles_migrate
        devices = [device] if device is not None else [
            d for d in DEVICES if self.adapter(d) is not None]
        if device is not None:
            self._need(device)
        if not profiles_migrate.is_3x_folder(path):
            raise _err("bad_file", "not a 3.x profile folder", path=path)
        name = os.path.basename(os.path.normpath(path)) or "Profil"
        created = []
        for d in devices:
            adapter = self.adapter(d)
            if adapter is None or not profiles_migrate.has_data(d, path):
                continue
            ensure_index(self.store, d, adapter)
            idx = self.store.index(d)
            if device is not None:
                self._check_limit(d, idx)
            elif LIMITS[d] is not None and len(idx["profiles"]) >= LIMITS[d]:
                continue
            # The new profile is the current state with the 3.x files in it.
            self._refresh_active(d, adapter, idx)
            pid = profiles_migrate.migrate_one(self.store, d, adapter, self.config, path)
            idx["profiles"].append({"id": pid, "name": name[:MAX_NAME], "picture": None,
                                    "program": None})
            self.store.save_index(d, idx)
            created.append({"device": d, "id": pid, "name": name[:MAX_NAME]})
            self.publish("changed", device=d)
        if device is not None and not created:
            raise _err("no_data", "the folder has nothing for %s" % device, device=device)
        if created:
            profiles_migrate.merge_macros(path, self.config, self.app.service("macros"))
        return {"ids": [c["id"] for c in created], "profiles": created}

    def _import_bcprofile(self, path, device=None):
        try:
            z = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            raise _err("bad_file", "not a profile file: %s" % e, path=path) from None
        with z:
            infos = z.infolist()
            if len(infos) > MAX_IMPORT_FILES or sum(i.file_size for i in infos) > MAX_IMPORT_BYTES \
                    or any(i.filename.endswith(".json") and i.file_size > MAX_IMPORT_JSON_BYTES
                           for i in infos):
                raise _err("bad_file", "the profile file is too large", path=path)
            for i in infos:
                if _clean_rel(i.filename) is None:
                    raise _err("bad_file", "bad entry %r" % i.filename, path=path)
            try:
                meta = json.loads(z.read("profile.json").decode("utf-8"))
            except Exception:
                raise _err("bad_file", "the file has no profile.json", path=path) from None
            if not isinstance(meta, dict) or meta.get("format") != EXPORT_FORMAT:
                raise _err("bad_file", "unknown profile format", path=path)
            dev = meta.get("device")
            if dev not in DEVICES:
                raise _err("bad_file", "unknown device in the file", path=path)
            if device is not None and device != dev:
                raise _err("wrong_device", "the profile is for %s" % dev, device=device, file_device=dev)
            adapter = self._need(dev)
            ensure_index(self.store, dev, adapter)
            idx = self.store.index(dev)
            self._check_limit(dev, idx)
            names = adapter_names(adapter)
            pid = self.store.new_id(dev)
            folder = self.store.folder(dev, pid)
            tmp = folder + ".import"
            _remove_quietly(tmp)
            try:
                os.makedirs(tmp)
                for i in infos:
                    if i.is_dir() or not i.filename.startswith("files/"):
                        continue
                    rel = i.filename[len("files/"):]
                    c = _clean_rel(rel)
                    if c is None:
                        continue
                    if rel not in (state_name(dev), PICTURE_FILE) \
                            and not self.store.is_profile_name(dev, rel, names):
                        log.warning("import: %s is not a profile file of %s, skipped", rel, dev)
                        continue
                    dst = os.path.join(tmp, c[0])
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with z.open(i) as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
                mapping = self._import_pictures(z, pid)
                old_cfg = meta.get("config_dir")
                if isinstance(old_cfg, str) and old_cfg and old_cfg != self.store.config_dir:
                    for sep in {"/", os.sep}:
                        mapping.setdefault(old_cfg.rstrip("/\\") + sep,
                                           self.store.config_dir.rstrip("/\\") + sep)
                _rewrite_json_tree(tmp, mapping)
                os.rename(tmp, folder)
            except CommandError:
                _remove_quietly(tmp)
                raise
            except Exception as e:
                _remove_quietly(tmp)
                raise _err("bad_file", "the profile could not be imported: %s" % e, path=path) from None
        name = meta.get("name")
        try:
            name = self._clean_name(name)
        except CommandError:
            name = NEW_NAME % (len(idx["profiles"]) + 1)
        program = meta.get("program") if isinstance(meta.get("program"), str) else None
        idx["profiles"].append({"id": pid, "name": name,
                                "picture": PICTURE_FILE if os.path.isfile(
                                    os.path.join(folder, PICTURE_FILE)) else None,
                                "program": program or None})
        self.store.save_index(dev, idx)
        self.publish("changed", device=dev)
        if program:
            self.watcher.wake()
        return {"id": pid, "device": dev}

    def _import_pictures(self, z, pid):
        """Put the file's pictures into the libraries; {old path: new path}."""
        try:
            rewrite = json.loads(z.read("rewrite.json").decode("utf-8"))
        except KeyError:
            return {}
        except Exception:
            raise _err("bad_file", "rewrite.json cannot be read") from None
        if not isinstance(rewrite, dict):
            raise _err("bad_file", "rewrite.json cannot be read")
        members = set(z.namelist())
        mapping = {}
        for old, arc in rewrite.items():
            if not isinstance(old, str) or not isinstance(arc, str) \
                    or not arc.startswith("pictures/") or arc not in members:
                continue
            # Only a picture's path is rewritten, never a short word that
            # would change every string it occurs in.
            if not ("/" in old or "\\" in old) or not os.path.splitext(old)[1]:
                continue
            data = z.read(arc)
            base = os.path.basename(old.replace("\\", "/"))
            if _clean_rel(base) is None or base in ("", ".", ".."):
                continue
            parent = os.path.basename(os.path.dirname(old.replace("\\", "/")))
            if parent in LIBRARY_DIRS:
                dest_dir = os.path.join(self.store.config_dir, parent)
            else:
                dest_dir = os.path.join(self.store.config_dir, PICTURES_DIR, pid)
            mapping[old] = _place_picture(dest_dir, base, data)
        return mapping

    # ── Commands ─────────────────────────────────────────────────────────────
    def commands(self):
        return {
            "list": self.list,
            "activate": self.activate,
            "create": self.create,
            "duplicate": self.duplicate,
            "rename": self.rename,
            "delete": self.delete,
            "reset": self.reset,
            "set_program": self.set_program,
            "set_picture": self.set_picture,
            "export": self.export,
            "import": self.import_,
        }


def _place_picture(dest_dir, base, data):
    """Write `data` as `dest_dir/base`, or reuse a file there with the same
    content; a different file of that name gets "<stem>-<md5>" instead."""
    os.makedirs(dest_dir, exist_ok=True)
    digest = hashlib.md5(data).hexdigest()
    stem, ext = os.path.splitext(base)
    for name in (base, "%s-%s%s" % (stem, digest[:8], ext)):
        dest = os.path.join(dest_dir, name)
        if os.path.isfile(dest):
            try:
                if _file_md5(dest) == digest:
                    return dest
            except OSError:
                pass
            continue
        _atomic_write_bytes(dest, data)
        return dest
    dest = os.path.join(dest_dir, "%s-%s%s" % (stem, digest, ext))
    _atomic_write_bytes(dest, data)
    return dest


def _rewrite_strings(obj, pattern, mapping):
    if isinstance(obj, str):
        return pattern.sub(lambda m: mapping[m.group(0)], obj)
    if isinstance(obj, list):
        return [_rewrite_strings(v, pattern, mapping) for v in obj]
    if isinstance(obj, dict):
        return {_rewrite_strings(k, pattern, mapping): _rewrite_strings(v, pattern, mapping)
                for k, v in obj.items()}
    return obj


def _rewrite_json_tree(folder, mapping):
    """Replace every old path of `mapping` by its new path in the strings of
    every JSON file under `folder` (one pass, longest match first)."""
    mapping = {k: v for k, v in mapping.items() if k and k != v}
    if not mapping:
        return
    pattern = re.compile("|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)))
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            path = os.path.join(root, fn)
            try:
                data = _read_json_file(path)
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            new = _rewrite_strings(data, pattern, mapping)
            if new != data:
                _atomic_write_json(path, new)


def install(app, **kw):
    return app.add_service(ProfileService(app, **kw))
