"""Everest 60 wire protocol, effect table and controller arguments. Pure.

Nothing in here does I/O: it builds the 65-byte feature reports the keyboard
takes (report ID 0, then the command byte, then the magic bytes 46 23 EA),
says which effects exist and what each one offers, and turns lighting
settings into the arguments of the controller CLI. The HID exchange is in
core/devices/everest60/device.py, the CLI in devices/everest60/controller.py.

Protocol (reverse engineered from OpenRGB's MountainKeyboard60Controller and
FransM's captures; interface 2, every report answered by a GET_REPORT that
echoes the command byte in resp[1]):

    0x16  set mode        [5]=1, [9]=effect
    0x17  mode details    [5]=effect, [7]=speed, [8]=brightness (both in 25s),
                          [9]=colour mode (0 single, 2 rainbow, 0x10 dual),
                          [10]=direction, [12..14] colour 1, [15..17] colour 2
    0x1a  latch           [5]=effect
    0x34  custom begin    [5]=brightness, [6]=0xC0
    0x35  custom map      [5]=0x0E more / 0x0A last, then 14 x (led, r, g, b)
    0x36  custom end
"""
import json
from collections import namedtuple

VID = 0x3282
PID_ANSI = 0x0005
PID_ISO = 0x0006
#: (pid, model name), in the order they are looked for.
MODELS = ((PID_ANSI, "Everest 60"), (PID_ISO, "Everest 60 ISO"))
DEFAULT_MODEL = "Everest 60"
INTERFACE = 2
REPORT_LEN = 65
NOT_FOUND = "Everest 60 not found (VID=0x3282 PID=0x0005/0x0006 IF2)"

MAGIC = (0x46, 0x23, 0xEA)

CMD_MODE = 0x16
CMD_DETAILS = 0x17
CMD_LATCH = 0x1A
CMD_MAP_BEGIN = 0x34
CMD_MAP = 0x35
CMD_MAP_END = 0x36

# Effect codes
EFFECT_STATIC = 0x01
EFFECT_WAVE = 0x02
EFFECT_TORNADO = 0x03
EFFECT_BREATHING = 0x04
EFFECT_REACTIVE = 0x05
EFFECT_MATRIX = 0x06     # issue #38, from matrix.pcapng (dual colour)
EFFECT_CUSTOM = 0x07
EFFECT_YETI = 0x08
EFFECT_OFF = 0x09

# Colour mode
COLOR_SINGLE = 0x00
COLOR_RAINBOW = 0x02
COLOR_DUAL = 0x10

# Direction values on the wire (Wave/Tornado)
DIR_WAVE = {"L→R": 0x00, "T→B": 0x02, "R→L": 0x04, "B→T": 0x06}
DIR_TORNADO = {"CW": 0x0A, "CCW": 0x09}

NUM_KEYS = 64

# Side LED ring: 44 RGB LEDs around the keyboard (indices 126..169), from a
# USBPcap of the Windows software lighting them one at a time, starting
# top-left (above ESC) and going clockwise.
SIDE_LED_INDICES = list(range(126, 170))
NUM_SIDE_LEDS = len(SIDE_LED_INDICES)

# Firmware LED address of each logical key. ESC is 0 (confirmed by @FransM,
# #46); it read dark in #15 only because a zero-padded 0x35 tail re-wrote
# index 0 black, which custom_map_packets() avoids.
LEDIDX = [
    # Row 0: ESC  1    2    3    4    5    6    7    8    9    0    -    =   BSPC
    0,   22,  23,  24,  25,  26,  27,  28,  29,  30,  31,  32,  33,  34,
    # Row 1: TAB  Q    W    E    R    T    Y    U    I    O    P    [    ]    \
    42,  43,  44,  45,  46,  47,  48,  49,  50,  51,  52,  53,  54,  55,
    # Row 2: CAPS A    S    D    F    G    H    J    K    L    ;    '   ENTER
    63,  64,  65,  66,  67,  68,  69,  70,  71,  72,  73,  74,  76,
    # Row 3: LSFT Z    X    C    V    B    N    M    ,    .    /   RSFT  ↑   DEL
    84,  85,  86,  87,  88,  89,  90,  91,  92,  93,  94,  97,  99,  56,
    # Row 4: LCTL LWIN LALT SPC  RALT FN   ←    ↓    →
    105, 106, 107, 110, 113, 115, 119, 120, 121,
]

