#!/usr/bin/env python3
"""
Mountain Everest 60 Controller
VID: 0x3282, PID: 0x0005 (ANSI) / 0x0006 (ISO)
Protocol: HID Feature Reports on Interface 2

Reverse-engineered from OpenRGB MountainKeyboard60Controller + FransM's findings.
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
  [9]    = color_mode (0=single, 2=rainbow cycle, 0x10=dual)
  [10]   = direction
  [12..14] = color1 R,G,B
  [15..17] = color2 R,G,B

After set_report, get_report should echo cmd byte in resp[1].
If not, retry (device may be busy).

Direct color mode (custom per-key):
  Begin:  cmd=0x34, [5]=brightness×25, [6]=0xC0
  Map:    cmd=0x35, [5]=stream_ctl (0x0E=more, 0x0A=last), then 14 × RGBI (56 bytes)
          byte[3] of each entry = LED hardware index (LEDIDX mapping)
  End:    cmd=0x36
"""
import os
import sys
import time

# The device controllers also run as standalone scripts (the app spawns them
# as subprocesses), so the repository root is not on sys.path by itself.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared import hid_compat
from core.devices.everest60 import device as _device
from core.devices.everest60 import protocol as _P
# The protocol lives in the core (core/devices/everest60/protocol.py); these
# names stay here for whoever imports them from the controller.
from core.devices.everest60.protocol import (                  # noqa: F401
    VID, PID_ANSI, PID_ISO, INTERFACE, MAGIC,
    EFFECT_STATIC, EFFECT_WAVE, EFFECT_TORNADO, EFFECT_BREATHING,
    EFFECT_REACTIVE, EFFECT_MATRIX, EFFECT_CUSTOM, EFFECT_YETI, EFFECT_OFF,
    COLOR_SINGLE, COLOR_RAINBOW, COLOR_DUAL, DIR_WAVE, DIR_TORNADO,
    NUM_KEYS, SIDE_LED_INDICES, NUM_SIDE_LEDS, LEDIDX,
)
HID_AVAILABLE = hid_compat.HID_AVAILABLE

PID         = PID_ANSI   # updated at runtime by detect_model()


def detect_model():
    """Detect which Everest 60 variant is connected. Returns (pid, name) or (None, None)."""
    global PID
    if not HID_AVAILABLE:
        return None, None
    pid, name = _device.detect()
    if pid:
        PID = pid
    return pid, name


def find_path():
    """Return HID path for Interface 2, or None."""
    if not HID_AVAILABLE:
        return None
    return _device.find_path()


def open_device():
    path = find_path()
    if path is None:
        raise RuntimeError(_P.NOT_FOUND)
    return hid_compat.open_path(path)


def _send(dev, buf, retries=3):
    """Send feature report, verify response echoes cmd byte in resp[1], retry if not."""
    return _device.exchange(dev, buf, retries)


_make_buf = _P.make_buf
_brightness_val = _P.brightness_val
_speed_val = _P.speed_val


# ── Lighting commands ─────────────────────────────────────────────────────────

def _send_mode(dev, effect, speed=50, brightness=100,
               r1=255, g1=255, b1=255, r2=0, g2=0, b2=0,
               color_mode=COLOR_DUAL, direction=0):
    """Switch to an effect (0x16), then send its details (0x17)."""
    for buf in _P.effect_packets(effect, speed=speed, brightness=brightness,
                                 r1=r1, g1=g1, b1=b1, r2=r2, g2=g2, b2=b2,
                                 color_mode=color_mode, direction=direction):
        _send(dev, buf)


def _set_mode_only(dev, effect):
    """Activate an effect (cmd 0x16) without colour details (0x17): custom mode
    must not send 0x17, whose default white colour 1 flashed the board (#33)."""
    _send(dev, _P.mode_packet(effect))


def _commit_mode(dev, effect):
    """Latch the active effect (cmd 0x1a), see #33."""
    _send(dev, _P.latch_packet(effect))


def _with_device(fn):
    dev = open_device()
    try:
        fn(dev)
    finally:
        dev.close()


def set_lighting_off(brightness=100):
    _with_device(lambda dev: _send_mode(dev, EFFECT_OFF, brightness=brightness))


def set_lighting_static(r, g, b, brightness=100):
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_STATIC, color_mode=COLOR_SINGLE, brightness=brightness,
        r1=r, g1=g, b1=b))


