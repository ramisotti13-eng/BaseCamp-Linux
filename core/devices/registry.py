"""Which devices are plugged in, which we may not open, which keyboard is shown.

``DeviceRegistry`` is the core service ``devices``. It was the polling half of
gui.py: every five seconds it asks, per device, whether the USB id is on the
bus and, for those that are, whether this user may open their /dev nodes. An
interface renders its state (``present``, ``denied``, ``keyboard_id``); it
does not compute any of it.

Presence and access checks are injected (``presence``, ``access_denied``,
``describe``), because gui.py hands in callables that look up its own
module-level functions at call time (the tests patch those) and a Windows port
hands in its own. Defaults are the Linux ones from core/platform/usb.py.

While it runs, the Everest Max, Everest 60, Makalu and MacroPad services hear about their
device from it (``wire_presence``: devices.changed -> ``set_present``), so
presence works without any screen.

Events: ``devices.changed {present, denied, keyboard_id}``, published when any
of the three differs from the last scan. A listener added with
``add_scan_listener`` hears every scan, changed or not; the CustomTkinter
interface uses that, because it also refreshes things (the OBS dot, a pad's
init state) that are not part of the registry's state.
"""
import logging

from core.service import Service

log = logging.getLogger("basecamp.core.devices.registry")

MOUNTAIN_VID = 0x3282

#: (id, vid, (pids,)). Makalu Max has 0x0002 beside the Makalu 67's 0x0003.
DEVICE_TABLE = (
    ("everest_max", MOUNTAIN_VID, (0x0001,)),
    ("everest60", MOUNTAIN_VID, (0x0005, 0x0006)),
    ("makalu67", MOUNTAIN_VID, (0x0003, 0x0002)),
    ("displaypad", MOUNTAIN_VID, (0x0009,)),
    ("macropad", MOUNTAIN_VID, (0x0008,)),
)

# A device node exists for a moment before udev has applied our rule to it,
# so a pad that re-enumerates, which the DisplayPad does on its own, is
# briefly unreadable through no fault of the installation. Saying so at the
# first sight of it put a full "cannot be opened" notice on screen during
# an ordinary page switch (#80). Only a denial that survives this many
# consecutive scans, roughly fifteen seconds, is a real one.
#
# Counted per node, not per device (#86). An upload detaches the kernel
# driver from an interface and gives it back afterwards, which makes the
# kernel build that interface a fresh hidraw node, and a fresh node is
# root:root 0600 until udev gets to it. Counting per device let three
# different nodes, each caught inside its own short window, add up to a
# verdict about a device whose entries were readable throughout, which is
# what produced a report that the reporter's own `ls` contradicted.
ACCESS_STRIKES = 3

#: Device services told when their device comes and goes:
#: (service name, device id). Their set_present(bool) is called from
#: devices.changed, on the scheduler, so presence works without any screen.
PRESENCE_SERVICES = (
    ("everest_max", "everest_max"),
    ("everest60", "everest60"),
    ("makalu", "makalu67"),
    ("macropad", "macropad"),
)


def wire_presence(app, services=PRESENCE_SERVICES):
    """Pass devices.changed on to the device services' set_present().

    Runs on the scheduler: a scan from another thread (a script asking for
    one over the control socket) is handed over first. Returns the function
    that ends it.
    """
    def apply(state):
        present = state.get("present") or {}
        for svc_name, dev_id in services:
            svc = app.service(svc_name)
            if svc is None or not hasattr(svc, "set_present"):
                continue
            try:
                svc.set_present(bool(present.get(dev_id)))
            except Exception:
                log.exception("telling %s about its device failed", svc_name)

    def on_changed(_topic, state):
        if app.scheduler.in_scheduler_thread():
            apply(state)
        else:
            app.scheduler.call_soon(apply, state)

    return app.bus.subscribe("devices.changed", on_changed)


