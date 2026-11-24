"""`--photos` for the demo: the real device photos in the throw-away config.

With --demo the devices are stand-ins and the configuration is a temporary
directory. --photos additionally imports Mountain's device photos from the
local Base Camp installation (or the folder given) into that temporary
configuration, read-only from the source, so the screens can be checked with
the real pictures and the click areas over them. Nothing is written outside
the demo's directory, and it goes away with it.
"""


def add_argument(ap):
    ap.add_argument("--photos", nargs="?", const="", default=None, metavar="PATH",
                    help="with --demo: use the device photos of a local Mountain Base Camp "
                         "installation (or of PATH); read-only, into the demo's configuration")


def apply(args, say=print):
    """Import the photos if --photos was given. Call after demo.prepare()."""
    if args.photos is None:
        return None
    if not args.demo:
        say("--photos only works together with --demo; to use the photos for real, "
            "run import_device_images.py")
        return None
    from core import device_images
    from shared.config import CONFIG_DIR
    try:
        m = device_images.import_images(args.photos or None, CONFIG_DIR)
    except device_images.ImageImportError as e:
        say("no device photos: %s" % e)
        return None
    say("device photos from %s: %s" % (m["source"], ", ".join(sorted(m["devices"]))))
    return m
