"""Everest 60 HID access: find the board, open it, exchange feature reports.

The board's interface 2 takes 65-byte feature reports (protocol.py builds
them). Each one is followed by a GET_REPORT that should echo the command byte
in resp[1]; a board that is busy does not, and the report is sent again, up
to three times.

hid_compat is looked up at call time (hid_compat.enumerate, .open_path,
.HID_AVAILABLE), so a test that replaces them reaches every caller.
"""
import time

from shared import hid_compat
from core.devices.everest60 import protocol as P


def detect():
    """(pid, model name) of the board that is plugged in, or (None, None)."""
    if not hid_compat.HID_AVAILABLE:
        return None, None
    for pid, name in P.MODELS:
        for d in hid_compat.enumerate(P.VID, pid):
            if d.get("interface_number") == P.INTERFACE:
                return pid, name
    return None, None


def find_path():
    """HID path of interface 2, or None."""
    if not hid_compat.HID_AVAILABLE:
        return None
    for pid, _name in P.MODELS:
        for d in hid_compat.enumerate(P.VID, pid):
            if d.get("interface_number") == P.INTERFACE:
                return d["path"]
    return None


def open_device():
    """Open interface 2. Raises RuntimeError when the board is not there."""
    path = find_path()
    if path is None:
        raise RuntimeError(P.NOT_FOUND)
    return hid_compat.open_path(path)


def exchange(dev, buf, retries=3):
    """Send one report and read the answer; resend until resp[1] echoes the
    command, at most `retries` times. Returns the last answer."""
    cmd = buf[1]
    resp = None
    for _attempt in range(retries):
        dev.send_feature_report(bytes(buf))
        time.sleep(0.05)
        resp = dev.get_feature_report(0x00, P.REPORT_LEN)
        time.sleep(0.05)
        if resp and len(resp) >= 2 and resp[1] == cmd:
            return resp
    return resp


def send_all(dev, packets):
    for p in packets:
        exchange(dev, p)