class DeviceRegistry(Service):
    name = "devices"
    ACCESS_STRIKES = ACCESS_STRIKES

    def __init__(self, app, presence=None, access_denied=None, describe=None,
                 table=DEVICE_TABLE):
        super().__init__(app)
        if presence is None or access_denied is None or describe is None:
            from core.platform import usb
            presence = presence or usb.usb_present
            access_denied = access_denied or usb.access_denied
            describe = describe or usb.describe_node
        self._presence = presence
        self._access_denied = access_denied
        self._describe = describe
        self.table = tuple(table)
        self.present = {dev_id: False for dev_id, _v, _p in self.table}
        self.denied = {}
        self.keyboard_id = "everest_max"
        self.prev_keyboard_id = "everest_max"
        self._denied_logged = set()
        self._denied_strikes = {}
        self._busy = {}            # dev id -> fn() -> bool
        self._virtual = {}         # id -> fn() -> bool (e.g. OBS)
        self._listeners = []
        self._poll = None
        self._polling = False
        self._interval = 5000
        self._last_published = None
        self._unwire = None

    # ── Wiring ─────────────────────────────────────────────────────────────
    def set_busy_probe(self, dev_id, fn):
        """`fn()` says whether the application itself is driving the device
        (an upload in progress); no access verdict is formed meanwhile."""
        self._busy[dev_id] = fn

    def add_virtual(self, dev_id, fn):
        """A 'device' that is not on USB (OBS): present = fn() at each scan."""
        self._virtual[dev_id] = fn
        self.present.setdefault(dev_id, False)

    def add_scan_listener(self, fn):
        self._listeners.append(fn)

    # ── Scanning ───────────────────────────────────────────────────────────
    def scan(self):
        """One presence and access scan. Returns the state dict."""
        usb = {}
        for dev_id, vid, pids in self.table:
            usb[dev_id] = any(self._presence(vid, pid) for pid in pids)
        self._check_access(usb)
        self.present.update(usb)
        for dev_id, fn in self._virtual.items():
            try:
                self.present[dev_id] = bool(fn())
            except Exception:
                self.present[dev_id] = False
        # Everest 60 takes priority if connected; with neither present the
        # last choice stands.
        self.prev_keyboard_id = self.keyboard_id
        if usb.get("everest60"):
            self.keyboard_id = "everest60"
        elif usb.get("everest_max"):
            self.keyboard_id = "everest_max"
        state = self.state()
        sig = (tuple(sorted(state["present"].items())),
               tuple(sorted((k, tuple(v)) for k, v in state["denied"].items())),
               state["keyboard_id"])
        if sig != self._last_published:
            self._last_published = sig
            self.publish("changed", **state)
        for fn in list(self._listeners):
            try:
                fn(state)
            except Exception:
                log.exception("device scan listener failed")
        return state

    def state(self):
        return {"present": dict(self.present),
                "denied": {k: list(v) for k, v in self.denied.items()},
                "keyboard_id": self.keyboard_id}

    def is_busy(self, dev_id):
        """True while the application itself is driving this device.

        The node churn during an upload is our own doing, so a permission
        verdict formed in the middle of one says nothing about the
        installation (#86). Only upload sessions count, which are short and
        bounded; the device worker holds its lock for long stretches while it
        listens for key presses, so waiting on that would switch this check
        off for good.
        """
        fn = self._busy.get(dev_id)
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            return False

    def _check_access(self, usb):
        """Note devices we can see but not open, and say so once (#49).

        A device with root-only /dev nodes, which is what a missing or
        unapplied udev rule leaves behind, enumerates normally: the screen said
        "connected" while every key action quietly did nothing, and the person
        had to find the permissions themselves.
        """
        for dev_id, vid, pids in self.table:
            present = usb.get(dev_id, False)
            if present and self.is_busy(dev_id):
                continue              # our own upload is churning the nodes
            denied = []
            if present:
                for pid in pids:
                    denied.extend(self._access_denied(vid, pid))
            # A set: a device with two product ids can list one node twice,
            # and a node counted twice per scan reaches the strike count in
            # two scans instead of three.
            denied = set(denied)
            strikes = self._denied_strikes.setdefault(dev_id, {})
            for node in list(strikes):
                if node not in denied:
                    del strikes[node]   # that one came back, forget it
            for node in denied:
                strikes[node] = strikes.get(node, 0) + 1
            persistent = sorted(n for n, c in strikes.items()
                                if c >= self.ACCESS_STRIKES)
            if persistent:
                self.denied[dev_id] = persistent
                if dev_id not in self._denied_logged:
                    self._denied_logged.add(dev_id)
                    described = ", ".join(self._describe(n) for n in persistent)
                    print(f"[Device] {dev_id}: no access to {described}. "
                          f"The udev rule is missing or has not been applied; "
                          f"see 'USB permissions' in the README.", flush=True)
                continue
            # Nothing has reached the mark on this scan. Two things must not
            # happen, and they pull opposite ways: a device already known to
            # be shut must not have its warning taken down and put back up
            # every time a node changes its number, which a pad that
            # re-enumerates does often, and the warning must not go on naming
            # a node that has since gone or become readable. So the warning
            # stands while anything at all is shut, and names what is shut
            # now. Before it has been said once, the count still rules.
            if dev_id in self._denied_logged and denied:
                self.denied[dev_id] = sorted(denied)
            else:
                # Access is back: drop the notice at once, no counting down.
                self.denied.pop(dev_id, None)
                self._denied_logged.discard(dev_id)

    # ── Polling ────────────────────────────────────────────────────────────
    def start_polling(self, interval_ms=5000):
        """Scan every `interval_ms` on the scheduler. The first scan is one
        interval from now; call scan() for an immediate one."""
        self._interval = interval_ms
        self._polling = True
        self._arm()

    def stop_polling(self):
        self._polling = False
        if self._poll is not None:
            self.app.scheduler.cancel(self._poll)
            self._poll = None

    def _arm(self):
        if self._polling:
            self._poll = self.app.scheduler.call_later(self._interval, self._tick)

    def _tick(self):
        self._poll = None
        if not self._polling:
            return
        try:
            self.scan()
        finally:
            self._arm()

    def start(self):
        # The Everest 60, Makalu and MacroPad services hear about their
        # device from here, whether or not a screen of theirs exists.
        if self._unwire is None:
            self._unwire = wire_presence(self.app)

    def stop(self):
        self.stop_polling()
        if self._unwire is not None:
            self._unwire()
            self._unwire = None

    def commands(self):
        return {"scan": self.scan, "state": self.state}