def set_lighting_breathing(r=255, g=0, b=0, r2=0, g2=0, b2=0, brightness=100, speed=50,
                           color_mode=COLOR_DUAL):
    # color_mode: COLOR_SINGLE (one colour) or COLOR_DUAL (two). Breathing also
    # supports rainbow via set_lighting_breathing_rainbow (issue #32).
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_BREATHING, speed=speed, brightness=brightness,
        r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2, color_mode=color_mode))


def set_lighting_breathing_rainbow(brightness=100, speed=50):
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_BREATHING, speed=speed, brightness=brightness,
        color_mode=COLOR_RAINBOW))


def set_lighting_wave(r=255, g=0, b=0, r2=0, g2=0, b2=0, brightness=100, speed=50,
                      direction=0, color_mode=COLOR_DUAL):
    # color_mode: COLOR_SINGLE or COLOR_DUAL. Rainbow via set_lighting_wave_rainbow.
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_WAVE, speed=speed, brightness=brightness,
        r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2, direction=direction,
        color_mode=color_mode))


def set_lighting_wave_rainbow(brightness=100, speed=50, direction=0):
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_WAVE, speed=speed, brightness=brightness,
        color_mode=COLOR_RAINBOW, direction=direction))


def set_lighting_tornado(r=255, g=0, b=0, brightness=100, speed=50, direction=0):
    wire = _P.tornado_direction(direction)
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_TORNADO, speed=speed, brightness=brightness,
        color_mode=COLOR_SINGLE, r1=r, g1=g, b1=b, direction=wire))


def set_lighting_tornado_rainbow(brightness=100, speed=50, direction=0):
    wire = _P.tornado_direction(direction)
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_TORNADO, speed=speed, brightness=brightness,
        color_mode=COLOR_RAINBOW, direction=wire))


def set_lighting_reactive(r=255, g=0, b=0, r2=0, g2=0, b2=0, brightness=100, speed=50):
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_REACTIVE, speed=speed, brightness=brightness,
        r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2))


def set_lighting_matrix(r=255, g=0, b=0, r2=0, g2=0, b2=255, brightness=100, speed=50):
    # Matrix is a dual-colour firmware effect (issue #38) with the same 0x17
    # layout as the others; only the effect code (0x06) differs.
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_MATRIX, speed=speed, brightness=brightness,
        r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2))


def set_lighting_yeti(r=255, g=0, b=0, r2=0, g2=0, b2=255, brightness=100, speed=50):
    _with_device(lambda dev: _send_mode(
        dev, EFFECT_YETI, speed=speed, brightness=brightness,
        r1=r, g1=g, b1=b, r2=r2, g2=g2, b2=b2))


def set_lighting_custom(colors, brightness=100, side_colors=None):
    """Set per-key RGB. colors: list of 64 (r,g,b) tuples mapped via LEDIDX.

    side_colors: optional list of NUM_SIDE_LEDS (r,g,b) for the perimeter ring,
                 clockwise starting top-left (above ESC). When None, side ring
                 LEDs are left untouched in this call but the device's custom
                 mode keeps them dark unless they were set previously.
    """
    stream = _P.custom_stream(colors, side_colors)
    _with_device(lambda dev: _write_custom_map(dev, stream, brightness=brightness))


def _write_custom_map(dev, stream, brightness=100):
    """Paint an explicit [(hw_index, r, g, b), ...] stream in custom mode
    (protocol.custom_map_packets has the sequence and why it pads as it does)."""
    for buf in _P.custom_map_packets(stream, brightness=brightness):
        _send(dev, buf)


def diagnose_esc_index(start=0, end=21, hold=3.0):
    """ESC-index finder for issue #46.

    ESC is index 0 (confirmed by @FransM), which is what LEDIDX[0] now uses. This
    scanner stays as a way to re-verify or to map a variant: it walks a range of
    candidate indices, lighting every other key dim blue and ONE candidate index
    bright red at a time, so someone with the keyboard can watch and report which
    index turns the physical ESC key red."""
    dev = open_device()
    print(f"ESC-index scan {start}..{end}. Watch the ESC key; note the index that "
          f"turns it RED. All other keys stay dim blue. {hold}s per index.\n")
    try:
        for cand in range(start, end + 1):
            _write_custom_map(dev, _P.esc_scan_stream(cand), brightness=100)
            print(f"  index {cand:3d}: ESC red now?", flush=True)
            time.sleep(hold)
    finally:
        dev.close()
    print("\nDone. Report the index that lit ESC so LEDIDX[0] can be corrected.")


