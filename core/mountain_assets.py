"""Mountain's downloadable assets: the device photos inside Base Camp's
installer, and Mountain's icon pack for the DisplayPad.

Both are Mountain's files. As with core/device_images.py, BaseCamp never
ships them: they are fetched from mountain.gg, or read from a file the user
picked, on the user's machine and on the user's request only, and just the
parts that are used are kept, in the user's configuration directory.

Device photos
    Mountain publishes Base Camp as a zip of its installer
    (BaseCamp_V1.9.10.msi) and the release notes. import_device_package()
    takes that zip or the bare .msi, looks up the pictures
    device_images.MAPPING asks for in the installer's database
    (core/msi.py), takes those few files and nothing else out of its
    cabinet (core/cab.py), and hands them to device_images.import_images(),
    so the result is the same as an import from an installed Base Camp.

Icon pack
    Mountain-Icon-Pack.zip, 57 JPGs under Mountain-Icon-Pack/.
    install_icon_pack() puts them, unchanged, into a subfolder of the
    DisplayPad library ("Mountain Icon Pack"), where both key pickers offer
    them (core/devices/displaypad/library.py).

Downloads (Downloader)
    https only (a redirect too), a generic User-Agent, a timeout and a size
    cap, streamed into a temporary file in CONFIG_DIR/cache that is deleted
    afterwards, whatever happens. Nothing here touches the network unless a
    command asks for it.

Services
    device_images   download {wait}, import_file {path, wait}, job, cancel
                    (on core/device_images.py's service, through Jobs)
    icon_packs      status, install {pack, wait}, remove {pack}, job, cancel

    <service>.progress {stage, pct}   stage "download" | "extract" | "import"
                                      (icon_packs: "download" | "install");
                                      pct 0-100, or None while a download's
                                      size is unknown; a download also
                                      carries done and total (bytes)
    <service>.changed {...status}     the job is done
    <service>.error {code, message}   it failed

Error codes: network, not_found, bad_package, no_images, too_large,
no_space, busy, cancelled, failed.
"""
import contextlib
import http.client
import io
import json
import logging
import os
import re
import shutil
import socket
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

from core.cab import Cabinet, CabError
from core.devices.displaypad import library as dplib
from core.msi import MsiPackage, MsiError, SIGNATURE as CFB_SIGNATURE

log = logging.getLogger("basecamp.core.mountain_assets")

BASECAMP_URL = "https://mountain.gg/assets/Base-Camp-Release-Notes_1.9.10-public.zip"
ICON_PACK_URL = "https://mountain.gg/assets/Mountain-Icon-Pack.zip"

USER_AGENT = "BaseCampLinux"
TIMEOUT = 30                                # seconds without an answer
DOWNLOAD_CAP = 400 * 1024 * 1024            # the Base Camp zip is 261 MB
ICON_PACK_CAP = 64 * 1024 * 1024            # the icon pack is 0.8 MB
CHUNK = 256 * 1024

#: The installer inside a zip: Base Camp 1.9.10's is 264 MB.
MAX_MSI = 1024 * 1024 * 1024
#: One device photo out of the installer (the largest is about 3 MB).
MAX_PICTURE_FILE = 64 * 1024 * 1024
#: Pixels of any picture read from a package (the largest photo is 2900 px wide).
MAX_PIXELS = 40 * 1000 * 1000
#: An icon pack: pictures, bytes per picture, bytes in all.
MAX_PACK_FILES = 2000
MAX_PACK_MEMBER = 8 * 1024 * 1024
MAX_PACK_TOTAL = 200 * 1024 * 1024
#: Kept free on the disk beyond what a step writes.
SPACE_MARGIN = 32 * 1024 * 1024

CACHE_DIR_NAME = "cache"
TEMP_PREFIX = "mountain-"

#: Packs that can be downloaded: id -> what they are.
PACKS = {
    "mountain": {"name": "Mountain Icon Pack", "url": ICON_PACK_URL,
                 "zip_folder": "Mountain-Icon-Pack"},
}


class AssetError(Exception):
    """A download or an import failed. `code` says why (see the module
    doc), so an interface can word it; the message is plain English."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ── Downloading ─────────────────────────────────────────────────────────────

class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to where the first request may go."""

    def __init__(self, downloader):
        super().__init__()
        self._dl = downloader

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._dl.check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Downloader:
    """Fetches one URL into one file.

    opener            (url, timeout=) -> a response (a context manager with
                      .headers and .read(n)); a stand-in for tests and the
                      demo. Without one, urllib with the User-Agent below.
    allow_http_hosts  hosts that may be reached over plain http: a test's
                      local server, nothing else.
    """

    def __init__(self, opener=None, timeout=TIMEOUT, user_agent=USER_AGENT,
                 allow_http_hosts=()):
        self._opener = opener
        self.timeout = timeout
        self.user_agent = user_agent
        self.allow_http_hosts = frozenset(allow_http_hosts)

    def check_url(self, url):
        try:
            p = urllib.parse.urlsplit(url)
            host = p.hostname
        except ValueError:
            host = None
            p = None
        if p is not None and host:
            if p.scheme == "https":
                return
            if p.scheme == "http" and host in self.allow_http_hosts:
                return
        raise AssetError("network", "only https downloads are allowed: %s" % url)

    def _open(self, url):
        if self._opener is not None:
            return self._opener(url, timeout=self.timeout)
        opener = urllib.request.build_opener(_HttpsRedirects(self))
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        return opener.open(req, timeout=self.timeout)

    def fetch(self, url, dest, max_bytes=DOWNLOAD_CAP, progress=None, cancel=None):
        """Download `url` into the file `dest`. Returns its size.

        `progress(done, total)` after each chunk (total 0 when the server
        does not say); `cancel()` returning true stops it. Raises
        AssetError; `dest` is then removed.
        """
        self.check_url(url)
        try:
            return self._fetch(url, dest, max_bytes, progress, cancel)
        except BaseException:
            _remove(dest)
            raise

    def _fetch(self, url, dest, max_bytes, progress, cancel):
        try:
            resp = self._open(url)
        except AssetError:
            raise
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise AssetError("not_found", "%s is not there any more (HTTP %d)"
                                 % (url, e.code)) from None
            raise AssetError("network", "%s: HTTP %d" % (url, e.code)) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException,
                ValueError) as e:
            raise AssetError("network", "%s: %s" % (url, _reason(e))) from None
        with resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", None) or 200
            if status in (404, 410):
                raise AssetError("not_found", "%s is not there any more (HTTP %d)"
                                 % (url, status))
            if not 200 <= status < 300:
                raise AssetError("network", "%s: HTTP %d" % (url, status))
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            if total > max_bytes:
                raise AssetError("too_large", "%s is %d bytes, more than the %d allowed"
                                 % (url, total, max_bytes))
            if total:
                need_space(os.path.dirname(dest), total)
            done = 0
            with open(dest, "wb") as out:
                while True:
                    if cancel is not None and cancel():
                        raise AssetError("cancelled", "the download was cancelled")
                    try:
                        chunk = resp.read(CHUNK)
                    except (OSError, http.client.HTTPException, ValueError) as e:
                        raise AssetError("network", "%s: %s" % (url, _reason(e))) from None
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > max_bytes:
                        raise AssetError("too_large", "%s is more than the %d bytes allowed"
                                         % (url, max_bytes))
                    out.write(chunk)
                    if progress:
                        progress(done, total)
            if total and done != total:
                raise AssetError("network", "the download stopped at %d of %d bytes"
                                 % (done, total))
            return done


def _reason(e):
    if isinstance(e, urllib.error.URLError) and getattr(e, "reason", None) is not None:
        e = e.reason
    if isinstance(e, socket.timeout):
        return "no answer in time"
    return str(e) or type(e).__name__


def need_space(folder, nbytes):
    """AssetError no_space unless `folder`'s disk has room for `nbytes`."""
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < nbytes + SPACE_MARGIN:
        raise AssetError("no_space", "%d MB are needed in %s, %d MB are free"
                         % ((nbytes + SPACE_MARGIN) >> 20, folder, free >> 20))


def _remove(path):
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif path:
            os.remove(path)
    except OSError:
        pass


