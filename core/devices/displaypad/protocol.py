"""Mountain DisplayPad on the wire: finding it, opening it, writing a key.

Protocol (reverse-engineered from JeLuF/mountain-displaypad and
MountainDisplayPadWorker.exe):
  VID=0x3282, PID=0x0009
  Interface 1: display writes (raw interrupt OUT EP 0x02, 512 B max packet)
  Interface 3: commands and key events (hidraw, EP 0x04 OUT / EP 0x83 IN)
  Key images: 102x102 BGR, 31 chunks of 1024 bytes (no report-ID prefix)
  Key events: data[42] bits (keys 1-7), data[47] bits (keys 8-12)

Moved here unchanged from devices/displaypad/panel.py (4.0), which still
offers every function under its old underscore name. This module is the
"transport" the DisplayPad service talks through: anything with the same
names (open_interfaces, init_device, close_interfaces, upload_button,
set_brightness, find_command_path, open_command_interface, find_on_bus) can
stand in for it, a test's stand-in pad or, later, a Windows backend.

hidapi is reached through shared.hid_compat and looked up at call time, so a
test that replaces hid_compat.enumerate or open_path reaches this code too.
"""
import gc
import time

from shared import hid_compat

HID_AVAILABLE = hid_compat.HID_AVAILABLE

try:
    import usb.core
    import usb.util
    PYUSB_AVAILABLE = True
except ImportError:
    PYUSB_AVAILABLE = False

# ── Protocol constants ────────────────────────────────────────────────────────

VID = 0x3282
PID = 0x0009

NUM_KEYS = 12
KEYS_PER_ROW = 6
ICON_SIZE = 102
CHUNK_SIZE = 1024
HEADER_SIZE = 306
PACKET_SIZE = 31438   # total payload = 31744 = 31 x 1024
EP_DISPLAY = 0x02

# Key-event byte/bit map: K1-K7 -> data[42], K8-K12 -> data[47]
KEY_MAP = (
    [(42, m) for m in (0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80)] +
    [(47, m) for m in (0x01, 0x02, 0x04, 0x08, 0x10)]
)

INIT_MSG = bytes.fromhex(
    "0011800000010000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000"
    "0000"
)
IMG_MSG_TEMPLATE = bytearray.fromhex(
    "0021000000FF3d00006565000000000000000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000"
    "0000"
)

BLANK_BGR = b'\x00' * (ICON_SIZE * ICON_SIZE * 3)

# ── The whole panel ─────────────────────────────────────────────
# The pad is ONE continuous 800 x 240 panel; the 6 x 2 keys are windows in it
# and the gaps between them are real pixels too. Any rectangle of it can be
# written (upload_panel). Captured on the device from the Windows software.
PANEL_W = 800
PANEL_H = 240

