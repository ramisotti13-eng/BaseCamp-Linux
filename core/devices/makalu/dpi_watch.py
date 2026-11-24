"""Follows the Makalu's DPI button.

When the DPI button on the mouse changes the level, the mouse says so on its
second HID interface: input report 3 (vendor page ff02), `03 02 <level>` with
the level counted from 1 (seen on a Makalu 67, 2026-11-23: 04, 05, 01, 02,
03 for five presses). Reading it means the application knows the active
level without asking the mouse every few seconds, which would start the
controller each time.

The node is the hidraw interface of a Mountain mouse whose report descriptor
declares report ID 3 (`85 03`). Reading hidraw is passive: every reader gets
its own copy, the desktop keeps getting the mouse's input.
"""
import glob
import logging
import os
import select
import threading

log = logging.getLogger("basecamp.core.devices.makalu.dpi_watch")

VID = "3282"
PIDS = ("0003", "0002")           # Makalu 67, Makalu Max
REPORT_ID = 0x03
DPI_KIND = 0x02
SYS_HIDRAW = "/sys/class/hidraw"


def parse(report):
    """The 0-based level of a `03 02 <level>` report, else None."""
    if len(report) >= 3 and report[0] == REPORT_ID and report[1] == DPI_KIND \
            and 1 <= report[2] <= 5:
        return report[2] - 1
    return None


def find_node(root=SYS_HIDRAW):
    """/dev/hidrawN of the interface that carries report 3, or None."""
    for path in sorted(glob.glob(os.path.join(root, "hidraw*"))):
        try:
            dev = os.path.basename(os.path.realpath(os.path.join(path, "device")))
        except OSError:
            continue
        # e.g. 0003:3282:0003.000B
        parts = dev.split(":")
        if len(parts) < 3 or parts[1].upper() != VID.upper() \
                or parts[2].split(".")[0].upper() not in PIDS:
            continue
        try:
            with open(os.path.join(path, "device", "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        if b"\x85" + bytes([REPORT_ID]) in desc:
            return os.path.join("/dev", os.path.basename(path))
    return None


class DpiWatcher:
    """A thread that reads the node and calls on_level(level) (0-based) for
    every level change the mouse reports. on_level runs on this thread."""

    POLL_S = 0.5

    def __init__(self, on_level, find=find_node):
        self._on_level = on_level
        self._find = find
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="makalu-dpi-watch",
                                        daemon=True)
        self._thread.start()

    def stop(self, wait=True):
        self._stop.set()
        t = self._thread
        self._thread = None
        if wait and t is not None and t is not threading.current_thread():
            t.join(timeout=2)

    def _run(self):
        node = None
        try:
            node = self._find()
        except Exception:
            log.exception("looking for the Makalu's DPI interface failed")
        if not node:
            return
        try:
            fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.info("cannot read %s, the DPI button is not followed: %s", node, e)
            return
        try:
            while not self._stop.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], self.POLL_S)
                except (OSError, ValueError):
                    return
                if not ready:
                    continue
                try:
                    data = os.read(fd, 64)
                except BlockingIOError:
                    continue
                except OSError:
                    return          # unplugged
                if not data:
                    return
                level = parse(data)
                if level is not None:
                    try:
                        self._on_level(level)
                    except Exception:
                        log.exception("handling a DPI level from the mouse failed")
        finally:
            os.close(fd)