# ── The cache ───────────────────────────────────────────────────────────────

def cache_dir(config_dir):
    return os.path.join(config_dir, CACHE_DIR_NAME)


def temp_path(config_dir, tag, suffix=""):
    """A new empty file in the cache, named mountain-<tag>-...<suffix>."""
    folder = cache_dir(config_dir)
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="%s%s-" % (TEMP_PREFIX, tag), suffix=suffix,
                                dir=folder)
    os.close(fd)
    return path


def temp_dir(config_dir, tag):
    folder = cache_dir(config_dir)
    os.makedirs(folder, exist_ok=True)
    return tempfile.mkdtemp(prefix="%s%s-" % (TEMP_PREFIX, tag), dir=folder)


def clean_cache(config_dir, tag):
    """Remove what an earlier job of this kind left (a crash, a kill)."""
    folder = cache_dir(config_dir)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for n in names:
        if n.startswith("%s%s-" % (TEMP_PREFIX, tag)):
            _remove(os.path.join(folder, n))


# ── Pictures from packages ──────────────────────────────────────────────────

_pixel_lock = threading.Lock()
_pixel_state = {"users": 0, "saved": None}


@contextlib.contextmanager
def pixel_limit():
    """PIL's decompression-bomb limit (Image.MAX_IMAGE_PIXELS) at
    MAX_PIXELS or lower while pictures from a package are read; the
    application's own limit is back once the last job is done (two jobs
    may overlap, the device photos and an icon pack)."""
    from PIL import Image
    with _pixel_lock:
        if _pixel_state["users"] == 0:
            old = _pixel_state["saved"] = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = MAX_PIXELS if old is None else min(old, MAX_PIXELS)
        _pixel_state["users"] += 1
    try:
        yield
    finally:
        with _pixel_lock:
            _pixel_state["users"] -= 1
            if _pixel_state["users"] == 0:
                Image.MAX_IMAGE_PIXELS = _pixel_state["saved"]


def check_picture(data_or_path):
    """True if it is a picture PIL reads, of at most MAX_PIXELS."""
    from PIL import Image
    try:
        src = io.BytesIO(data_or_path) if isinstance(data_or_path, (bytes, bytearray)) \
            else data_or_path
        with Image.open(src) as im:
            w, h = im.size
            if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
                return False
            im.verify()
        return True
    except Exception:
        return False


def package_kind(path):
    """"msi", "zip" or None, by the file's first bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head == CFB_SIGNATURE:
        return "msi"
    if head[:2] == b"PK" and zipfile.is_zipfile(path):
        return "zip"
    return None


#: What a damaged or odd zip raises while it is read.
_ZIP_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError,
               ValueError, RuntimeError, zlib.error)


def _copy_member(z, info, dest, limit, progress=None, cancel=None):
    """Stream one zip member into `dest`, at most `limit` bytes."""
    if info.flag_bits & 0x1:
        raise AssetError("bad_package", "%s is encrypted" % info.filename)
    if info.file_size > limit:
        raise AssetError("too_large", "%s is too big (%d bytes)" % (info.filename, info.file_size))
    done = 0
    with z.open(info) as src, open(dest, "wb") as out:
        while True:
            if cancel is not None and cancel():
                raise AssetError("cancelled", "cancelled")
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            done += len(chunk)
            if done > limit:
                raise AssetError("too_large", "%s is too big" % info.filename)
            out.write(chunk)
            if progress:
                progress(done, info.file_size)
    return done


def msi_from_zip(zip_path, dest, progress=None, cancel=None):
    """Write the Base Camp installer inside `zip_path` to `dest`."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            cands = [i for i in z.infolist()
                     if not i.is_dir() and i.filename.lower().endswith(".msi")]
            if not cands:
                raise AssetError("bad_package", "the zip holds no installer (.msi)")
            cands.sort(key=lambda i: (not os.path.basename(i.filename).lower()
                                      .startswith("basecamp"), -i.file_size))
            info = cands[0]
            need_space(os.path.dirname(dest), info.file_size)
            _copy_member(z, info, dest, MAX_MSI, progress, cancel)
            return os.path.basename(info.filename.replace("\\", "/"))
    except AssetError:
        raise
    except _ZIP_ERRORS as e:
        raise AssetError("bad_package", "the zip cannot be read: %s" % e) from None


