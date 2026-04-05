#!/usr/bin/env python3
"""
Mountain Everest 60 Controller
VID: 0x3282, PID: 0x0005 (ANSI) / 0x0006 (ISO)
Protocol: HID Feature Reports on Interface 2

Reverse-engineered from OpenRGB MountainKeyboard60Controller.
Report size: 65 bytes (Report ID 0x00 + 64 bytes data).
Magic bytes [2..4] = 0x46 0x23 0xEA on every command.

SetMode (cmd=0x16):
  [1]    = 0x16
  [2..4] = 0x46 0x23 0xEA
  [5]    = 0x01
  [9]    = effect code (activates the mode)

SendModeDetails (cmd=0x17):
  [1]    = 0x17
  [2..4] = 0x46 0x23 0xEA
  [5]    = effect code
  [7]    = speed   × 25   (0/25/50/75/100)
  [8]    = brightness × 25
  [9]    = color_mode (0=single, 2=rainbow cycle, 0x10 = dual)
  [10]   = direction
  [12..14] = color1 R,G,B
  [15..17] = color2 R,G,B

After set_report, get_report should echo cmd byte in resp[0].
If not, retry (device may be busy).

Direct color mode (custom per-key):
  Begin:  cmd=0x34, [5]=brightness×25, [6]=0xC0
  Map:    cmd=0x35, [5]=stream_ctl (0x0E first, 0x0A rest), then 14 × RGBA (56 bytes)
  End:    cmd=0x36
"""
import sys
import time

try:
    import hid
    HID_AVAILABLE = True
except ImportError:
    HID_AVAILABLE = False

VID         = 0x3282
PID_ANSI    = 0x0005
PID_ISO     = 0x0006
PID         = PID_ANSI   # updated at runtime by detect_model()
INTERFACE   = 2

MAGIC = (0x46, 0x23, 0xEA)

# Effect codes
EFFECT_STATIC    = 0x01
EFFECT_WAVE      = 0x02
EFFECT_TORNADO   = 0x03
EFFECT_BREATHING = 0x04
EFFECT_REACTIVE  = 0x05
EFFECT_CUSTOM    = 0x07
EFFECT_YETI      = 0x08
EFFECT_OFF       = 0x09

# Color mode
COLOR_SINGLE  = 0x00
COLOR_RAINBOW = 0x02
COLOR_DUAL    = 0x10

# Direction values (Wave/Tornado)
DIR_WAVE    = {"L→R": 0x00, "T→B": 0x02, "R→L": 0x04, "B→T": 0x06}
DIR_TORNADO = {"CW": 0x0A, "CCW": 0x09}

NUM_KEYS = 191


def detect_model():
    """Detect which Everest 60 variant is connected. Returns (pid, name) or (None, None)."""
    global PID
    if not HID_AVAILABLE:
        return None, None

    for pid, name in [(PID_ANSI, "Everest 60"), (PID_ISO, "Everest 60 ISO")]:
        for d in hid.enumerate(VID, pid):
            if d.get('interface_number') == INTERFACE:
                PID = pid
                return pid, name
    return None, None


def find_path():
    """Return HID path for Interface 2, or None."""
    if not HID_AVAILABLE:
        return None
    for pid in (PID_ANSI, PID_ISO):
        for d in hid.enumerate(VID, pid):
            if d.get('interface_number') == INTERFACE:
                return d['path']
    return None


def open_device():
    path = find_path()
    if path is None:
        raise RuntimeError("Everest 60 not found (VID=0x3282 PID=0x0005/0x0006 IF2)")
    dev = hid.Device(path=path)
    return dev


def _send(dev, buf, retries=3):
    """Send feature report, verify response echoes cmd byte, retry if not."""
    cmd = buf[1]
    for attempt in range(retries):
        dev.send_feature_report(bytes(buf))
        time.sleep(0.05)
        resp = dev.get_feature_report(0x00, 65)
        time.sleep(0.05)
        if resp and len(resp) >= 2 and resp[1] == cmd:
            return resp
    return resp if 'resp' in dir() else None


