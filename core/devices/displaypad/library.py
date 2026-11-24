"""The DisplayPad's picture library: the folder its key picker offers.

The library is CONFIG_DIR/displaypad_library (shared.config
DISPLAYPAD_LIBRARY_DIR). Its own pictures lie in the folder itself: the
102x102 PNGs every picked picture leaves there (named by a hash of their
pixels) and the icons BaseCamp ships, copied in on first use. A picture
pack (the Mountain icon pack, core/mountain_assets.py) is a subfolder of
its own with a PACK_MANIFEST beside its pictures; removing the pack is
removing the folder. Both pickers show the subfolders: CustomTkinter's
LibraryPickerDialog and the web interface's "From library"
(displaypad.library).
"""
import json
import os
import shutil

#: What the pickers show.
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp")

#: In a pack's folder: {"id", "name", "count", "source", "installed_at"}.
PACK_MANIFEST = ".pack.json"

#: Set once the shipped icons were copied in (the 3.x picker's marker).
_BUNDLED_MARKER = ".bundled_v1"


def default_dir():
    from shared.config import DISPLAYPAD_LIBRARY_DIR
    return DISPLAYPAD_LIBRARY_DIR


def pictures(folder):
    """The pictures directly in `folder`, sorted (no dot files)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names
                  if not n.startswith(".") and n.lower().endswith(IMAGE_EXTS)
                  and os.path.isfile(os.path.join(folder, n)))


def pack_folders(lib_dir):
    """The library's subfolders, sorted by name (each one a pack)."""
    try:
        names = os.listdir(lib_dir)
    except OSError:
        return []
    return sorted(n for n in names
                  if not n.startswith(".") and os.path.isdir(os.path.join(lib_dir, n)))


def read_manifest(folder):
    try:
        with open(os.path.join(folder, PACK_MANIFEST), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def entries(lib_dir=None):
    """Every picture of the library: [{path, name, pack}], the library's
    own first (pack None), then each pack's (pack = its folder's name).
    `name` is the file name without its extension."""
    lib_dir = lib_dir or default_dir()
    out = []
    for n in pictures(lib_dir):
        out.append({"path": os.path.join(lib_dir, n),
                    "name": os.path.splitext(n)[0], "pack": None})
    for pack in pack_folders(lib_dir):
        folder = os.path.join(lib_dir, pack)
        for n in pictures(folder):
            out.append({"path": os.path.join(folder, n),
                        "name": os.path.splitext(n)[0], "pack": pack})
    return out


def contains(path, lib_dir=None):
    """True if `path` lies in the library (in a pack, too)."""
    lib_dir = lib_dir or default_dir()
    try:
        real = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(lib_dir))
    except (TypeError, ValueError):
        return False
    return real.startswith(root.rstrip(os.sep) + os.sep)


def ensure_bundled_icons(lib_dir=None, res_dir=None):
    """Copy the icons BaseCamp ships (resources/dp_icons) into the library
    once, as the CustomTkinter picker does when it first opens. A user who
    deleted one later does not get it back."""
    lib_dir = lib_dir or default_dir()
    if res_dir is None:
        from core import runtime
        res_dir = os.path.join(runtime.RES, "resources", "dp_icons")
    if not os.path.isdir(res_dir):
        return False
    marker = os.path.join(lib_dir, _BUNDLED_MARKER)
    if os.path.exists(marker):
        return False
    os.makedirs(lib_dir, exist_ok=True)
    for n in os.listdir(res_dir):
        if n.endswith(".png"):
            dst = os.path.join(lib_dir, n)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(res_dir, n), dst)
    with open(marker, "w") as f:
        f.write("1")
    return True