def _image_index(files):
    """{path below wwwroot/images, lower case: MsiFile}, a file under
    resources/bin/wwwroot/images winning over any other wwwroot/images."""
    index = {}
    for f in files.values():
        low = f.path.lower()
        for rank, marker in enumerate(("/resources/bin/wwwroot/images/", "/wwwroot/images/")):
            i = ("/" + low).rfind(marker)
            if i < 0:
                continue
            rel = ("/" + low)[i + len(marker):]
            if rel and (rel not in index or rank < index[rel][0]):
                index[rel] = (rank, f)
            break
    return {k: v[1] for k, v in index.items()}


def _choose_device_pictures(index):
    from core.device_images import MAPPING
    chosen = {}                 # candidate -> MsiFile
    for views in MAPPING.values():
        for cands in views.values():
            for c in cands:
                hit = index.get(c.lower())
                if hit is not None:
                    chosen[c] = hit
                    break
    return chosen


def extract_device_pictures(msi_path, images_dir, progress=None, cancel=None,
                            choose=None):
    """Take the device pictures out of a Base Camp installer.

    For each view of device_images.MAPPING the first candidate the
    installer has, as import_images() picks it from a folder, is written to
    images_dir/<candidate>. Returns {candidate: bytes}. Raises AssetError.

    `choose(index)` may name other files: {path below wwwroot/images:
    MsiFile} from the installer's {lower-case path: MsiFile} (the icon
    categories, core/basecamp_icons.py).
    """
    try:
        with MsiPackage.open(msi_path) as pkg:
            index = _image_index(pkg.files())
            chosen = (choose or _choose_device_pictures)(index)
            if not chosen:
                raise AssetError("no_images", "the installer has none of the pictures asked for")
            remaining = {f.key: c for c, f in chosen.items()}
            cabinets = pkg.cabinets()
            if not cabinets:
                raise AssetError("bad_package", "the installer has no cabinet inside")
            written = {}
            for n, cab_name in enumerate(cabinets):
                if not remaining:
                    break
                with pkg.open_stream(cab_name) as stream:
                    cab = Cabinet(stream)
                    here = {}
                    for key, c in list(remaining.items()):
                        cf = cab.find(key)
                        if cf is None:
                            continue
                        if cf.size > MAX_PICTURE_FILE:
                            raise AssetError("too_large", "%s is too big" % c)
                        dest = os.path.join(images_dir, *c.split("/"))
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        here[key] = dest
                    if not here:
                        continue

                    def step(done, total, n=n):
                        if progress and total:
                            progress((n + done / total) / len(cabinets))
                    got = cab.extract(here, progress=step, cancel=cancel)
                    for key in here:
                        written[remaining.pop(key)] = got[key]
            if remaining:
                raise AssetError("bad_package", "the installer's cabinet lacks %s"
                                 % ", ".join(sorted(remaining.values())))
            return written
    except AssetError:
        raise
    except CabError as e:
        if str(e) == "cancelled":
            raise AssetError("cancelled", "cancelled") from None
        raise AssetError("bad_package", "the installer's cabinet: %s" % e) from None
    except MsiError as e:
        raise AssetError("bad_package", "the installer: %s" % e) from None
    except (EOFError, ValueError, KeyError, IndexError, struct.error) as e:
        raise AssetError("bad_package", "the installer: %s: %s" % (type(e).__name__, e)) from None


