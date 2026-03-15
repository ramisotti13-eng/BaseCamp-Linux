#!/usr/bin/env python3
"""Controller for Mountain Everest Max display (time, CPU, numpad icons & buttons)."""
import usb.core
import usb.util
import datetime
import sys
import os
import time
import json
import subprocess
import threading
import psutil
import struct
import pwd as _pwd

VID = 0x3282
PID = 0x0001
INTERFACE = 3
EP_OUT = 0x05
EP_IN  = 0x84
PKT_SIZE = 64

STYLE_ANALOG  = 0x00
STYLE_DIGITAL = 0x01

# Button lookup: (byte_index, value) → button index 0-3 (D1-D4)
BTN_LOOKUP = {
    (42, 0x02): 0,  # D1
    (42, 0x04): 1,  # D2
    (42, 0x08): 2,  # D3
    (42, 0x10): 3,  # D4
}

# Icon IDs: 0x40 + button_index*9 + variant (0-8)
ICON_BASE    = 0x40
ICONS_PER_BTN = 9

_real_home = _pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir if os.environ.get("SUDO_USER") else os.path.expanduser("~")
CONFIG_DIR  = os.path.join(_real_home, ".config", "mountain-time-sync")
STYLE_FILE  = os.path.join(CONFIG_DIR, "style")
BUTTON_FILE = os.path.join(CONFIG_DIR, "buttons.json")

CPU_INTERVAL = 0.2

# ── Helpers ────────────────────────────────────────────────────────────────

def make_packet(*args):
    pkt = bytearray(PKT_SIZE)
    for i, v in enumerate(args):
        pkt[i] = v
    return pkt

def _claim(dev):
    dev._reattach = False
    if dev.is_kernel_driver_active(INTERFACE):
        dev.detach_kernel_driver(INTERFACE)
        dev._reattach = True
    usb.util.claim_interface(dev, INTERFACE)

def _release(dev):
    usb.util.release_interface(dev, INTERFACE)
    if getattr(dev, '_reattach', False):
        try:
            dev.attach_kernel_driver(INTERFACE)
        except Exception:
            pass
    usb.util.dispose_resources(dev)

_EMPTY_PKT = bytes(PKT_SIZE)

def _read(dev, timeout=1000):
    """Read one packet, return it. Returns empty packet on timeout."""
    try:
        data = dev.read(EP_IN, PKT_SIZE, timeout=timeout)
        return data
    except usb.core.USBTimeoutError:
        return _EMPTY_PKT

def icon_id(button_idx, variant):
    """Return the icon_id for a given button (0-3) and variant (0-8)."""
    return ICON_BASE + button_idx * ICONS_PER_BTN + variant

# ── Protocol ───────────────────────────────────────────────────────────────

def _send_time_packet(dev, style):
    now = datetime.datetime.now()
    try:
        with open(os.path.join(CONFIG_DIR, "clock_format")) as f:
            fmt = f.read().strip()
    except FileNotFoundError:
        fmt = "24H"
    hour = (now.hour % 12) or 12 if fmt == "12H" else now.hour
    dev.write(EP_OUT, make_packet(0x11, 0x80, 0x00, 0x00, 0x01))
    _read(dev)
    dev.write(EP_OUT, make_packet(0x11, 0x84, 0x00, 0x00))
    _read(dev)
    dev.write(EP_OUT, make_packet(
        0x11, 0x84, 0x00, 0x01, 0x00, 0x00,
        now.month, now.day, hour, now.minute,
        now.second, style
    ))
    _read(dev)

def _send_cpu_packet(dev, cpu_percent):
    dev.write(EP_OUT, make_packet(0x11, 0x14))
    _read(dev)
    dev.write(EP_OUT, make_packet(0x11, 0x81, 0x00, 0x00, int(cpu_percent)))
    _read(dev)

def _set_icon(dev, button_idx, variant):
    """Set icon for one numpad button."""
    iid = icon_id(button_idx, variant)
    dev.write(EP_OUT, make_packet(0x11, 0x00))
    try:
        _read(dev)
    except usb.core.USBTimeoutError:
        pass
    dev.write(EP_OUT, make_packet(0x11, 0x02, 0x00, 0x01, 0x01, iid, 0x02))
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