def set_lighting_side_static(r, g, b, brightness=100, key_colors=None):
    """Light all 44 side-ring LEDs in a single colour.

    The device only addresses the side ring through custom mode, so a key map
    goes along. With no `key_colors` the keys are lit white rather than
    blanked: a dark keyboard under low light is worse than a neutral default
    (issue #4, FransM). Callers pass the saved colours when they have them.
    """
    side = [(r & 0xFF, g & 0xFF, b & 0xFF)] * NUM_SIDE_LEDS
    keys = key_colors if key_colors is not None else [(255, 255, 255)] * NUM_KEYS
    set_lighting_custom(keys, brightness=brightness, side_colors=side)


def _load_saved_key_colors():
    """Return the user's last per-key colours so a side-ring command keeps the
    main keys lit instead of blanking them (issue #4). The GUI side picker does
    this via per-key-rgb; loading the same saved state here makes the CLI /
    control-interface `side-static` behave identically. None if unavailable."""
    try:
        from shared.config import _load_per_key_60
        leds, _side, _bri = _load_per_key_60()
        return [tuple(c) for c in leds][:NUM_KEYS]
    except Exception:
        return None


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
                cm  = int(sub_args[9]) if len(sub_args) > 9 else COLOR_DUAL
                set_lighting_breathing(r, g, b, r2, g2, b2, brightness=bri, speed=spd,
                                       color_mode=cm)
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
                cm  = int(sub_args[10]) if len(sub_args) > 10 else COLOR_DUAL
                set_lighting_wave(r, g, b, r2, g2, b2, brightness=bri, speed=spd,
                                  direction=d, color_mode=cm)
            elif sub == "wave-rainbow":
                bri = int(sub_args[1]) if len(sub_args) > 1 else 100
                spd = int(sub_args[2]) if len(sub_args) > 2 else 50
                d   = int(sub_args[3]) if len(sub_args) > 3 else 0
                set_lighting_wave_rainbow(brightness=bri, speed=spd, direction=d)
            elif sub == "tornado":
                r  = int(sub_args[1]) if len(sub_args) > 1 else 255
                g  = int(sub_args[2]) if len(sub_args) > 2 else 0
                b  = int(sub_args[3]) if len(sub_args) > 3 else 0
                bri = int(sub_args[4]) if len(sub_args) > 4 else 100
                spd = int(sub_args[5]) if len(sub_args) > 5 else 50
                d   = int(sub_args[6]) if len(sub_args) > 6 else 0
                set_lighting_tornado(r, g, b, brightness=bri, speed=spd, direction=d)
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
            elif sub == "matrix":
                r  = int(sub_args[1]) if len(sub_args) > 1 else 255
                g  = int(sub_args[2]) if len(sub_args) > 2 else 0
                b  = int(sub_args[3]) if len(sub_args) > 3 else 0
                r2 = int(sub_args[4]) if len(sub_args) > 4 else 0
                g2 = int(sub_args[5]) if len(sub_args) > 5 else 0
                b2 = int(sub_args[6]) if len(sub_args) > 6 else 255
                bri = int(sub_args[7]) if len(sub_args) > 7 else 100
                spd = int(sub_args[8]) if len(sub_args) > 8 else 50
                set_lighting_matrix(r, g, b, r2, g2, b2, brightness=bri, speed=spd)
            elif sub == "side-static":
                if len(sub_args) < 4:
                    _die("rgb side-static R G B [brightness]")
                r, g, b = int(sub_args[1]), int(sub_args[2]), int(sub_args[3])
                bri = int(sub_args[4]) if len(sub_args) > 4 else 100
                # Keep the user's main-key colours instead of blanking the
                # keyboard (issue #4) — matches what the GUI side picker does.
                set_lighting_side_static(r, g, b, brightness=bri,
                                         key_colors=_load_saved_key_colors())
            elif sub == "esc-scan":
                # ESC-index finder (issue #46): rgb esc-scan [start] [end] [hold]
                start = int(sub_args[1]) if len(sub_args) > 1 else 0
                end   = int(sub_args[2]) if len(sub_args) > 2 else 21
                hold  = float(sub_args[3]) if len(sub_args) > 3 else 3.0
                diagnose_esc_index(start=start, end=end, hold=hold)
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
        side_raw   = d.get("side", [])
        brightness = int(d.get("brightness", 100))
        colors = [tuple(c) for c in leds_raw]
        side = [tuple(c) for c in side_raw] if side_raw else None
        try:
            set_lighting_custom(colors, brightness=brightness, side_colors=side)
            print("ok")
        except RuntimeError as e:
            _die(str(e))

    else:
        _die(f"unknown command '{cmd}'")


if __name__ == "__main__":
    main()