def import_device_package(path, config_dir, progress=None, cancel=None, label=None):
    """Import the device photos from a Base Camp zip or installer, or from
    an installation folder as device_images.import_images() does.

    `progress(stage, pct)` with stage "extract" or "import"; `cancel()`
    returning true stops it. The installer taken out of a zip and the
    pictures taken out of the installer go to CONFIG_DIR/cache and are
    removed again. `label` is what the manifest names as the source
    (default: the file). Returns the manifest. Raises AssetError.
    """
    from core import device_images as di

    def say(stage, pct):
        if progress:
            progress(stage, max(0, min(100, int(pct))))

    if not path or not os.path.exists(path):
        raise AssetError("not_found", "no such file: %s" % path)
    if os.path.isdir(path):
        say("import", 0)
        with pixel_limit():
            try:
                manifest = di.import_images(path, config_dir,
                                            progress=lambda d, t: say("import", 100 * d / t))
            except di.ImageImportError as e:
                raise AssetError(e.code, str(e)) from None
        say("import", 100)
        return manifest
    kind = package_kind(path)
    if kind is None:
        raise AssetError("bad_package", "%s is neither a zip nor an installer (.msi)"
                         % os.path.basename(path))
    label = label or os.path.abspath(path)
    clean_cache(config_dir, "work")
    msi_tmp = None
    work = temp_dir(config_dir, "work")
    try:
        msi_path = path
        # Unzipping the 264 MB installer takes about twice as long as
        # reading the pictures out of it (the first 27 MB of four folders).
        cab_from, cab_span = 0, 100
        if kind == "zip":
            msi_tmp = temp_path(config_dir, "work", ".msi")
            name = msi_from_zip(path, msi_tmp,
                                progress=lambda d, t: say("extract", 60 * d / t if t else 0),
                                cancel=cancel)
            msi_path = msi_tmp
            label = "%s (%s)" % (label, name)
            cab_from, cab_span = 60, 40
        say("extract", cab_from)
        images = os.path.join(work, "images")
        os.makedirs(images)
        extract_device_pictures(msi_path, images,
                                progress=lambda f: say("extract", cab_from + cab_span * f),
                                cancel=cancel)
        _remove(msi_tmp)
        msi_tmp = None
        say("import", 0)
        with pixel_limit():
            for root, _dirs, names in os.walk(images):
                for n in names:
                    if not check_picture(os.path.join(root, n)):
                        raise AssetError("bad_package", "%s is not a picture" % n)
            try:
                manifest = di.import_images(
                    images, config_dir, label=label,
                    progress=lambda d, t: say("import", 100 * d / t))
            except di.ImageImportError as e:
                raise AssetError(e.code, str(e)) from None
        say("import", 100)
        return manifest
    finally:
        _remove(msi_tmp)
        _remove(work)


def download_device_images(config_dir, downloader, progress=None, cancel=None,
                           url=BASECAMP_URL):
    """Download Base Camp (the zip on mountain.gg), import its device photos
    and delete the download. `progress(stage, pct, **extra)`: "download"
    with done and total bytes, then "extract" and "import"."""
    clean_cache(config_dir, "download")
    zip_path = temp_path(config_dir, "download", ".zip")
    try:
        def got(done, total):
            if progress:
                progress("download", int(done * 100 / total) if total else None,
                         done=done, total=total)
        downloader.fetch(url, zip_path, progress=got, cancel=cancel)
        return import_device_package(zip_path, config_dir, progress=progress,
                                     cancel=cancel, label=url)
    finally:
        _remove(zip_path)


# ── Icon packs ──────────────────────────────────────────────────────────────

_UNSAFE = re.compile(r"[^\w .,+()&'-]+", re.UNICODE)


def safe_name(name, fallback):
    """`name` as a file or folder name: no separators, no control
    characters, no leading dot, at most 80 characters."""
    name = _UNSAFE.sub("_", name or "").strip(" ._")[:80].strip(" ._")
    return name or fallback


def _pack_pictures(z):
    """[(ZipInfo, file name)] of the pictures in an icon pack zip.

    Only a member's base name is ever used (zip-slip): folders, absolute
    paths and ".." go nowhere. macOS's __MACOSX shadows and dot files are
    left out, and so is anything that is not a picture by its name.
    """
    out, seen = [], set()
    for info in z.infolist():
        if info.is_dir():
            continue
        parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
        if not parts or "__MACOSX" in parts:
            continue
        base = parts[-1]
        if base.startswith(".") or not base.lower().endswith(dplib.IMAGE_EXTS):
            continue
        stem, ext = os.path.splitext(base)
        name = safe_name(stem, "icon") + ext.lower()
        n = 2
        while name.lower() in seen:
            name = "%s-%d%s" % (safe_name(stem, "icon"), n, ext.lower())
            n += 1
        seen.add(name.lower())
        out.append((info, name))
    return out