def _write_action(dev, button_idx, command):
    """Schreibt eine Aktion (Linux-Befehl) in den Keyboard-Flash für einen Button."""
    cmd_bytes = command.encode('utf-8')[:55]  # max ~55 Zeichen
    # Paketformat: 17 aa [total_len] 00 04 [cmd_bytes]
    total_len = 1 + len(cmd_bytes)  # 1 Byte für Typ (0x04) + Befehl
    pkt = make_packet(0x17, 0xAA, total_len, 0x00, 0x04, *cmd_bytes)
    # Button auswählen: 12 08 00 [btn_idx+1] 00
    dev.write(EP_OUT, make_packet(0x12, 0x08, 0x00, button_idx + 1))
    try:
        _read(dev)
    except usb.core.USBTimeoutError:
        pass
    dev.write(EP_OUT, pkt)
    try:
        _read(dev)
    except usb.core.USBTimeoutError:
        pass

ICON_IMG_SIZE = 10368  # 72×72 × 2 bytes (RGB565)
MAIN_IMG_SIZE = 97920  # 240×204 × 2 bytes (RGB565)
MAIN_DISPLAY_W = 240
MAIN_DISPLAY_H = 204

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
    for sector_idx, sector_cmd in enumerate(sectors, start=1):
        _ctrl_set_report(dev, bytes([0xaa, 0x55, 0x21, sector_cmd]))
        _ctrl_get_report(dev)
        for i in range(100):
            time.sleep(0.030)
            _ctrl_set_report(dev, bytes([0xaa, 0x55, 0x80, 0x00]))
            r = _ctrl_get_report(dev)
            if r[2:4] == bytes([0x80, 0xfa]):
                break
        _ctrl_set_report(dev, bytes([0xaa, 0x55, 0x21, 0x00]))
        _ctrl_get_report(dev)

def _upload_icon_image(dev, button_idx, img_bytes):
    """Upload 72×72 RGB565 LE image to numpad button via HID Feature Reports.

    Protocol (reverse-engineered from USB capture_icon_change.bin):
      Two erase sessions (21 01/21 02 per sector, 5.5s gap between sessions)
      aa55 2104 / aa55 2200 handshake
      aa55 1080 2800 6be9 0000 0201 [btn_idx] descriptor
      Stream 64-byte chunks at 30ms intervals until all 10368 bytes committed
    """
    assert len(img_bytes) == ICON_IMG_SIZE

    # Init interrupt endpoint
    dev.write(EP_OUT, make_packet(0x11, 0x12))
    try: dev.read(EP_IN, PKT_SIZE, timeout=1000)
    except Exception: pass
    for _ in range(3):
        dev.write(EP_OUT, make_packet(0x11, 0x14))
        try: dev.read(EP_IN, PKT_SIZE, timeout=500)
        except Exception: pass
        time.sleep(0.05)

    _ctrl_get_report(dev)  # initial state poll
    print("PROGRESS:5", flush=True)

    # Sector numbers depend on button index: D1→(1,2), D2→(3,4), D3→(5,6), D4→(7,8)
    sectors = (button_idx * 2 + 1, button_idx * 2 + 2)
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
    desc = bytes([0xaa, 0x55, 0x10, 0x80, 0x28, 0x00,
                  0x6b, 0xe9,
                  0x00, 0x00, 0x02, 0x01, button_idx])
    for _try in range(3):
        _ctrl_set_report(dev, bytes([0xaa, 0x55, 0x21, 0x04]))
        _ctrl_get_report(dev)
        _ctrl_set_report(dev, bytes([0xaa, 0x55, 0x22, 0x00]))
        _ctrl_get_report(dev)
        _ctrl_set_report(dev, desc)
        time.sleep(0.130)
        r = _ctrl_get_report(dev)
        if r[2:4] == bytes([0x10, 0xfa]):
            break
        for _ in range(100):
            time.sleep(0.150)
            r = _ctrl_get_report(dev)
            if r[2:4] == bytes([0x10, 0xfa]):
                break
        else:
            if _try < 2:
                time.sleep(3.0)  # extra wait before retry
                continue
            raise RuntimeError("Descriptor never became ready (fa)")
        break
    print("PROGRESS:60", flush=True)

    # Stream chunks: resend same chunk until committed (fa), then advance offset.
    # This matches Windows behavior: only advance to next chunk after fa confirms commit.
    target = ICON_IMG_SIZE
    bytes_committed = 0
    next_offset = 0
    for attempt in range(600):
        chunk = img_bytes[next_offset:next_offset + 64]
        _ctrl_set_report(dev, chunk)
        time.sleep(0.030)
        r = _ctrl_get_report(dev)
        if r[2:4] == bytes([0x10, 0xfa]):
            bytes_committed = int.from_bytes(r[4:6], 'little')
            next_offset = bytes_committed
            pct = 60 + int(bytes_committed * 40 / target)
            print(f"PROGRESS:{pct}", flush=True)
            if bytes_committed >= target:
                break
        # if fb: don't advance next_offset, retry same chunk next iteration

    # Wait for display update signal on interrupt endpoint (max 4s)
    dev.write(EP_OUT, make_packet(0x11, 0x14))
    for _ in range(20):
        try:
            r = bytes(dev.read(EP_IN, PKT_SIZE, timeout=200))
            if r[:2] == bytes([0xff, 0xaa]):
                break
        except usb.core.USBTimeoutError:
            pass

