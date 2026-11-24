"""Device pictures from the user's own Mountain Base Camp installation.

BaseCamp Linux ships its own drawings of the devices. Whoever has Mountain's
Base Camp installed (on Windows, in a Wine prefix, or as a copy of its
folder) can have the application use Mountain's device photos instead. They
are read from that installation on the user's machine and copied into the
user's configuration directory; they are never part of this project and
never shipped. Removing them brings the drawings back.

Without an installation, the same pictures come out of Base Camp's
installer: core/mountain_assets.py downloads it from mountain.gg when the
user asks (the service's download command), or reads the zip or .msi the
user picked (import_file), and imports through import_images() below.

    python3 import_device_images.py                 # look in the usual places
    python3 import_device_images.py "/path/to/Mountain Base Camp"
    python3 import_device_images.py BaseCamp_V1.9.10.msi   # or the zip
    python3 import_device_images.py --remove

Each device has two views: "home" (the picture on its card on the home
screen) and "page" (the picture on its own screen). Mountain's file names do
not always say what is in them (images/MKD/mkd_pro.png is the DisplayPad,
images/MKD/dkd_pro.png the MacroPad), so the mapping below was made by
looking at the pictures of Base Camp 1.9.9. Each view lists candidates in
order, so a later Base Camp that renames a file still finds the next one.
"""
import json
import os
import shutil
import time

from core.platform import IS_WINDOWS

#: device id -> view -> candidate paths below wwwroot/images
MAPPING = {
    "displaypad": {"home": ["MKD/mkd_pro.png", "DKD/TOP ON STAND_DKD.png"],
                   "page": ["DKD/DKD_Setting.png", "DKD/TOP ON STAND_DKD.png"]},
    "macropad":   {"home": ["MKD/PERS 45 ON STAND_MKD RGB.png", "MKD/dkd_pro.png"],
                   "page": ["MKD/MKD_setting.png"]},
    "makalu67":   {"home": ["makalu67_home.png"],
                   "page": ["makalu67_light.png", "makalu67.png"]},
    "makalu_max": {"home": ["Makalu/makalu-mouse.png"],
                   "page": ["Makalu/makalu_light.png", "Makalu/mouse.png"]},
    "everest_max": {"home": ["everest-keyboard.png", "everest-keyboard-bk.png"],
                    "page": ["everest-keyboard-bk.png", "everest-keyboard.png"]},
    "everest60":  {"home": ["Everest60/everest_60_home.png"],
                   "page": ["Everest60/EV60_NoNumpad.png", "Everest60/keyboardv2.png"]},
}

#: Longest side of an imported picture. The originals are up to 2900 px;
#: the interface never shows them larger than about 600 px.
MAX_SIDE = 1400

DIR_NAME = "device-images"
MANIFEST = "manifest.json"
#: In the config folder: the hint on the drawings ("take the photos from Base Camp") is hidden.
HINT_HIDDEN_FILE = "device_photo_hint_hidden"
_IMAGES_REL = os.path.join("resources", "bin", "wwwroot", "images")