def _pack_identity(z, zip_path, pack):
    """(id, folder name) of the pack in zip `z`."""
    if pack in PACKS:
        return pack, PACKS[pack]["name"]
    tops = {i.filename.replace("\\", "/").split("/")[0] for i in z.infolist()}
    for pid, meta in PACKS.items():
        if meta["zip_folder"] in tops:
            return pid, meta["name"]
    stem = os.path.splitext(os.path.basename(zip_path))[0]
    tops.discard("__MACOSX")
    if len(tops) == 1:
        only = next(iter(tops))
        if any(i.filename.replace("\\", "/").startswith(only + "/") for i in z.infolist()):
            stem = only
    name = safe_name(stem.replace("-", " ").replace("_", " "), "Icon Pack")
    return name, name


def install_icon_pack(zip_path, lib_dir, pack=None, source=None, progress=None,
                      cancel=None):
    """Put the pictures of an icon pack zip, as they are, into a subfolder of
    the DisplayPad library. Replaces an earlier install of the same pack.

    `pack` is an id of PACKS, or None to tell by the zip (Mountain's own
    pack is recognised by its Mountain-Icon-Pack/ folder; any other is
    named after its folder or file). `progress(pct)`. Returns the pack's
    status entry. Raises AssetError.
    """
    if not zipfile.is_zipfile(zip_path):
        raise AssetError("bad_package", "%s is not a zip" % os.path.basename(zip_path))
    staging = None
    try:
        with zipfile.ZipFile(zip_path) as z, pixel_limit():
            pics = _pack_pictures(z)
            if not pics:
                raise AssetError("no_images", "the zip holds no pictures")
            if len(pics) > MAX_PACK_FILES:
                raise AssetError("too_large", "the zip holds %d pictures" % len(pics))
            if sum(i.file_size for i, _n in pics) > MAX_PACK_TOTAL:
                raise AssetError("too_large", "the pictures are too big")
            pid, folder_name = _pack_identity(z, zip_path, pack)
            os.makedirs(lib_dir, exist_ok=True)
            need_space(lib_dir, sum(i.file_size for i, _n in pics))
            staging = tempfile.mkdtemp(prefix=".installing-", dir=lib_dir)
            names = []
            for k, (info, name) in enumerate(pics):
                if cancel is not None and cancel():
                    raise AssetError("cancelled", "cancelled")
                if info.flag_bits & 0x1 or info.file_size > MAX_PACK_MEMBER:
                    continue
                with z.open(info) as src:
                    data = src.read(MAX_PACK_MEMBER + 1)
                if len(data) > MAX_PACK_MEMBER or not check_picture(data):
                    continue            # a broken picture does not spoil the pack
                with open(os.path.join(staging, name), "wb") as out:
                    out.write(data)
                names.append(name)
                if progress:
                    progress(int((k + 1) * 100 / len(pics)))
            if not names:
                raise AssetError("no_images", "none of the zip's pictures can be read")
            manifest = {"id": pid, "name": folder_name, "count": len(names),
                        "source": source or os.path.abspath(zip_path),
                        "installed_at": int(time.time()), "files": sorted(names)}
            with open(os.path.join(staging, dplib.PACK_MANIFEST), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=1)
        dest = os.path.join(lib_dir, folder_name)
        old = None
        if os.path.exists(dest):
            old = tempfile.mkdtemp(prefix=".removing-", dir=lib_dir)
            os.rmdir(old)
            os.replace(dest, old)
        os.replace(staging, dest)
        staging = None
        _remove(old)
        return _pack_entry(lib_dir, folder_name)
    except AssetError:
        raise
    except _ZIP_ERRORS as e:
        raise AssetError("bad_package", "the zip cannot be read: %s" % e) from None
    finally:
        _remove(staging)


def _pack_entry(lib_dir, folder_name):
    folder = os.path.join(lib_dir, folder_name)
    m = dplib.read_manifest(folder)
    return {"id": m.get("id") or folder_name, "name": folder_name, "folder": folder,
            "count": len(dplib.pictures(folder)), "source": m.get("source"),
            "installed_at": m.get("installed_at"), "managed": bool(m)}


