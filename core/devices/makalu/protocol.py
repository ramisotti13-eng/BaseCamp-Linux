"""Makalu 67 / Makalu Max wire protocol, model data and tables. Pure.

Nothing in here does I/O. It builds the 64-byte feature reports the mouse
takes on interface 1 (report ID 0xA1; the mouse answers 0xA0 when it took
them), knows the models and their buttons, and holds the tables the screen
used to keep to itself: effects, speeds, brightness steps, directions, the
remap catalogue. The HID exchange is in core/devices/makalu/device.py, the
CLI in devices/makalu67/controller.py.

Reverse engineered from USB captures of a Makalu 67 (PID 0x0003):

    0x0C  lighting       [5]=1, [16]=effect, [17..19] colour (or 8 x RGB from
                         [17] for custom 0x0F), [20..22] colour 2, [41]
                         brightness 0..100, [42] direction, [43] speed 0..2
    0x0D  settings       [2]=sub: 1 polling rate, 2 debounce, 3 angle
                         snapping, 4 lift-off, 0x0A DPI levels; [5..] value
    0x0B  read           [2]=7 profile data: [22] active level (1-based),
                         [23..42] 5 x (X lo, X hi, Y lo, Y hi)
    0x0A  button remap   [6]=button, [16]=category, [17]=code, [22]=0x0F

The Makalu Max (PID 0x0002) does not take these commands (#115): the Windows
software drives it through a different library that reads and writes the
mouse's settings memory instead (tools/makalu_max_probe.py). Until that is
worked out the Max gets the Makalu 67 commands, as it always has.
"""
from collections import namedtuple

VID = 0x3282
PID_67 = 0x0003    # Makalu 67
PID_MAX = 0x0002   # Makalu Max
#: (pid, model name), in the order they are looked for.
MODELS = ((PID_67, "Makalu 67"), (PID_MAX, "Makalu Max"))
DEFAULT_MODEL = "Makalu 67"
INTERFACE = 1
#: The collection on interface 1 that carries the 0xA1/0xA0 feature reports.
#: Linux opens the interface as one hidraw node; Windows lists every top-level
#: collection of it as a path of its own (keyboard, consumer, two of usage
#: page 0xFF02, and this one) and only this one takes the reports.
VENDOR_USAGE_PAGE = 0xFF01
REPORT_LEN = 64
NOT_FOUND = "Makalu mouse not found (VID=0x3282 PID=0x0003/0x0002 IF1)"

REPORT_ID = 0xA1
RESP_ID = 0xA0
CMD_LIGHTING = 0x0C
CMD_POLLING_RATE = 0x0D   # also every other sensor setting and the DPI levels
CMD_DPI = 0x0B            # read (sub 0x07 returns the profile data)
CMD_WRITE_MEM = 0xDE      # byte-by-byte RAM write (WriteMemData in Makalu.dll)
CMD_ENABLE = 0xC4         # EnableSetting / commit written RAM to flash
CMD_REMAP = 0x0A          # button remapping

# Physical button indices (1-based)
BTN_LEFT = 1
BTN_RIGHT = 2
BTN_MIDDLE = 3
BTN_BACK = 4
BTN_FORWARD = 5
BTN_DPI = 6
# Makalu Max extra buttons
BTN_MAX_FORWARD = 7
BTN_MAX_BACK = 8

# Function codes: (category, code)
REMAP_FUNCTIONS = {
    "left":       (0x00, 0x01),
    "right":      (0x00, 0x02),
    "middle":     (0x00, 0x04),
    "back":       (0x00, 0x08),
    "forward":    (0x00, 0x10),
    "dpi+":       (0x09, 0xF1),
    "dpi-":       (0x09, 0xF3),
    "scroll_up":  (0x01, 0x01),
    "scroll_down": (0x01, 0xFF),
    "disabled":   (0xFF, 0x01),
}

# The controller's own default tables, int keys. Kept as they were: nothing
# reads them for a decision, and the Max one has no button 4 (BTN_DPI is 6,
# and the 6 entry after it wins). What the application uses are the
# string-keyed defaults of each Model below, which match shared.config.
REMAP_DEFAULTS_67 = {
    BTN_LEFT:    "left",
    BTN_RIGHT:   "right",
    BTN_MIDDLE:  "middle",
    BTN_BACK:    "back",
    BTN_FORWARD: "forward",
    BTN_DPI:     "dpi+",
}
REMAP_DEFAULTS_MAX = {
    BTN_LEFT:        "left",
    BTN_RIGHT:       "right",
    BTN_MIDDLE:      "middle",
    BTN_DPI:         "dpi+",
    5:               "disabled",
    6:               "disabled",
    BTN_MAX_FORWARD: "forward",
    BTN_MAX_BACK:    "back",
}

