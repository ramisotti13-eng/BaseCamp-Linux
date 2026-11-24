"""Looking for a newer release, downloading it and installing it.

``UpdateService`` (service ``update``) is what gui.py's ``_check_for_update``
and ``run_update_download`` were. It does not show anything and does not
translate: events carry codes and numbers, an interface builds the texts.

    check()                  -> UpdateInfo or None, publishes update.available
    check_async()            the same on a thread; events come on the scheduler
    download_and_install()   update.progress {pct}, update.installing,
                             update.done {kind}, update.error {code, message}
    restart()                re-executes the AppImage

Error codes: no_asset, no_checksum, bad_checksum, failed (message holds the
first 80 characters of the exception).

The network goes through an ``http`` object (``get_json``, ``get_text``,
``open``), so tests supply one and nothing is downloaded or replaced.
"""
import hashlib
import json
import logging
import os
import re
import threading
import urllib.request
from dataclasses import asdict, dataclass

from core.commands import CommandError
from core.platform import install as _install
from core.service import Service

log = logging.getLogger("basecamp.core.updates")

RELEASES_URL = ("https://api.github.com/repos/ramisotti13-eng/"
                "BaseCamp-Linux/releases?per_page=20")


@dataclass
class UpdateInfo:
    version: str            # "3.3.0", no leading v
    install_type: str       # appimage | arch | debian | source
    url: str = ""           # what to download: source tarball, else AppImage
    sha_url: str = ""       # checksum sidecar of the source tarball
    kind: str = "appimage"  # "source" | "appimage"

    def as_dict(self):
        return asdict(self)


_PRE_RANK = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "rc": 3}


def version_tuple(s):
    """A sortable key for "v4.0.0", "4.0.0-beta.2", "3.2.4": the numbers
    (padded to four), then 1 for a release or 0 for a pre-release, then the
    pre-release's kind (dev < alpha < beta < rc) and number. So 4.0.0-beta.1
    ranks below 4.0.0 (read as 4.0.0.1 it ranked above, and a beta would
    never have been offered the release)."""
    main, dash, pre = str(s or "").strip().lstrip("vV").partition("-")
    nums = []
    for p in main.split("."):
        num = "".join(c for c in p if c.isdigit())
        nums.append(int(num) if num else 0)
    nums = (nums + [0, 0, 0, 0])[:max(4, len(nums))]
    if not dash:
        return tuple(nums) + (1, 0, 0)
    m = re.match(r"([A-Za-z]*)[.\-_]?(\d*)", pre)
    word, num = (m.group(1), m.group(2)) if m else ("", "")
    return tuple(nums) + (0, _PRE_RANK.get(word.lower(), 0), int(num) if num else 0)


def is_prerelease(s):
    return "-" in str(s or "")


