"""Taking over the profiles of BaseCamp-Linux 3.x, once.

3.x kept one list of profiles for everything, as snapshots of a few files
under CONFIG_DIR/profiles/<name>/ (buttons.json, obs.json, the legacy
DisplayPad files, displaypad_rotation, macros.json) and the active one's
name in CONFIG_DIR/active_profile. 4.0 has profiles per device
(core/profiles.py). migrate() turns each 3.x folder into one profile per
device it has data for:

- Everest Max, if it has a buttons.json: the current Everest Max profile
  (its files and state) with that buttons.json;
- DisplayPad, if it has a legacy DisplayPad file: the current DisplayPad
  profile with its pages converted from those files, and its rotation;
- Makalu: 3.x profiles never held Makalu files, so none.

Macros of a 3.x profile whose id the global library lacks are added; none
is overwritten. obs.json is ignored. The 3.x active profile and then the
others (sorted, case-insensitive) take the free places; what does not fit
under a device's limit is reported as skipped and can be imported later
(profiles.import with the folder).

Nothing inside CONFIG_DIR/profiles and nothing of active_profile is ever
written, so a downgrade to 3.x still finds them. A device whose index says
"migrated_3x" is not migrated again.
"""
import json
import logging
import os
import shutil

from core.profiles import (DEVICES, LIMITS, MAX_NAME, PICTURE_FILE, _remove_quietly,
                           ensure_index)

log = logging.getLogger("basecamp.core.profiles_migrate")

LEGACY_DP_FILES = ("displaypad_buttons.json", "displaypad_pages.json",
                   "displaypad_actions.json", "displaypad_fullscreen.json")
PROFILES_DIR_NAME = "profiles"
ACTIVE_FILE_NAME = "active_profile"


def has_data(device, folder):
    """Does the 3.x profile `folder` hold anything for `device`?"""
    if device == "everest_max":
        return os.path.isfile(os.path.join(folder, "buttons.json"))
    if device == "displaypad":
        return any(os.path.isfile(os.path.join(folder, f)) for f in LEGACY_DP_FILES)
    return False


def is_3x_folder(folder):
    return os.path.isdir(folder) and any(has_data(d, folder) for d in DEVICES)


def _3x_order(profiles_dir):
    """The 3.x profile names: the active one first, then the others sorted
    case-insensitively."""
    try:
        names = [n for n in os.listdir(profiles_dir)
                 if os.path.isdir(os.path.join(profiles_dir, n))]
    except OSError:
        return []
    active = ""
    try:
        with open(os.path.join(os.path.dirname(profiles_dir), ACTIVE_FILE_NAME)) as f:
            active = f.read().strip()
    except OSError:
        pass
    rest = sorted((n for n in names if n != active), key=lambda n: (n.lower(), n))
    return ([active] if active in names else []) + rest


def migrate_one(store, device, adapter, config, src_dir, base_pid=None):
    """A new profile folder for `device` made from the 3.x folder `src_dir`
    on top of profile `base_pid` (default: the active one). Returns its id;
    the caller adds it to the index."""
    idx = store.index(device)
    base = base_pid or idx.get("active")
    pid = store.new_id(device)
    folder = store.folder(device, pid)
    tmp = folder + ".migrate"
    _remove_quietly(tmp)
    try:
        base_folder = store.folder(device, base) if base else None
        if base_folder and os.path.isdir(base_folder):
            shutil.copytree(base_folder, tmp, symlinks=True,
                            ignore=lambda d, names: [PICTURE_FILE]
                            if os.path.normpath(d) == os.path.normpath(base_folder) else [])
        else:
            os.makedirs(tmp)
        if device == "everest_max":
            shutil.copyfile(os.path.join(src_dir, "buttons.json"),
                            os.path.join(tmp, "buttons.json"))
        elif device == "displaypad":
            pages = os.path.join(tmp, "displaypad_pages")
            _remove_quietly(pages)
            os.makedirs(pages)
            config.migrate_legacy_displaypad_pages(
                src_dir=src_dir, page_dir=pages, fallback_dir=config.CONFIG_DIR)
            rot = os.path.join(src_dir, "displaypad_rotation")
            if os.path.isfile(rot):
                shutil.copyfile(rot, os.path.join(tmp, "displaypad_rotation"))
        os.rename(tmp, folder)
    except BaseException:
        _remove_quietly(tmp)
        raise
    return pid


