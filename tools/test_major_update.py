#!/usr/bin/env python3
"""The updater and a new major version (3.x -> 4.0).

Checked:
  - shared/update_select.py: a source patch only within the same major
    version, the AppImage for a newer one (also when that release carries a
    `source-` tarball), the debian/fedora choice, the backup path;
  - gui.App._check_for_update against a release list served from a file:
    a 3.2.x release still comes as the patch, 4.0.0 as the AppImage with
    `_update_major` set, a pre-release is not offered;
  - gui.App.run_update_download for 4.0.0: the settings are saved to the
    backup ZIP first, then the AppImage is replaced in place (executable);
    a failing backup stops the update before anything is replaced.

Run (needs a display, as gui imports Tk):  xvfb-run -a python3 tools/test_major_update.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="basecamp-major-update-test-")
os.environ["HOME"] = _TMP
os.environ.setdefault("XDG_RUNTIME_DIR", _TMP)

import shared.update_select as U   # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + ("" if ok else "   " + str(detail)))
    if not ok:
        failures.append(name)


def asset(name):
    return {"name": name, "browser_download_url": "https://x/" + name}


# ── update_select ─────────────────────────────────────────────────────────────
check("versions", U.version_tuple("v3.2.5") == (3, 2, 5) and U.version_tuple("4.0.0-beta.1") == (4, 0, 0))
check("major upgrade", U.is_major_upgrade("3.2.5", "4.0.0") and not U.is_major_upgrade("3.2.5", "3.3.0"))
patch = [asset("source-3.2.6.tar.gz"), asset("source-3.2.6.tar.gz.sha256")]
p = U.pick_assets(patch, "appimage", "/x/BaseCamp-Linux-x86_64-debian.AppImage", "3.2.5", "3.2.6")
check("same major: the patch", p["source_url"].endswith("source-3.2.6.tar.gz") and p["source_sha_url"])
four = [asset("source-4.0.0.tar.gz"), asset("source-4.0.0.tar.gz.sha256"),
        asset("BaseCamp-Linux-4.0.0-x86_64-debian.AppImage")]
p = U.pick_assets(four, "appimage", "/x/BaseCamp-Linux-x86_64-debian.AppImage", "3.2.5", "4.0.0")
check("new major: never the patch, the AppImage",
      p["source_url"] == "" and p["url"].endswith("debian.AppImage"), p)
p = U.pick_assets(four, "appimage", "/x/BaseCamp-Linux-x86_64-fedora.AppImage", "3.2.5", "4.0.0")
check("fedora install, only a debian AppImage: that one", p["url"].endswith("debian.AppImage"), p)
check("no AppImage install: nothing", U.pick_assets(four, "arch", "", "3.2.5", "4.0.0")
      == {"source_url": "", "source_sha_url": "", "url": ""})
p = U.pick_assets([asset("source-3.2.6.tar.gz")], "appimage", "/x/a.AppImage", "3.2.5", "3.2.6")
check("a patch without its .sha256 is not taken", p["source_url"] == "")
check("backup path beside the settings",
      U.backup_path("/h/.config/mountain-time-sync", "3.2.5") == "/h/.config/mountain-time-sync-backup-3.2.5.zip")

# ── The real 3.x code: check and download ────────────────────────────────────
import gui                          # noqa: E402
from shared.config import CONFIG_DIR  # noqa: E402

os.makedirs(CONFIG_DIR, exist_ok=True)
with open(os.path.join(CONFIG_DIR, "buttons.json"), "w") as f:
    json.dump([{"type": "url", "action": "https://example.org"}], f)

work = os.path.join(_TMP, "work")
os.makedirs(work)
old_img = os.path.join(work, "BaseCamp-Linux-x86_64-debian.AppImage")
with open(old_img, "wb") as f:
    f.write(b"old 3.x appimage")
new_img = os.path.join(work, "served-4.0.0.AppImage")
with open(new_img, "wb") as f:
    f.write(b"\x7fELF new 4.0 appimage " * 1000)
os.environ["APPIMAGE"] = old_img

RELEASES = []
_real_urlopen = urllib.request.urlopen


def fake_urlopen(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else req
    if url.startswith("https://api.github.com/"):
        path = os.path.join(work, "releases.json")
        with open(path, "w") as f:
            json.dump(RELEASES, f)
        return _real_urlopen("file://" + path)
    if url.startswith("https://x/"):
        name = url[len("https://x/"):]
        return _real_urlopen("file://" + (new_img if name.endswith(".AppImage") else os.path.join(work, name)))
    raise OSError("unexpected url " + url)


urllib.request.urlopen = fake_urlopen


class Stub:
    """What _check_for_update and run_update_download use of the App."""
    def __init__(self):
        self.popups = 0

    def T(self, key, **kw):
        return key + (repr(sorted(kw.items())) if kw else "")

    def after(self, _ms, fn, *a):
        fn(*a)

    def _detect_install_type(self):
        return "appimage"

    def _show_update_popup(self):
        self.popups += 1


def check_for(releases):
    RELEASES[:] = releases
    s = Stub()
    gui.App._check_for_update(s)
    end = time.time() + 5
    while time.time() < end and not getattr(s, "_update_url", ""):
        time.sleep(0.05)
    return s


def rel(tag, names, pre=False):
    return {"tag_name": tag, "prerelease": pre, "draft": False,
            "assets": [asset(n) for n in names]}


s = check_for([rel("v3.2.6", ["source-3.2.6.tar.gz", "source-3.2.6.tar.gz.sha256",
                              "BaseCamp-Linux-x86_64-debian.AppImage"])])
check("3.x check: a 3.2.x release comes as the patch",
      s._update_kind == "source" and not s._update_major and s.popups == 1, vars(s))
s = check_for([rel("v4.0.0-beta.2", ["BaseCamp-Linux-4.0.0-beta.2-x86_64-debian.AppImage"], pre=True),
               rel("v4.0.0", ["source-4.0.0.tar.gz", "source-4.0.0.tar.gz.sha256",
                              "BaseCamp-Linux-4.0.0-x86_64-debian.AppImage"]),
               rel("v3.2.5", ["source-3.2.5.tar.gz", "source-3.2.5.tar.gz.sha256"])])
check("3.x check: 4.0.0 comes as the AppImage, marked as a major update",
      s._update_kind == "appimage" and s._update_major is True
      and s._update_url.endswith("4.0.0-x86_64-debian.AppImage") and s._update_version == "4.0.0", vars(s))
check("3.x check: the message names the major update", "update_major_hint" in s._update_message)
s_pre = check_for([rel("v4.0.0-beta.2", ["x.AppImage"], pre=True)])
check("3.x check: a pre-release is not offered", not getattr(s_pre, "_update_url", ""))


def download(stub):
    done = threading.Event()
    result = {}
    gui.App.run_update_download(
        stub, on_progress=lambda pct: None, on_installing=lambda: None,
        on_done=lambda: (result.setdefault("done", True), done.set()),
        on_error=lambda err: (result.setdefault("error", err), done.set()))
    done.wait(20)
    return result


r = download(s)
backup = U.backup_path(CONFIG_DIR, gui.APP_VERSION)
check("download: done", r.get("done") is True, r)
check("download: the settings were saved first",
      os.path.isfile(backup) and "buttons.json" in zipfile.ZipFile(backup).namelist(), backup)
check("download: the AppImage was replaced in place, executable",
      open(old_img, "rb").read() == open(new_img, "rb").read() and os.access(old_img, os.X_OK))
check("download: no .new file left", not os.path.exists(old_img + ".new"))

# A backup that cannot be written stops the update before anything changes.
with open(old_img, "wb") as f:
    f.write(b"old 3.x appimage")
os.remove(backup)
os.makedirs(backup)                 # a folder where the ZIP should go
r = download(s)
check("backup fails: the update stops with the message",
      "update_backup_failed" in str(r.get("error")) and open(old_img, "rb").read() == b"old 3.x appimage", r)

urllib.request.urlopen = _real_urlopen
print()
if failures:
    print("%d FAILED" % len(failures))
    sys.exit(1)
print("all passed")