def _upload_main_display_image(dev, img_bytes):
    """Upload a 240×204 RGB565 LE image to the main display.

    Protocol (reverse-engineered from capture_main_display.pcap):
      11 14 keepalive (no 11 12!) → 21 03 session open → 22 00 slot prepare →
      descriptor (send, if fb resend once) → chunk stream (fb=retry, fa=advance) →
      11 14 mode switch (b10=0x01) → 13 41 00 00 01 activate
    """
    assert len(img_bytes) == MAIN_IMG_SIZE, f"Expected {MAIN_IMG_SIZE} bytes, got {len(img_bytes)}"

    # 11 14 keepalive to read dev_bytes for mode switch later (no 11 12 — main display
    # upload uses only ctrl_transfer; 11 12 interferes with the flash controller state)
    dev_info = bytearray(PKT_SIZE)
    dev.write(EP_OUT, make_packet(0x11, 0x14))
    try:
        resp = bytearray(dev.read(EP_IN, PKT_SIZE, timeout=500))
        if any(resp[7:10]):
            dev_info = resp
    except Exception: pass
    print("PROGRESS:5", flush=True)

    # Session open → slot prepare
    _ctrl_set_report(dev, bytes([0xaa, 0x55, 0x21, 0x03]))
    _ctrl_get_report(dev)
    _ctrl_set_report(dev, bytes([0xaa, 0x55, 0x22, 0x00]))
    _ctrl_get_report(dev)

    # Descriptor: send once, poll 130ms; if fb resend (capture: 2 sends → fb → fa)
    desc = bytes([0xaa, 0x55, 0x10, 0x80, 0x7e, 0x01, 0xec, 0x46, 0x00, 0x00, 0x02, 0x01, 0x00])
    _ctrl_set_report(dev, desc)
    for _ in range(5):
        time.sleep(0.130)
        r = _ctrl_get_report(dev)
        if r[2:4] == bytes([0x10, 0xfa]):
            break
        _ctrl_set_report(dev, desc)  # fb: resend (matches capture)
    else:
        raise RuntimeError("Main display descriptor never became ready (fa)")
    print("PROGRESS:10", flush=True)

    # Stream chunks: fb = busy (retry same chunk), fa = committed (advance)
    target = MAIN_IMG_SIZE
    next_offset = 0
    fa_count = 0
    for _ in range(4000):
        chunk = img_bytes[next_offset:next_offset + 64]
        _ctrl_set_report(dev, chunk)
        time.sleep(0.030)
        r = _ctrl_get_report(dev)
        if r[2:4] == bytes([0x10, 0xfa]):
            fa_count += 1
            next_offset = fa_count * 64
            pct = 10 + int(next_offset * 90 / target)
            print(f"PROGRESS:{pct}", flush=True)
            if next_offset >= target:
                break
        # fb: retry same chunk (don't advance)

    # Mode switch + activate within the same session (from capture_main_display.pcap)
    dev_bytes = bytes(dev_info[7:10]) if any(dev_info[7:10]) else bytes([0xf3, 0xcc, 0x23])
    mode_pkt = make_packet(
        0x11, 0x14, 0x00, 0x01, 0x02, 0xff, 0x00,
        dev_bytes[0], dev_bytes[1], dev_bytes[2],
        0x01, 0x1e, 0x00, 0x1e, 0x00, 0x01,
        0x12, 0x13, 0x14, 0x15, 0x02, 0x00, 0x32, 0x00)
    dev.write(EP_OUT, mode_pkt)
    try: dev.read(EP_IN, PKT_SIZE, timeout=1000)
    except Exception: pass
    time.sleep(0.050)

    dev.write(EP_OUT, make_packet(0x13, 0x41, 0x00, 0x00, 0x01))
    try: dev.read(EP_IN, PKT_SIZE, timeout=2000)
    except Exception: pass