class UrllibHttp:
    def __init__(self, user_agent):
        self.user_agent = user_agent

    def _request(self, url):
        return urllib.request.Request(url, headers={"User-Agent": self.user_agent})

    def get_json(self, url, timeout=5):
        with urllib.request.urlopen(self._request(url), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_text(self, url, max_bytes=256, timeout=10):
        with urllib.request.urlopen(self._request(url), timeout=timeout) as r:
            return r.read(max_bytes).decode("utf-8", "replace")

    def open(self, url, timeout=15):
        """A context manager with .headers and .read(n)."""
        return urllib.request.urlopen(self._request(url), timeout=timeout)


class UpdateService(Service):
    name = "update"

    def __init__(self, app, version, home=None, http=None):
        super().__init__(app)
        self.version = version
        if home is None:
            from core.platform.user import resolve_real_user
            home = resolve_real_user()[0]
        self.home = home
        self.http = http or UrllibHttp("BaseCamp-Linux/%s" % version)
        self.info = None            # the last UpdateInfo found

    # ── Install type ───────────────────────────────────────────────────────
    def install_type(self):
        return _install.detect_install_type()

    # ── Checking ───────────────────────────────────────────────────────────
    def check(self):
        """Ask GitHub for a newer release. Returns UpdateInfo, or None when
        there is none or anything went wrong (offline, rate limited, odd
        data: silent, as it always was). Publishes update.available."""
        try:
            info = self._check()
        except Exception:
            log.debug("update check failed", exc_info=True)
            return None
        if info is None:
            return None
        self.info = info
        self.publish("available", **info.as_dict())
        return info

    def _check(self):
        # Scan the recent release feed rather than /latest: the Latest marker
        # is a manual pin and is not necessarily the highest version. Picks
        # the release with the highest version number, skipping drafts and
        # prereleases.
        releases = self.http.get_json(RELEASES_URL, timeout=5)
        data = None
        best_ver = (0,)
        # A pre-release (a beta) is offered newer pre-releases too; a release
        # only releases.
        with_pre = is_prerelease(self.version)
        for r in releases or []:
            if r.get("draft") or (r.get("prerelease") and not with_pre):
                continue
            candidate_tag = (r.get("tag_name") or "").strip()
            if not candidate_tag:
                continue
            v = version_tuple(candidate_tag)
            if v > best_ver:
                best_ver = v
                data = r
        if data is None:
            return None
        tag = (data.get("tag_name") or "").strip()
        if not tag or version_tuple(tag) <= version_tuple(self.version):
            return None
        ver = tag.lstrip("v")
        install_type = self.install_type()
        assets = data.get("assets") or []
        # Prefer the source-overlay tarball when the release ships one: it is
        # ~1500x smaller than the AppImage and works across both Debian and
        # Fedora builds. Only when native deps changed (no source tarball
        # published) do we fall back to the full AppImage download.
        # A matching .sha256 sidecar is REQUIRED; without it we silently fall
        # back to the AppImage path, because an unsigned tarball is the
        # easiest tamper point on the update flow (compromised release ->
        # arbitrary code).
        source_url = ""
        source_sha_url = ""
        url = ""
        if install_type == "appimage":
            for asset in assets:
                name = (asset.get("name") or "").lower()
                if name.startswith("source-") and name.endswith(".tar.gz"):
                    source_url = asset.get("browser_download_url") or ""
                elif name.startswith("source-") and name.endswith(".tar.gz.sha256"):
                    source_sha_url = asset.get("browser_download_url") or ""
            if not source_sha_url:
                source_url = ""
            variant = _install.appimage_variant(os.environ.get("APPIMAGE", "") or "")
            for asset in assets:
                name = (asset.get("name") or "").lower()
                if name.endswith(".appimage") and variant in name:
                    url = asset.get("browser_download_url") or ""
                    break
            if not url:
                for asset in assets:
                    name = (asset.get("name") or "").lower()
                    if name.endswith(".appimage"):
                        url = asset.get("browser_download_url") or ""
                        break
        return UpdateInfo(version=ver, install_type=install_type,
                          url=source_url or url, sha_url=source_sha_url,
                          kind="source" if source_url else "appimage")

    def check_async(self):
        """check() on a thread; the event is published on the scheduler."""
        def run():
            try:
                info = self._check()
            except Exception:
                log.debug("update check failed", exc_info=True)
                return
            if info is not None:
                self.app.scheduler.call_soon(self._found, info)
        threading.Thread(target=run, name="update-check", daemon=True).start()

    def _found(self, info):
        self.info = info
        self.publish("available", **info.as_dict())

    # ── Downloading and installing ─────────────────────────────────────────
    def download_and_install(self, info=None, on_progress=None,
                             on_installing=None, on_done=None, on_error=None,
                             wait=False):
        """Download the update found by check() (or `info`) and install it.

        AppImage installs only. Source kind: SHA256 against the sidecar
        (mandatory), extracted to the overlay directory; the AppImage binary
        stays untouched. AppImage kind: full swap via atomic rename.

        Progress and results are published as events and also passed to the
        optional callbacks, on the scheduler (the interface's main thread
        under Tk). With wait=True everything runs on the calling thread
        (tests).
        """
        info = info or self.info
        url = info.url if info else ""
        sha_url = info.sha_url if info else ""
        kind = info.kind if info else "appimage"
        appimg = os.environ.get("APPIMAGE", "")

        def say(event, cb, **payload):
            def deliver():
                self.publish(event, **payload)
                if cb:
                    cb(*(payload[k] for k in cb_args.get(event, ())))
            if wait:
                deliver()
            else:
                self.app.scheduler.call_soon(deliver)

        cb_args = {"progress": ("pct",), "installing": (), "done": (),
                   "error": ("code", "message")}

        def fail(code, message=""):
            say("error", on_error, code=code, message=message)

        if not url or not appimg or not os.path.isfile(appimg):
            fail("no_asset")
            return
        if kind == "source" and not sha_url:
            # Belt-and-suspenders: check() already drops the source url in
            # this case, but guard here too.
            fail("no_checksum")
            return
        if wait:
            self._run(url, sha_url, kind, appimg, say, fail,
                      on_progress, on_installing, on_done)
        else:
            threading.Thread(
                target=self._run, name="update-download", daemon=True,
                args=(url, sha_url, kind, appimg, say, fail,
                      on_progress, on_installing, on_done)).start()

    def _run(self, url, sha_url, kind, appimg, say, fail,
             on_progress, on_installing, on_done):
        tmp_path = ((appimg + ".new") if kind == "appimage"
                    else _install.download_cache(
                        self.home, "basecamp-source-update.tar.gz"))
        try:
            expected_sha = ""
            if kind == "source":
                os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                # Fetch the checksum sidecar FIRST. If this fails we abort
                # before even touching the tarball.
                raw = self.http.get_text(sha_url).strip().split()
                if raw:
                    expected_sha = raw[0].lower()
                if len(expected_sha) != 64 or not all(
                        c in "0123456789abcdef" for c in expected_sha):
                    fail("bad_checksum")
                    return
            hasher = hashlib.sha256()
            with self.http.open(url) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done, last = 0, -1
                with open(tmp_path, "wb") as out:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        if total > 0:
                            pct = int(done * 100 / total)
                            if pct != last:
                                last = pct
                                say("progress", on_progress, pct=pct)
            if kind == "source":
                if hasher.hexdigest().lower() != expected_sha:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    fail("bad_checksum")
                    return
            say("installing", on_installing)
            if kind == "source":
                self._install_source(tmp_path)
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            else:
                os.chmod(tmp_path, 0o755)
                os.replace(tmp_path, appimg)
            say("done", on_done, kind=kind)
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            fail("failed", str(e)[:80])

    def _install_source(self, tarball_path):
        """Extract the source tarball to the overlay dir. Stages into a
        sibling directory then renames so a half-extracted tree never replaces
        the running one (which the bootstrap hook would happily load)."""
        import shutil
        import tarfile
        root = _install.overlay_root(self.home)
        os.makedirs(root, exist_ok=True)
        staging = os.path.join(root, "source-overlay.new")
        final = os.path.join(root, "source-overlay")
        if os.path.exists(staging):
            shutil.rmtree(staging)
        os.makedirs(staging)
        with tarfile.open(tarball_path, "r:gz") as tf:
            # Strip the top-level 'source-overlay/' dir so members land
            # directly in our staging path.
            def _members():
                for m in tf.getmembers():
                    parts = m.name.split("/", 1)
                    if len(parts) < 2 or parts[0] != "source-overlay":
                        continue
                    m.name = parts[1]
                    if not m.name:
                        continue
                    yield m
            # filter="data" (Python 3.12+) rejects path traversal, absolute
            # paths, symlinks pointing outside dest, device nodes, and strips
            # setuid/setgid bits. Fall back to manual checks on older Python;
            # the source tarball is produced by us so the added value of
            # data_filter is defense-in-depth against a compromised release
            # pipeline.
            try:
                tf.extractall(staging, members=_members(), filter="data")
            except TypeError:
                for m in _members():
                    if (m.name.startswith("/")
                            or ".." in m.name.split("/")
                            or m.issym() or m.islnk()
                            or m.isdev() or m.ischr() or m.isfifo()):
                        continue
                    tf.extract(m, staging)
        if not os.path.isfile(os.path.join(staging, "gui.py")):
            raise RuntimeError("source tarball missing gui.py")
        if os.path.exists(final):
            shutil.rmtree(final)
        os.replace(staging, final)

    # ── Restart ────────────────────────────────────────────────────────────
    def restart(self, execve=None, popen=None):
        """Re-exec the (now updated) AppImage.

        The new instance starts from a sanitised environment, the way the
        desktop launcher would start it. Handing it our own environment made
        the AppImage runtime stack a second mount point onto LD_LIBRARY_PATH
        and XDG_DATA_DIRS and record the pair as the "original" value, so from
        then on every launched program inherited the mount of an instance that
        no longer existed (#49).

        Returns only if the process was not replaced; the caller (which also
        owns the tray helper) then closes the old window.
        """
        from shared.macros import clean_child_env
        kwargs = {}
        if execve:
            kwargs["execve"] = execve
        if popen:
            kwargs["popen"] = popen
        _install.restart_self(os.environ.get("APPIMAGE", ""),
                              clean_child_env(), **kwargs)
        self.publish("restarting")

    def commands(self):
        def check():
            info = self.check()
            return {"update": info.as_dict() if info else None}
        def status():
            return {"version": self.version, "install_type": self.install_type(),
                    "can_install": self.can_install(),
                    "update": self.info.as_dict() if self.info else None}

        def download():
            if self.info is None:
                raise CommandError("no update has been found; check first")
            self.download_and_install()
            return {"started": True}

        def restart():
            self.restart()
            return {"restarting": False}    # only reached when nothing was replaced

        return {"check": check, "status": status, "download": download,
                "restart": restart}

    def can_install(self):
        """True when this installation can replace itself (an AppImage that
        exists); the others are updated by their package manager or git."""
        appimg = os.environ.get("APPIMAGE", "")
        return bool(appimg) and os.path.isfile(appimg)
