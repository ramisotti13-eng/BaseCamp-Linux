"""Base Camp's own icon sets as DisplayPad library packs.

Mountain's Base Camp offers its pictures in a select of the image editor
("Images": Default, Adobe Illustrator, Adobe Photoshop, ...). They are
Mountain's files: BaseCamp never ships them. Whoever has Base Camp installed
(a folder, a Wine prefix) or has its installer (the .msi, or the zip on
mountain.gg) can have them as library packs, one per category, in the user's
own configuration directory, where both key pickers and the image editor
offer them:

    icon_packs.install {pack: "basecamp"}                  # an installation found,
                                                           # else the download
    icon_packs.install {pack: "basecamp", path: "..."}     # a folder, .msi or zip
    icon_packs.remove  {pack: "basecamp"}                  # all ten at once

Each category is a pack folder named like the category, with a manifest
{"id": "basecamp-<folder>", "group": "basecamp"}.
"""
import os
import shutil
import tempfile
import time

from core import mountain_assets as ma
from core.devices.displaypad import library as dplib

GROUP = "basecamp"

#: (folder below wwwroot/images, the original's label), in the order of its select.
CATEGORIES = (
    ("default-profile", "Default"),
    ("illustrator-profile", "Adobe Illustrator"),
    ("photoshop-profile", "Adobe Photoshop"),
    ("premiere-profile", "Adobe Premiere"),
    ("davinci-profile", "BlackMagic DaVinci Resolve"),
    ("obs-profile", "OBS Studio"),
    ("Twitch-Icon", "Twitch"),
    ("Youtube", "Youtube"),
    ("spotify", "Spotify"),
    ("Zoom", "Zoom"),
)
_BY_DIR = {d.lower(): (d, label) for d, label in CATEGORIES}
_IMAGES_REL = os.path.join("resources", "bin", "wwwroot", "images")

#: SVGs and the like are not offered; the library knows these.
EXTS = dplib.IMAGE_EXTS


def icons_root(path):
    """The wwwroot/images folder under `path` that has category folders, or None."""
    if not path or not os.path.isdir(path):
        return None
    path = os.path.normpath(path)
    for cand in (os.path.join(path, _IMAGES_REL), os.path.join(path, "wwwroot", "images"),
                 os.path.join(path, "images"), path):
        if os.path.isdir(cand) and any(
                os.path.isdir(os.path.join(cand, d)) for d, _l in CATEGORIES):
            return cand
    return None


def category_files(images_dir):
    """{label: [file path]} of the categories found in `images_dir`
    (directly in each category's folder, sorted)."""
    out = {}
    try:
        names = os.listdir(images_dir)
    except OSError:
        return out
    for name in names:
        hit = _BY_DIR.get(name.lower())
        folder = os.path.join(images_dir, name)
        if hit is None or not os.path.isdir(folder):
            continue
        files = [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                 if not n.startswith(".") and n.lower().endswith(EXTS)
                 and os.path.isfile(os.path.join(folder, n))]
        if files:
            out[hit[1]] = files
    return {label: out[label] for _d, label in CATEGORIES if label in out}


def _choose(index):
    """From an installer's {lower path: MsiFile}: every picture directly in
    a category's folder, as {"<folder>/<file>" in its own case: MsiFile}."""
    chosen = {}
    for rel, f in index.items():
        parts = rel.split("/")
        if len(parts) != 2 or parts[0] not in _BY_DIR or not rel.endswith(EXTS):
            continue
        orig = f.path.replace("\\", "/")
        chosen["%s/%s" % (_BY_DIR[parts[0]][0], orig.rsplit("/", 1)[-1])] = f
    return chosen