def image_to_rgb565(image_path, size=(72, 72), frame=0):
    """Convert image file to RGB565 little-endian bytes at the given size.

    size=(72, 72)    → numpad button displays (D1-D4)
    size=(240, 204)  → main OLED display

    For animated GIFs, ``frame`` selects which frame to use (0-based).
    """
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("Pillow not installed. Run: pip install pillow")
    img = Image.open(image_path)
    if frame > 0 and getattr(img, 'n_frames', 1) > 1:
        img.seek(min(frame, img.n_frames - 1))
    img = img.resize(size, Image.LANCZOS).convert('RGB')
    data = bytearray()
    for y in range(size[1]):
        for x in range(size[0]):
            r, g, b = img.getpixel((x, y))
            value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            data += struct.pack('<H', value)  # little-endian
    return bytes(data)

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
    """Returns list of 4 dicts: {icon: 0-8, action: str}"""
    default = [{"icon": 7, "action": ""} for _ in range(4)]
    try:
        with open(BUTTON_FILE) as f:
            data = json.load(f)
        for i in range(4):
            if i < len(data):
                default[i].update(data[i])
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return default

def save_buttons(buttons):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(BUTTON_FILE, "w") as f:
        json.dump(buttons, f, indent=2)

# ── High-level entry points ─────────────────────────────────────────────────

def send_time(style=STYLE_ANALOG):
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard nicht gefunden!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        for _ in range(3):
            dev.write(EP_OUT, make_packet(0x11, 0x14))
            dev.read(EP_IN, PKT_SIZE, timeout=1000)
        _send_time_packet(dev, style)
    finally:
        _release(dev)

def set_main_display_mode(mode, style=STYLE_ANALOG):
    """Switch the main display between 'image' and 'clock' mode.

    Protocol from capture_main_clock_switch.pcap:
      Send 11 14 00 01 02 ff 00 [dev_bytes_7-9] [b10] 1e 00 1e 00 01 12 13 14 15 02 00 32 00...
      b10=0x11 → clock active, b10=0x01 → image active
      dev_bytes_7-9 are device-specific, read from keyboard's own 11 14 response.
    """
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard nicht gefunden!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        # Send keepalives to wake up keyboard before mode switch
        # (required after upload or idle — keyboard ignores mode switch without prior keepalives)
        for _ in range(5):
            dev.write(EP_OUT, make_packet(0x11, 0x14))
            try: dev.read(EP_IN, PKT_SIZE, timeout=300)
            except Exception: pass
            time.sleep(0.1)

        # Read keyboard's current 11 14 state to get device-specific bytes[7-9]
        dev.write(EP_OUT, make_packet(0x11, 0x14))
        try:
            info = bytearray(dev.read(EP_IN, PKT_SIZE, timeout=1000))
        except Exception:
            info = bytearray(PKT_SIZE)

        dev_bytes = bytes(info[7:10]) if any(info[7:10]) else bytes([0xf3, 0xcc, 0x23])

        # Build mode-switch packet
        b10 = 0x11 if mode == "clock" else 0x01
        pkt = bytearray(PKT_SIZE)
        pkt[0]     = 0x11
        pkt[1]     = 0x14
        pkt[3]     = 0x01          # write flag
        pkt[4]     = 0x02
        pkt[5]     = 0xff
        pkt[7:10]  = dev_bytes
        pkt[10]    = b10
        pkt[11]    = 0x1e
        pkt[13]    = 0x1e
        pkt[15]    = 0x01
        pkt[16:20] = bytes([0x12, 0x13, 0x14, 0x15])
        pkt[20]    = 0x02
        pkt[22]    = 0x32

        dev.write(EP_OUT, bytes(pkt))
        try: dev.read(EP_IN, PKT_SIZE, timeout=500)
        except Exception: pass

        # When switching to image mode, also activate the committed image data
        if mode == "image":
            dev.write(EP_OUT, make_packet(0x13, 0x41, 0x00, 0x00, 0x01))
            try: dev.read(EP_IN, PKT_SIZE, timeout=2000)
            except Exception: pass
    finally:
        _release(dev)


def upload_main_display(image_path, frame=0):
    """Upload a custom image (PNG/JPG/GIF) to the main 240×204 OLED display."""
    img_bytes = image_to_rgb565(image_path, size=(MAIN_DISPLAY_W, MAIN_DISPLAY_H), frame=frame)
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard nicht gefunden!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        _upload_main_display_image(dev, img_bytes)
    finally:
        _release(dev)


def upload_icon(button_idx, image_path, frame=0):
    """Upload a custom image (PNG/JPG/GIF frame) to a numpad button display."""
    img_bytes = image_to_rgb565(image_path, frame=frame)
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard nicht gefunden!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        _upload_icon_image(dev, button_idx, img_bytes)
    finally:
        # Release interface before USB reset to restore numpad
        usb.util.release_interface(dev, INTERFACE)
        if getattr(dev, '_reattach', False):
            try:
                dev.attach_kernel_driver(INTERFACE)
            except Exception:
                pass
        usb.util.dispose_resources(dev)

