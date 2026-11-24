"""The software trigger: hears the F13-F24 a key was made to send.

A key bound to something only the application can do (a URL, a program, a
macro it cannot write into the keyboard...) is remapped to a free F13-F24
(`14 20 <src> <F-key>`, keybindings.py). The trigger tells the service when
the keyboard sends one of those.

KeyTrigger is the interface: Base Camp's own way (`EnableKeyFunc` = `12 05`
and the SDK's key callback) is still being researched and may replace the
one implementation here, EvdevTrigger.

EvdevTrigger reads the Everest Max's own input devices
(/dev/input/event* whose USB id is 3282:0001), every one of them, since which
interface carries F13-F24 is not known. It never grabs them: the desktop
keeps getting every key, so the keys used are the ones the desktop leaves
alone (keybindings.trigger_fkeys: on Linux not F13 or F20-F23). Only key
presses of F13-F24 are looked at; everything else read is dropped at once.
The nodes are root:input on most systems; our udev rule (99-mountain.rules)
gives the logged-in user access, and `state` says "denied" while that is
missing, so the screen can say what to do.
"""
import glob
import logging
import os
import select
import struct
import threading
import time

log = logging.getLogger("basecamp.core.everest_max.keytrigger")

VID, PID = "3282", "0001"
SYS_INPUT = "/sys/class/input"
EV_KEY = 1
KEY_F13 = 183                   # input-event-codes.h: KEY_F13 .. KEY_F24 = 183 .. 194
FKEY_FIRST = 230                # the keyboard's (SDK) number of F13
#: struct input_event: timeval (two longs), type, code, value.
EVENT = struct.Struct("llHHi")

#: States: not started, reading, no node of the keyboard found, a node that
#: cannot be opened (permissions).
STATES = ("off", "listening", "no_device", "denied")


def fkey_of(code):
    """The SDK number (230-241) of a Linux key code for F13-F24, else None."""
    return FKEY_FIRST + code - KEY_F13 if KEY_F13 <= code <= KEY_F13 + 11 else None


def presses(data):
    """The F-keys (SDK numbers) pressed in a run of whole input events."""
    out = []
    for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
        _s, _us, etype, code, value = EVENT.unpack_from(data, off)
        if etype == EV_KEY and value == 1:
            fk = fkey_of(code)
            if fk is not None:
                out.append(fk)
    return out


def find_nodes(root=SYS_INPUT):
    """/dev/input/eventN of every input device of the Everest Max."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, "event*"))):
        ids = []
        for name in ("vendor", "product"):
            try:
                with open(os.path.join(path, "device", "id", name)) as f:
                    ids.append(f.read().strip().lower())
            except OSError:
                break
        if ids == [VID, PID]:
            out.append(os.path.join("/dev/input", os.path.basename(path)))
    return out


class KeyTrigger:
    """start(on_key) calls on_key(fkey) (230-241) on a thread of its own for
    every F13-F24 the keyboard sends; stop() ends that. `state` is one of
    STATES."""

    state = "off"

    def start(self, on_key):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class EvdevTrigger(KeyTrigger):
    POLL_S = 0.5
    RESCAN_S = 3.0              # how often to look for the keyboard's nodes (not open yet)

    def __init__(self, find=None, open_node=None):
        # find_nodes looked up when used, so the demo's fence (ui_web/demo.py) reaches it.
        self._find = find or (lambda: find_nodes())
        self._open = open_node or (lambda node: os.open(node, os.O_RDONLY | os.O_NONBLOCK))
        self._stop = threading.Event()
        self._thread = None
        self.state = "off"

    def start(self, on_key):
        if self._thread is not None and self._thread.is_alive():
            return
        self._on_key = on_key
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="everest_max-keytrigger",
                                        daemon=True)
        self._thread.start()

    def stop(self, wait=True):
        self._stop.set()
        t, self._thread = self._thread, None
        if wait and t is not None and t is not threading.current_thread():
            t.join(timeout=2)
        self.state = "off"

    def _scan(self, fds):
        """Open the keyboard's nodes that are not open yet."""
        try:
            nodes = self._find()
        except Exception:
            log.exception("looking for the Everest Max's input devices failed")
            nodes = []
        denied = False
        for node in nodes:
            if node in fds.values():
                continue
            try:
                fds[self._open(node)] = node
            except PermissionError:
                denied = True
            except OSError as e:
                log.info("cannot read %s: %s", node, e)
        if fds:
            self.state = "listening"
        else:
            self.state = "denied" if denied else "no_device"
        if denied and fds:
            log.info("some of the Everest Max's input devices cannot be read")

    def _run(self):
        fds = {}                            # fd -> node
        pending = {}                        # fd -> bytes of an event read in part
        next_scan = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                # Also while reading: the nodes of a keyboard just plugged in
                # appear one by one, and a node may get its access a moment
                # after it appears; the one carrying F13-F24 may come later.
                if now >= next_scan:
                    self._scan(fds)
                    next_scan = now + self.RESCAN_S
                if not fds:
                    self._stop.wait(self.POLL_S)
                    continue
                try:
                    ready, _, _ = select.select(list(fds), [], [], self.POLL_S)
                except (OSError, ValueError):
                    ready = list(fds)
                for fd in ready:
                    try:
                        data = os.read(fd, EVENT.size * 64)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""
                    if not data:            # unplugged: look for it again
                        self._close(fd, fds, pending)
                        next_scan = 0.0
                        continue
                    data = pending.pop(fd, b"") + data
                    whole = len(data) - len(data) % EVENT.size
                    if whole < len(data):
                        pending[fd] = data[whole:]
                    for fk in presses(data[:whole]):
                        try:
                            self._on_key(fk)
                        except Exception:
                            log.exception("handling F-key %d failed", fk)
        finally:
            for fd in list(fds):
                self._close(fd, fds, pending)

    @staticmethod
    def _close(fd, fds, pending):
        fds.pop(fd, None)
        pending.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass
