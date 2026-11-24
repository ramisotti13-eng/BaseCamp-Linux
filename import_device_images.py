#!/usr/bin/env python3
"""Use Mountain's device photos from your own Base Camp installation.

Optional. BaseCamp Linux shows its own drawings of the devices; with this the
new interface shows the photos from a Mountain Base Camp installation on your
machine instead (Windows, a Wine prefix, or a copy of its folder), or from
Base Camp's installer (the zip from mountain.gg, or the .msi in it). The
photos are copied into your configuration directory only. Nothing is
downloaded unless you ask for it with --download, and nothing leaves your
machine.

    python3 import_device_images.py              # search the usual places
    python3 import_device_images.py PATH         # the Base Camp folder, or a copy
    python3 import_device_images.py FILE         # Base Camp's zip or .msi
    python3 import_device_images.py --download   # Base Camp from mountain.gg
    python3 import_device_images.py --status
    python3 import_device_images.py --remove     # back to the drawings
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    from core import device_images as di
    from shared.config import CONFIG_DIR

    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv and argv[0] == "--remove":
        print("Removed." if di.remove_images(CONFIG_DIR) else "Nothing was imported.")
        return 0
    if argv and argv[0] == "--status":
        st = di.status(CONFIG_DIR)
        if not st["imported"]:
            print("No device photos imported; the drawings are used.")
            return 0
        print("Imported from: %s" % st.get("source"))
        for dev, views in sorted(st["devices"].items()):
            print("  %-12s %s" % (dev, ", ".join(sorted(views))))
        return 0
    from core import mountain_assets as ma

    def progress(stage, pct, **_extra):
        if pct is not None:
            print("\r%-8s %3d%%" % (stage, pct), end="", flush=True)
    try:
        if argv and argv[0] == "--download":
            from shared.tls import ensure_ca_bundle
            ensure_ca_bundle()
            manifest = ma.download_device_images(CONFIG_DIR, ma.Downloader(), progress)
            print()
        elif argv and os.path.isfile(argv[0]):
            manifest = ma.import_device_package(argv[0], CONFIG_DIR, progress)
            print()
        else:
            manifest = di.import_images(argv[0] if argv else None, CONFIG_DIR)
    except (di.ImageImportError, ma.AssetError) as e:
        print("\nNot imported (%s): %s" % (e.code, e), file=sys.stderr)
        return 1
    print("Imported from: %s" % manifest["source"])
    for dev, views in sorted(manifest["devices"].items()):
        print("  %-12s %s" % (dev, ", ".join(sorted(views))))
    print("Saved in %s" % di.target_dir(CONFIG_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
