#!/usr/bin/env python3
"""Controller for Mountain Everest Max display (time, CPU, numpad icons & buttons)."""
import usb.core
import usb.util
import datetime
import signal
import sys
import os
import time
import json
import subprocess
import threading
import psutil
import pwd as _pwd
from shared.image_utils import image_to_rgb565
from shared.volume import (system_volume as _system_volume,
                           start_watch as _start_volume_watch)
# The packets themselves are built by the core's protocol module, which
# tools/test_everest_max_protocol.py pins byte for byte. This file is the
# pyusb transport and the command line around them.
from core.devices.everest_max import protocol as P
from core.devices.everest_max.protocol import (   # noqa: F401  (module API)
    VID, PID, INTERFACE, EP_OUT, EP_IN, PKT_SIZE,
    STYLE_ANALOG, STYLE_DIGITAL, BTN_LOOKUP, ICON_BASE, ICONS_PER_BTN,
    VOLUME_CMD, ICON_IMG_SIZE, MAIN_IMG_SIZE, MAIN_DISPLAY_W, MAIN_DISPLAY_H,
    MAIN_DISPLAY_MODES, ZONE_LEDS, make_packet, icon_id,
)

_real_home = _pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir if os.environ.get("SUDO_USER") else os.path.expanduser("~")
CONFIG_DIR  = os.path.join(_real_home, ".config", "mountain-time-sync")
STYLE_FILE  = os.path.join(CONFIG_DIR, "style")
BUTTON_FILE = os.path.join(CONFIG_DIR, "buttons.json")

CPU_INTERVAL = 0.2

# ── Helpers ────────────────────────────────────────────────────────────────

_detached = False   # this run took interface 3 away from usbhid


def _claim(dev):
    global _detached
    if dev.is_kernel_driver_active(INTERFACE):
        dev.detach_kernel_driver(INTERFACE)
        _detached = True
    usb.util.claim_interface(dev, INTERFACE)

def _release(dev, expect_gone=False):
    """Hand interface 3 back. `expect_gone` for a handle already known dead.

    A reclaim after a port reset is releasing a handle whose device has just
    re-enumerated, so the release failing is the premise rather than news,
    and saying so would be a warning on every suspend.
    """
    try:
        usb.util.release_interface(dev, INTERFACE)
    except Exception as e:
        # A keyboard that has gone away mid-run cannot be released, and that
        # must not skip the reattach below or escape the caller's `finally`:
        # this is the one place that hands interface 3 back.
        if not expect_gone:
            print(f"interface {INTERFACE} not released: {e}",
                  file=sys.stderr, flush=True)
    # usbhid owns this interface when we are not using it, so hand it back
    # whether or not this process is the one that took it away. A run that
    # was killed before it could release leaves the interface with no driver
    # at all, and the next run then sees nothing to reattach and would leave
    # it that way for good. Reporting a failure matters more than the attempt:
    # silence here is what let that state survive unnoticed.
    if getattr(dev, "_no_reattach", False):
        usb.util.dispose_resources(dev)
        return
    failed = None
    try:
        dev.attach_kernel_driver(INTERFACE)
    except Exception as e:
        failed = e
    if failed is not None and not _driver_back(dev):
        # The attempt failing is not the news; the interface still having no
        # driver is. A port reset re-enumerates the board and the kernel
        # binds it again by itself, so the attach comes back "Resource busy"
        # or "Entity not found" and the interface is nonetheless in the hands
        # it belongs in. Saying so anyway would be a warning after every
        # suspend, and warnings nobody can act on are how the real one got
        # missed for as long as it did.
        print(f"kernel driver not reattached on interface {INTERFACE}: {failed}",
              file=sys.stderr, flush=True)
    usb.util.dispose_resources(dev)


def _driver_back(dev):
    """True if interface 3 has a kernel driver on it, whoever put it there."""
    try:
        return bool(dev.is_kernel_driver_active(INTERFACE))
    except Exception:
        # The handle is dead, which is the re-enumeration case: ask sysfs,
        # which is about the device rather than about this handle.
        try:
            for entry in os.listdir("/sys/bus/usb/devices"):
                path = f"/sys/bus/usb/devices/{entry}:1.{INTERFACE}/driver"
                if entry.count(":") == 0 and os.path.islink(path):
                    vid = open(f"/sys/bus/usb/devices/{entry}/idVendor").read()
                    pid = open(f"/sys/bus/usb/devices/{entry}/idProduct").read()
                    if vid.strip() == f"{VID:04x}" and pid.strip() == f"{PID:04x}":
                        return True
        except OSError:
            pass
        return False

def _get_claimed_device():
    """Find keyboard, claim Interface 3 with retries. Returns dev or exits."""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr); sys.exit(1)
    # Re-find on every try: a handle found while the board is mid
    # re-enumeration (deauthorized, or reset again on a resume) is dead once
    # it comes back, so retrying the claim on it can never succeed.
    for i in range(20):
        try:
            _claim(dev)
            if i:
                print(f"claimed on try {i + 1}", file=sys.stderr, flush=True)
            return dev
        except usb.core.USBError:
            usb.util.dispose_resources(dev)
            time.sleep(0.5)
            try:
                dev = usb.core.find(idVendor=VID, idProduct=PID) or dev
            except usb.core.USBError:
                pass  # still settling, as in _reclaim; next try uses the old one
    # A try that detached usbhid from interface 3 before its claim failed
    # left it with no driver, and nothing after this exit would give it back.
    # Only then, though: refused because the running Monitor Mode holds the
    # interface, it is not ours to touch. _release says so if it cannot; the
    # release itself is expected to fail, nothing is held.
    if _detached:
        _release(dev, expect_gone=True)
    print("Failed to claim interface", file=sys.stderr); sys.exit(1)


def _reclaim(dev):
    """Claim the keyboard again after a USB port reset.

    Suspend/resume resets the port and re-enumerates the board, which leaves
    the claimed interface dead: every write fails from then on.
    """
    try:
        _release(dev, expect_gone=True)
    except Exception:
        pass
    for _ in range(20):
        try:
            if usb.core.find(idVendor=VID, idProduct=PID) is not None:
                break
        except usb.core.USBError:
            pass  # still settling
        time.sleep(0.5)
    dev = _get_claimed_device()  # exits if the keyboard really went away
    try:
        dev.write(EP_OUT, P.init_packet()); _read(dev)
        dev.write(EP_OUT, P.keepalive_packet()); _read(dev)
        print("USB reclaimed after device reset", file=sys.stderr, flush=True)
    except usb.core.USBError as e:
        # Board back but not awake yet: keep the handle, the loop retries.
        print(f"re-init after reclaim failed: {e}", file=sys.stderr, flush=True)
    return dev

_EMPTY_PKT = bytes(PKT_SIZE)

def _read(dev, timeout=1000):
    """Read one packet, return it. Returns empty packet on timeout."""
    try:
        data = dev.read(EP_IN, PKT_SIZE, timeout=timeout)
        return data
    except usb.core.USBTimeoutError:
        return _EMPTY_PKT

# ── System volume ──────────────────────────────────────────────────────────
#
# The wheel display's Volume mode (0x71) shows whatever the host last pushed
# over `11 83 00 00 <level>` (VOLUME_CMD). The firmware does not track the
# system level on its own — its wheel is an ordinary HID consumer control — so
# without this the readout drifts from the real volume and ignores any change
# made elsewhere.

