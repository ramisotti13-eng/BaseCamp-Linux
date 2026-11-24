#!/usr/bin/env python3
"""
Makalu 67 Controller
VID: 0x3282, PID: 0x0003
Protocol: HID Feature Reports on Interface 1

Protocol reverse-engineered from USB capture (Windows VirtualBox session).
Report ID 0xA1, 64 bytes, SET_REPORT to Interface 1.
Response: Report ID 0xA0, byte[1]=0x01 = OK.

Command 0x0C layout (64 bytes):
  [0]      = 0xA1  (Report ID)
  [1]      = 0x0C  (command = Update_lighting_settings)
  [5]      = 0x01  (always 1)
  [16]     = effect (0=Off, 1=Static, 2=Rainbow, 5=Breathing, 6=RGB Breathing,
                     7=Responsive, 8=Yeti, 0x0F=Custom per-LED)
  [17]     = R (Zone 1 / LED 0 for Custom)
  [18]     = G
  [19]     = B
  [41]     = brightness (0–100); device always saves to flash (resp[1]=0x03 for all values)
  [42]     = param1 (always 0)
  [43]     = param2 (animation speed: 0=slow, 1=medium, 2=fast)

Custom per-LED mode (effect=0x0F), confirmed by USB capture:
  [16]     = 0x0F
  [17..19] = LED 0 R,G,B  (top-left)
  [20..22] = LED 1 R,G,B
  [23..25] = LED 2 R,G,B
  [26..28] = LED 3 R,G,B  (bottom-left)
  [29..31] = LED 4 R,G,B  (bottom-right)
  [32..34] = LED 5 R,G,B
  [35..37] = LED 6 R,G,B
  [38..40] = LED 7 R,G,B  (top-right)
  [41]     = brightness (0–100)

  Physical layout:
    LED[0] ·· LED[7]
    LED[1]    LED[6]
    LED[2]    LED[5]
    LED[3] ·· LED[4]
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
from core.devices.makalu import device as _device
from core.devices.makalu import protocol as _P
# The protocol lives in the core (core/devices/makalu/protocol.py); these
# names stay here for whoever imports them from the controller.
from core.devices.makalu.protocol import (                     # noqa: F401
    VID, PID_67, PID_MAX, REPORT_ID, RESP_ID,
    CMD_LIGHTING, CMD_POLLING_RATE, CMD_DPI, CMD_WRITE_MEM, CMD_ENABLE, CMD_REMAP,
    BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_BACK, BTN_FORWARD, BTN_DPI,
    BTN_MAX_FORWARD, BTN_MAX_BACK, REMAP_FUNCTIONS,
    REMAP_DEFAULTS_67, REMAP_DEFAULTS_MAX, POLLING_RATE_MAP, DEBOUNCE_VALUES,
    DPI_MAX, DPI_STEP,
    EFFECT_OFF, EFFECT_STATIC, EFFECT_RAINBOW, EFFECT_BREATHING,
    EFFECT_RGB_BREATHING, EFFECT_RESPONSIVE, EFFECT_YETI, EFFECT_CUSTOM,
)
HID_AVAILABLE = hid_compat.HID_AVAILABLE

# Model-dependent, set by detect_model(). main() never calls it, so the CLI
# always works with the Makalu 67 values (see #115 for the Max).
PID = PID_67
REMAP_DEFAULTS = REMAP_DEFAULTS_67
DPI_MIN = _P.DPI_MIN


# ── Device access ─────────────────────────────────────────────────────────────

def detect_model():
    """Detect which Makalu is connected. Returns (pid, model_name) or (None, None)."""
    global PID, DPI_MIN, REMAP_DEFAULTS
    if not HID_AVAILABLE:
        return None, None
    pid, name = _device.detect()
    if pid is None:
        return None, None
    PID = pid
    if pid == PID_MAX:
        DPI_MIN = _P.DPI_MIN_MAX
        REMAP_DEFAULTS = REMAP_DEFAULTS_MAX
    else:
        DPI_MIN = _P.DPI_MIN
        REMAP_DEFAULTS = REMAP_DEFAULTS_67
    return pid, name


def find_path():
    """Return path of Interface 1 hidraw node, or None."""
    if not HID_AVAILABLE:
        return None
    return _device.find_path()


def open_device():
    path = find_path()
    if path is None:
        raise RuntimeError(_P.NOT_FOUND)
    return hid_compat.open_path(path)


def _max_button():
    return 8 if PID == PID_MAX else 6


# ── Lighting ──────────────────────────────────────────────────────────────────

_lighting_report = _P.lighting_report


def _run_cmd(buf):
    """Open device, send feature report, get response, close. Returns True if OK."""
    return _device.run(buf, opener=open_device)


def set_lighting_off(brightness=100):
    return _run_cmd(_lighting_report(EFFECT_OFF, brightness=brightness))


def set_lighting_static(r, g, b, brightness=100):
    return _run_cmd(_lighting_report(EFFECT_STATIC, r, g, b, brightness=brightness))


def set_lighting_breathing(r=0, g=0, b=0, brightness=100, speed=1):
    return _run_cmd(_lighting_report(EFFECT_BREATHING, r, g, b,
                                     brightness=brightness, param1=speed, param2=speed))


def set_lighting_rainbow(brightness=100):
    return _run_cmd(_lighting_report(EFFECT_RAINBOW, brightness=brightness, param1=1, param2=1))


def set_lighting_rgb_breathing(brightness=100):
    return _run_cmd(_lighting_report(EFFECT_RGB_BREATHING, brightness=brightness, param1=1, param2=1))


def set_lighting_responsive(brightness=100):
    return _run_cmd(_lighting_report(EFFECT_RESPONSIVE, brightness=brightness, param1=1, param2=1))


def set_lighting_yeti(brightness=100):
    return _run_cmd(_lighting_report(EFFECT_YETI, brightness=brightness, param1=1, param2=1))


def set_lighting_custom(leds, brightness=100):
    """Set per-LED colors.

    leds: list of 8 (r, g, b) tuples.
    Physical layout:
      leds[0]=top-left   leds[7]=top-right
      leds[1]            leds[6]
      leds[2]            leds[5]
      leds[3]=bot-left   leds[4]=bot-right
    """
    return _run_cmd(_P.custom_report(leds, brightness))


# ── Polling rate ──────────────────────────────────────────────────────────────

def set_polling_rate(hz):
    """Set polling rate. hz must be one of 125, 250, 500, 1000."""
    return _run_cmd(_P.polling_rate_report(hz))


def set_debounce(ms):
    """Set button debounce time. ms must be one of 2, 4, 6, 8, 10, 12."""
    return _run_cmd(_P.debounce_report(ms))


def set_lift_off(high):
    """Set lift-off distance. high=True → High, high=False → Low."""
    return _run_cmd(_P.lift_off_report(high))


def set_angle_snapping(enabled):
    """Enable or disable angle snapping. enabled: True/False."""
    return _run_cmd(_P.angle_snapping_report(enabled))


# ── DPI ───────────────────────────────────────────────────────────────────────

def get_dpi():
    """Read all 5 DPI level values from the mouse: (levels, active level 0-based).

    Protocol: CMD 0x0B sub=0x07 (Read_profile_data); see protocol.parse_dpi.
    """
    dev = open_device()
    try:
        resp = _device.query(dev, _P.dpi_query_report())
        return _P.parse_dpi(resp, DPI_MIN)
    finally:
        dev.close()


def _dpi_to_raw(dpi):
    """User DPI to the PAW3335 sensor encoding (unused; see protocol.dpi_to_raw)."""
    return _P.dpi_to_raw(dpi, DPI_MIN)


def _write_mem_byte(dev, address, value):
    """Write a single byte to mouse RAM at the given address (CMD 0xDE, unused).

    Profile memory layout (from Makalu.dll): profile i starts at 256 + i*256;
    DPI1 lo/hi at offsets 5/6.
    """
    dev.send_feature_report(bytes(_P.write_mem_report(address, value)))
    time.sleep(0.05)


def set_all_dpi(dpi_list, active_level=1):
    """Set all 5 DPI levels (CMD 0x0D sub 0x0A, Set_dpi_settings).

    dpi_list: 5 ints, 50–19000 in steps of 50, the levels the DPI button
    cycles through. active_level is 1-based. Applied to all profiles
    (ALL_PROFILE=6). See protocol.dpi_report for the layout (the DPI_T struct
    of BaseCamp.Service.exe).
    """
    return _run_cmd(_P.dpi_report(dpi_list, active_level, DPI_MIN))


# ── Button remapping ──────────────────────────────────────────────────────────

def set_button_sniper(button_index, sniper_dpi):
    """Assign DPI Sniper to a button: while held, the firmware switches to
    sniper_dpi; on release the level's DPI comes back (category 0x0C,
    capture_makalu_sniper.pcapng)."""
    return _run_cmd(_P.sniper_report(button_index, sniper_dpi, _max_button(), DPI_MIN))


def set_button_remap(button_index, function_name):
    """Remap a physical button to a function from REMAP_FUNCTIONS.

    button_index: 1–6 (1=Left, 2=Right, 3=Middle, 4=Back, 5=Forward, 6=DPI+)
    (capture_makalu_remap2.pcap).
    """
    return _run_cmd(_P.remap_report(button_index, function_name, _max_button()))


# ── CLI ───────────────────────────────────────────────────────────────────────

def _die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _usage():
    print("""Usage: makalu-controller <command> [args]