#: Entries per 0x35 packet: (65 - 9 header bytes) // 4.
COLORS_PER_PKT = 14


# ── Packets ──────────────────────────────────────────────────────────────────

def make_buf(cmd):
    """A zeroed 65-byte report (as a list) with the command and magic set."""
    buf = [0x00] * REPORT_LEN
    buf[1] = cmd
    buf[2], buf[3], buf[4] = MAGIC
    return buf


def step25(pct):
    """0..100 to the nearest step of 25, the resolution the firmware takes."""
    pct = max(0, min(100, int(pct)))
    return round(pct / 25) * 25


brightness_val = step25
speed_val = step25


def mode_packet(effect):
    """0x16: switch to an effect."""
    buf = make_buf(CMD_MODE)
    buf[5] = 1
    buf[9] = effect
    return bytes(buf)


def details_packet(effect, speed=50, brightness=100, r1=255, g1=255, b1=255,
                   r2=0, g2=0, b2=0, color_mode=COLOR_DUAL, direction=0):
    """0x17: an effect's speed, brightness, colour mode, direction, colours.

    A rainbow carries no colour; a single colour carries colour 1 only.
    """
    buf = make_buf(CMD_DETAILS)
    buf[5] = effect
    buf[7] = speed_val(speed)
    buf[8] = brightness_val(brightness)
    buf[9] = color_mode
    buf[10] = direction
    if color_mode != COLOR_RAINBOW:
        buf[12] = r1 & 0xFF
        buf[13] = g1 & 0xFF
        buf[14] = b1 & 0xFF
        if color_mode == COLOR_DUAL:
            buf[15] = r2 & 0xFF
            buf[16] = g2 & 0xFF
            buf[17] = b2 & 0xFF
    return bytes(buf)


def effect_packets(effect, **details):
    """The two reports that set a firmware effect: mode, then details."""
    return [mode_packet(effect), details_packet(effect, **details)]


def latch_packet(effect):
    """0x1a: latch the active effect. Windows sends it after a mode switch and
    after each custom map; without it a fresh map could leave keys showing
    their old colour (half of #33)."""
    buf = make_buf(CMD_LATCH)
    buf[5] = effect
    return bytes(buf)


def tornado_direction(direction):
    """The CLI's tornado direction (0 clockwise, 1 counter-clockwise, up to
    10) as the firmware wants it."""
    return 10 - max(0, min(10, direction))


def custom_stream(colors, side_colors=None):
    """[(led address, r, g, b)] for the 64 keys and, if given, the side ring.

    Missing keys or ring LEDs are black; extra ones are dropped.
    """
    num_keys = len(LEDIDX)
    colors = list(colors)[:num_keys]
    while len(colors) < num_keys:
        colors.append((0, 0, 0))
    stream = [(LEDIDX[i], colors[i][0], colors[i][1], colors[i][2])
              for i in range(num_keys)]
    if side_colors is not None:
        side = list(side_colors)[:NUM_SIDE_LEDS]
        while len(side) < NUM_SIDE_LEDS:
            side.append((0, 0, 0))
        for i, hw in enumerate(SIDE_LED_INDICES):
            r, g, b = side[i]
            stream.append((hw, r & 0xFF, g & 0xFF, b & 0xFF))
    return stream


def custom_map_packets(stream, brightness=100):
    """Every report that paints an explicit (led, r, g, b) stream.

    Custom mode is switched on without 0x17 details (those default colour 1
    to white and flashed the board white first, #33), latched, then the map
    goes out 14 entries per report and is latched again. The last report is
    padded with copies of the last real entry, never with zeros: a zero entry
    is a black write to LED 0, which is ESC (#15/#46).
    """
    packets = [mode_packet(EFFECT_CUSTOM), latch_packet(EFFECT_CUSTOM)]
    buf = make_buf(CMD_MAP_BEGIN)
    buf[5] = brightness_val(brightness)
    buf[6] = 0xC0
    packets.append(bytes(buf))

    stream = list(stream)
    if stream:
        while len(stream) % COLORS_PER_PKT != 0:
            stream.append(stream[-1])
    total = len(stream)
    idx = 0
    while idx < total:
        buf = make_buf(CMD_MAP)
        pos = 9
        count = 0
        while idx < total and count < COLORS_PER_PKT:
            hw, r, g, b = stream[idx]
            buf[pos] = hw & 0xFF
            buf[pos + 1] = r & 0xFF
            buf[pos + 2] = g & 0xFF
            buf[pos + 3] = b & 0xFF
            pos += 4
            idx += 1
            count += 1
        buf[5] = 0x0A if idx == total else 0x0E
        packets.append(bytes(buf))

    packets.append(bytes(make_buf(CMD_MAP_END)))
    packets.append(latch_packet(EFFECT_CUSTOM))
    return packets