def _make_buf(cmd):
    buf = [0x00] * 65
    buf[1] = cmd
    buf[2], buf[3], buf[4] = MAGIC
    return buf


def _brightness_val(pct):
    """Convert 0-100% to nearest 25-step value."""
    pct = max(0, min(100, int(pct)))
    return round(pct / 25) * 25


def _speed_val(pct):
    """Convert 0-100% to nearest 25-step value."""
    pct = max(0, min(100, int(pct)))
    return round(pct / 25) * 25


# ── Lighting commands ─────────────────────────────────────────────────────────

def _send_mode(dev, effect, speed=50, brightness=100,
               r1=255, g1=255, b1=255, r2=0, g2=0, b2=0,
               color_mode=COLOR_DUAL, direction=0):
    # Step 1: Switch mode (cmd 0x16) — activates the effect
    buf = _make_buf(0x16)
    buf[5] = 1
    buf[9] = effect
    _send(dev, buf)

    # Step 2: Send mode details (cmd 0x17) — effect code in [5]
    buf = _make_buf(0x17)
    buf[5]  = effect
    buf[7]  = _speed_val(speed)
    buf[8]  = _brightness_val(brightness)
    buf[9]  = color_mode
    buf[10] = direction
    if color_mode != COLOR_RAINBOW:
        buf[12] = r1 & 0xFF
        buf[13] = g1 & 0xFF
        buf[14] = b1 & 0xFF
        if color_mode == COLOR_DUAL:
            buf[15] = r2 & 0xFF
            buf[16] = g2 & 0xFF
            buf[17] = b2 & 0xFF
    _send(dev, buf)


def set_lighting_off(brightness=100):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_OFF, brightness=brightness)
    finally:
        dev.close()


def set_lighting_static(r, g, b, brightness=100):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_STATIC, color_mode=COLOR_SINGLE, brightness=brightness,
                   r1=r, g1=g, b1=b)
    finally:
        dev.close()


def set_lighting_breathing(r=255, g=0, b=0, r2=0, g2=0, b2=0, brightness=100, speed=50):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_BREATHING, speed=speed, brightness=brightness,
                   r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2)
    finally:
        dev.close()


def set_lighting_breathing_rainbow(brightness=100, speed=50):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_BREATHING, speed=speed, brightness=brightness,
                   color_mode=COLOR_RAINBOW)
    finally:
        dev.close()


def set_lighting_wave(r=255, g=0, b=0, r2=0, g2=0, b2=0, brightness=100, speed=50, direction=0):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_WAVE, speed=speed, brightness=brightness,
                   r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2, direction=direction)
    finally:
        dev.close()


def set_lighting_wave_rainbow(brightness=100, speed=50, direction=0):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_WAVE, speed=speed, brightness=brightness,
                   color_mode=COLOR_RAINBOW, direction=direction)
    finally:
        dev.close()


def set_lighting_tornado(r=255, g=0, b=0, r2=0, g2=0, b2=0, brightness=100, speed=50, direction=0):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_TORNADO, speed=speed, brightness=brightness,
                   color_mode=COLOR_SINGLE, r1=r, g1=g, b1=b, direction=10-direction)
    finally:
        dev.close()


def set_lighting_tornado_rainbow(brightness=100, speed=50, direction=0):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_TORNADO, speed=speed, brightness=brightness,
                   color_mode=COLOR_RAINBOW, direction=10-direction)
    finally:
        dev.close()


def set_lighting_reactive(r=255, g=0, b=0, r2=0, g2=0, b2=0, brightness=100, speed=50):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_REACTIVE, speed=speed, brightness=brightness,
                   r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2)
    finally:
        dev.close()


def set_lighting_yeti(r=255, g=0, b=0, r2=0, g2=0, b2=255, brightness=100, speed=50):
    dev = open_device()
    try:
        _send_mode(dev, EFFECT_YETI, speed=speed, brightness=brightness,
                   r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2)
    finally:
        dev.close()