# ── Protocol ───────────────────────────────────────────────────────────────

def _read_clock_format():
    try:
        with open(os.path.join(CONFIG_DIR, "clock_format")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "24H"


def _send_time_packet(dev, style):
    now = datetime.datetime.now()
    for pkt in P.time_packets(now, _read_clock_format(), style):
        dev.write(EP_OUT, pkt)
        _read(dev)

def _send_cpu_packet(dev, cpu_percent):
    dev.write(EP_OUT, make_packet(0x11, 0x14))
    _read(dev)
    dev.write(EP_OUT, make_packet(0x11, 0x81, 0x00, 0x00, int(cpu_percent)))
    _read(dev)

def _set_icon(dev, button_idx, variant):
    """Set icon for one numpad button."""
    select, request = P.icon_packets(button_idx, variant)
    dev.write(EP_OUT, select)
    try:
        _read(dev)
    except usb.core.USBTimeoutError:
        pass
    dev.write(EP_OUT, request)
    # Drain all response chunks
    while True:
        try:
            chunk = dev.read(EP_IN, PKT_SIZE, timeout=200)
            if chunk[0] == 0x01:
                return  # button event, stop draining
            # last chunk has counter byte == 0
            if len(chunk) >= 5 and chunk[4] == 0x00:
                break
        except usb.core.USBTimeoutError:
            break

_action_type_byte = P.action_type_byte     # 0x02 = URL, 0x04 = everything else


def _write_action(dev, button_idx, command, action_type=0x04):
    """Write one button's action into the keyboard's flash.

    12 08 00 [btn+1] selects the slot (the byte-42 mechanism), 17 AA writes
    the action, cut to 55 bytes.
    action_type: 0x02 = URL/browser, 0x04 = shell/folder/none
    """
    select, pkt = P.action_packets(button_idx, command, action_type)
    dev.write(EP_OUT, select)
    try:
        _read(dev)
    except usb.core.USBTimeoutError:
        pass
    dev.write(EP_OUT, pkt)
    try:
        _read(dev)
    except usb.core.USBTimeoutError:
        pass


def _ctrl_set_report(dev, data):
    """Send HID Feature SET_REPORT (64 bytes) on interface 3."""
    pkt = bytearray(64)
    pkt[:len(data)] = data
    dev.ctrl_transfer(0x21, 0x09, 0x0300, INTERFACE, bytes(pkt), timeout=3000)

def _ctrl_get_report(dev):
    """Read HID Feature GET_REPORT (64 bytes) on interface 3."""
    return bytes(dev.ctrl_transfer(0xA1, 0x01, 0x0300, INTERFACE, 64, timeout=3000))

def _erase_session(dev, session_num, sectors=(0x01, 0x02)):
    """One erase session — per-sector protocol confirmed from USB captures.

    Sends a separate 21 XX command for each sector and polls until 80 fa.
    sectors: tuple of (sector1_cmd, sector2_cmd) — depends on button index.
    """
    for sector_cmd in sectors:
        _ctrl_set_report(dev, P.erase_sector_packet(sector_cmd))
        _ctrl_get_report(dev)
        for _ in range(100):
            time.sleep(0.030)
            _ctrl_set_report(dev, P.ERASE_POLL)
            r = _ctrl_get_report(dev)
            if P.erase_done(r):
                break
        _ctrl_set_report(dev, P.ERASE_END)
        _ctrl_get_report(dev)

def _upload_icon_image(dev, button_idx, img_bytes):
    """Upload 72×72 RGB565 LE image to numpad button via HID Feature Reports.

    Protocol (reverse-engineered from USB capture_icon_change.bin):
      Two erase sessions (21 01/21 02 per sector, 5.5s gap between sessions)
      aa55 2104 / aa55 2200 handshake
      aa55 1080 2800 [chk_lo chk_hi] 0000 0201 [btn_idx] descriptor
      Stream 64-byte chunks at 30ms intervals until all 10368 bytes committed
    """
    if len(img_bytes) != ICON_IMG_SIZE:
        raise ValueError(
            f"Icon payload must be {ICON_IMG_SIZE} bytes, got {len(img_bytes)}")

    # Init interrupt endpoint
    dev.write(EP_OUT, P.init_packet())
    try: dev.read(EP_IN, PKT_SIZE, timeout=1000)
    except Exception: pass
    for _ in range(3):
        dev.write(EP_OUT, P.keepalive_packet())
        try: dev.read(EP_IN, PKT_SIZE, timeout=500)
        except Exception: pass
        time.sleep(0.05)

    _ctrl_get_report(dev)  # initial state poll
    print("PROGRESS:5", flush=True)

    # Sector numbers depend on button index: D1→(1,2), D2→(3,4), D3→(5,6), D4→(7,8)
    sectors = P.icon_sectors(button_idx)
    # Two erase sessions with 5.5s gap (from capture)
    _erase_session(dev, 1, sectors)
    print("PROGRESS:20", flush=True)
    for _i in range(11):  # 5.5s gap, report progress every 0.5s
        time.sleep(0.5)
        print(f"PROGRESS:{22 + _i * 2}", flush=True)  # 22..42
    _erase_session(dev, 2, sectors)
    time.sleep(2.0)
    print("PROGRESS:55", flush=True)
    # Handshake + descriptor, retry once if descriptor poll times out
    desc = P.icon_descriptor(img_bytes, button_idx)
    for _try in range(3):
        _ctrl_set_report(dev, P.ICON_SESSION)
        _ctrl_get_report(dev)
        _ctrl_set_report(dev, P.SLOT_PREPARE)
        _ctrl_get_report(dev)
        _ctrl_set_report(dev, desc)
        time.sleep(0.130)
        r = _ctrl_get_report(dev)
        if P.committed(r):
            break
        for _poll in range(100):
            time.sleep(0.150)
            r = _ctrl_get_report(dev)
            if P.committed(r):
                break
        else:
            if _try < 2:
                time.sleep(3.0)
                continue
            raise RuntimeError("Descriptor never became ready (fa)")
        break
    print("PROGRESS:60", flush=True)

    # Stream chunks: resend same chunk until committed (fa), then advance offset.
    # This matches Windows behavior: only advance to next chunk after fa confirms commit.
    target = ICON_IMG_SIZE
    next_offset = 0
    for _ in range(600):
        chunk = img_bytes[next_offset:next_offset + P.CHUNK]
        _ctrl_set_report(dev, chunk)
        time.sleep(0.030)
        r = _ctrl_get_report(dev)
        if P.committed(r):
            next_offset = P.committed_offset(r)
            pct = 60 + int(next_offset * 40 / target)
            print(f"PROGRESS:{pct}", flush=True)
            if next_offset >= target:
                break
        # if fb: don't advance next_offset, retry same chunk next iteration

    # Wait for display update signal on interrupt endpoint (max 4s)
    dev.write(EP_OUT, P.keepalive_packet())
    for _ in range(20):
        try:
            r = bytes(dev.read(EP_IN, PKT_SIZE, timeout=200))
            if P.display_updated(r):
                break
        except usb.core.USBTimeoutError:
            pass

def _upload_main_display_image(dev, img_bytes, activate=False):
    """Upload a 240×204 RGB565 LE image to the main display.

    Protocol: 21 03 session → 22 00 slot → descriptor → chunk stream.
    No mode switch here — the GUI sends 'main-mode image' before calling
    this, which handles the mode switch + its EP_IN freeze. By the time
    the chunks finish (~50s), the freeze is already over.
    Image appears automatically after chunks commit (per protocol doc §6.6).

    activate=True: after chunks, send 13 41 00 00 00 to explicitly activate
    the custom image slot (needed after 13 41 00 00 01 dial-reset).
    """
    if len(img_bytes) != MAIN_IMG_SIZE:
        raise ValueError(
            f"Main display payload must be {MAIN_IMG_SIZE} bytes, got {len(img_bytes)}")
    print("PROGRESS:5", flush=True)

    # Session open → slot prepare
    _ctrl_set_report(dev, P.MAIN_SESSION)
    _ctrl_get_report(dev)
    _ctrl_set_report(dev, P.SLOT_PREPARE)
    _ctrl_get_report(dev)

    # Descriptor: compute actual checksum (sum of all bytes, 16-bit LE)
    desc = P.main_descriptor(img_bytes)
    _ctrl_set_report(dev, desc)
    for _ in range(5):
        time.sleep(0.130)
        r = _ctrl_get_report(dev)
        if P.committed(r):
            break
        _ctrl_set_report(dev, desc)
    else:
        raise RuntimeError("Main display descriptor never became ready (fa)")
    print("PROGRESS:10", flush=True)

    # Stream chunks: fb = busy (retry same chunk), fa = committed (advance)
    target = MAIN_IMG_SIZE
    next_offset = 0
    fa_count = 0
    for _ in range(4000):
        chunk = img_bytes[next_offset:next_offset + P.CHUNK]
        _ctrl_set_report(dev, chunk)
        time.sleep(0.030)
        r = _ctrl_get_report(dev)
        if P.committed(r):
            fa_count += 1
            next_offset = fa_count * P.CHUNK
            pct = 10 + int(next_offset * 90 / target)
            print(f"PROGRESS:{pct}", flush=True)
            if next_offset >= target:
                break
        # fb: retry same chunk (don't advance)



def _read_action(dev, button_idx):
    """Liest die gespeicherte Aktion aus dem Keyboard-Flash zurück."""
    iid = icon_id(button_idx, 7)  # Standard-Variant
    dev.write(EP_OUT, make_packet(0x11, 0x00))
    try:
        _read(dev)
    except usb.core.USBTimeoutError:
        return ""
    dev.write(EP_OUT, make_packet(0x11, 0x02, 0x00, 0x01, 0x01, iid, 0x02))
    while True:
        try:
            chunk = dev.read(EP_IN, PKT_SIZE, timeout=200)
            # Aktions-Response: 11 02 00 01 00 [len] 00 [type] [data]
            if chunk[0] == 0x11 and len(chunk) >= 9 and chunk[4] == 0x00:
                action_type = chunk[7]
                if action_type == 0x04:  # Pfad/Befehl
                    data_len = chunk[5]
                    raw = bytes(chunk[8:8 + data_len - 1])
                    return raw.rstrip(b'\xff\x00').decode('utf-8', errors='replace')
                break
            if chunk[0] == 0x01:
                break
        except usb.core.USBTimeoutError:
            break
    return ""

# ── Config ─────────────────────────────────────────────────────────────────

def read_style():
    try:
        with open(STYLE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "analog"

def read_buttons():
    """Returns list of 4 dicts: {icon: 0-8, action: str, type: shell|url|folder|none}"""
    default = [dict(P.DEFAULT_BUTTON) for _ in range(4)]
    try:
        with open(BUTTON_FILE) as f:
            data = json.load(f)
        for i in range(4):
            if i < len(data):
                default[i].update(data[i])
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return default

# ── High-level entry points ─────────────────────────────────────────────────

def send_time(style=STYLE_ANALOG):
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    for _attempt in range(20):
        try:
            _claim(dev)
            break
        except usb.core.USBError:
            if _attempt < 19:
                time.sleep(1.0)
            else:
                raise
    try:
        for _ in range(3):
            dev.write(EP_OUT, P.keepalive_packet())
            try: dev.read(EP_IN, PKT_SIZE, timeout=1000)
            except Exception: pass
        _send_time_packet(dev, style)
    finally:
        _release(dev)


def set_main_display_mode(mode, style=STYLE_ANALOG):
    """Switch the main display mode (image, clock, cpu, gpu, hd, network, ram, apm).

    Protocol: read FW_EXTEND_INFO (`11 14`) and write it back (`11 14 00 01`)
    with byte 6's high nibble set from MAIN_DISPLAY_MODES, everything else as
    read. Without an answer: the fixed packet with menu byte (b10) set to the
    mode value from MAIN_DISPLAY_MODES. stderr says which.
    """
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    # Retry claim up to 60s — kernel may auto-rebind HID driver during post-upload
    # flash write freeze (~32s) or after mode-switch EP_IN freeze (~32s)
    for _attempt in range(60):
        try:
            _claim(dev)
            break
        except usb.core.USBError:
            if _attempt < 59:
                time.sleep(1.0)
            else:
                raise
    try:
        # 11 12 re-init: resets keyboard state after kernel HID driver init packets
        # (safe after upload is fully done — prohibited only before/during upload)
        dev.write(EP_OUT, P.init_packet())
        try: dev.read(EP_IN, PKT_SIZE, timeout=500)
        except Exception: pass
        time.sleep(0.1)

        # Send keepalives to stabilise before mode switch. Their answers are
        # the keyboard's FW_EXTEND_INFO too.
        replies = []
        for _ in range(3):
            dev.write(EP_OUT, P.keepalive_packet())
            try: replies.append(bytes(dev.read(EP_IN, PKT_SIZE, timeout=300)))
            except Exception: pass
            time.sleep(0.1)

        # Read keyboard's current 11 14 state
        dev.write(EP_OUT, P.keepalive_packet())
        try:
            info = bytearray(dev.read(EP_IN, PKT_SIZE, timeout=1000))
        except Exception:
            info = bytearray(PKT_SIZE)
        replies.append(bytes(info))

        # Read-modify-write: the latest answer with only the display (byte 6's
        # high nibble) changed, so the dial keeps its menus, colour and
        # timers. A keyboard that does not answer gets the fixed packet with
        # the menu byte (b10) from MAIN_DISPLAY_MODES, as before.
        base = next((P.extend_body(r) for r in reversed(replies)
                     if P.is_extend_reply(r) and any(P.extend_body(r))), None)
        if base is not None:
            pkt = P.extend_write_packet(P.display_mode_body(base, mode))
            print(f"main-mode {mode}: read-modify-write of 11 14 (only the display changes)",
                  file=sys.stderr, flush=True)
        else:
            pkt = P.mode_switch_packet(P.device_bytes(info), mode)
            print(f"main-mode {mode}: no answer to 11 14, the fixed packet",
                  file=sys.stderr, flush=True)
        dev.write(EP_OUT, pkt)
        try: dev.read(EP_IN, PKT_SIZE, timeout=500)
        except Exception: pass

        # Note: 0x13 0x41 0x00 0x00 0x01 is NOT an activation command —
        # it resets the dial to the factory Mountain logo. Do NOT send it here.
    finally:
        _release(dev)


def set_rgb(effect, speed=50, brightness=100, color1=(255, 0, 0), color2=(0, 0, 255), direction=0):
    """Set keyboard RGB lighting effect via 0x14 0x2c command."""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    for _attempt in range(5):
        try:
            _claim(dev)
            break
        except usb.core.USBError:
            if _attempt < 4:
                time.sleep(1.0)
            else:
                raise
    try:
        # Speed: GUI 1–100 (100=fast) → hardware 1–100 (1=fast), inverted there
        pkt = P.rgb_packet(effect, speed, brightness, color1, color2, direction)
        # Sent twice: coming from custom mode (per-key colours) the keyboard
        # answers the first one but stays in custom mode; the second switches
        # (seen on an Everest Max, 2026-11-23). Out of a normal effect the
        # second packet changes nothing.
        for n in range(2):
            dev.write(EP_OUT, pkt)
            try: dev.read(EP_IN, PKT_SIZE, timeout=1000)
            except Exception: pass
            if n == 0:
                time.sleep(0.150)   # as long as entering custom mode takes
    finally:
        _release(dev)


def _write_custom_rgb(dev, leds, side, brightness, effects=None, effect_params=None,
                      in_custom=False):
    """The custom-colour sequence (§5.11): mode, 8 key packets, 3 ring
    packets, 3 bind packets; each answered, 20 ms apart.

    With an effect on any key (protocol.custom_rgb_packets): the table
    carries the effects, the effects' parameters follow, then the custom slot
    is saved and custom mode entered again. Returns True when the sequence
    saved the slot (`13 55 00 00 06`)."""
    pkts = P.custom_rgb_packets(leds, side, brightness, effects, effect_params)
    # Coming from breathing the keyboard answers the first `14 2c 0a` (custom
    # mode) but stays in breathing and takes the colours into the custom layer
    # without showing them; a second one switches (seen on an Everest Max,
    # 2026-11-23, as with set_rgb the other way round). Already in custom
    # mode (`in_custom`, Individuell writing on every change) one is enough:
    # each `14 2c 0a` makes the whole keyboard blink once.
    if not in_custom:
        pkts = [pkts[0]] + pkts
    saved = False
    for n, pkt in enumerate(pkts):
        is_save = pkt[:2] == b"\x13\x55"
        dev.write(EP_OUT, pkt)
        try: dev.read(EP_IN, PKT_SIZE, timeout=300 if is_save else 200)
        except Exception: pass
        time.sleep(0.020)
        if n <= (0 if in_custom else 1):
            time.sleep(0.150)  # the keyboard needs time to enter custom mode
        if is_save:
            saved = True
            time.sleep(0.600)  # the flash write (Mountain's tools wait as long)
        elif saved:
            time.sleep(0.150)  # custom mode again, as after the first packet
    return saved


def set_custom_rgb(zone_colors, side_color=(0, 0, 0), brightness=100):
    """Set keyboard zones to individual colors via custom mode (§5.11).
    zone_colors: dict of zone_name -> (r, g, b). Unspecified zones = black.
    side_color: (r, g, b) for all 45 side ring LEDs.
    """
    # LED color array: 8 packets × 19 slots = 152 entries
    leds = P.zone_leds(zone_colors)

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    for _attempt in range(5):
        try:
            _claim(dev)
            break
        except usb.core.USBError:
            if _attempt < 4:
                time.sleep(1.0)
            else:
                raise
    try:
        _write_custom_rgb(dev, leds, [side_color] * P.NUM_SIDE_LEDS, brightness)
    finally:
        _release(dev)


def set_per_key_rgb(led_colors, side_colors=None, brightness=100, effects=None,
                    effect_params=None, in_custom=False):
    """Set per-key RGB colors via custom mode (§5.11).
    led_colors: list of (r,g,b) indexed by color index 0–125 (see §11.1).
    side_colors: list of (r,g,b) for 45 side ring LEDs, or None for black.
    effects: an effect name per key (protocol.PER_KEY_EFFECTS), or None;
    effect_params: {name: settings} for the effects used.
    Returns True when the custom slot was saved on the way (effects).
    """
    leds = P.key_leds(led_colors)       # padded to 152 (8 packets × 19 slots)
    side = P.side_leds(side_colors)

    dev = _get_claimed_device()
    try:
        return _write_custom_rgb(dev, leds, side, brightness, effects, effect_params,
                                 in_custom)
    finally:
        _release(dev)


# ── Media dock settings and firmware versions ──────────────────────────────

def _read_extend(dev, tries=3):
    """The 29 bytes of the keyboard's answer to `11 14`, or None when it does
    not answer (or answers all zeros, which is no answer either)."""
    for _ in range(tries):
        dev.write(EP_OUT, P.keepalive_packet())
        for _ in range(4):
            r = bytes(_read(dev, timeout=300))
            if P.is_extend_reply(r):
                body = P.extend_body(r)
                if any(body):
                    return body
                break
            if not any(r):
                break           # a timeout: ask again
    return None


DOCK_CHANGE_KEYS = ("menus", "color", "screensaver", "turn_off", "displays", "content")


def dock_settings(changes=None):
    """Read the media dock's settings (FW_EXTEND_INFO) and, with `changes`
    ({menus, color, screensaver, turn_off, displays, content}, see
    protocol.apply_dock_settings),
    write them back with only those fields changed. Prints {"before", "raw"}
    as hex on stdout. Returns the exit code: 1 when the keyboard did not
    answer the read, and then nothing is written."""
    dev = _get_claimed_device()
    try:
        body = _read_extend(dev)
        if body is None:
            print("the keyboard did not answer 11 14; nothing written",
                  file=sys.stderr, flush=True)
            return 1
        out = {"before": body.hex()}
        if changes:
            new = P.apply_dock_settings(body, **{k: changes[k] for k in DOCK_CHANGE_KEYS
                                                 if k in changes})
            dev.write(EP_OUT, P.extend_write_packet(new))
            try: dev.read(EP_IN, PKT_SIZE, timeout=500)      # the echo
            except Exception: pass
            body = new
        out["raw"] = body.hex()
        print(json.dumps(out), flush=True)
        return 0
    finally:
        _release(dev)


# ── Settings page: game mode and indicator LEDs ───────────────────────────

def _read_setting(dev, sub, tries=3):
    """The value of one of the page's settings (`12 <sub>`), or None when the
    keyboard does not answer. Other packets (key reports, echoes) are skipped."""
    for _ in range(tries):
        dev.write(EP_OUT, P.setting_read_packet(sub))
        for _ in range(6):
            r = bytes(_read(dev, timeout=300))
            if P.is_setting_reply(r, sub):
                return P.setting_value_of(r, sub)
            if not any(r):
                break           # a timeout: ask again
    return None


def _board_writes(mask, leds, changes):
    """[(sub, value)] the changes need; a value the keyboard has already is not
    written again."""
    out = []
    if changes.get("game_mode"):
        new = P.apply_game_mode(mask, changes["game_mode"])
        if new != mask:
            out.append((P.SETTING_GAME_MODE, new))
    if changes.get("leds") is not None and int(bool(changes["leds"])) != leds:
        out.append((P.SETTING_INDICATOR_LEDS, int(bool(changes["leds"]))))
    return out


def board_settings(changes=None):
    """Read the game mode mask and the indicator LEDs and, with `changes`
    ({game_mode: {key: bool}, leds: bool}), write them with only those keys
    changed (the other game mode bits stay as read). Prints
    {"game_mode": mask, "leds": 0|1} as read back (the written value when the
    read-back stays silent). Returns the exit code: 1 when the keyboard did
    not answer the reads, and then nothing is written."""
    dev = _get_claimed_device()
    try:
        mask = _read_setting(dev, P.SETTING_GAME_MODE)
        leds = _read_setting(dev, P.SETTING_INDICATOR_LEDS)
        if mask is None or leds is None:
            print("the keyboard did not answer 12 06 / 12 07; nothing written",
                  file=sys.stderr, flush=True)
            return 1
        for sub, value in _board_writes(mask, leds, changes or {}):
            dev.write(EP_OUT, P.setting_write_packet(sub, value))
            _read(dev, timeout=500)             # the echo
            back = _read_setting(dev, sub)
            if sub == P.SETTING_GAME_MODE:
                mask = value if back is None else back
            else:
                leds = value if back is None else back
        print(json.dumps({"game_mode": mask, "leds": leds}), flush=True)
        return 0
    finally:
        _release(dev)


# ── Key remapping (Tastenbelegung) ─────────────────────────────────────────

def remap_keys(entries):
    """Write key remaps in one claim: each entry is {src, target} (`14 20`),
    {src, key, mods} (`14 21`) or {src, macro} (a key macro), see
    protocol.remap_packets. Every packet's echo is read. Prints {"sent",
    "echoed"} as JSON on stdout. Returns the exit code: 2 for an entry that
    is not one (nothing written), 1 when the keyboard echoed none."""
    try:
        pkts = [p for e in entries for p in P.remap_packets(e)]
    except ValueError as e:
        print("remap: %s; nothing written" % e, file=sys.stderr, flush=True)
        return 2
    dev = _get_claimed_device()
    try:
        echoed = 0
        for pkt in pkts:
            dev.write(EP_OUT, pkt)
            for _ in range(3):
                r = bytes(_read(dev, timeout=300))
                if P.is_remap_echo(r, pkt):
                    echoed += 1
                    break
                if not any(r):
                    break           # a timeout: no echo for this one
        print(json.dumps({"sent": len(pkts), "echoed": echoed}), flush=True)
        if pkts and not echoed:
            print("the keyboard did not answer 14 20 / 14 21", file=sys.stderr, flush=True)
            return 1
        return 0
    finally:
        _release(dev)


def _feature(dev, data):
    """One feature report out, its answer back."""
    _ctrl_set_report(dev, data)
    return _ctrl_get_report(dev)


def _feature_info(dev):
    """`aa 55 80` until it answers `80 fa` (a few tries), the last answer."""
    r = b""
    for i in range(5):
        r = _feature(dev, P.DEVICE_INFO)
        if P.parse_device_info(r):
            break
        time.sleep(0.030)
    return r


def read_versions():
    """Firmware of the keyboard (and its bootloader), the media dock and the
    numpad over feature reports, printed as JSON (numbers or null). Only
    while no image upload runs: the same channel carries them, and the
    service sees to that."""
    out = {"keyboard": None, "bootloader": None, "dock": None, "numpad": None}
    dev = _get_claimed_device()
    try:
        try:
            info = P.parse_device_info(_feature_info(dev))
            if info:
                out["keyboard"], out["bootloader"] = info["firmware"], info["bootloader"]
        except usb.core.USBError as e:
            print(f"device info: {e}", file=sys.stderr, flush=True)
        for name, n in P.SUB_DEVICES:
            try:
                _feature(dev, P.sub_device_packet(n))
                out[name] = P.sub_device_firmware(_feature_info(dev))
            except usb.core.USBError as e:
                print(f"{name} info: {e}", file=sys.stderr, flush=True)
            finally:
                try:
                    _feature(dev, P.SUB_DEVICE_DESELECT)
                except usb.core.USBError:
                    pass
    finally:
        _release(dev)
    print(json.dumps(out), flush=True)


def reset_dial_image():
    """Reset the dial image to the factory Mountain logo (protocol §3.4)."""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        dev.write(EP_OUT, P.reset_dial_packet())
        try: dev.read(EP_IN, PKT_SIZE, timeout=1000)
        except Exception: pass
    finally:
        _release(dev)


FACTORY_RESET_WAIT = 15.0   # seconds; the keyboard echoes after about 3.3 s


def factory_reset():
    """The keyboard's own factory reset (ResetFlash, `13 40 00 00 01`).

    Waits for the keyboard's echo of the packet; other packets (key reports)
    that arrive meanwhile are skipped. Exits 1 without an echo.
    """
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        dev.write(EP_OUT, P.factory_reset_packet())
        end = time.monotonic() + FACTORY_RESET_WAIT
        while time.monotonic() < end:
            try:
                data = dev.read(EP_IN, PKT_SIZE, timeout=500)
            except usb.core.USBTimeoutError:
                continue
            except Exception:
                time.sleep(0.1)
                continue
            if P.is_factory_reset_reply(data):
                return
        print("The keyboard did not confirm the factory reset (no answer in %d s)"
              % FACTORY_RESET_WAIT, file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        _release(dev)


def upload_main_display(image_path, frame=0, activate=False):
    """Upload a custom image (PNG/JPG/GIF) to the main 240×204 OLED display."""
    img_bytes = image_to_rgb565(image_path, size=(MAIN_DISPLAY_W, MAIN_DISPLAY_H), frame=frame)
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        _upload_main_display_image(dev, img_bytes, activate=activate)
        dev._no_reattach = True   # the upload resets the port, usbhid returns by itself
    finally:
        _release(dev)


def upload_icon(button_idx, image_path, frame=0):
    """Upload a custom image (PNG/JPG/GIF frame) to a numpad button display."""
    img_bytes = image_to_rgb565(image_path, frame=frame)
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        _upload_icon_image(dev, button_idx, img_bytes)
    finally:
        # Release interface before USB reset to restore numpad
        _release(dev)

def set_icon_once(button_idx, variant, action=None, action_type=0x04):
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        _set_icon(dev, button_idx, variant)
        if action is not None:
            _write_action(dev, button_idx, action, action_type=action_type)
    finally:
        _release(dev)

def _load_obs_config():
    obs_file = os.path.join(CONFIG_DIR, "obs.json")
    default = {"host": "localhost", "port": 4455, "password": "",
               "buttons": [{"type": "none", "scene": ""} for _ in range(4)]}
    try:
        with open(obs_file) as f:
            data = json.load(f)
        for k in ("host", "port", "password"):
            if k in data:
                default[k] = data[k]
        for i in range(4):
            if i < len(data.get("buttons", [])):
                default["buttons"][i].update(data["buttons"][i])
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return default

def _execute_obs_action(btn_cfg, obs_cfg, obs_holder):
    """Execute OBS WebSocket action. obs_holder is a list [client] for mutability."""
    action_type = btn_cfg.get("type", "none")
    if action_type == "none":
        return
    try:
        import obsws_python as obs
        if obs_holder[0] is None:
            obs_holder[0] = obs.ReqClient(
                host=obs_cfg["host"], port=obs_cfg["port"],
                password=obs_cfg.get("password", ""), timeout=3)
        cl = obs_holder[0]
        if action_type == "scene":
            cl.set_current_program_scene(btn_cfg.get("scene", ""))
        elif action_type == "record":
            cl.toggle_record()
        elif action_type == "stream":
            cl.toggle_stream()
    except Exception as e:
        print(f"OBS error: {e}", flush=True)
        obs_holder[0] = None  # reconnect next time

def _launch(btype, action):
    """Start a shell/app command, or open a url/folder, for a D-key.

    Under sudo the program runs as the person logged in, with their session
    bus and display, not as root. clean_child_env() strips the
    AppImage/PyInstaller library-path injection (issue #11).
    """
    from shared.macros import clean_child_env, user_scope_prefix
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        uid = _pwd.getpwnam(sudo_user).pw_uid
        runtime = f"/run/user/{uid}"
        env = {
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
            "XDG_RUNTIME_DIR": runtime,
            "HOME": _pwd.getpwnam(sudo_user).pw_dir,
            "USER": sudo_user,
            "LOGNAME": sudo_user,
            "PATH": clean_child_env().get("PATH", "/usr/bin:/bin"),
        }
        if os.path.exists(os.path.join(runtime, "wayland-0")):
            env["WAYLAND_DISPLAY"] = "wayland-0"
            env["XDG_SESSION_TYPE"] = "wayland"
        else:
            env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            env["XAUTHORITY"] = os.path.join(
                _pwd.getpwnam(sudo_user).pw_dir, ".Xauthority")
        if btype in ("url", "folder"):
            subprocess.Popen(["sudo", "-u", sudo_user, "-E", "xdg-open", action], env=env)
        else:
            subprocess.Popen(["sudo", "-u", sudo_user, "-E"]
                             + user_scope_prefix(env) + ["bash", "-c", action], env=env)
    else:
        child_env = clean_child_env()
        if btype in ("url", "folder"):
            subprocess.Popen(["xdg-open", action], env=child_env)
        else:
            subprocess.Popen(user_scope_prefix(child_env)
                             + ["bash", "-c", action], env=child_env)


OWNER = "everest_max"


def make_button_dispatcher(obs_cfg, obs_holder, send=None):
    """The D-keys' action dispatcher: the application's one key action
    dispatcher (core/actions.py), set up for this daemon, a process of its own.

    - A page switch and a key redefinition go to the application over the
      control socket (issues #16, #18, #20): `send(command)`.
    - OBS goes through this daemon's own connection (obs.json).
    - Macros run on a MacroRunner of its own; a toggle macro is stopped by
      the next press of an Everest Max key bound to it.
    - Programs are started with this daemon's sudo and user-session handling
      (_launch).
    - Plugin action types are not available in this process, so a key bound
      to one does nothing. It used to run the value, written for the plugin,
      as a shell command.
    """
    from core.actions import ActionDispatcher, ActionEnv
    from core.macros.service import MacroRunner

    if send is None:
        def send(command):
            from shared.ipc import send_command
            return send_command(command)

    class DaemonEnv(ActionEnv):
        def run_shell(self, command):
            _launch("shell", command)

        def open_target(self, target):
            _launch("url", target)

    def execute_obs(kind, scene=""):
        btn = {"type": "scene", "scene": scene} if kind == "scene" else {"type": kind}
        threading.Thread(target=_execute_obs_action, args=(btn, obs_cfg, obs_holder),
                         daemon=True).start()

    def page(value):
        if value:
            send({"cmd": "page", "page": value})

    def set_key(value):
        try:
            d = json.loads(value)
        except (TypeError, ValueError):
            return
        if isinstance(d, dict):
            d["cmd"] = "set_key"
            send(d)

    dispatcher = ActionDispatcher(macros=MacroRunner(), env=DaemonEnv(),
                                  obs=lambda: execute_obs)
    dispatcher.register("page", page, owner=OWNER)
    dispatcher.register("set_key", set_key, owner=OWNER)
    return dispatcher


def run_button_action(dispatcher, btype, action):
    """Run one D-key action (issue #17 runs several in a row). Returns the
    dispatcher's result code, or None for a key with nothing to do."""
    if not btype or btype == "none" or not action:
        return None
    result = dispatcher.run(btype, action, owner=OWNER)
    if result == "macro_missing":
        print(f"[Macro] UUID '{action}' not found", flush=True)
    elif result in ("macro_started", "macro_stopped"):
        print(f"[Macro] {result[6:].capitalize()} '{action}'", flush=True)
    return result


def controller_loop(style=STYLE_ANALOG):
    """Main loop: CPU display + time sync + numpad button monitoring."""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard not found!", file=sys.stderr)
        sys.exit(1)
    for _attempt in range(10):
        try:
            _claim(dev)
            break
        except usb.core.USBError:
            if _attempt < 9:
                time.sleep(0.5)
            else:
                raise
    try:
        # Init
        dev.write(EP_OUT, P.init_packet())
        _read(dev)
        dev.write(EP_OUT, P.keepalive_packet())
        _read(dev)

        # Set the icons and write the actions into the keyboard's flash.
        buttons = read_buttons()
        for i, btn in enumerate(buttons):
            _set_icon(dev, i, btn["icon"])
            btype = btn.get("type", "shell")
            action = btn.get("action", "").strip()
            # The byte-42 fix: the keyboard only reports a press (byte 42) for
            # a key with something in its flash, so "none" or an empty action
            # writes ":" (no effect, but the key reports).
            cmd, type_byte = P.startup_flash_entry(btype, action)
            _write_action(dev, i, cmd, action_type=type_byte)

        last_time_sync = 0
        last_config_check = 0
        last_clock_format = None
        last_btn_state = None
        last_btn_action_time = 0
        obs_cfg = _load_obs_config()
        obs_holder = [None]
        # What a D-key does: the shared key action dispatcher (core/actions.py).
        _buttons_dispatcher = make_button_dispatcher(obs_cfg, obs_holder)
        psutil.cpu_percent()
        _start_volume_watch()
        _net_prev = psutil.net_io_counters()
        _net_prev_time = time.monotonic()
        _gpu_cache = 0
        _gpu_last = 0
        _ram_cache = 0
        _hdd_cache = 0
        _slow_last = 0  # tracks last update time for RAM/HDD
        # Smoothed metric values (EMA, alpha=0.2)
        _smooth = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
        _usb_fail = 0
        # BOOTTIME counts time spent suspended, MONOTONIC does not.
        _asleep = time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()

        def _run_action(btype, action):
            """Execute one button action by type. Issue #17 lets several run in
            sequence; issue #16/#18 add page-switch and key-redefine that reach
            the GUI over the control socket (issue #20)."""
            run_button_action(_buttons_dispatcher, btype, action)

        def _handle_btn_resp(resp):
            nonlocal last_btn_state, last_btn_action_time
            if resp[0] != 0x01:
                return
            pressed = BTN_LOOKUP.get((42, resp[42]))
            if pressed is not None and pressed != last_btn_state and (now - last_btn_action_time) >= 0.8:
                i = pressed
                last_btn_action_time = now
                obs_btn = obs_cfg["buttons"][i] if i < len(obs_cfg["buttons"]) else {}
                if obs_btn.get("type", "none") != "none":
                    threading.Thread(
                        target=_execute_obs_action,
                        args=(obs_btn, obs_cfg, obs_holder),
                        daemon=True).start()
                else:
                    # Primary action, then any chained extras in order (issue #17).
                    _run_action(buttons[i].get("type", "shell"),
                                (buttons[i].get("action", "") or "").strip())
                    for step in buttons[i].get("actions", []) or []:
                        st = step.get("type", "none")
                        sa = (step.get("action", "") or "").strip()
                        if st and st != "none":
                            time.sleep(0.05)
                            _run_action(st, sa)
            last_btn_state = pressed  # None when byte42=0 (released)

        _mod_state = {"last": None, "sent": 0.0}

        def _report_modules(resp):
            """The keepalive's answer says what is plugged into the keyboard
            (numpad, media dock). Tell the application when it changes, and
            every ten seconds so one that started later learns it too."""
            mods = P.modules_of(resp)
            if mods is None:
                return
            now_ = time.monotonic()
            if mods == _mod_state["last"] and now_ - _mod_state["sent"] < 10:
                return
            _mod_state["last"], _mod_state["sent"] = mods, now_

            def _send():
                try:
                    from shared.ipc import send_command
                    send_command({"cmd": "modules", **mods})
                except Exception:
                    pass
            threading.Thread(target=_send, daemon=True).start()

        while True:
            now = time.monotonic()

            # A jump means the machine was suspended, which reset the port.
            _asleep_now = time.clock_gettime(time.CLOCK_BOOTTIME) - now
            if _asleep_now - _asleep > 5:
                dev = _reclaim(dev)
                last_time_sync = 0  # the clock stood still while asleep
            _asleep = _asleep_now

            # Re-read button config every 2 seconds
            if now - last_config_check >= 2.0:
                buttons = read_buttons()
                obs_cfg = _load_obs_config()
                last_config_check = now
                try:
                    with open(os.path.join(CONFIG_DIR, "clock_format")) as f:
                        cur_fmt = f.read().strip()
                except FileNotFoundError:
                    cur_fmt = "24H"
                if cur_fmt != last_clock_format:
                    last_clock_format = cur_fmt
                    last_time_sync = 0  # force immediate time sync

            # Gather all metrics
            cpu = psutil.cpu_percent(interval=None)

            # RAM and HDD: update every 2s (values change slowly)
            if now - _slow_last >= 2.0:
                _ram_cache = int(psutil.virtual_memory().percent)
                _hdd_cache = int(psutil.disk_usage('/').percent)
                _slow_last = now
            ram = _ram_cache
            hdd = _hdd_cache

            # GPU: update every 2s via nvidia-smi
            if now - _gpu_last >= 2.0:
                try:
                    r = subprocess.run(
                        ['nvidia-smi', '--query-gpu=utilization.gpu',
                         '--format=csv,noheader,nounits'],
                        capture_output=True, text=True, timeout=1)
                    _gpu_cache = int(r.stdout.strip())
                except Exception:
                    _gpu_cache = 0
                _gpu_last = now
            gpu = _gpu_cache

            # Network speed in MB/s (combined up+down), only update every ~1s
            _net_now = psutil.net_io_counters()
            _net_dt = now - _net_prev_time
            if _net_dt >= 0.8:
                net_bytes = (_net_now.bytes_sent - _net_prev.bytes_sent +
                             _net_now.bytes_recv - _net_prev.bytes_recv)
                net_mbs = net_bytes / _net_dt / 1_000_000
                _net_prev, _net_prev_time = _net_now, now
            else:
                net_mbs = _smooth[3]

            # EMA smoothing (alpha=0.2 → slow, stable display)
            alpha = 0.4
            raw = {0: float(cpu), 1: float(gpu), 2: float(hdd),
                   3: net_mbs, 4: float(ram)}
            for k in _smooth:
                _smooth[k] = alpha * raw[k] + (1 - alpha) * _smooth[k]

            # Send keepalive + check buttons
            try:
                dev.write(EP_OUT, P.keepalive_packet())
                _keepalive_resp = _read(dev, timeout=150)
                _handle_btn_resp(_keepalive_resp)
                _report_modules(_keepalive_resp)

                # Send all metrics (keyboard shows whichever the wheel selects)
                for metric_type in range(5):
                    # One packet byte, so 0-255 (metric_packet clamps).
                    # Network MB/s is the only metric that is not a percentage
                    # and can leave that range.
                    dev.write(EP_OUT, P.metric_packet(metric_type,
                                                      _smooth[metric_type]))
                    _handle_btn_resp(_read(dev, timeout=150))

                # Volume rides its own command, not a metric slot — the board
                # accepts exactly six slots (0-4 above, 5 is APM) and rejects
                # anything else with `ff aa`.
                # Read it here, not on a timer: a stale level pushed after a
                # wheel turn overwrites what the firmware just displayed, and
                # the screen snaps back to the old number.
                vol = _system_volume()
                if vol is not None:
                    dev.write(EP_OUT, P.volume_packet(vol))
                    _handle_btn_resp(_read(dev, timeout=150))

                # Time sync once per minute, in here so a USBError from it
                # cannot escape and end the loop.
                if now - last_time_sync >= 60:
                    _send_time_packet(dev, style)
                    last_time_sync = now
                _usb_fail = 0
            except usb.core.USBError as e:
                # One bad cycle is transient; every cycle failing means the
                # handle is dead and wants re-claiming.
                _usb_fail += 1
                if _usb_fail == 1:
                    print(f"USB write failed: {e}", file=sys.stderr, flush=True)
                if _usb_fail >= 10:
                    dev = _reclaim(dev)
                    _usb_fail = 0

            time.sleep(CPU_INTERVAL)
    except KeyboardInterrupt:
        pass  # Normal termination via SIGINT
    finally:
        try:
            _release(dev)
        except Exception:
            pass

# ── CLI ────────────────────────────────────────────────────────────────────

_stopping = False


def _exit_on_term(_sig, _frame):
    """Leave through the normal exit path when the GUI stops us.

    Every caller that stops a controller uses Popen.terminate(), which is
    SIGTERM, and the default handling for that ends the process where it
    stands: no `finally`, so the claimed interface is never released and
    usbhid never gets it back. Turning it into KeyboardInterrupt makes a stop
    from the application take exactly the path Ctrl-C already took.

    Only the first one raises. The application stops a monitor in one place
    and clears strays with pkill in another, so two can arrive close
    together, and the second landing inside the release would interrupt the
    very cleanup this exists to run.
    """
    global _stopping
    if _stopping:
        return
    _stopping = True
    # The application waits for this process on its own interface thread, so
    # a cleanup that blocks would freeze the window. Only the first signal
    # raises, so a second one cannot break into the release; this is what
    # ends the process if the release itself does not come back.
    try:
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(5)
    except Exception:
        pass
    raise KeyboardInterrupt


def main():
    """Entry point for both ways in.

    The stop has to be caught here rather than under `if __name__ ==
    "__main__"`: the packaged build runs emax_entry.py, which imports this
    module and calls main(), so it never reaches that guard. Without it a
    stop during an upload or an icon write ended with a traceback and a
    non-zero exit, which the screen reads as the upload having failed.
    """
    signal.signal(signal.SIGTERM, _exit_on_term)
    try:
        _run(sys.argv[1:])
    except KeyboardInterrupt:
        pass    # Ctrl-C or the stop from the application; both are normal


def _run(args):
    mode = "time"
    style_arg = None
    btn_idx = None
    variant = None

    action_str        = None
    action_type_str   = "shell"
    image_path        = None
    main_display_mode = "image"
    gif_frame         = 0
    activate_custom   = False
    rgb_args          = []
    per_key_json      = "{}"
    per_key_persist   = False
    per_key_in_custom = False
    dock_changes      = None
    remap_json        = "[]"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "cpu":
            mode = "cpu"
        elif a in ("analog", "digital"):
            style_arg = a
        elif a == "icon" and i + 2 < len(args):
            mode = "icon"
            btn_idx = int(args[i + 1])
            variant  = int(args[i + 2])
            i += 2
        elif a == "upload" and i + 2 < len(args):
            mode = "upload"
            btn_idx = int(args[i + 1])
            image_path = args[i + 2]
            i += 2
        elif a == "upload-main" and i + 1 < len(args):
            mode = "upload-main"
            image_path = args[i + 1]
            i += 1
        elif a == "main-mode" and i + 1 < len(args):
            mode = "main-mode"
            main_display_mode = args[i + 1]  # "image" or "clock"
            i += 1
        elif a == "write-action" and i + 1 < len(args):
            mode = "write-action"
            btn_idx = int(args[i + 1])
            i += 1
        elif a == "reset-buttons":
            mode = "reset-buttons"
        elif a == "reset-dial":
            mode = "reset-dial"
        elif a == "factory-reset":
            mode = "factory-reset"
        elif a == "rgb" and i + 6 < len(args):
            mode = "rgb"
            rgb_args = args[i + 1:i + 7]
            i += 6
        elif a == "side-rgb" and i + 1 < len(args):
            mode = "side-rgb"
            side_rgb_hex = args[i + 1]
            i += 1
        elif a == "custom-rgb":
            mode = "custom-rgb"
            # remaining args are zone:rrggbb pairs and optional brightness:N
            custom_rgb_args = args[i + 1:]
            i = len(args)
        elif a == "per-key-rgb" and i + 1 < len(args):
            mode = "per-key-rgb"
            per_key_json = args[i + 1]
            i += 1
        elif a == "--persist":
            per_key_persist = True
        elif a == "--in-custom":
            per_key_in_custom = True
        elif a == "dock-settings":
            mode = "dock-settings"
        elif a == "--set" and i + 1 < len(args):
            dock_changes = args[i + 1]
            i += 1
        elif a == "board-settings":
            mode = "board-settings"
        elif a == "versions":
            mode = "versions"
        elif a == "remap" and i + 1 < len(args):
            mode = "remap"
            remap_json = args[i + 1]
            i += 1
        elif a == "--activate-custom":
            activate_custom = True
        elif a == "--frame" and i + 1 < len(args):
            gif_frame = int(args[i + 1])
            i += 1
        elif a == "action" and i + 1 < len(args):
            action_str = args[i + 1]
            i += 1
        elif a == "--type" and i + 1 < len(args):
            action_type_str = args[i + 1]
            i += 1
        i += 1

    if style_arg is None:
        style_arg = read_style()
    style = STYLE_DIGITAL if style_arg == "digital" else STYLE_ANALOG

    if mode == "cpu":
        controller_loop(style)
    elif mode == "icon":
        set_icon_once(btn_idx, variant, action=action_str,
                      action_type=_action_type_byte(action_type_str))
    elif mode == "write-action":
        btns = read_buttons()
        btn = btns[btn_idx]
        btype = btn.get("type", "shell")
        action = btn.get("action", "").strip()
        dev = _get_claimed_device()
        try:
            # Host-dispatched action types (obs/macro/keypress/text) only need
            # byte42 detection from flash; the actual action runs on the host.
            cmd, type_byte = P.flash_entry(btype, action)
            _write_action(dev, btn_idx, cmd, action_type=type_byte)
        finally:
            _release(dev)
    elif mode == "reset-buttons":
        dev = _get_claimed_device()
        try:
            btns = read_buttons()
            for i, btn in enumerate(btns):
                btype = btn.get("type", "shell")
                action = btn.get("action", "").strip()
                cmd, type_byte = P.flash_entry(btype, action)
                _write_action(dev, i, cmd, action_type=type_byte)
        finally:
            _release(dev)
    elif mode == "upload":
        upload_icon(btn_idx, image_path, frame=gif_frame)
    elif mode == "upload-main":
        upload_main_display(image_path, frame=gif_frame, activate=activate_custom)
    elif mode == "main-mode":
        set_main_display_mode(main_display_mode, style)
    elif mode == "reset-dial":
        reset_dial_image()
    elif mode == "factory-reset":
        factory_reset()
    elif mode == "rgb":
        eff, spd, bri, c1_hex, c2_hex, dr = rgb_args
        c1 = (int(c1_hex[0:2], 16), int(c1_hex[2:4], 16), int(c1_hex[4:6], 16))
        c2 = (int(c2_hex[0:2], 16), int(c2_hex[2:4], 16), int(c2_hex[4:6], 16))
        set_rgb(eff, int(spd), int(bri), c1, c2, int(dr))
    elif mode == "side-rgb":
        h = side_rgb_hex.lstrip("#")
        set_custom_rgb({}, side_color=(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    elif mode == "custom-rgb":
        zone_colors = {}
        side_color = (0, 0, 0)
        brightness = 100
        for token in custom_rgb_args:
            if ":" not in token:
                continue
            k, v = token.split(":", 1)
            if k == "brightness":
                brightness = int(v)
            else:
                h = v.lstrip("#")
                rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                if k == "side":
                    side_color = rgb
                else:
                    zone_colors[k] = rgb
        set_custom_rgb(zone_colors, side_color=side_color, brightness=brightness)
    elif mode == "dock-settings":
        changes = json.loads(dock_changes) if dock_changes else None
        code = dock_settings(changes if isinstance(changes, dict) else None)
        if code:
            sys.exit(code)
    elif mode == "board-settings":
        changes = json.loads(dock_changes) if dock_changes else None
        code = board_settings(changes if isinstance(changes, dict) else None)
        if code:
            sys.exit(code)
    elif mode == "versions":
        read_versions()
    elif mode == "remap":
        try:
            entries = json.loads(remap_json)
        except ValueError:
            entries = None
        code = remap_keys(entries) if isinstance(entries, list) else 2
        if code:
            if not isinstance(entries, list):
                print("remap: expected a JSON list of entries", file=sys.stderr, flush=True)
            sys.exit(code)
    elif mode == "per-key-rgb":
        leds, side, bri = P.parse_per_key_payload(per_key_json)
        effects, effect_params = P.parse_per_key_effects(per_key_json)
        saved = set_per_key_rgb(leds, side, bri, effects, effect_params, per_key_in_custom)
        if per_key_persist and not saved:
            # §5.11.8: commit to slot 6 (Custom)
            dev = _get_claimed_device()
            try:
                dev.write(EP_OUT, P.persist_packet())
                try: dev.read(EP_IN, PKT_SIZE, timeout=300)
                except Exception: pass
            finally:
                _release(dev)
    else:
        send_time(style)


if __name__ == "__main__":
    main()