# 1000 -> 500 -> 250 -> 125 Hz
POLLING_RATE_MAP = {1000: 0x01, 500: 0x02, 250: 0x04, 125: 0x08}
POLLING_RATES = (125, 250, 500, 1000)
# Debounce in ms
DEBOUNCE_VALUES = [2, 4, 6, 8, 10, 12]

# DPI range (from a GET response: min 50, max 19000, step 50); the Max starts
# at 100.
DPI_MIN = 50
DPI_MIN_MAX = 100
DPI_MAX = 19000
DPI_STEP = 50
DPI_LEVELS = 5

# Effect codes (physical testing + USB capture, PID 0x0003)
EFFECT_OFF = 0
EFFECT_STATIC = 1
EFFECT_RAINBOW = 2
EFFECT_BREATHING = 5
EFFECT_RGB_BREATHING = 6
EFFECT_RESPONSIVE = 7
EFFECT_YETI = 8
EFFECT_CUSTOM = 0x0F   # per-LED colours

NUM_LEDS = 8


# ── Models ───────────────────────────────────────────────────────────────────

#: A remappable button: id as stored in makalu_remap.json, the English name
#: the screen used, and the translation key for its label.
Button = namedtuple("Button", "id name label_key")

Model = namedtuple("Model", "pid name is_max dpi_min max_button buttons remap_defaults")

_BUTTONS_67 = (
    Button("1", "Left Btn", "makalu_remap_btn_left"),
    Button("2", "Right Btn", "makalu_remap_btn_right"),
    Button("3", "Middle Btn", "makalu_remap_btn_middle"),
    Button("4", "Back", "makalu_remap_btn_back"),
    Button("5", "Forward", "makalu_remap_btn_forward"),
    Button("6", "DPI+", "makalu_remap_btn_dpi"),
)
_BUTTONS_MAX = (
    Button("1", "Left Btn", "makalu_remap_btn_left"),
    Button("2", "Right Btn", "makalu_remap_btn_right"),
    Button("3", "Middle Btn", "makalu_remap_btn_middle"),
    Button("4", "DPI", "makalu_remap_btn_dpi"),
    Button("5", "Btn 5", "makalu_remap_btn_5"),
    Button("6", "Btn 6", "makalu_remap_btn_6"),
    Button("7", "Forward", "makalu_remap_btn_forward"),
    Button("8", "Back", "makalu_remap_btn_back"),
)

MAKALU_67 = Model(
    PID_67, "Makalu 67", False, DPI_MIN, 6, _BUTTONS_67,
    {"1": "left", "2": "right", "3": "middle", "4": "back", "5": "forward",
     "6": "dpi+"})
MAKALU_MAX = Model(
    PID_MAX, "Makalu Max", True, DPI_MIN_MAX, 8, _BUTTONS_MAX,
    {"1": "left", "2": "right", "3": "middle", "4": "dpi+", "5": "disabled",
     "6": "disabled", "7": "forward", "8": "back"})


def model_for(pid):
    """The Model for a PID; the Makalu 67 for anything else (or None)."""
    return MAKALU_MAX if pid == PID_MAX else MAKALU_67


def model_dict(model):
    """A JSON-serialisable description of a model."""
    return {
        "pid": model.pid, "model": model.name, "is_max": model.is_max,
        "dpi_min": model.dpi_min, "dpi_max": DPI_MAX, "dpi_step": DPI_STEP,
        "buttons": [b._asdict() for b in model.buttons],
        "remap_defaults": dict(model.remap_defaults),
    }


# ── Reports ──────────────────────────────────────────────────────────────────

def _report(cmd):
    buf = [0] * REPORT_LEN
    buf[0] = REPORT_ID
    buf[1] = cmd
    return buf


def lighting_report(effect, r=0, g=0, b=0, brightness=100, param1=0, param2=0):
    """0x0C. param1 is the direction, param2 the speed. Returns a list."""
    buf = _report(CMD_LIGHTING)
    buf[5] = 0x01
    buf[16] = effect
    buf[17] = r & 0xFF
    buf[18] = g & 0xFF
    buf[19] = b & 0xFF
    buf[41] = max(0, min(100, brightness))  # brightness 0-100 (also acts as save flag)
    buf[42] = param1
    buf[43] = param2
    return buf