class ImageImportError(Exception):
    """The source has no Base Camp pictures, or they could not be read.

    `code` says why (`not_found`, `no_images`, `failed`) so an interface can
    word it in its own language; the message is plain English for the CLI.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ── Finding an installation ─────────────────────────────────────────────────

def default_candidates(env=None, home=None):
    """Folders where a Base Camp installation usually is, in order."""
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    out = []
    if IS_WINDOWS:
        for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
            base = env.get(var)
            if base:
                out.append(os.path.join(base, "Mountain Base Camp"))
    prefixes = []
    if env.get("WINEPREFIX"):
        prefixes.append(env["WINEPREFIX"])
    prefixes.append(os.path.join(home, ".wine"))
    for pre in prefixes:
        for pf in ("Program Files (x86)", "Program Files"):
            out.append(os.path.join(pre, "drive_c", pf, "Mountain Base Camp"))
    seen, uniq = set(), []
    for p in out:
        key = os.path.normcase(os.path.normpath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def images_root(path):
    """The wwwroot/images folder for `path`, or None.

    `path` may be the installation folder, its resources/bin/wwwroot, or the
    images folder itself (a copy taken from another machine).
    """
    if not path:
        return None
    path = os.path.normpath(path)
    for cand in (os.path.join(path, _IMAGES_REL),
                 os.path.join(path, "wwwroot", "images"),
                 os.path.join(path, "images"),
                 path):
        if _looks_like_images(cand):
            return cand
    return None


def _looks_like_images(folder):
    if not os.path.isdir(folder):
        return False
    hits = 0
    for views in MAPPING.values():
        for cands in views.values():
            if any(os.path.isfile(os.path.join(folder, c)) for c in cands):
                hits += 1
    return hits >= 2


def find_installation(env=None, home=None):
    """The images folder of the first installation found, or None."""
    for cand in default_candidates(env, home):
        root = images_root(cand)
        if root:
            return root
    return None


# ── Importing ───────────────────────────────────────────────────────────────

def target_dir(config_dir):
    return os.path.join(config_dir, DIR_NAME)


def import_images(source, config_dir, max_side=MAX_SIDE, label=None, progress=None):
    """Copy the device pictures from `source` into the config directory.

    `source` is anything images_root() accepts, or None to search the usual
    places. `label` is what the manifest names as the source instead of the
    folder (core/mountain_assets.py imports from a temporary folder and
    names the installer). `progress(done, total)` is called after each
    view. Returns the manifest written. Raises ImageImportError.
    """
    from PIL import Image

    root = images_root(source) if source else find_installation()
    if not root:
        if source:
            raise ImageImportError("no_images",
                               "No Mountain Base Camp pictures found in %s" % source)
        raise ImageImportError("not_found",
                           "No Mountain Base Camp installation found. Give the "
                           "path of its folder (or of a copy of it).")
    dest = target_dir(config_dir)
    staging = dest + ".new"
    if os.path.exists(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    devices = {}
    sources = {}        # which Base Camp file each picture came from
    total = sum(len(views) for views in MAPPING.values())
    done = 0
    try:
        for dev, views in MAPPING.items():
            for view, cands in views.items():
                done += 1
                if progress:
                    progress(done - 1, total)
                rel = next((c for c in cands
                            if os.path.isfile(os.path.join(root, c))), None)
                if not rel:
                    continue
                src = os.path.join(root, rel)
                im = Image.open(src)
                im.load()
                im = im.convert("RGBA")
                box = im.getchannel("A").getbbox()
                if box:
                    im = im.crop(box)
                im.thumbnail((max_side, max_side), Image.LANCZOS)
                name = "%s_%s.png" % (dev, view)
                im.save(os.path.join(staging, name), optimize=True)
                devices.setdefault(dev, {})[view] = name
                sources.setdefault(dev, {})[view] = rel.replace(os.sep, "/")
        if not devices:
            raise ImageImportError("no_images",
                               "The folder has none of the expected device pictures.")
        manifest = {"source": label or root, "imported_at": int(time.time()),
                    "devices": devices, "sources": sources}
        with open(os.path.join(staging, MANIFEST), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
    except ImageImportError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise ImageImportError("failed", "%s: %s" % (type(e).__name__, e))
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.replace(staging, dest)
    return manifest


def remove_images(config_dir):
    """Delete the imported pictures. True if there were any."""
    dest = target_dir(config_dir)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
        return True
    return False


def status(config_dir):
    """What is imported: {"imported": bool, "source", "imported_at",
    "devices": {device: {view: absolute path}},
    "sources": {device: {view: Base Camp file it came from}}}.

    `sources` matters to an interface that lays click areas over a photo:
    those are measured on one particular file (say DKD/DKD_Setting.png), and
    a fallback candidate from another Base Camp version has its keys
    elsewhere. Such an interface shows its own drawing when the source is
    not the file its click areas were measured on.
    """
    dest = target_dir(config_dir)
    try:
        with open(os.path.join(dest, MANIFEST), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return {"imported": False, "devices": {}}
    devices = {}
    for dev, views in (manifest.get("devices") or {}).items():
        for view, name in views.items():
            path = os.path.join(dest, os.path.basename(name))
            if os.path.isfile(path):
                devices.setdefault(dev, {})[view] = path
    return {"imported": bool(devices), "source": manifest.get("source"),
            "imported_at": manifest.get("imported_at"), "devices": devices,
            "sources": manifest.get("sources") or {}}


# ── As a core service ───────────────────────────────────────────────────────

def make_service(app, config_dir=None, downloader=None):
    """A Service named "device_images".

    Commands: status, import {path}, remove; download {wait}, import_file
    {path, wait}, job, cancel (core/mountain_assets.py). download and
    import_file run on a worker thread and answer {started: true} at once
    (with wait=true: when done, with the status); they publish
    device_images.progress {stage: "download"|"extract"|"import", pct},
    then device_images.changed {...status} or device_images.error {code,
    message}. A request that cannot start is answered {ok: false, error:
    "<code>: <message>"} (busy, not_found). `downloader` replaces
    mountain_assets.Downloader() (tests, the demo)."""
    from core.service import Service
    from core.commands import CommandError

    class DeviceImagesService(Service):
        name = "device_images"

        def __init__(self, app):
            super().__init__(app)
            from core import mountain_assets as ma
            self._ma = ma
            self.downloader = downloader or ma.Downloader()
            self.jobs = ma.Jobs(self, self.status)

        def _dir(self):
            if config_dir:
                return config_dir
            from shared.config import CONFIG_DIR
            return CONFIG_DIR

        def commands(self):
            return {"status": self.status, "set_hint_hidden": self.set_hint_hidden,
                    "import": self.do_import,
                    "remove": self.remove, "find": self.find, "download": self.download,
                    "import_file": self.import_file, "job": self.jobs.state,
                    "cancel": lambda: {"cancelled": self.jobs.cancel()}}

        def find(self):
            """Where a Base Camp installation is, or None: so an interface can
            offer "import from the installation found" or ask for a folder."""
            root = find_installation()
            return {"found": bool(root), "path": root}

        def _hint_file(self):
            return os.path.join(self._dir(), HINT_HIDDEN_FILE)

        def status(self):
            st = status(self._dir())
            st["hint_hidden"] = os.path.isfile(self._hint_file())
            return st

        def set_hint_hidden(self, hidden=True):
            """Hide (or show again) the hint on the drawings that offers the
            import, for someone who wants the drawings."""
            path = self._hint_file()
            if hidden:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("1\n")
            elif os.path.isfile(path):
                os.remove(path)
            st = self.status()
            self.publish("changed", **st)
            return st

        def do_import(self, path=None):
            if self.jobs.running:
                raise CommandError("busy: a %s is running" % self.jobs.kind)
            try:
                import_images(path, self._dir())
            except ImageImportError as e:
                raise CommandError("%s: %s" % (e.code, e), code=e.code)
            st = self.status()
            self.publish("changed", **st)
            return st

        def download(self, wait=False):
            """Base Camp from mountain.gg: its device photos, then the
            download is deleted."""
            ma = self._ma
            return self.jobs.run("download", lambda progress, cancelled:
                                 ma.download_device_images(self._dir(), self.downloader,
                                                           progress, cancelled),
                                 wait=bool(wait))

        def import_file(self, path, wait=False):
            """The device photos from a Base Camp zip or .msi the user
            picked (or an installation folder)."""
            if not isinstance(path, str) or not path or not os.path.exists(path):
                raise CommandError("not_found: no such file: %s" % (path,))
            ma = self._ma
            return self.jobs.run("import_file", lambda progress, cancelled:
                                 ma.import_device_package(path, self._dir(), progress,
                                                          cancelled),
                                 wait=bool(wait))

        def remove(self):
            if self.jobs.running:
                raise CommandError("busy: a %s is running" % self.jobs.kind)
            removed = remove_images(self._dir())
            st = self.status()
            self.publish("changed", **st)
            return {"removed": removed, **st}

    return DeviceImagesService(app)