def set_icon_once(button_idx, variant, action=None):
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard nicht gefunden!", file=sys.stderr)
        sys.exit(1)
    _claim(dev)
    try:
        _set_icon(dev, button_idx, variant)
        if action is not None:
            _write_action(dev, button_idx, action)
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
        print(f"OBS Fehler: {e}", flush=True)
        obs_holder[0] = None  # reconnect next time

def controller_loop(style=STYLE_ANALOG):
    """Main loop: CPU display + time sync + numpad button monitoring."""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Keyboard nicht gefunden!", file=sys.stderr)
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
        dev.write(EP_OUT, make_packet(0x11, 0x12))
        _read(dev)
        dev.write(EP_OUT, make_packet(0x11, 0x14))
        _read(dev)

        # Icons setzen und Original-BaseCamp-Aktionen deaktivieren
        buttons = read_buttons()
        obs_cfg_init = _load_obs_config()
        for i, btn in enumerate(buttons):
            _set_icon(dev, i, btn["icon"])
            # Immer ":" in Flash schreiben: deaktiviert Original-BaseCamp-Befehle
            # und stellt sicher dass byte42 korrekt gesetzt wird (byte42-Fix).
            _write_action(dev, i, ":")

        last_time_sync = 0
        last_config_check = 0
        last_clock_format = None
        last_btn_state = None
        last_btn_action_time = 0
        obs_cfg = _load_obs_config()
        obs_holder = [None]
        psutil.cpu_percent()
        _net_prev = psutil.net_io_counters()
        _net_prev_time = time.monotonic()
        _gpu_cache = 0
        _gpu_last = 0
        # Smoothed metric values (EMA, alpha=0.2)
        _smooth = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}

        while True:
            now = time.monotonic()

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
                        action = buttons[i].get("action", "").strip()
                        if action:
                            env = os.environ.copy()
                            if os.environ.get("SUDO_USER"):
                                env.setdefault("DISPLAY", ":0")
                                env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                                    f"unix:path=/run/user/{_pwd.getpwnam(os.environ['SUDO_USER']).pw_uid}/bus")
                                subprocess.Popen(
                                    ["sudo", "-u", os.environ["SUDO_USER"], "bash", "-c", action],
                                    env=env)
                            else:
                                subprocess.Popen(action, shell=True)
                last_btn_state = pressed  # None when byte42=0 (released)

            # Gather all metrics
            cpu = psutil.cpu_percent(interval=None)
            ram = int(psutil.virtual_memory().percent)
            hdd = int(psutil.disk_usage('/').percent)

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
                dev.write(EP_OUT, make_packet(0x11, 0x14))
                _handle_btn_resp(_read(dev, timeout=150))

                # Send all metrics (keyboard shows whichever the wheel selects)
                for metric_type in range(5):
                    value = min(int(_smooth[metric_type]), 999)
                    dev.write(EP_OUT, make_packet(0x11, 0x81, metric_type, 0x00, value))
                    _handle_btn_resp(_read(dev, timeout=150))
            except usb.core.USBError:
                pass  # Transient USB error — skip this cycle

            # Time sync once per minute
            if now - last_time_sync >= 60:
                _send_time_packet(dev, style)
                last_time_sync = now

            time.sleep(CPU_INTERVAL)
    except KeyboardInterrupt:
        pass  # Normal termination via SIGINT
    finally:
        _release(dev)

# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    mode = "time"
    style_arg = None
    btn_idx = None
    variant = None

    action_str        = None
    image_path        = None
    main_display_mode = "image"
    gif_frame         = 0
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
        elif a == "--frame" and i + 1 < len(args):
            gif_frame = int(args[i + 1])
            i += 1
        elif a == "action" and i + 1 < len(args):
            action_str = args[i + 1]
            i += 1
        i += 1

    if style_arg is None:
        style_arg = read_style()
    style = STYLE_DIGITAL if style_arg == "digital" else STYLE_ANALOG

    if mode == "cpu":
        controller_loop(style)
    elif mode == "icon":
        set_icon_once(btn_idx, variant, action=action_str)
    elif mode == "upload":
        upload_icon(btn_idx, image_path, frame=gif_frame)
    elif mode == "upload-main":
        upload_main_display(image_path, frame=gif_frame)
    elif mode == "main-mode":
        set_main_display_mode(main_display_mode, style)
    else:
        send_time(style)