def lighting_report2(effect, r, g, b, r2, g2, b2, brightness=100, param1=0, param2=0):
    """0x0C with a second colour at [20..22] (two-colour effects)."""
    buf = lighting_report(effect, r, g, b, brightness=brightness,
                          param1=param1, param2=param2)
    buf[20] = r2 & 0xFF
    buf[21] = g2 & 0xFF
    buf[22] = b2 & 0xFF
    buf[23] = 0x00
    return buf


def custom_report(leds, brightness=100):
    """0x0C effect 0x0F: eight LEDs, [0] top-left down to [3] bottom-left,
    then [4] bottom-right up to [7] top-right."""
    buf = _report(CMD_LIGHTING)
    buf[5] = 0x01
    buf[16] = EFFECT_CUSTOM
    for i, (r, g, b) in enumerate(list(leds)[:NUM_LEDS]):
        buf[17 + i * 3] = r & 0xFF
        buf[18 + i * 3] = g & 0xFF
        buf[19 + i * 3] = b & 0xFF
    buf[41] = max(0, min(100, brightness))
    return buf


def _setting(sub, value):
    buf = _report(CMD_POLLING_RATE)
    buf[2] = sub
    buf[5] = 0x01
    buf[6] = value
    return buf


def polling_rate_report(hz):
    code = POLLING_RATE_MAP.get(int(hz))
    if code is None:
        raise ValueError(f"Invalid polling rate {hz}. Must be one of: 125, 250, 500, 1000")
    return _setting(0x01, code)


def debounce_report(ms):
    ms = int(ms)
    if ms not in DEBOUNCE_VALUES:
        raise ValueError(f"Invalid debounce {ms}ms. Must be one of: {DEBOUNCE_VALUES}")
    return _setting(0x02, ms)


def angle_snapping_report(enabled):
    return _setting(0x03, 0x01 if enabled else 0x00)


def lift_off_report(high):
    return _setting(0x04, 0x01 if high else 0x00)


def dpi_query_report():
    """0x0B sub 0x07: read the profile data (DPI levels, active level)."""
    buf = _report(CMD_DPI)
    buf[2] = 0x07
    buf[5] = 0x01
    return buf


def parse_dpi(resp, dpi_min=DPI_MIN):
    """(levels, active 0-based) from a profile-data answer."""
    current = max(0, min(4, resp[22] - 1))   # resp[22] is 1-based
    levels = []
    for i in range(DPI_LEVELS):
        lo = resp[23 + i * 4]
        hi = resp[24 + i * 4]
        levels.append(max(dpi_min, min(DPI_MAX, lo | (hi << 8))))
    return levels, current


def snap_dpi(dpi, dpi_min=DPI_MIN):
    """Clamp to dpi_min..19000, then to the nearest step of 50 (halves to
    even, as Python rounds)."""
    dpi = max(dpi_min, min(DPI_MAX, int(dpi)))
    return round(dpi / DPI_STEP) * DPI_STEP


def dpi_report(levels, active_level=1, dpi_min=DPI_MIN):
    """0x0D sub 0x0A (Set_dpi_settings): five levels for all profiles.

    [5]=6 (ALL_PROFILE), [6]=5 levels, [7]=active level (1-based), then each
    level as LE16 X and the same Y from [16].
    """
    if len(levels) != DPI_LEVELS:
        raise ValueError("dpi_list must have exactly 5 values")
    buf = _report(CMD_POLLING_RATE)
    buf[2] = 0x0A
    buf[5] = 6
    buf[6] = 5
    buf[7] = max(1, min(5, int(active_level)))
    for i, dpi in enumerate(levels):
        dpi = snap_dpi(dpi, dpi_min)
        lo = dpi & 0xFF
        hi = (dpi >> 8) & 0xFF
        buf[16 + i * 4] = lo
        buf[16 + i * 4 + 1] = hi
        buf[16 + i * 4 + 2] = lo
        buf[16 + i * 4 + 3] = hi
    return buf


def dpi_to_raw(dpi, dpi_min=DPI_MIN):
    """User DPI to the PAW3335 sensor encoding (Makalu.dll
    UpdateUIDatatoSensor, SensorID 78). Unused: the GET answer and the 0x0A
    write both use plain DPI."""
    dpi = snap_dpi(dpi, dpi_min)
    data = dpi // DPI_STEP
    if data <= 200:
        return data - 1
    return 199 + (data - 199) // 2