def icon_packs_status(lib_dir):
    """{"folder": the library, "packs": [{id, name, folder, count, source,
    installed_at, managed}], "count": pictures in packs,
    "available": [{id, name, url, installed}]}.

    Every subfolder of the library counts as a pack (the pickers show them
    all); `managed` says it was installed here and can be removed."""
    packs = [_pack_entry(lib_dir, n) for n in dplib.pack_folders(lib_dir)]
    ids = {p["id"] for p in packs if p["managed"]}
    groups = {dplib.read_manifest(p["folder"]).get("group") for p in packs}
    available = [{"id": pid, "name": m["name"], "url": m["url"],
                  "installed": pid in ids} for pid, m in PACKS.items()]
    # Base Camp's own icon categories (core/basecamp_icons.py): one id, many folders.
    available.append({"id": "basecamp", "name": "Base Camp icons", "url": BASECAMP_URL,
                      "installed": "basecamp" in groups})
    return {"folder": lib_dir, "packs": packs,
            "count": sum(p["count"] for p in packs), "available": available}


def remove_icon_pack(lib_dir, pack):
    """Delete an installed pack, by id or folder name. True if it was there.
    Only folders installed by install_icon_pack (with a manifest) go."""
    if not isinstance(pack, str) or not pack:
        return False
    removed = False
    for n in dplib.pack_folders(lib_dir):
        folder = os.path.join(lib_dir, n)
        m = dplib.read_manifest(folder)
        if m and (m.get("id") == pack or n == pack or m.get("group") == pack):
            shutil.rmtree(folder)
            removed = True
    return removed


# ── Jobs and services ───────────────────────────────────────────────────────

class Jobs:
    """One job at a time for a service, on a worker thread of its own.

    run(kind, work) calls `work(progress, cancelled)`. `progress(stage,
    pct, **extra)` becomes the event <service>.progress {stage, pct, ...}
    (only when stage or pct changed); success publishes <service>.changed
    with `status()`, an AssetError <service>.error {code, message}. With
    wait=True the job runs on the caller's thread and the command returns
    the status or raises CommandError("<code>: <message>").
    """

    def __init__(self, service, status):
        self._svc = service
        self._status = status
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.kind = None
        self.stage = None
        self.pct = None
        self._done = 0

    @property
    def running(self):
        return self.kind is not None

    def state(self):
        return {"running": self.running, "kind": self.kind, "stage": self.stage,
                "pct": self.pct}

    def cancel(self):
        if self.running:
            self._cancel.set()
            return True
        return False

    def run(self, kind, work, wait=False):
        from core.commands import CommandError
        with self._lock:
            if self.kind is not None:
                raise CommandError("busy: a %s is running already" % self.kind)
            self.kind, self.stage, self.pct, self._done = kind, None, None, 0
            self._cancel.clear()
        if wait:
            err = self._body(work)
            if err is not None:
                raise CommandError("%s: %s" % (err.code, err))
            return self._status()
        threading.Thread(target=self._body, args=(work,), daemon=True,
                         name="%s-%s" % (self._svc.name, kind)).start()
        return {"started": True, "kind": kind}

    def _progress(self, stage, pct, **extra):
        if pct is not None:
            pct = max(0, min(100, int(pct)))
        done = extra.get("done") or 0
        if (stage, pct) == (self.stage, self.pct):
            # Nothing new; a download of unknown size says so once a MiB.
            if pct is not None or done - self._done < 1024 * 1024:
                return
        self.stage, self.pct, self._done = stage, pct, done
        self._svc.publish("progress", stage=stage, pct=pct, **extra)

    def _body(self, work):
        err = None
        try:
            work(self._progress, self._cancel.is_set)
        except AssetError as e:
            err = e
        except Exception as e:
            log.exception("%s: %s failed", self._svc.name, self.kind)
            err = AssetError("failed", "%s: %s" % (type(e).__name__, e))
        finally:
            with self._lock:
                self.kind = self.stage = self.pct = None
        if err is None:
            self._svc.publish("changed", **self._status())
        else:
            self._svc.publish("error", code=err.code, message=str(err))
        return err