#: Where key k (0-11, six per row) sits in panel pixels, as (left, top, w, h).
#: APPROXIMATE, TO BE MEASURED: keys are 102 x 102 with the outer ones at the
#: panel's edges, which gives a pitch of (800 - 102) / 5 = 139.6 px across and
#: 240 - 102 = 138 px down (the first estimate across was ~137.6, which does
#: not reach the right edge). This is the only place the geometry lives.
KEY_PITCH_X = (PANEL_W - ICON_SIZE) / (KEYS_PER_ROW - 1)
KEY_PITCH_Y = PANEL_H - ICON_SIZE
KEY_PANEL_RECTS = tuple(
    (int(round((k % KEYS_PER_ROW) * KEY_PITCH_X)),
     int(round((k // KEYS_PER_ROW) * KEY_PITCH_Y)), ICON_SIZE, ICON_SIZE)
    for k in range(NUM_KEYS))


def panel_header(left, top, right, bottom, save=False):
    """The 64-byte command that announces a write to a rectangle of the panel
    (right and bottom inclusive): 21 00 00 01 <blocks u16> <left u16>
    <top u16> <right u16> <bottom u16>, zeros after. blocks is the pixel
    data's size in 512-byte blocks, rounded up.

    save=True is the start-up picture, written to the pad's flash:
    21 02 00 00 <blocks> 00 00 00 00 1f 03 ef 00 (byte 1 = 02, byte 3 = 00),
    always the whole panel."""
    if save and (left, top, right, bottom) != (0, 0, PANEL_W - 1, PANEL_H - 1):
        raise ValueError("the start-up picture is the whole panel")
    if not (0 <= left <= right < PANEL_W and 0 <= top <= bottom < PANEL_H):
        raise ValueError("rectangle outside the %dx%d panel" % (PANEL_W, PANEL_H))
    w, h = right - left + 1, bottom - top + 1
    blocks = (w * h * 3 + 511) // 512
    msg = bytearray(64)
    msg[0:4] = b"\x21\x02\x00\x00" if save else b"\x21\x00\x00\x01"
    for i, v in enumerate((blocks, left, top, right, bottom)):
        msg[4 + 2 * i:6 + 2 * i] = int(v).to_bytes(2, "little")
    return bytes(msg)


BLANK_PANEL_BGR = b"\x00" * (PANEL_W * PANEL_H * 3)


def set_brightness(hid_dev, percent):
    """Set DisplayPad backlight brightness. percent: 0, 25, 50, 75, or 100."""
    percent = max(0, min(100, int(percent)))
    buf = bytearray(64)
    buf[0] = 0x12
    buf[1] = 0x03
    buf[4] = percent
    hid_dev.write(bytes(buf))


# ── Finding the pad ───────────────────────────────────────────────────────────

def find_interface_path(iface):
    """hidraw path of interface `iface` of the pad, or None. hidapi errors
    pass through; callers that only look decide what an error means."""
    for d in hid_compat.enumerate(VID, PID):
        if d['interface_number'] == iface:
            return d['path']
    return None


def find_command_path():
    """hidraw path of the command interface (3), or None.

    The one enumerate loop the panel had four copies of: the device monitor,
    opening the interfaces, the brightness write and the IF0 key report."""
    return find_interface_path(3)


def open_command_interface():
    """Open interface 3 on its own, for a one-off command (brightness).
    Raises when the pad or the interface is not there."""
    path = find_command_path()
    if path is None:
        raise RuntimeError("DisplayPad Interface 3 not found")
    return hid_compat.open_path(path)


def find_on_bus():
    """The pad as pyusb sees it on the bus, or None. Used to tell "not
    plugged in" from "plugged in but interface 3 missing" (the usbhid
    interface-order quirk, #36). None as well when pyusb is missing."""
    if not PYUSB_AVAILABLE:
        return None
    return usb.core.find(idVendor=VID, idProduct=PID)


# ── USB helpers ───────────────────────────────────────────────────────────────

def open_interfaces():
    if not HID_AVAILABLE:
        raise RuntimeError("hidapi not installed (pip install hid)")
    if not PYUSB_AVAILABLE:
        raise RuntimeError("PyUSB not installed (pip install pyusb)")
    # Force GC so previous Device wrappers are fully freed before find() — prevents
    # libusb refcount assertion when old Python objects haven't been collected yet.
    gc.collect()
    device_path = find_command_path()
    if device_path is None:
        raise RuntimeError("DisplayPad Interface 3 not found")
    # Opening the hidraw node or claiming interface 1 can transiently fail with
    # [Errno 16] Resource busy if a previous owner (the key listener or a prior
    # upload session) hasn't fully let go yet. Retry a couple of times with a
    # short backoff before giving up (issue #26).
    last_err = None
    for attempt in range(3):
        hid_dev = None
        try:
            hid_dev = hid_compat.open_path(device_path)
            hid_dev.nonblocking = False
            usb_dev = usb.core.find(idVendor=VID, idProduct=PID)
            if usb_dev is None:
                hid_dev.close()
                raise RuntimeError("DisplayPad not found via PyUSB")
            usb.util.claim_interface(usb_dev, 1)
            init_handshake_ctrl(usb_dev)
            return usb_dev, hid_dev
        except Exception as e:
            last_err = e
            if hid_dev is not None:
                try:
                    hid_dev.close()
                except Exception:
                    pass
            time.sleep(0.2)
    raise last_err if last_err else RuntimeError("DisplayPad open failed")


KEY_REPORT = bytes([0x03, 0x01])   # output report 3 on IF0: report key events
_if0_reported = False               # an IF0 left without its driver, said once


def key_report_through_driver():
    """Send IF0's SET_REPORT through the HID driver that owns IF0. True if sent.

    IF0 has no OUT endpoint, so usbhid turns an output report written to its
    hidraw node into the SET_REPORT control transfer itself: request 0x21/
    0x09, wValue 0x0203, wIndex 0, data 03 01, byte for byte what the direct
    route below sends after taking the interface away from the driver.
    """
    try:
        path = find_interface_path(0)
    except Exception:
        return False
    if path is None:
        return False
    h = None
    for attempt in range(2):
        try:
            h = hid_compat.open_path(path)
            break
        except Exception:
            # A node the kernel has only just made is root's until udev has
            # applied the rule (#86); a moment later it is the user's.
            if attempt == 0:
                time.sleep(0.2)
    if h is None:
        return False
    try:
        n = h.write(KEY_REPORT)
        return n is None or n > 0
    except Exception:
        return False
    finally:
        h.close()


def init_handshake_ctrl(usb_dev):
    """
    The handshake the pad needs before it reports key presses (measured by
    USB capture on Windows): SET_IDLE (bRequest 0x0A, wValue 0) on IF0, IF1
    and IF3, and SET_REPORT (output report 3, payload {0x03, 0x01}) on IF0.

    IF0 (the keyboard interface) belongs to usbhid, which already sent IF0
    and IF3 their SET_IDLE when it bound them, and it sends the SET_REPORT
    for us when the report is written to IF0's hidraw node. That is the way
    taken: the interface stays with its driver.

    This used to detach usbhid from IF0, send both requests itself and
    attach it again, on every open, so on every page upload. The pad does
    not take that for long. Measured on a DisplayPad here: the third
    re-attach in a session, a few seconds after the second, had the kernel's
    probe of IF0 time out four times over (`usbhid 3-3:1.0: probe with
    driver usbhid failed with error -110`), about twenty seconds in which
    the upload held the device and no page could switch, and IF0 was left
    with no driver until the pad was replugged. Each detach also rebuilt the
    pad's hidraw nodes, which is the node churn behind #86.

    The direct route is what is left for an IF0 that has no driver (it
    lost it, as above), and the fallback should the driver's route fail:
    then the driver is detached as before, and whether it came back is
    checked rather than assumed.

    SET_IDLE on IF1 is sent here: IF1 is ours, claimed by the caller for the
    rest of the session. EPIPE or any other failure of a SET_IDLE only means
    the interface does not support it, and is ignored.
    """
    global _if0_reported

    def _set_idle(iface):
        try:
            usb_dev.ctrl_transfer(0x21, 0x0A, 0x0000, iface, None, timeout=500)
        except Exception:
            pass

    _set_idle(1)  # IF_PIXELS

    try:
        if0_bound = bool(usb_dev.is_kernel_driver_active(0))
    except Exception:
        if0_bound = False
    if if0_bound and key_report_through_driver():
        return

    if if0_bound:
        try:
            usb_dev.detach_kernel_driver(0)
        except Exception:
            pass
    try:
        usb.util.claim_interface(usb_dev, 0)
    except Exception:
        return
    try:
        _set_idle(0)
        _set_idle(3)  # IF_CMD: usbhid's, so normally refused, and harmless
        try:
            usb_dev.ctrl_transfer(0x21, 0x09, 0x0203, 0x0000,
                                  KEY_REPORT, timeout=500)
        except Exception:
            pass
    finally:
        try:
            usb.util.release_interface(usb_dev, 0)
        except Exception:
            pass
    if not if0_bound:
        return
    try:
        usb_dev.attach_kernel_driver(0)
    except Exception:
        pass
    try:
        back = bool(usb_dev.is_kernel_driver_active(0))
    except Exception:
        back = False
    if not back and not _if0_reported:
        _if0_reported = True
        print("[DisplayPad] the pad's keyboard interface (IF0) did not get "
              "its driver back; replugging the pad restores it", flush=True)


def close_interfaces(usb_dev, hid_dev):
    try:
        usb.util.release_interface(usb_dev, 1)
    except Exception:
        pass
    try:
        usb.util.dispose_resources(usb_dev)
    except Exception:
        pass
    try:
        hid_dev.close()
    except Exception:
        pass


def init_device(hid_dev):
    """Bring the DisplayPad up: send INIT_MSG on IF3 and wait for a matching echo

    Send INIT_MSG, then wait up to 500 ms for a reply. The reply is accepted
    once its first 5 bytes echo what we sent. Retry up to 60 times with a
    10 ms gap after a failed write or read — the device can be slow to come
    up after a fresh plug-in (issue #40)."""
    # INIT_MSG here carries a leading HID report-ID byte (0x00) that
    # hid_dev.write() needs; the device's raw 64-byte packet — and the echo
    # it sends back via hid_dev.read() — does not include that byte, so the
    # comparison is offset by one to line up with init.cpp's INIT_MSG[0:5]
    # (0x11 0x80 0x00 0x00 0x01).
    pkt = INIT_MSG
    echo = pkt[1:6]
    ack_received = False
    for _attempt in range(60):
        try:
            hid_dev.write(pkt)
        except Exception:
            time.sleep(0.01)
            continue

        try:
            resp = hid_dev.read(64, timeout=500)
        except Exception:
            resp = None
        if not resp:
            time.sleep(0.01)
            continue

        # Accept if the first 5 bytes echo what we sent.
        if len(resp) >= 5 and bytes(resp[:5]) == echo:
            # The pad acknowledges INIT before its display engine is ready to
            # accept pixel data; streaming immediately after a fresh
            # enumeration timed out the first image write ("Connection timed
            # out", issue #43). A short settle lets the firmware come up.
            time.sleep(0.25)
            ack_received = True
            break

    if not ack_received:
        raise RuntimeError("DisplayPad did not respond to INIT")


def upload_button(usb_dev, hid_dev, key_index, bgr_pixels, key_events=None):
    """Upload a single button image. If key_events list is provided,
    any HID key-event packets (data[0]==0x01) encountered during
    upload are appended to it instead of being discarded."""
    msg = bytearray(IMG_MSG_TEMPLATE)
    msg[5] = key_index
    hid_dev.write(bytes(msg))
    for _ in range(50):
        resp = hid_dev.read(64, timeout=200)
        if resp and resp[0] == 0x21 and resp[1] == 0x00 and resp[2] == 0x00:
            break
        if key_events is not None and resp and len(resp) >= 48 and resp[0] == 0x01:
            key_events.append(list(resp))
    else:
        raise RuntimeError(f"No ready response for key {key_index}")
    payload = bytearray(HEADER_SIZE + PACKET_SIZE)
    payload[HEADER_SIZE:HEADER_SIZE + len(bgr_pixels)] = bgr_pixels
    for i in range(0, len(payload), CHUNK_SIZE):
        usb_dev.write(EP_DISPLAY, bytes(payload[i:i + CHUNK_SIZE]), timeout=2000)
    for _ in range(100):
        resp = hid_dev.read(64, timeout=200)
        if resp and resp[0] == 0x21 and resp[1] == 0x00 and resp[2] == 0xFF:
            return
        if key_events is not None and resp and len(resp) >= 48 and resp[0] == 0x01:
            key_events.append(list(resp))
    raise RuntimeError(f"Transfer not confirmed for key {key_index}")


def upload_panel(usb_dev, hid_dev, left, top, right, bottom, bgr_bytes, key_events=None,
                 save=False):
    """Write a rectangle of the 800 x 240 panel: the header command, then the
    raw BGR pixels (w * h * 3 bytes, row by row, no header of their own) in
    1024-byte chunks to the display endpoint. right/bottom are inclusive.
    Key events met while waiting go into `key_events`, as in upload_button.

    save=True stores the whole panel as the start-up picture in the pad's
    flash (shown from the next plug-in on): the pad echoes `21 02 ..` first and
    answers `21 02 02 00 ff ..` after about 6 s of writing, so the last wait
    is up to 15 s."""
    header = panel_header(left, top, right, bottom, save)
    w, h = right - left + 1, bottom - top + 1
    if len(bgr_bytes) != w * h * 3:
        raise ValueError("a %dx%d rectangle needs %d bytes, got %d"
                         % (w, h, w * h * 3, len(bgr_bytes)))
    hid_dev.write(b"\x00" + header)       # the report id first, as in IMG_MSG_TEMPLATE
    for _ in range(50):
        resp = hid_dev.read(64, timeout=200)
        if resp and resp[0] == 0x21 and ((save and resp[1] == 0x02)
                                         or (not save and resp[1] == 0x00 and resp[2] == 0x00)):
            break
        if key_events is not None and resp and len(resp) >= 48 and resp[0] == 0x01:
            key_events.append(list(resp))
    else:
        raise RuntimeError("No ready response for the panel write")
    data = bytes(bgr_bytes)
    for i in range(0, len(data), CHUNK_SIZE):
        usb_dev.write(EP_DISPLAY, data[i:i + CHUNK_SIZE], timeout=2000)
    # The flash write takes ~5.7 s; wait up to 15 s for it (a key upload waits 2 s at most).
    for _ in range(75 if save else 100):
        resp = hid_dev.read(64, timeout=200)
        if save:
            if resp and resp[0] == 0x21 and resp[1] == 0x02 and resp[2] == 0x02:
                return
        elif resp and resp[0] == 0x21 and resp[1] == 0x00 and resp[2] == 0xFF:
            return
        if key_events is not None and resp and len(resp) >= 48 and resp[0] == 0x01:
            key_events.append(list(resp))
    raise RuntimeError("Panel transfer not confirmed")


# ── Key events ────────────────────────────────────────────────────────────────

def is_key_packet(data):
    """A key-event packet: data[0] == 0x01 and long enough to hold both key
    bytes. Everything else on interface 3 (0x11 init, 0x21 image replies) is
    not about keys."""
    return bool(data) and len(data) >= 48 and data[0] == 0x01


class KeyEdgeDecoder:
    """Which keys went down, packet by packet.

    A key fires on a rising edge only, its bit set in this packet and clear in
    the one before, so a key held through several packets fires once. The
    panel had three copies of this loop; they differ only in how the previous
    packet is kept, which is what `keep_short` says:

    - False (an upload's captured packets): a packet replaces the previous
      one only when it is at least `width` bytes, and then its first `width`.
    - True (the device worker's idle read): every packet replaces it, whole.
    """

    def __init__(self, width=64, keep_short=False):
        self._width = width
        self._keep_short = keep_short
        self.reset()

    def reset(self):
        """Forget the previous packet, as for a fresh session."""
        self._prev = [0] * self._width

    def feed(self, pkt):
        """Indexes (0-11) of the keys that went down in `pkt`, in key order."""
        prev = self._prev
        down = [k for k, (bi, mask) in enumerate(KEY_MAP)
                if bi < len(pkt) and (pkt[bi] & mask) and not (prev[bi] & mask)]
        if self._keep_short:
            self._prev = list(pkt)
        elif len(pkt) >= self._width:
            self._prev = list(pkt[:self._width])
        return down