def merge_macros(src_dir, config, macros_service=None):
    """Add the macros of `src_dir`/macros.json whose id the global library
    lacks. Returns the ids added; an existing macro is never overwritten."""
    path = os.path.join(src_dir, "macros.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        theirs = data.get("macros") if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        log.warning("3.x macros in %s cannot be read", path)
        return []
    if not isinstance(theirs, dict):
        return []
    svc = macros_service
    if svc is not None and all(hasattr(svc, a) for a in ("_lock", "_load", "_save")):
        with svc._lock:
            live = svc._load()
            added = [k for k, m in theirs.items() if k not in live and isinstance(m, dict)]
            if added:
                for k in added:
                    live[k] = theirs[k]
                svc._save(live, added[0] if len(added) == 1 else None, "imported")
        return added
    whole = config.load_macros() or {}
    if not isinstance(whole, dict):
        whole = {}
    live = whole.get("macros")
    if not isinstance(live, dict):
        live = whole["macros"] = {}
    added = [k for k, m in theirs.items() if k not in live and isinstance(m, dict)]
    if added:
        for k in added:
            live[k] = theirs[k]
        config.save_macros(whole)
    return added


def migrate(store, adapters, config, toast_cb=None, macros=None):
    """Take the 3.x profiles over for every device in `adapters` ({device:
    adapter}) whose index lacks "migrated_3x". Makes sure each such device
    has its "Standard Profil" from the live files first. Sets "migrated_3x"
    in every one of those indexes, even when nothing was created.

    Returns {device: {"created": [names], "skipped": [names]}} for the
    devices it handled ({} when there was nothing to do). `toast_cb(device,
    created, skipped)` is called per device; `macros` is the macros service
    (else shared.config's load/save is used).
    """
    todo = [d for d in DEVICES if d in adapters]
    for device in todo:
        ensure_index(store, device, adapters[device])
    todo = [d for d in todo if not store.index(device=d).get("migrated_3x")]
    if not todo:
        return {}
    profiles_dir = os.path.join(store.config_dir, PROFILES_DIR_NAME)
    order = _3x_order(profiles_dir) if os.path.isdir(profiles_dir) else []
    results = {}
    for device in todo:
        idx = store.index(device)
        base = idx.get("active")
        existing = {p["name"] for p in idx["profiles"]}
        # Entries an earlier run took over carry the 3.x folder's name: a run
        # cut off before goes on after them. Any other entry with the same
        # name (a 3.x profile called "Standard Profil") is a different one.
        resumed = {p.get("from_3x") for p in idx["profiles"] if p.get("from_3x")}
        limit = LIMITS[device]
        created, skipped = [], []
        for name in order:
            src = os.path.join(profiles_dir, name)
            if not has_data(device, src):
                continue
            if name in resumed:
                continue                    # taken over by a run cut off before
            pname = name[:MAX_NAME]
            n = 2
            while pname in existing:
                tail = " (3.x)" if n == 2 else " (3.x %d)" % n
                pname = name[:MAX_NAME - len(tail)] + tail
                n += 1
            if limit is not None and len(idx["profiles"]) >= limit:
                skipped.append(name)
                continue
            try:
                pid = migrate_one(store, device, adapters[device], config, src, base)
            except Exception:
                log.exception("3.x profile %r could not be taken over for %s", name, device)
                skipped.append(name)
                continue
            idx["profiles"].append({"id": pid, "name": pname, "picture": None,
                                    "program": None, "from_3x": name})
            existing.add(pname)
            store.save_index(device, idx)
            created.append(name)
        idx["migrated_3x"] = True
        idx["migrated_3x_skipped"] = skipped
        store.save_index(device, idx)
        results[device] = {"created": created, "skipped": skipped}
        if toast_cb is not None:
            try:
                toast_cb(device, created, skipped)
            except Exception:
                log.exception("migration toast callback failed")
    # The macros of every 3.x profile taken over for some device (a skipped
    # one brings its own when it is imported later).
    taken = {n for r in results.values() for n in r["created"]}
    for name in order:
        if name in taken:
            try:
                merge_macros(os.path.join(profiles_dir, name), config, macros)
            except Exception:
                log.exception("merging the macros of 3.x profile %r failed", name)
    return results