def esc_scan_stream(candidate):
    """The ESC finder's map (#46): every known key dim blue, one candidate
    address bright red."""
    return [(hw, 0, 0, 40) for hw in LEDIDX[1:]] + [(candidate, 255, 0, 0)]


# ── Effects as the interface offers them ────────────────────────────────────

#: One firmware effect as the interface shows it. `id` is what the core's
#: API speaks; `name` is what rgb_settings.json stores (and the menu shows);
#: `sub` is the controller's rgb subcommand. `color_modes` lists the colour
#: modes the effect supports (issue #32); `direction` is None, "wave" (four
#: ways) or "tornado" (two).
EffectSpec = namedtuple("EffectSpec", "id name sub color_modes has_speed "
                                      "has_brightness direction")

RGB_EFFECTS = (
    EffectSpec("static", "Static", "static", ("single",), False, True, None),
    EffectSpec("breathing", "Breathing", "breathing", ("single", "dual", "rainbow"),
               True, True, None),
    EffectSpec("wave", "Wave", "wave", ("single", "dual", "rainbow"), True, True, "wave"),
    EffectSpec("tornado", "Tornado", "tornado", ("single", "rainbow"), True, True, "tornado"),
    EffectSpec("reactive", "Reactive", "reactive", ("dual",), True, True, None),
    EffectSpec("matrix", "Matrix", "matrix", ("dual",), True, True, None),     # #38
    EffectSpec("yeti", "Yeti", "yeti", ("dual",), True, True, None),
    # "Custom" (#34) is the per-key editor; it has no effect controls.
    EffectSpec("custom", "Custom", "custom", (), False, False, None),
    EffectSpec("off", "Off", "off", (), False, False, None),
)

COLOR_MODES = ("single", "dual", "rainbow")

#: Directions per kind: (id, label stored in rgb_settings.json, CLI value).
#: The tornado's CLI value is turned into the wire value by the controller
#: (tornado_direction).
DIRECTIONS = {
    "wave": (("l2r", "→ L→R", 0), ("t2b", "↓ T→B", 2),
             ("r2l", "← R→L", 4), ("b2t", "↑ B→T", 6)),
    "tornado": (("cw", "↻ CW", 0), ("ccw", "↺ CCW", 1)),
}

#: What the screen shows before anything was saved.
DEFAULT_SETTINGS = {
    "effect": "static", "color_mode": "dual", "speed": 50, "brightness": 100,
    "color1": [255, 0, 0], "color2": [0, 0, 255], "direction": "l2r",
}

_BY_ID = {e.id: e for e in RGB_EFFECTS}
_BY_NAME = {e.name: e for e in RGB_EFFECTS}
_DIR_BY_ID = {d[0]: d for kind in DIRECTIONS.values() for d in kind}
_DIR_BY_LABEL = {d[1]: d for kind in DIRECTIONS.values() for d in kind}


def effect(key):
    """The EffectSpec for an id ("wave") or a stored name ("Wave"), or None."""
    if not isinstance(key, str):
        return None
    return _BY_ID.get(key) or _BY_NAME.get(key)


def direction(key):
    """(id, label, value) for a direction id or stored label, or None."""
    if not isinstance(key, str):
        return None
    return _DIR_BY_ID.get(key) or _DIR_BY_LABEL.get(key)


def effective_color_mode(spec, color_mode):
    """The colour mode an effect actually runs in: the one asked for if the
    effect has it, else its first; None for effects without colours."""
    if spec is None or not spec.color_modes:
        return None
    return color_mode if color_mode in spec.color_modes else spec.color_modes[0]


def effective_direction(spec, key):
    """(id, label, value) the effect runs with: the one asked for if it is one
    of the effect's directions, else the first. None if it has none."""
    if spec is None or spec.direction is None:
        return None
    choices = DIRECTIONS[spec.direction]
    d = direction(key)
    return d if d in choices else choices[0]


def _pct(value, fallback):
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return fallback


def _rgb(value, fallback):
    try:
        r, g, b = (int(c) for c in value)
        return [r, g, b]
    except (TypeError, ValueError):
        return list(fallback)