def make_icon_pack_service(app, library_dir=None, config_dir=None, downloader=None):
    """The service "icon_packs": status, install {pack, wait}, remove {pack},
    job, cancel; events icon_packs.progress {stage, pct}, icon_packs.changed
    {...status}, icon_packs.error {code, message}."""
    from core.service import Service
    from core.commands import CommandError

    class IconPackService(Service):
        name = "icon_packs"

        def __init__(self, app):
            super().__init__(app)
            self.downloader = downloader or Downloader()
            self.jobs = Jobs(self, self.status)

        def _lib(self):
            return library_dir or dplib.default_dir()

        def _cfg(self):
            if config_dir:
                return config_dir
            from shared.config import CONFIG_DIR
            return CONFIG_DIR

        def commands(self):
            return {"status": self.status, "install": self.install,
                    "remove": self.remove, "job": self.jobs.state,
                    "cancel": lambda: {"cancelled": self.jobs.cancel()}}

        def status(self):
            st = icon_packs_status(self._lib())
            st["job"] = self.jobs.state()
            return st

        def _install_basecamp(self, path, wait):
            """Base Camp's icon categories: from `path` (an installation
            folder, .msi or zip), else an installation found, else the
            download from mountain.gg."""
            from core import basecamp_icons as bi
            from core.device_images import find_installation
            if path is not None and not os.path.exists(path):
                raise CommandError("not_found: no such file: %s" % (path,))

            def work(progress, cancelled):
                cfg = self._cfg()
                src = path or find_installation()
                tmp = None
                try:
                    if not src:
                        clean_cache(cfg, "icons")
                        tmp = src = temp_path(cfg, "icons", ".zip")

                        def got(done, total):
                            progress("download", int(done * 100 / total) if total else None,
                                     done=done, total=total)
                        self.downloader.fetch(BASECAMP_URL, tmp, progress=got, cancel=cancelled)
                    bi.install(src, self._lib(), cfg, progress=progress, cancel=cancelled)
                finally:
                    if tmp:
                        _remove(tmp)
                        clean_cache(cfg, "icons")
            return self.jobs.run("install", work, wait=bool(wait))

        def install(self, pack="mountain", wait=False, path=None):
            if not isinstance(pack, str) or not pack:
                raise CommandError("install: a pack id or a zip file is needed")
            if pack == "basecamp":
                return self._install_basecamp(path, wait)
            if pack in PACKS:
                meta = PACKS[pack]

                def work(progress, cancelled):
                    cfg = self._cfg()
                    clean_cache(cfg, "icons")
                    tmp = temp_path(cfg, "icons", ".zip")
                    try:
                        def got(done, total):
                            progress("download", int(done * 100 / total) if total else None,
                                     done=done, total=total)
                        self.downloader.fetch(meta["url"], tmp, max_bytes=ICON_PACK_CAP,
                                              progress=got, cancel=cancelled)
                        progress("install", 0)
                        install_icon_pack(tmp, self._lib(), pack=pack, source=meta["url"],
                                          progress=lambda p: progress("install", p),
                                          cancel=cancelled)
                    finally:
                        _remove(tmp)
                        clean_cache(cfg, "icons")
            else:
                path = pack
                if not os.path.isfile(path):
                    raise CommandError("not_found: no pack %r and no such file (known packs: %s)"
                                       % (pack, ", ".join(sorted(PACKS))))

                def work(progress, cancelled):
                    progress("install", 0)
                    install_icon_pack(path, self._lib(), source=os.path.abspath(path),
                                      progress=lambda p: progress("install", p),
                                      cancel=cancelled)
            return self.jobs.run("install", work, wait=bool(wait))

        def remove(self, pack):
            if self.jobs.running:
                raise CommandError("busy: a %s is running" % self.jobs.kind)
            removed = remove_icon_pack(self._lib(), pack)
            st = self.status()
            if removed:
                self.publish("changed", **st)
            return {"removed": removed, **st}

    return IconPackService(app)
