"""Makalu HID access: find the mouse, open interface 1, exchange reports.

Every operation opens the device, sends one 64-byte feature report, waits
50 ms, reads the answer (report 0xA1) and closes again; the mouse took the
report when the answer starts with 0xA0.

hid_compat is looked up at call time, so a test that replaces
hid_compat.enumerate / open_path / HID_AVAILABLE reaches every caller.
"""
import time

from shared import hid_compat
from core.devices.makalu import protocol as P


def command_entry(entries):
    """The enumerate entry to talk to among one model's entries, or None.

    Interface 1, and on Windows, where each collection of that interface is a
    path of its own, the vendor collection (usage page 0xFF01). On Linux the
    interface is one hidraw node whose usage page is that of its first
    collection, so when no entry carries 0xFF01 the first entry of interface
    1 is the one, as before.
    """
    on_if = [d for d in entries if d.get("interface_number") == P.INTERFACE]
    vendor = [d for d in on_if if d.get("usage_page") == P.VENDOR_USAGE_PAGE]
    pick = vendor or on_if
    return pick[0] if pick else None


def detect():
    """(pid, model name) of the mouse that is plugged in, or (None, None).
    A Makalu 67 is looked for before a Max."""
    if not hid_compat.HID_AVAILABLE:
        return None, None
    for pid, name in P.MODELS:
        if command_entry(hid_compat.enumerate(P.VID, pid)) is not None:
            return pid, name
    return None, None


def find_path():
    """Path of the command collection on interface 1, or None."""
    if not hid_compat.HID_AVAILABLE:
        return None
    for pid, _name in P.MODELS:
        entry = command_entry(hid_compat.enumerate(P.VID, pid))
        if entry is not None:
            return entry["path"]
    return None


def open_device():
    """Open interface 1. Raises RuntimeError when the mouse is not there."""
    path = find_path()
    if path is None:
        raise RuntimeError(P.NOT_FOUND)
    return hid_compat.open_path(path)


def query(dev, buf, wait=0.05):
    """Send one report on an open device and return the answer."""
    dev.send_feature_report(bytes(buf))
    time.sleep(wait)
    return dev.get_feature_report(P.REPORT_ID, P.REPORT_LEN)


def answered_ok(resp):
    return len(resp) >= 1 and resp[0] == P.RESP_ID


def run(buf, opener=open_device):
    """Open, send one report, read the answer, close. True if it was taken."""
    dev = opener()
    try:
        return answered_ok(query(dev, buf))
    finally:
        dev.close()
