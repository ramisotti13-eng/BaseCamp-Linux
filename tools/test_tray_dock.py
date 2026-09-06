#!/usr/bin/env python3
"""
Checks the tray icon notices when it has quietly lost the notification area.

    python3 tools/test_tray_dock.py

Issue #100: pystray's Xorg backend catches its own "cannot dock" assertion,
logs it with a traceback, and comments that it will retry later. There is no
later. Nothing in that backend docks again, and `run()` neither returns nor
raises, so the supervision in tray_helper had nothing to react to and the icon
was gone for the rest of the session while the log showed only a traceback.

No display and no tray: the watcher is driven against stand-in icons.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tray_helper                                 # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-54s %s" % (name, detail))
    if not ok:
        failures.append("%s: %s" % (name, detail))


class XorgIcon:
    """As much of pystray's Xorg backend as the watcher looks at."""

    def __init__(self, managers):
        self._managers = list(managers)
        self._systray_manager = None
        self.stopped = False

    def step(self):
        if self._managers:
            self._systray_manager = self._managers.pop(0)

    def stop(self):
        self.stopped = True


class AppIndicatorIcon:
    """The backend on a desktop that has no systray host window at all."""

    def stop(self):
        raise AssertionError("must never be stopped by the watcher")


def run(icon, ticks):
    """Drive the watcher `ticks` times without waiting for real time."""
    left = [ticks]

    def should_run():
        return left[0] > 0

    def sleep(_seconds):
        left[0] -= 1
        icon.step()

    return tray_helper.watch_docked(icon, should_run, poll=0, sleep=sleep)


# A host that is there, goes away, and is not replaced: the icon is gone and
# only a rebuild brings it back.
icon = XorgIcon([object(), object(), None, None])
check("a host that disappears ends the run", run(icon, 6) is True)
check("and the icon was stopped so it can be rebuilt", icon.stopped)

# A desktop with no notification area yet must not be mistaken for one that
# lost it: that is the state every icon starts in.
icon = XorgIcon([None, None, None])
check("never having been docked is not a loss", run(icon, 3) is False)
check("and nothing was stopped", not icon.stopped)

# A host that stays is left alone.
host = object()
icon = XorgIcon([host, host, host, host])
check("a docked icon is left running", run(icon, 4) is False)
check("and is not stopped", not icon.stopped)

# The watcher only applies where the failure exists.
check("the Xorg backend is watched",
      tray_helper.watch_docked_applies(XorgIcon([])))
check("the AppIndicator backend is not",
      not tray_helper.watch_docked_applies(AppIndicatorIcon()))

print()
if failures:
    print("%d check(s) failed:" % len(failures))
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("all checks passed")
