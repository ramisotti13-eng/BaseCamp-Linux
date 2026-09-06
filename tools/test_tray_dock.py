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
    """As much of pystray's Xorg backend as the watcher looks at.

    `managers` is what the icon holds after each poll; `hosts` is what the
    display would answer if asked, which is not the same thing: an icon that
    failed to dock holds nothing while a host is sitting right there.
    """

    def __init__(self, managers, hosts=None):
        self._managers = list(managers)
        self._hosts = list(hosts) if hosts is not None else None
        self._host = None
        self._systray_manager = None
        self.stopped = False

    def step(self):
        if self._managers:
            self._systray_manager = self._managers.pop(0)
        if self._hosts:
            self._host = self._hosts.pop(0)

    def _get_systray_manager(self):
        return self._host

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

# One look is not enough: pystray clears the host window before it goes
# looking for a new one, so a single glance can land inside its own recovery.
host = object()
icon = XorgIcon([host, None, host, host])
check("a host gone for one look only is left alone", run(icon, 4) is False)
check("and that icon keeps running", not icon.stopped)

# A desktop with no notification area yet must not be mistaken for one that
# lost it: that is the state every icon starts in.
icon = XorgIcon([None, None, None])
check("never having been docked is not a loss", run(icon, 3) is False)
check("and nothing was stopped", not icon.stopped)

# But an icon that never managed to dock, with a host now sitting there, is
# the dead end a rebuild lands in when the panel is still restarting: pystray
# logs its failure once and never looks again.
icon = XorgIcon([None, None, None, None], hosts=[None, None, object(), object()])
check("a host appearing under an undocked icon ends the run",
      run(icon, 4) is True)
check("so a fresh one can dock into it", icon.stopped)

# A host that stays is left alone.
host = object()
icon = XorgIcon([host, host, host, host])
check("a docked icon is left running", run(icon, 4) is False)
check("and is not stopped", not icon.stopped)

# A session that never had a notification area must not leave the watcher
# running: the supervisor builds a fresh icon on every backoff, and a watcher
# per icon that never ends is a thread per icon that never ends.
icon = XorgIcon([None] * 50)
ticks = [0]


def counting_should_run():
    ticks[0] += 1
    return ticks[0] <= 5


check("a watcher stops when its own run is over",
      tray_helper.watch_docked(icon, counting_should_run, poll=0,
                               sleep=lambda _s: None) is False)
check("and did not stop an icon it never saw docked", not icon.stopped)

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