def set_lighting_custom(colors, brightness=100):
    """Set per-key RGB. colors: list of 191 (r,g,b) tuples."""
    colors = list(colors)[:NUM_KEYS]
    while len(colors) < NUM_KEYS:
        colors.append((0, 0, 0))

    dev = open_device()
    try:
        # Begin
        buf = _make_buf(0x34)
        buf[5] = _brightness_val(brightness)
        buf[6] = 0xC0
        _send(dev, buf)

        # Map — 14 RGBA colors per packet (65 - 6 header bytes = 59, 59//4 = 14)
        COLORS_PER_PKT = 14
        idx = 0
        pkt_no = 0
        while idx < NUM_KEYS:
            buf = _make_buf(0x35)
            buf[5] = 0x0E if pkt_no == 0 else 0x0A
            pos = 6
            count = 0
            while idx < NUM_KEYS and count < COLORS_PER_PKT:
                r, g, b = colors[idx]
                buf[pos]     = r & 0xFF
                buf[pos + 1] = g & 0xFF
                buf[pos + 2] = b & 0xFF
                buf[pos + 3] = 0xFF  # alpha/padding
                pos += 4
                idx += 1
                count += 1
            _send(dev, buf)
            pkt_no += 1

        # End
        _send(dev, _make_buf(0x36))
    finally:
        dev.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: everest60-controller <rgb|status> [args]")
        sys.exit(1)

    cmd = args[0]

    if cmd == "status":
        path = find_path()
        if path:
            pid, name = detect_model()
            print(f"connected: {name} {path.decode() if isinstance(path, bytes) else path}")
        else:
            print("not connected")
            sys.exit(1)

    elif cmd == "rgb":
        if len(args) < 2:
            _die("rgb: subcommand required")
        live = args[1] == "live"
        sub_args = args[2:] if live else args[1:]
        sub = sub_args[0] if sub_args else ""
        try:
            if sub == "off":
                set_lighting_off()
            elif sub == "static":
                if len(sub_args) < 4:
                    _die("rgb static R G B [brightness]")
                r, g, b = int(sub_args[1]), int(sub_args[2]), int(sub_args[3])
                bri = int(sub_args[4]) if len(sub_args) > 4 else 100
                set_lighting_static(r, g, b, brightness=bri)
            elif sub == "breathing":
                r  = int(sub_args[1]) if len(sub_args) > 1 else 255
                g  = int(sub_args[2]) if len(sub_args) > 2 else 0
                b  = int(sub_args[3]) if len(sub_args) > 3 else 0
                r2 = int(sub_args[4]) if len(sub_args) > 4 else 0
                g2 = int(sub_args[5]) if len(sub_args) > 5 else 0
                b2 = int(sub_args[6]) if len(sub_args) > 6 else 0
                bri = int(sub_args[7]) if len(sub_args) > 7 else 100
                spd = int(sub_args[8]) if len(sub_args) > 8 else 50
                set_lighting_breathing(r, g, b, r2, g2, b2, brightness=bri, speed=spd)
            elif sub == "breathing-rainbow":
                bri = int(sub_args[1]) if len(sub_args) > 1 else 100
                spd = int(sub_args[2]) if len(sub_args) > 2 else 50
                set_lighting_breathing_rainbow(brightness=bri, speed=spd)
            elif sub == "wave":
                r  = int(sub_args[1]) if len(sub_args) > 1 else 255
                g  = int(sub_args[2]) if len(sub_args) > 2 else 0
                b  = int(sub_args[3]) if len(sub_args) > 3 else 0
                r2 = int(sub_args[4]) if len(sub_args) > 4 else 0
                g2 = int(sub_args[5]) if len(sub_args) > 5 else 0
                b2 = int(sub_args[6]) if len(sub_args) > 6 else 0
                bri = int(sub_args[7]) if len(sub_args) > 7 else 100
                spd = int(sub_args[8]) if len(sub_args) > 8 else 50
                d   = int(sub_args[9]) if len(sub_args) > 9 else 0
                set_lighting_wave(r, g, b, r2, g2, b2, brightness=bri, speed=spd, direction=d)
            elif sub == "wave-rainbow":
                bri = int(sub_args[1]) if len(sub_args) > 1 else 100
                spd = int(sub_args[2]) if len(sub_args) > 2 else 50
                d   = int(sub_args[3]) if len(sub_args) > 3 else 0
                set_lighting_wave_rainbow(brightness=bri, speed=spd, direction=d)
            elif sub == "tornado":
                r  = int(sub_args[1]) if len(sub_args) > 1 else 255
                g  = int(sub_args[2]) if len(sub_args) > 2 else 0
                b  = int(sub_args[3]) if len(sub_args) > 3 else 0
                r2 = int(sub_args[4]) if len(sub_args) > 4 else 0
                g2 = int(sub_args[5]) if len(sub_args) > 5 else 0
                b2 = int(sub_args[6]) if len(sub_args) > 6 else 0
                bri = int(sub_args[7]) if len(sub_args) > 7 else 100
                spd = int(sub_args[8]) if len(sub_args) > 8 else 50
                d   = int(sub_args[9]) if len(sub_args) > 9 else 0
                set_lighting_tornado(r, g, b, r2, g2, b2, brightness=bri, speed=spd, direction=d)
            elif sub == "tornado-rainbow":
                bri = int(sub_args[1]) if len(sub_args) > 1 else 100
                spd = int(sub_args[2]) if len(sub_args) > 2 else 50
                d   = int(sub_args[3]) if len(sub_args) > 3 else 0
                set_lighting_tornado_rainbow(brightness=bri, speed=spd, direction=d)
            elif sub == "reactive":
                r  = int(sub_args[1]) if len(sub_args) > 1 else 255
                g  = int(sub_args[2]) if len(sub_args) > 2 else 0
                b  = int(sub_args[3]) if len(sub_args) > 3 else 0
                r2 = int(sub_args[4]) if len(sub_args) > 4 else 0
                g2 = int(sub_args[5]) if len(sub_args) > 5 else 0
                b2 = int(sub_args[6]) if len(sub_args) > 6 else 0
                bri = int(sub_args[7]) if len(sub_args) > 7 else 100
                spd = int(sub_args[8]) if len(sub_args) > 8 else 50
                set_lighting_reactive(r, g, b, r2, g2, b2, brightness=bri, speed=spd)
            elif sub == "yeti":
                r  = int(sub_args[1]) if len(sub_args) > 1 else 255
                g  = int(sub_args[2]) if len(sub_args) > 2 else 0
                b  = int(sub_args[3]) if len(sub_args) > 3 else 0
                r2 = int(sub_args[4]) if len(sub_args) > 4 else 0
                g2 = int(sub_args[5]) if len(sub_args) > 5 else 0
                b2 = int(sub_args[6]) if len(sub_args) > 6 else 255
                bri = int(sub_args[7]) if len(sub_args) > 7 else 100
                spd = int(sub_args[8]) if len(sub_args) > 8 else 50
                set_lighting_yeti(r, g, b, r2, g2, b2, brightness=bri, speed=spd)
            else:
                _die(f"unknown rgb subcommand '{sub}'")
            print("ok")
        except RuntimeError as e:
            _die(str(e))
    elif cmd == "per-key-rgb":
        if len(args) < 2:
            _die("per-key-rgb: JSON payload required")
        import json as _j
        try:
            d = _j.loads(args[1])
        except Exception as e:
            _die(f"per-key-rgb: invalid JSON: {e}")
        leds_raw   = d.get("leds", [])
        brightness = int(d.get("brightness", 100))
        colors = [tuple(c) for c in leds_raw]
        try:
            set_lighting_custom(colors, brightness=brightness)
            print("ok")
        except RuntimeError as e:
            _die(str(e))

    else:
        _die(f"unknown command '{cmd}'")


if __name__ == "__main__":
    main()