Commands:
  rgb off                     Turn LEDs off
  rgb static <R> <G> <B>      Static color  (0-255 each)
  rgb breathing [R G B]       Breathing effect (optional color)
  rgb rainbow                 Rainbow cycling effect
  rgb live static <R> <G> <B> Live preview (no flash write)
  status                      Check device presence
""")


def main():
    args = sys.argv[1:]
    if not args:
        _usage()
        sys.exit(1)

    cmd = args[0]

    if cmd == "status":
        path = find_path()
        if path:
            print(f"connected: {path.decode() if isinstance(path, bytes) else path}")
        else:
            print("not connected")
            sys.exit(1)

    elif cmd == "rgb":
        if len(args) < 2:
            _die("rgb: subcommand required (off/static/breathing/rainbow/live)")

        live = args[1] == "live"
        sub_args = args[2:] if live else args[1:]
        sub = sub_args[0] if sub_args else ""
        save = not live

        try:
            if sub == "off":
                ok = set_lighting_off(brightness=100)
            elif sub == "static":
                if len(sub_args) < 4:
                    _die("rgb static requires R G B")
                r, g, b = int(sub_args[1]), int(sub_args[2]), int(sub_args[3])
                ok = set_lighting_static(r, g, b)
            elif sub == "breathing":
                r = int(sub_args[1]) if len(sub_args) > 1 else 0
                g = int(sub_args[2]) if len(sub_args) > 2 else 0
                b = int(sub_args[3]) if len(sub_args) > 3 else 0
                ok = set_lighting_breathing(r, g, b)
            elif sub == "rainbow":
                ok = set_lighting_rainbow()
            elif sub == "custom":
                # rgb custom R0 G0 B0 R1 G1 B1 ... R7 G7 B7 [brightness]
                if len(sub_args) < 25:
                    _die("rgb custom requires 24 values (R G B × 8 LEDs)")
                leds = []
                for i in range(8):
                    r = int(sub_args[1 + i * 3])
                    g = int(sub_args[2 + i * 3])
                    b = int(sub_args[3 + i * 3])
                    leds.append((r, g, b))
                bri = int(sub_args[25]) if len(sub_args) > 25 else 100
                ok = set_lighting_custom(leds, brightness=bri)
            elif sub == "code":
                if len(sub_args) < 2:
                    _die("rgb code requires effect code")
                code = int(sub_args[1])
                r   = int(sub_args[2]) if len(sub_args) > 2 else 0
                g   = int(sub_args[3]) if len(sub_args) > 3 else 0
                b   = int(sub_args[4]) if len(sub_args) > 4 else 0
                bri = int(sub_args[5]) if len(sub_args) > 5 else 100
                spd = int(sub_args[6]) if len(sub_args) > 6 else 0
                dir_ = int(sub_args[7]) if len(sub_args) > 7 else 0
                buf = _lighting_report(code, r, g, b, brightness=bri, param1=dir_, param2=spd)
                ok = _run_cmd(buf)
            elif sub == "code2":
                # rgb code2 <effect> <R> <G> <B> <R2> <G2> <B2> <brightness> [speed]
                if len(sub_args) < 8:
                    _die("rgb code2 requires effect R G B R2 G2 B2")
                code = int(sub_args[1])
                r,  g,  b  = int(sub_args[2]), int(sub_args[3]), int(sub_args[4])
                r2, g2, b2 = int(sub_args[5]), int(sub_args[6]), int(sub_args[7])
                bri = int(sub_args[8]) if len(sub_args) > 8 else 100
                spd  = int(sub_args[9])  if len(sub_args) > 9  else 0
                dir_ = int(sub_args[10]) if len(sub_args) > 10 else 0
                ok = _run_cmd(_P.lighting_report2(code, r, g, b, r2, g2, b2, brightness=bri,
                                                  param1=dir_, param2=spd))
            else:
                _die(f"rgb: unknown subcommand '{sub}'")
            print("ok" if ok else "failed")
        except RuntimeError as e:
            _die(str(e))

    elif cmd == "lift-off":
        if len(args) < 2:
            _die("lift-off requires <low|high>")
        val = args[1].lower()
        if val not in ("low", "high"):
            _die("lift-off: must be 'low' or 'high'")
        try:
            ok = set_lift_off(val == "high")
            print("ok" if ok else "failed")
        except RuntimeError as e:
            _die(str(e))

    elif cmd == "angle-snapping":
        if len(args) < 2:
            _die("angle-snapping requires <on|off>")
        val = args[1].lower()
        if val not in ("on", "off"):
            _die("angle-snapping: must be 'on' or 'off'")
        try:
            ok = set_angle_snapping(val == "on")
            print("ok" if ok else "failed")
        except RuntimeError as e:
            _die(str(e))

    elif cmd == "debounce":
        if len(args) < 2:
            _die("debounce requires <ms>: 2, 4, 6, 8, 10, 12")
        try:
            ok = set_debounce(int(args[1]))
            print("ok" if ok else "failed")
        except (ValueError, RuntimeError) as e:
            _die(str(e))

    elif cmd == "polling-rate":
        if len(args) < 2:
            _die("polling-rate requires <hz>: 125, 250, 500, 1000")
        try:
            ok = set_polling_rate(int(args[1]))
            print("ok" if ok else "failed")
        except (ValueError, RuntimeError) as e:
            _die(str(e))

    elif cmd == "dpi":
        if len(args) < 2:
            _die("dpi requires 'get' or 5 DPI values (P1 P2 P3 P4 P5)")
        if args[1] == "dump":
            # Full 64-byte dump of sub=0x07 (Read_profile_data)
            dev = open_device()
            try:
                resp = _device.query(dev, _P.dpi_query_report(), wait=0.08)
                print(" ".join(f"{b:02x}" for b in resp))
                for i in range(16, len(resp)):
                    print(f"  [{i:2d}] = 0x{resp[i]:02x} ({resp[i]})")
            except RuntimeError as e:
                _die(str(e))
            finally:
                dev.close()
        elif args[1] == "get":
            try:
                levels, current = get_dpi()
                print(" ".join(str(p) for p in levels) + f" {current}")
            except RuntimeError as e:
                _die(str(e))
        else:
            if len(args) < 6:
                _die("dpi requires exactly 5 values: dpi <p1> <p2> <p3> <p4> <p5> [active]")
            try:
                dpi_list = [int(args[i + 1]) for i in range(5)]
                active = int(args[6]) if len(args) > 6 else 1
                ok = set_all_dpi(dpi_list, active_level=active)
                print("ok" if ok else "failed")
            except (ValueError, RuntimeError) as e:
                _die(str(e))

    elif cmd == "remap":
        # remap <button_index> <function>
        # e.g.: remap 2 forward
        if len(args) < 3:
            fns = ", ".join(REMAP_FUNCTIONS.keys())
            _die(f"remap requires <button 1-6> <function>\n  functions: {fns}")
        try:
            ok = set_button_remap(int(args[1]), args[2])
            print("ok" if ok else "failed")
        except (ValueError, RuntimeError) as e:
            _die(str(e))

    elif cmd == "sniper":
        # sniper <button_index> <dpi>
        # e.g.: sniper 4 400
        if len(args) < 3:
            _die("sniper requires <button 1-6> <dpi 50-19000>")
        try:
            ok = set_button_sniper(int(args[1]), int(args[2]))
            print("ok" if ok else "failed")
        except (ValueError, RuntimeError) as e:
            _die(str(e))

    else:
        _die(f"unknown command '{cmd}'")


if __name__ == "__main__":
    main()
