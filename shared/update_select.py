"""Which file of a release the updater downloads.

Pure functions, so they can be checked without a window or a network.

A release can carry a source patch (`source-<ver>.tar.gz` with its `.sha256`)
and AppImages. A patch only replaces Python files, so it is used only within
the same major version: a 3.x AppImage cannot run 4.0, which needs a web view
(Qt WebEngine) the 3.x bundle does not have. For a newer major version the
updater always takes the AppImage, and saves the settings first
(backup_path()).
"""
import os


def version_tuple(s):
    """"v3.2.5" -> (3, 2, 5). A pre-release suffix is cut ("4.0.0-beta.1"
    is (4, 0, 0)); the updater never offers pre-releases anyway."""
    parts = []
    for p in str(s or "").strip().lstrip("vV").split("-", 1)[0].split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def major(s):
    t = version_tuple(s)
    return t[0] if t else 0


def is_major_upgrade(current, new):
    return major(new) > major(current)


def _variant(appimage_path, os_release):
    base = os.path.basename(appimage_path or "").lower()
    if "debian" in base:
        return "debian"
    if "fedora" in base:
        return "fedora"
    osr = (os_release or "").lower()
    if ("id=debian" in osr or "id=ubuntu" in osr or "id=linuxmint" in osr
            or "id_like=debian" in osr or "id_like=ubuntu" in osr):
        return "debian"
    return "fedora"


def pick_assets(assets, install_type, appimage_path, current, new, os_release=None):
    """{source_url, source_sha_url, url} for a release's `assets`.

    source_url is set only for an AppImage install, within the same major
    version, and when the patch's .sha256 is there too. url is the AppImage
    whose name matches the install's variant (debian or fedora), else the
    first AppImage."""
    out = {"source_url": "", "source_sha_url": "", "url": ""}
    if install_type != "appimage":
        return out
    assets = assets or []
    if not is_major_upgrade(current, new):
        for a in assets:
            name = (a.get("name") or "").lower()
            if name.startswith("source-") and name.endswith(".tar.gz"):
                out["source_url"] = a.get("browser_download_url") or ""
            elif name.startswith("source-") and name.endswith(".tar.gz.sha256"):
                out["source_sha_url"] = a.get("browser_download_url") or ""
        if not out["source_sha_url"]:
            out["source_url"] = ""
    variant = _variant(appimage_path, os_release)
    images = [a for a in assets if (a.get("name") or "").lower().endswith(".appimage")]
    match = next((a for a in images if variant in (a.get("name") or "").lower()), None)
    pick = match or (images[0] if images else None)
    if pick is not None:
        out["url"] = pick.get("browser_download_url") or ""
    return out


def backup_path(config_dir, current):
    """Where the settings are saved before a major update: beside the
    settings folder, named after the version they come from."""
    parent = os.path.dirname(os.path.normpath(config_dir))
    return os.path.join(parent, "mountain-time-sync-backup-%s.zip" % current)