def normalize(settings=None, **fields):
    """Lighting settings in the core's form, from anything close to it.

    Takes ids or the names/labels rgb_settings.json stores, and the old
    "cmode" key as well as "color_mode". Unknown or missing values fall back
    to DEFAULT_SETTINGS, which is what the screen showed for them.
    """
    src = dict(settings or {})
    src.update(fields)
    out = dict(DEFAULT_SETTINGS)
    out["color1"] = list(out["color1"])
    out["color2"] = list(out["color2"])
    spec = effect(src.get("effect"))
    if spec is not None:
        out["effect"] = spec.id
    cm = src.get("color_mode", src.get("cmode"))
    if cm in COLOR_MODES:
        out["color_mode"] = cm
    if "speed" in src:
        out["speed"] = _pct(src["speed"], out["speed"])
    if "brightness" in src:
        out["brightness"] = _pct(src["brightness"], out["brightness"])
    if "color1" in src:
        out["color1"] = _rgb(src["color1"], out["color1"])
    if "color2" in src:
        out["color2"] = _rgb(src["color2"], out["color2"])
    d = direction(src.get("direction"))
    if d is not None:
        out["direction"] = d[0]
    return out


def to_saved(settings):
    """The rgb_settings.json form of normalized settings.

    The file is shared with the Everest Max screen and read by 3.x, so it
    keeps the names and labels it has always held ("Wave", "← R→L"); they are
    fixed English identifiers, never translated.
    """
    spec = effect(settings["effect"])
    d = direction(settings.get("direction")) or DIRECTIONS["wave"][0]
    return {
        "effect": spec.name,
        "cmode": effective_color_mode(spec, settings.get("color_mode")) or "dual",
        "speed": settings["speed"],
        "brightness": settings["brightness"],
        "color1": list(settings["color1"]),
        "color2": list(settings["color2"]),
        "direction": d[1],
    }


def rgb_args(settings):
    """Controller arguments ("rgb", sub, ...) for normalized settings, or None
    for Custom (the per-key editor applies that) and unknown effects.

    A rainbow colour mode goes to the `<effect>-rainbow` subcommand; single
    and dual pass a trailing colour-mode value (0/16) where the CLI takes one.
    """
    spec = effect(settings.get("effect"))
    if spec is None or spec.sub == "custom":
        return None
    cmode = effective_color_mode(spec, settings.get("color_mode"))
    speed = str(int(settings["speed"]))
    bri = str(int(settings["brightness"]))
    r1, g1, b1 = (str(c) for c in settings["color1"])
    r2, g2, b2 = (str(c) for c in settings["color2"])
    d = effective_direction(spec, settings.get("direction"))
    dval = str(d[2]) if d else "0"
    cm_val = "0" if cmode == "single" else "16"     # COLOR_SINGLE / COLOR_DUAL
    sub = spec.sub
    if sub == "off":
        return ["rgb", "off"]
    if sub == "static":
        return ["rgb", "static", r1, g1, b1, bri]
    if cmode == "rainbow" and sub in ("breathing", "wave", "tornado"):
        args = ["rgb", "%s-rainbow" % sub, bri, speed]
        if spec.direction is not None:
            args.append(dval)
        return args
    if sub == "breathing":
        return ["rgb", "breathing", r1, g1, b1, r2, g2, b2, bri, speed, cm_val]
    if sub == "wave":
        return ["rgb", "wave", r1, g1, b1, r2, g2, b2, bri, speed, dval, cm_val]
    if sub == "tornado":
        return ["rgb", "tornado", r1, g1, b1, bri, speed, dval]
    if sub in ("reactive", "matrix", "yeti"):
        return ["rgb", sub, r1, g1, b1, r2, g2, b2, bri, speed]
    return None


def per_key_payload(leds, side, brightness):
    """The JSON argument of the controller's per-key-rgb verb."""
    return json.dumps({
        "leds": [list(c) for c in leds],
        "side": [list(c) for c in side] if side else [],
        "brightness": int(brightness),
    })


def side_ring_payload(saved_keys, rgb, brightness):
    """per-key-rgb payload for one side-ring colour, keeping the saved key
    colours (#4); with none saved the keys are lit white rather than left to
    go dark."""
    leds = [list(c) for c in (saved_keys or [])]
    if not leds:
        leds = [[255, 255, 255]] * NUM_KEYS
    r, g, b = rgb
    return per_key_payload(leds, [[r, g, b]] * NUM_SIDE_LEDS, brightness)
