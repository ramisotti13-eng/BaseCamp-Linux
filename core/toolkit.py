"""The once-per-version notice about a newer CustomTkinter than this version
handles (#116). The decision is made here; the dialog is the interface's.

A source install can pull a CustomTkinter newer than this version knows, so
the dropdowns use CustomTkinter's own menus (install_inline_dropdown
declined), which work, but without the fix for #66. The AppImage carries the
CustomTkinter it was tested with, so it never says anything.
"""


def toolkit_notice(app_version, toolkit_version, *, frozen, declined,
                   seen=None, mark=None):
    """The notice due now, or None. Remembers that it was given.

    Returns {"version": ..., "below": "major.minor"} the first time a given
    (application version, toolkit version) pair is met with `declined` set.
    `seen(key)` and `mark(key)` default to the configuration files.
    """
    if frozen or not declined:
        return None
    if seen is None or mark is None:
        from shared.config import _toolkit_notice_seen, _mark_toolkit_notice_seen
        seen = seen or _toolkit_notice_seen
        mark = mark or _mark_toolkit_notice_seen
    ver = toolkit_version or "?"
    key = "%s|%s" % (app_version, ver)
    if seen(key):
        return None
    mark(key)
    parts = (ver.split(".") + ["0"])[:2]
    return {"version": ver, "below": ".".join(parts)}