def write_mem_report(address, value):
    """0xDE: write one byte of mouse RAM. Unused."""
    buf = _report(CMD_WRITE_MEM)
    buf[2] = address % 256
    buf[3] = address // 256
    buf[4] = value & 0xFF
    return buf


def _check_button(button_index, max_btn):
    if not 1 <= button_index <= max_btn:
        raise ValueError(f"Button index must be 1–{max_btn}, got {button_index}")


def sniper_report(button_index, sniper_dpi, max_btn=6, dpi_min=DPI_MIN):
    """0x0A category 0x0C: hold the button for `sniper_dpi`, release for the
    level's DPI. The DPI is rounded down to 50."""
    _check_button(button_index, max_btn)
    sniper_dpi = max(dpi_min, min(DPI_MAX, (sniper_dpi // DPI_STEP) * DPI_STEP))
    buf = _report(CMD_REMAP)
    buf[5] = 0x01
    buf[6] = button_index
    buf[16] = 0x0C
    buf[17] = 0x01
    buf[18] = sniper_dpi & 0xFF
    buf[19] = (sniper_dpi >> 8) & 0xFF
    buf[20] = sniper_dpi & 0xFF
    buf[21] = (sniper_dpi >> 8) & 0xFF
    buf[22] = 0x0F
    return buf


def remap_report(button_index, function_name, max_btn=6):
    """0x0A: give a button one of REMAP_FUNCTIONS."""
    fn = REMAP_FUNCTIONS.get(function_name.lower())
    if fn is None:
        raise ValueError(f"Unknown function '{function_name}'. Valid: {list(REMAP_FUNCTIONS)}")
    _check_button(button_index, max_btn)
    buf = _report(CMD_REMAP)
    buf[5] = 0x01
    buf[6] = button_index
    buf[16] = fn[0]
    buf[17] = fn[1]
    buf[22] = 0x0F
    return buf


# ── What the interface offers ────────────────────────────────────────────────

#: A lighting effect: id (the core's API and makalu_settings.json), the name
#: the menu shows, the firmware code, and which controls it uses.
EffectSpec = namedtuple("EffectSpec", "id name code has_speed has_color1 "
                                      "has_color2 has_direction")

RGB_EFFECTS = (
    EffectSpec("static", "Static", EFFECT_STATIC, False, True, False, False),
    EffectSpec("breathing", "Breathing", EFFECT_BREATHING, True, True, True, False),
    EffectSpec("rgb_breathing", "RGB Breathing", EFFECT_RGB_BREATHING, True, False, False, False),
    EffectSpec("rainbow", "Rainbow", EFFECT_RAINBOW, True, False, False, True),
    EffectSpec("responsive", "Responsive", EFFECT_RESPONSIVE, False, True, False, False),
    EffectSpec("yeti", "Yeti", EFFECT_YETI, True, True, True, False),
    EffectSpec("off", "Off", EFFECT_OFF, False, False, False, False),
)

#: (id, label the screen shows, firmware value)
SPEEDS = (("slow", "Slow", 0), ("medium", "Medium", 1), ("fast", "Fast", 2))
DIRECTIONS = (("left", "←", 0), ("right", "→", 1))
BRIGHTNESS_LEVELS = (0, 25, 50, 75, 100)

#: Remap catalogue: category id and the functions it offers. "sniper" is not
#: a REMAP_FUNCTIONS entry: it is stored as "sniper:<dpi>" and sent with
#: sniper_report.
REMAP_CATEGORIES = (
    ("mouse", ("left", "right", "middle", "back", "forward", "disabled")),
    ("dpi", ("dpi+", "dpi-", "disabled")),
    ("scroll", ("scroll_up", "scroll_down", "disabled")),
    ("sniper", ("sniper",)),
)
SNIPER_DEFAULT_DPI = 400

DEFAULT_LIGHTING = {
    "effect": "static", "speed": "medium", "brightness": 100,
    "direction": "right", "color1": [0, 118, 204], "color2": [255, 0, 0],
}
DEFAULT_SENSOR = {
    "polling_rate": 1000, "debounce": 2, "lift_off": "low", "angle_snapping": False,
}

_BY_ID = {e.id: e for e in RGB_EFFECTS}
_BY_NAME = {e.name: e for e in RGB_EFFECTS}


def effect(key):
    """EffectSpec for an id ("rgb_breathing") or name ("RGB Breathing")."""
    if not isinstance(key, str):
        return None
    return _BY_ID.get(key) or _BY_NAME.get(key)


def _lookup(table, key):
    for row in table:
        if key in (row[0], row[1]):
            return row
    return None


def speed(key):
    """(id, label, value) for a speed id or label, or None."""
    return _lookup(SPEEDS, key)


def direction(key):
    """(id, label, value) for a direction id or arrow, or None."""
    return _lookup(DIRECTIONS, key)


def snap_brightness(value):
    """The nearest of the brightness steps the screen offers."""
    return min(BRIGHTNESS_LEVELS, key=lambda x: abs(x - int(value)))


def normalize_lighting(settings=None, **fields):
    """Lighting settings in the core's form from ids or the screen's labels
    ("Slow", "75%", "→"); missing or unknown values take the defaults."""
    src = dict(settings or {})
    src.update(fields)
    out = dict(DEFAULT_LIGHTING)
    out["color1"] = list(out["color1"])
    out["color2"] = list(out["color2"])
    spec = effect(src.get("effect"))
    if spec:
        out["effect"] = spec.id
    s = speed(src.get("speed"))
    if s:
        out["speed"] = s[0]
    d = direction(src.get("direction"))
    if d:
        out["direction"] = d[0]
    bri = src.get("brightness")
    if isinstance(bri, str):
        bri = bri.strip().rstrip("%")
    try:
        out["brightness"] = max(0, min(100, int(bri)))
    except (TypeError, ValueError):
        pass
    for key in ("color1", "color2"):
        if key in src:
            try:
                r, g, b = (int(c) for c in src[key])
                out[key] = [r, g, b]
            except (TypeError, ValueError):
                pass
    return out


def lighting_args(settings):
    """Controller arguments for normalized lighting settings.

    Two-colour effects go to `rgb code2`, the others to `rgb code`; colour 1
    goes along even where the effect ignores it, and the speed is 0 for
    effects without one.
    """
    spec = effect(settings["effect"]) or RGB_EFFECTS[-1]
    r1, g1, b1 = settings["color1"]
    r2, g2, b2 = settings["color2"]
    bri = int(settings["brightness"])
    spd = speed(settings["speed"])[2] if spec.has_speed else 0
    dir_ = direction(settings["direction"])[2]
    if spec.has_color2:
        return ["rgb", "code2", str(spec.code), str(r1), str(g1), str(b1),
                str(r2), str(g2), str(b2), str(bri), str(spd), str(dir_)]
    return ["rgb", "code", str(spec.code), str(r1), str(g1), str(b1),
            str(bri), str(spd), str(dir_)]


def custom_args(leds, brightness):
    """Controller arguments for the eight custom LEDs."""
    flat = []
    for r, g, b in leds:
        flat.extend([str(r), str(g), str(b)])
    return ["rgb", "custom"] + flat + [str(int(brightness))]


def remap_args(button, assignment):
    """Controller arguments for a stored assignment ("dpi+", "sniper:800")."""
    if assignment.startswith("sniper:"):
        return ["sniper", str(button), str(sniper_dpi_of(assignment))]
    return ["remap", str(button), assignment]


def sniper_dpi_of(assignment, default=SNIPER_DEFAULT_DPI):
    """The DPI of a "sniper:<dpi>" assignment (default when unreadable)."""
    try:
        return int(assignment.split(":")[1])
    except (IndexError, ValueError):
        return default


def function_of(assignment):
    """The function id of an assignment: "sniper" for "sniper:<dpi>"."""
    return "sniper" if assignment.startswith("sniper:") else assignment


def category_of(function):
    """The category id that offers a function, or None."""
    for cat, fns in REMAP_CATEGORIES:
        if function in fns:
            return cat
    return None


def normalize_sensor(settings=None, **fields):
    """Sensor settings in the core's form; unknown values keep the defaults."""
    src = dict(settings or {})
    src.update(fields)
    out = dict(DEFAULT_SENSOR)
    try:
        hz = int(src.get("polling_rate", out["polling_rate"]))
        if hz in POLLING_RATE_MAP:
            out["polling_rate"] = hz
    except (TypeError, ValueError):
        pass
    try:
        ms = int(src.get("debounce", out["debounce"]))
        if ms in DEBOUNCE_VALUES:
            out["debounce"] = ms
    except (TypeError, ValueError):
        pass
    lo = src.get("lift_off")
    if lo in ("low", "high"):
        out["lift_off"] = lo
    elif isinstance(lo, bool):
        out["lift_off"] = "high" if lo else "low"
    snap = src.get("angle_snapping")
    if isinstance(snap, bool):
        out["angle_snapping"] = snap
    elif snap in ("on", "off"):
        out["angle_snapping"] = snap == "on"
    return out
