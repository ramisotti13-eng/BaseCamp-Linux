#!/usr/bin/env python3
"""
Checks the "we cannot open this device" test that puts a notice on screen.

    python3 tools/test_device_access.py

No hardware, no display. It drives gui._device_access_denied() against a
handful of made-up nodes, because getting this wrong is expensive in both
directions: a missed denial leaves someone with a screen full of controls
that quietly do nothing (issue #49), and a false one puts a full "no
permission" notice over a device that is working (issue #86).
"""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui   # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-48s %s" % (name, detail))
    if not ok:
        failures.append("%s: %s" % (name, detail))


def denied_for(nodes):
    """What the check makes of this list of nodes."""
    real = gui._device_nodes
    gui._device_nodes = lambda _vid, _pid: list(nodes)
    try:
        return gui._device_access_denied(0x3282, 0x0009)
    finally:
        gui._device_nodes = real


work = tempfile.mkdtemp(prefix="basecamp-access-test-")
readable = os.path.join(work, "hidraw-open")
unreadable = os.path.join(work, "hidraw-locked")
missing = os.path.join(work, "hidraw-gone")

with open(readable, "w") as f:
    f.write("")
with open(unreadable, "w") as f:
    f.write("")
os.chmod(readable, 0o666)
os.chmod(unreadable, 0o000)

running_as_root = os.geteuid() == 0

check("a node we can read and write is not reported",
      denied_for([readable]) == [], denied_for([readable]))

if running_as_root:
    print("skip  a node we cannot open                        (running as root)")
else:
    check("a node we cannot open is reported",
          denied_for([unreadable]) == [unreadable], denied_for([unreadable]))

# The one from #86: the DisplayPad drops and re-adds its hidraw node by
# itself, so the node listed a moment ago can be gone by the time it is
# checked. os.access() says False for a path that does not exist, and that
# was being reported as a permission problem on a device that was fine.
check("a node that has gone away is not a permission problem",
      denied_for([missing]) == [], denied_for([missing]))

check("a vanished node does not hide a real denial",
      denied_for([missing, unreadable]) == ([] if running_as_root else [unreadable]),
      denied_for([missing, unreadable]))

check("nothing to check means nothing to report", denied_for([]) == [])

# The log line has to carry enough to tell a false report from a real one.
described = gui._describe_node(readable)
check("the log line names owner, group and mode",
      readable in described and "666" in described, described)
check("and survives a node that is not there",
      isinstance(gui._describe_node(missing), str), gui._describe_node(missing))

# ── Strikes are counted per node, not per device (#86) ───────────────────────
# An upload detaches the kernel driver from an interface and hands it back,
# and the fresh hidraw node the kernel then builds is root:root 0600 until
# udev gets to it. Three different nodes each caught inside their own short
# window used to add up to a verdict about a device that was readable the
# whole time, which is the report an `ls` then contradicted.

def scan_with(app, denied_per_scan):
    """Run the periodic check once per entry, with that entry's denials."""
    logged = []
    real_denied = gui._device_access_denied
    real_describe = gui._describe_node
    gui._describe_node = lambda n: n
    try:
        for nodes in denied_per_scan:
            gui._device_access_denied = lambda _v, _p, _n=nodes: list(_n)
            before = set(app._denied_logged)
            app._check_device_access(False, False, False, True, False)
            if set(app._denied_logged) - before:
                logged.append(sorted(app._dev_denied.get("displaypad", [])))
    finally:
        gui._device_access_denied = real_denied
        gui._describe_node = real_describe
    return logged


# The real App is a Tk window and cannot be built without one, so the two
# methods under test are borrowed onto a plain object along with the ids they
# read. Nothing here is a stand-in for the logic itself.
_App = type("_App", (), dict(
    {name: getattr(gui.App, name) for name in (
        "EVEREST_MAX_VID", "EVEREST_MAX_PID", "EVEREST60_VID",
        "EVEREST60_PID_ANSI", "EVEREST60_PID_ISO", "MAKALU67_VID",
        "MAKALU67_PID", "DISPLAYPAD_VID", "DISPLAYPAD_PID",
        "MACROPAD_VID", "MACROPAD_PID", "_ACCESS_STRIKES",
        "_busy_with_device", "_check_device_access")}))


def fresh_app():
    app = _App()
    app._dev_denied = {}
    app._denied_logged = set()
    app._denied_strikes = {}
    return app


app = fresh_app()
check("a different node each scan never adds up",
      scan_with(app, [["/dev/hidraw7"], ["/dev/hidraw8"], ["/dev/hidraw9"],
                      ["/dev/hidraw7"], ["/dev/hidraw8"]]) == [],
      app._dev_denied)

app = fresh_app()
check("the same node three scans running is reported",
      scan_with(app, [["/dev/hidraw7"], ["/dev/hidraw7"], ["/dev/hidraw7"]])
      == [["/dev/hidraw7"]], app._dev_denied)

app = fresh_app()
check("two scans is not enough",
      scan_with(app, [["/dev/hidraw7"], ["/dev/hidraw7"]]) == [],
      app._dev_denied)

app = fresh_app()
scan_with(app, [["/dev/hidraw7"], ["/dev/hidraw7"], ["/dev/hidraw7"]])
scan_with(app, [[]])
check("access coming back drops the notice at once",
      "displaypad" not in app._dev_denied and
      "displaypad" not in app._denied_logged, app._dev_denied)

# A node that is denied throughout must not be held up by one that comes and
# goes beside it.
app = fresh_app()
check("a real denial is still found next to a flapping one",
      scan_with(app, [["/dev/hidraw7", "/dev/hidraw8"],
                      ["/dev/hidraw7", "/dev/hidraw9"],
                      ["/dev/hidraw7"]]) == [["/dev/hidraw7"]],
      app._dev_denied)

# And nothing is said while the application is the one churning the nodes.
app = fresh_app()


class _BusyPanel:
    _uploading = True
    _animating = False


app._displaypad_panel = _BusyPanel()
check("no verdict while our own upload is running",
      scan_with(app, [["/dev/hidraw7"]] * 5) == [], app._dev_denied)

os.chmod(unreadable, stat.S_IRUSR | stat.S_IWUSR)
for path in (readable, unreadable):
    os.unlink(path)
os.rmdir(work)

print()
if failures:
    print("%d check(s) failed:" % len(failures))
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("all checks passed")