def _install_category(lib_dir, label, files, source, cancel=None):
    """One category as a pack folder; replaces an earlier install."""
    staging = tempfile.mkdtemp(prefix=".installing-", dir=lib_dir)
    try:
        names, seen = [], set()
        for src in files:
            if cancel is not None and cancel():
                raise ma.AssetError("cancelled", "cancelled")
            if os.path.getsize(src) > ma.MAX_PACK_MEMBER or not ma.check_picture(src):
                continue
            stem, ext = os.path.splitext(os.path.basename(src))
            name = ma.safe_name(stem, "icon") + ext.lower()
            n = 2
            while name.lower() in seen:
                name = "%s-%d%s" % (ma.safe_name(stem, "icon"), n, ext.lower())
                n += 1
            seen.add(name.lower())
            shutil.copyfile(src, os.path.join(staging, name))
            names.append(name)
        if not names:
            return None
        folder_name = ma.safe_name(label, "Base Camp")
        manifest = {"id": "%s-%s" % (GROUP, folder_name.lower().replace(" ", "-")),
                    "name": folder_name, "group": GROUP, "count": len(names),
                    "source": source, "installed_at": int(time.time()),
                    "files": sorted(names)}
        import json
        with open(os.path.join(staging, dplib.PACK_MANIFEST), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
        dest = os.path.join(lib_dir, folder_name)
        old = None
        if os.path.exists(dest):
            if not dplib.read_manifest(dest).get("group") == GROUP \
                    and not dplib.read_manifest(dest).get("id", "").startswith(GROUP):
                raise ma.AssetError("exists", "a folder %r of your own is in the library" % folder_name)
            old = tempfile.mkdtemp(prefix=".removing-", dir=lib_dir)
            os.rmdir(old)
            os.replace(dest, old)
        os.replace(staging, dest)
        staging = None
        ma._remove(old)
        return folder_name
    finally:
        ma._remove(staging)


def install_from_folder(images_dir, lib_dir, source=None, progress=None, cancel=None):
    """Install every category found in `images_dir`. Returns the pack folder
    names. Raises AssetError("no_images")."""
    cats = category_files(images_dir)
    if not cats:
        raise ma.AssetError("no_images", "no Base Camp icon folders found")
    os.makedirs(lib_dir, exist_ok=True)
    total = sum(len(v) for v in cats.values())
    ma.need_space(lib_dir, sum(os.path.getsize(f) for v in cats.values() for f in v))
    done, made = 0, []
    with ma.pixel_limit():
        for label, files in cats.items():
            name = _install_category(lib_dir, label, files, source or images_dir, cancel)
            if name:
                made.append(name)
            done += len(files)
            if progress:
                progress(int(done * 100 / total))
    if not made:
        raise ma.AssetError("no_images", "none of the pictures can be read")
    return made


def install(path, lib_dir, config_dir, progress=None, cancel=None):
    """The categories out of `path`: an installation or images folder, a
    Base Camp .msi, or the zip around it. `progress(stage, pct)`, stage
    "extract" then "install". Returns the pack folder names."""
    def say(stage, pct):
        if progress:
            progress(stage, max(0, min(100, int(pct))))

    if not path or not os.path.exists(path):
        raise ma.AssetError("not_found", "no such file: %s" % path)
    if os.path.isdir(path):
        root = icons_root(path)
        if root is None:
            raise ma.AssetError("no_images", "%s has no Base Camp icon folders" % path)
        say("install", 0)
        return install_from_folder(root, lib_dir, os.path.abspath(path),
                                   lambda p: say("install", p), cancel)
    kind = ma.package_kind(path)
    if kind is None:
        raise ma.AssetError("bad_package", "%s is neither a zip nor an installer (.msi)"
                            % os.path.basename(path))
    ma.clean_cache(config_dir, "icons-work")
    work = ma.temp_dir(config_dir, "icons-work")
    msi_tmp = None
    try:
        msi_path, lo, span = path, 0, 100
        if kind == "zip":
            msi_tmp = ma.temp_path(config_dir, "icons-work", ".msi")
            ma.msi_from_zip(path, msi_tmp, progress=lambda d, t: say("extract", 50 * d / t if t else 0),
                            cancel=cancel)
            msi_path, lo, span = msi_tmp, 50, 50
        say("extract", lo)
        images = os.path.join(work, "images")
        os.makedirs(images)
        ma.extract_device_pictures(msi_path, images, progress=lambda f: say("extract", lo + span * f),
                                   cancel=cancel, choose=_choose)
        ma._remove(msi_tmp)
        msi_tmp = None
        say("install", 0)
        return install_from_folder(images, lib_dir, os.path.abspath(path),
                                   lambda p: say("install", p), cancel)
    finally:
        ma._remove(msi_tmp)
        ma._remove(work)
