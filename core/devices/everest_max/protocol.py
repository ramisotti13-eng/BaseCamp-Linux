"""The Everest Max's wire protocol: packet builders and decoders, no I/O.

Everything here is a pure function of its arguments, so the same bytes can be
sent over pyusb (emax_controller.py today) or hidapi (the Windows port later)
and checked in a test without a keyboard (tools/test_everest_max_protocol.py
pins them against the controller as it was before they moved here).

Two channels:

- Interrupt endpoints (EP_OUT 0x05 / EP_IN 0x84), 64-byte packets, for the
  clock, the meters, icons, actions, the main display mode and the lighting.
- HID feature reports on interface 3 (SET_REPORT/GET_REPORT, 64 bytes) for the
  image uploads: an erase of two flash sectors, a descriptor, then the picture
  in 64-byte chunks, each acknowledged `10 fa` (committed) or `10 fb` (busy,
  send it again). The short commands below are given unpadded; the transport
  pads them to 64 bytes.

Section numbers (§) refer to the protocol notes the reverse engineering
produced.
"""
import json

VID = 0x3282
PID = 0x0001
INTERFACE = 3
EP_OUT = 0x05
EP_IN = 0x84
PKT_SIZE = 64

STYLE_ANALOG = 0x00
STYLE_DIGITAL = 0x01
STYLES = ("analog", "digital")
CLOCK_FORMATS = ("24H", "12H")

# Button report: (byte_index, value) -> button index 0-3 (D1-D4)
BTN_LOOKUP = {
    (42, 0x02): 0,  # D1
    (42, 0x04): 1,  # D2
    (42, 0x08): 2,  # D3
    (42, 0x10): 3,  # D4
}

# Icon IDs: 0x40 + button_index*9 + variant (0-8)
ICON_BASE = 0x40
ICONS_PER_BTN = 9

# The wheel display's Volume mode shows whatever the host last pushed with
# `11 83 00 00 <level>`; the firmware does not track the system level itself.
VOLUME_CMD = 0x83

# Metric slots of `11 81 <slot> 00 <value>`; 5 is APM, which the host never sends.
METRIC_CPU, METRIC_GPU, METRIC_HDD, METRIC_NET, METRIC_RAM = range(5)

ICON_W = ICON_H = 72
MAIN_DISPLAY_W = 240
MAIN_DISPLAY_H = 204
ICON_IMG_SIZE = ICON_W * ICON_H * 2                     # 10368, RGB565
MAIN_IMG_SIZE = MAIN_DISPLAY_W * MAIN_DISPLAY_H * 2     # 97920, RGB565

MAIN_DISPLAY_MODES = {
    # mode name -> byte 6 of FW_EXTEND_INFO: the high nibble is the display;
    # the 0x01 (screensaver on) is only used by the fixed-base fallback, a
    # read-modify-write keeps the keyboard's own low bits.
    "image":   0x01,
    "clock":   0x11,
    "volume":  0x71,
    "cpu":     0x91,
    "gpu":     0xA1,
    "hd":      0xB1,
    "network": 0xC1,
    "ram":     0xD1,
    "apm":     0xE1,
}
MAIN_MODES = tuple(MAIN_DISPLAY_MODES)      # in the order a menu lists them
DEFAULT_DEVICE_BYTES = bytes([0xF3, 0xCC, 0x23])

# LED index -> zone (§11.1)
ZONE_LEDS = {
    "fn":     [0, 9, 18, 27, 36, 45, 54, 63, 72, 81, 90, 99, 108, 117, 114, 123],
    "num":    [1, 10, 19, 28, 37, 46, 55, 64, 73, 82, 91, 100, 109, 87, 96, 88, 115],
    "qwerty": [2, 11, 20, 29, 38, 47, 56, 65, 74, 83, 92, 101, 110, 119, 105, 106, 97],
    "home":   [3, 12, 21, 30, 39, 48, 57, 66, 75, 84, 93, 102, 120, 124],
    "shift":  [4, 22, 31, 40, 49, 58, 67, 76, 85, 94, 103, 121, 104, 113, 122],
    "bottom": [5, 14, 23, 41, 68, 77, 86, 95],
    "numpad": [6, 7, 15, 16, 24, 33, 34, 42, 43, 51, 52, 60, 61, 69, 70, 78, 79],
}
NUM_LEDS = 126          # colour indices of the keys
NUM_SIDE_LEDS = 45      # the ring around keyboard and numpad
LED_SLOTS = 152         # 8 packets x 19 slots
SIDE_PACKET_COUNTS = (19, 19, 7)

# Button action types the keyboard's flash knows: 0x02 opens a URL, 0x04 is
# everything else. The host runs the action itself in every case; the flash
# entry is what makes the key report its press (byte 42).
ACTION_URL = 0x02
ACTION_OTHER = 0x04
ACTION_MAX_BYTES = 55
# Types the host carries out on its own: their flash entry is only ":".
HOST_SIDE_TYPES = ("none", "obs", "macro", "keypress", "text")
# What a D-key can be set to, in the order a menu lists them. Plugins add
# their own action type ids after these.
BUTTON_TYPES = ("none", "shell", "url", "folder", "app", "obs", "macro",
                "keypress", "text", "page", "set_key")
DEFAULT_BUTTON = {"icon": 7, "action": "", "type": "shell"}


# ── Interrupt packets ───────────────────────────────────────────────────────

def make_packet(*args):
    pkt = bytearray(PKT_SIZE)
    for i, v in enumerate(args):
        pkt[i] = v
    return pkt


def init_packet():
    """`11 12`: re-initialise the interrupt interface."""
    return make_packet(0x11, 0x12)


def keepalive_packet():
    """`11 14`: keepalive; the reply carries button state and device bytes."""
    return make_packet(0x11, 0x14)


def style_byte(style):
    """'digital' -> 0x01, anything else -> 0x00 (analog)."""
    return STYLE_DIGITAL if style == "digital" else STYLE_ANALOG


def clock_hour(now, fmt):
    """The hour the dial shows: 1-12 for "12H", 0-23 otherwise."""
    return (now.hour % 12) or 12 if fmt == "12H" else now.hour


def time_packets(now, fmt, style):
    """The three packets of a time sync, `style` as its byte (STYLE_*)."""
    return [
        make_packet(0x11, 0x80, 0x00, 0x00, 0x01),
        make_packet(0x11, 0x84, 0x00, 0x00),
        make_packet(0x11, 0x84, 0x00, 0x01, 0x00, 0x00,
                    now.month, now.day, clock_hour(now, fmt), now.minute,
                    now.second, style),
    ]


def metric_packet(slot, value):
    """`11 81 slot 00 value`; one byte, so the value is clamped to 0-255."""
    return make_packet(0x11, 0x81, slot, 0x00, max(0, min(int(value), 255)))


def volume_packet(level):
    return make_packet(0x11, VOLUME_CMD, 0x00, 0x00, level)


def icon_id(button_idx, variant):
    """The icon id for a button (0-3) and variant (0-8)."""
    return ICON_BASE + button_idx * ICONS_PER_BTN + variant


def icon_packets(button_idx, variant):
    """Select-icon: `11 00`, then `11 02 00 01 01 <iid> 02`."""
    return [make_packet(0x11, 0x00),
            make_packet(0x11, 0x02, 0x00, 0x01, 0x01,
                        icon_id(button_idx, variant), 0x02)]


def action_type_byte(type_str):
    """0x02 for a URL, 0x04 for shell/folder/none and everything else."""
    return ACTION_URL if type_str == "url" else ACTION_OTHER


def action_packets(button_idx, command, action_type=ACTION_OTHER):
    """Write an action into the keyboard's flash for one button.

    `12 08 00 <btn+1>` selects the slot, `17 AA <len> 00 <type> <cmd>` writes
    it; the command is cut to 55 bytes of UTF-8.
    """
    cmd_bytes = command.encode("utf-8")[:ACTION_MAX_BYTES]
    return [make_packet(0x12, 0x08, 0x00, button_idx + 1),
            make_packet(0x17, 0xAA, 1 + len(cmd_bytes), 0x00, action_type,
                        *cmd_bytes)]


def flash_entry(btype, action):
    """(command, type byte) that write-action/reset-buttons put in the flash.

    Host-side types only need the key to report its press, so they write ":";
    the type byte follows the button's type either way. `action` is taken as
    stripped.
    """
    cmd = ":" if btype in HOST_SIDE_TYPES or not action else action
    return cmd, action_type_byte(btype)


def startup_flash_entry(btype, action):
    """(command, type byte) Monitor Mode writes for a button when it starts.

    Not the same rule as flash_entry(): only "none" and an empty action become
    ":" (always as 0x04), so a macro button gets its macro id written. Kept as
    it was; the keyboard only needs something there. `action` is taken as
    stripped.
    """
    if btype == "none" or not action:
        return ":", ACTION_OTHER
    return action, action_type_byte(btype)


def button_from_report(resp):
    """The button (0-3) a `01 …` report says is held, else None."""
    if not resp or resp[0] != 0x01 or len(resp) <= 42:
        return None
    return BTN_LOOKUP.get((42, resp[42]))


def device_bytes(info):
    """Bytes 7-9 of the keyboard's `11 14` reply, or the known default."""
    b = bytes(info[7:10]) if info is not None else b""
    return b if len(b) == 3 and any(b) else DEFAULT_DEVICE_BYTES


def mode_switch_packet(dev_bytes, mode):
    """`11 14 00 01 02 ff 00 b7 b8 b9 MODE 1e 00 1e 00 01 12 13 14 15 02 00 32`.

    An unknown mode switches to the clock. A fixed base: it resets the dial's
    menus, timers and screensaver, so it is only the fallback for a keyboard
    that does not answer `11 14`; otherwise the controller writes
    display_mode_body() of what it read.
    """
    pkt = bytearray(PKT_SIZE)
    pkt[0] = 0x11
    pkt[1] = 0x14
    pkt[3] = 0x01          # write flag
    pkt[4] = 0x02
    pkt[5] = 0xff
    pkt[7:10] = dev_bytes
    pkt[10] = MAIN_DISPLAY_MODES.get(mode, 0x11)
    pkt[11] = 0x1e
    pkt[13] = 0x1e
    pkt[15] = 0x01
    pkt[16:20] = bytes([0x12, 0x13, 0x14, 0x15])
    pkt[20] = 0x02
    pkt[22] = 0x32
    return bytes(pkt)


def reset_dial_packet():
    """`13 41 00 00 01`: back to the factory Mountain logo (§3.4).

    Not an activation of the custom image, whatever it looks like.
    """
    return make_packet(0x13, 0x41, 0x00, 0x00, 0x01)


def factory_reset_packet():
    """`13 40 00 00 01`: ResetFlash(bAll=true), the keyboard's factory reset.

    Lighting, key remaps and macros, D1-D4 and dial pictures and the dock
    settings go back to the factory state; layout and firmware stay. The
    keyboard echoes the packet after about 3.3 s (captured 2026-11-24).
    """
    return make_packet(0x13, 0x40, 0x00, 0x00, 0x01)


def is_factory_reset_reply(pkt):
    """True for the echo of factory_reset_packet(). Key reports (`01 ...`)
    and other packets may arrive before it."""
    return bytes(pkt[:5]) == b"\x13\x40\x00\x00\x01"


def persist_packet():
    """`13 55 00 00 06`: commit the custom colours to slot 6 (§5.11.8)."""
    pkt = bytearray(PKT_SIZE)
    pkt[0] = 0x13
    pkt[1] = 0x55
    pkt[4] = 0x06
    return bytes(pkt)


# ── Lighting ────────────────────────────────────────────────────────────────

def hw_speed(speed):
    """Interface speed 1-100 (100 = fast) to the keyboard's 1-100 (1 = fast)."""
    return max(1, 101 - speed)


def rgb_packet(effect, speed=50, brightness=100, color1=(255, 0, 0),
               color2=(0, 0, 255), direction=0):
    """The `14 2c` effect packet. An unknown effect gives a bare `14 2c`."""
    pkt = bytearray(PKT_SIZE)
    pkt[0] = 0x14
    pkt[1] = 0x2c
    r1, g1, b1 = color1
    r2, g2, b2 = color2
    hs = hw_speed(speed)
    if effect == "static":
        pkt[2] = 0x00; pkt[4] = 0xff; pkt[5] = brightness; pkt[6] = 0x00
        pkt[7] = 0xff; pkt[8] = 0xff; pkt[9] = r1; pkt[10] = g1; pkt[11] = b1
    elif effect == "breathing":
        pkt[2] = 0x01; pkt[4] = hs; pkt[5] = brightness; pkt[6] = 0x00
        pkt[7] = 0xff; pkt[8] = 0xff; pkt[9] = r1; pkt[10] = g1; pkt[11] = b1
    elif effect == "breathing-rainbow":
        pkt[2] = 0x01; pkt[4] = hs; pkt[5] = brightness; pkt[6] = 0x02
        pkt[7] = 0xff; pkt[8] = 0xff
    elif effect == "breathing-dual":
        pkt[2] = 0x01; pkt[4] = hs; pkt[5] = brightness; pkt[6] = 0x10
        pkt[7] = 0xff; pkt[8] = 0xff
        pkt[9] = r1; pkt[10] = g1; pkt[11] = b1
        pkt[12] = r2; pkt[13] = g2; pkt[14] = b2
    elif effect == "reactive":
        pkt[2] = 0x03; pkt[4] = hs; pkt[5] = brightness; pkt[6] = 0x00
        pkt[7] = 0xff; pkt[8] = 0xff
        pkt[9] = r1; pkt[10] = g1; pkt[11] = b1
        pkt[18] = r2; pkt[19] = g2; pkt[20] = b2
    elif effect in ("wave", "wave-rainbow", "tornado", "tornado-rainbow"):
        is_tornado = "tornado" in effect
        pkt[2] = 0x04 if "wave" in effect else 0x07
        pkt[4] = hs; pkt[5] = brightness
        # Tornado direction: 9=CW, 10=CCW; Wave direction: 0-7
        dir_val = max(9, min(10, direction)) if is_tornado else direction
        if "rainbow" in effect:
            pkt[6] = 0x02; pkt[7] = dir_val
            pkt[8] = 0x02; pkt[10] = 0xff; pkt[14] = 0xff
        else:
            pkt[6] = 0x00; pkt[7] = dir_val
            pkt[8] = 0x00; pkt[9] = 0x01; pkt[10] = 0x64
            pkt[11] = r1; pkt[12] = g1; pkt[13] = b1; pkt[14] = 0xff
    elif effect in ("yeti", "matrix"):
        pkt[2] = 0x06 if effect == "yeti" else 0x09
        pkt[4] = hs; pkt[5] = brightness; pkt[6] = 0x00
        pkt[7] = 0xff; pkt[8] = 0xff
        pkt[9] = r1; pkt[10] = g1; pkt[11] = b1
        pkt[18] = r2; pkt[19] = g2; pkt[20] = b2
    elif effect == "off":
        pkt[2] = 0x0c; pkt[4] = 0xff; pkt[5] = 64
        pkt[6] = 0xff; pkt[7] = 0xff; pkt[8] = 0xff
    return bytes(pkt)


def zone_leds(zone_colors):
    """{zone: (r,g,b)} -> the 152 LED slots; other keys black."""
    leds = [(0, 0, 0)] * LED_SLOTS
    for zone, color in zone_colors.items():
        for idx in ZONE_LEDS.get(zone, []):
            if idx < LED_SLOTS:
                leds[idx] = color
    return leds


def key_leds(led_colors):
    """Per-key colours (index 0-125) -> the 152 LED slots, padded black."""
    leds = list(led_colors)[:NUM_LEDS]
    leds += [(0, 0, 0)] * max(0, NUM_LEDS - len(leds))
    leds += [(0, 0, 0)] * (LED_SLOTS - NUM_LEDS)
    return leds


def side_leds(side_colors):
    """The ring's 45 colours from a list (None or short: padded black)."""
    side = list(side_colors or [])
    side += [(0, 0, 0)] * max(0, NUM_SIDE_LEDS - len(side))
    return side[:NUM_SIDE_LEDS]


def custom_mode_packet(brightness):
    """§5.11.1: switch to custom mode."""
    pkt = bytearray(PKT_SIZE)
    pkt[0] = 0x14; pkt[1] = 0x2c; pkt[2] = 0x0a
    pkt[4] = 0xff; pkt[5] = brightness
    return bytes(pkt)


def key_color_packets(leds, brightness):
    """§5.11.3: static colours of the keys, 8 packets of 19 slots."""
    out = []
    for ix in range(8):
        pkt = bytearray(PKT_SIZE)
        pkt[0] = 0x14; pkt[1] = 0x2c; pkt[2] = 0x00; pkt[3] = 0x01
        pkt[4] = ix; pkt[5] = brightness
        for i in range(19):
            r, g, b = leds[ix * 19 + i]
            pkt[7 + i * 3] = r; pkt[7 + i * 3 + 1] = g; pkt[7 + i * 3 + 2] = b
        out.append(bytes(pkt))
    return out


def side_color_packets(side):
    """§5.11.4: the ring, 3 packets (19 + 19 + 7 = 45 LEDs)."""
    out = []
    for ix, count in enumerate(SIDE_PACKET_COUNTS):
        pkt = bytearray(PKT_SIZE)
        pkt[0] = 0x14; pkt[1] = 0x2d; pkt[2] = 0x0a
        pkt[4] = ix; pkt[5] = 0xff
        for i in range(count):
            r, g, b = side[ix * 19 + i]
            pkt[7 + i * 3] = r; pkt[7 + i * 3 + 1] = g; pkt[7 + i * 3 + 2] = b
        out.append(bytes(pkt))
    return out


EFFECT_TABLE_CHUNKS = 3
EFFECT_TABLE_CHUNK = 60         # slots per `14 a0` packet; 126 of the 180 are keys


def effect_table_packets(values=()):
    """`14 a0 <chunk> 01 <60 values>` x 3: the effect each LED slot runs in
    custom mode (slot = the key's colour index). Missing values are 0
    (static)."""
    values = list(values)[:EFFECT_TABLE_CHUNKS * EFFECT_TABLE_CHUNK]
    out = []
    for chunk in range(EFFECT_TABLE_CHUNKS):
        pkt = bytearray(PKT_SIZE)
        pkt[0] = 0x14; pkt[1] = 0xa0; pkt[2] = chunk; pkt[3] = 0x01
        part = values[chunk * EFFECT_TABLE_CHUNK:(chunk + 1) * EFFECT_TABLE_CHUNK]
        pkt[4:4 + len(part)] = bytes(int(v) & 0xFF for v in part)
        out.append(bytes(pkt))
    return out


def bind_static_packets():
    """§5.11.5: bind every key to static mode, 3 packets of 60 key slots."""
    return effect_table_packets()


# ── Effects per key in custom mode ("Individuell") ──────────────────────────
#
# Checked on the keyboard on 2026-11-23 by reading its own LED colours back:
# half the keys stayed static
# red while the other half breathed green. The sequence is
#
#   1. `14 2c 0a 00 ff <bri>`             custom mode
#   2. 8x `14 2c 00 01 <ix> <bri> 00 …`   the static colours (key_color_packets)
#      (3x `14 2d …`                      the ring, as the colour-only sequence)
#   3. 3x `14 a0 <chunk> 01 <values>`     the effect of every slot
#   4. per effect used: `14 2c <eff> 01 …` its parameters for the custom layer:
#      rgb_packet()'s bytes with byte 3 ("byAll") = 1. byAll = 0 would switch
#      the whole keyboard to that effect and ignore the table.
#   5. `13 55 00 00 06`                   save the custom slot; without it the
#                                          keys with an effect stay dark
#   6. `14 2c 0a 00 ff <bri>`             custom mode again, now running them
#
# The ring LEDs are not in the table: they keep their static colours.

#: (name, value in the `14 a0` table), in the order the editor lists them.
PER_KEY_EFFECTS = (("static", 0x00), ("breathing", 0x01), ("reactive", 0x03),
                   ("wave", 0x04), ("tornado", 0x07), ("matrix", 0x09),
                   ("yeti", 0x06), ("off", 0x0c))
PER_KEY_EFFECT_IDS = tuple(e[0] for e in PER_KEY_EFFECTS)
PER_KEY_EFFECT_VALUES = dict(PER_KEY_EFFECTS)
#: The RGB_EFFECTS ids (colour modes) each per-key effect can run as.
PER_KEY_VARIANTS = {
    "static": ("static",),
    "breathing": ("breathing", "breathing-rainbow", "breathing-dual"),
    "reactive": ("reactive",),
    "wave": ("wave", "wave-rainbow"),
    "tornado": ("tornado", "tornado-rainbow"),
    "matrix": ("matrix",),
    "yeti": ("yeti",),
    "off": ("off",),
}


def per_key_effect_values(effects):
    """Effect names per key (index = colour index) -> the 126 table values.
    A missing or unknown name is static (0)."""
    effects = list(effects or [])[:NUM_LEDS]
    effects += ["static"] * (NUM_LEDS - len(effects))
    return [PER_KEY_EFFECT_VALUES.get(e, 0) for e in effects]


def effect_settings(name, params=None, brightness=100):
    """One per-key effect's parameters, checked and filled in.

    `params` has the shape of the normal effect settings: {effect (the
    colour mode, an RGB_EFFECTS id of this effect; default the effect
    itself), speed, brightness, color1, color2, direction (value or label)}.
    Returns {effect, speed, brightness, color1, color2, direction} with
    colours as 'rrggbb'; ValueError for anything that does not fit.
    """
    if name not in PER_KEY_VARIANTS:
        raise ValueError("unknown per-key effect: %r" % (name,))
    p = params if isinstance(params, dict) else {}
    variant = p.get("effect") or name
    if variant not in PER_KEY_VARIANTS[name]:
        raise ValueError("%r is not a colour mode of %s" % (variant, name))
    direction = p.get("direction", 9 if name == "tornado" else 0)
    if isinstance(direction, str):
        if direction in DIRECTION_VALUES:
            direction = DIRECTION_VALUES[direction]
        elif direction == "":
            direction = 9 if name == "tornado" else 0
        else:
            raise ValueError("unknown direction: %r" % direction)
    out = {
        "effect": variant,
        "speed": max(1, min(100, int(p.get("speed", 60)))),
        "brightness": max(0, min(100, int(p.get("brightness", brightness)))),
        "color1": hex_color(parse_color(p.get("color1", "0044ff"))),
        "color2": hex_color(parse_color(p.get("color2", "0044ff"))),
        "direction": int(direction),
    }
    return out


def custom_effect_packet(effect, speed=50, brightness=100, color1=(255, 0, 0),
                         color2=(0, 0, 255), direction=0):
    """`14 2c <eff> 01 …`: an effect's parameters for the keys the table gives
    it. rgb_packet()'s bytes for the effect with byte 3 (byAll) = 1."""
    if effect not in RGB_EFFECT_IDS:
        raise ValueError("unknown effect: %r" % (effect,))
    pkt = bytearray(rgb_packet(effect, speed, brightness, color1, color2, direction))
    pkt[3] = 0x01
    return bytes(pkt)


def per_key_effect_packets(effects, effect_params=None, brightness=100):
    """One custom_effect_packet() for every effect but static that a key
    uses, in PER_KEY_EFFECTS order. `effect_params` maps an effect name to
    its parameters (effect_settings()); a missing entry takes the defaults."""
    used = set(effects or [])
    params = effect_params if isinstance(effect_params, dict) else {}
    out = []
    for name, _value in PER_KEY_EFFECTS:
        if name == "static" or name not in used:
            continue
        s = effect_settings(name, params.get(name), brightness)
        out.append(custom_effect_packet(s["effect"], s["speed"], s["brightness"],
                                        parse_color(s["color1"]),
                                        parse_color(s["color2"]), s["direction"]))
    return out


def has_key_effects(effects):
    """True when a key runs anything but static."""
    return any(per_key_effect_values(effects))


def custom_rgb_packets(leds, side, brightness, effects=None, effect_params=None):
    """The whole custom-colour sequence.

    Without effects (or all static): mode, keys, ring, bind (15 packets), as
    it always was. With an effect on any key: mode, keys, ring, the effect
    table, one parameter packet per effect used, save (`13 55 00 00 06`) and
    custom mode again; see the notes above PER_KEY_EFFECTS.

    `leds` are the 152 slots, `side` the 45 ring colours. The keyboard needs a
    moment after the first packet (the controller waits 150 ms).
    """
    head = ([custom_mode_packet(brightness)] + key_color_packets(leds, brightness)
            + side_color_packets(side))
    if not has_key_effects(effects):
        return head + bind_static_packets()
    return (head + effect_table_packets(per_key_effect_values(effects))
            + per_key_effect_packets(effects, effect_params, brightness)
            + [persist_packet(), custom_mode_packet(brightness)])


# ── Image uploads (feature reports) ─────────────────────────────────────────

ERASE_POLL = bytes([0xaa, 0x55, 0x80, 0x00])
ERASE_END = bytes([0xaa, 0x55, 0x21, 0x00])
ICON_SESSION = bytes([0xaa, 0x55, 0x21, 0x04])
MAIN_SESSION = bytes([0xaa, 0x55, 0x21, 0x03])
SLOT_PREPARE = bytes([0xaa, 0x55, 0x22, 0x00])
CHUNK = 64


def erase_sector_packet(sector):
    return bytes([0xaa, 0x55, 0x21, sector])


def icon_sectors(button_idx):
    """Flash sectors of a key's picture: D1 (1,2), D2 (3,4), D3 (5,6), D4 (7,8)."""
    return (button_idx * 2 + 1, button_idx * 2 + 2)


def image_checksum(data):
    """Sum of all bytes, 16 bits."""
    return sum(data) & 0xFFFF


def icon_descriptor(data, button_idx):
    """`aa55 10 80 28 00 <chk> 00 00 02 01 <btn>` for a 72x72 picture."""
    chk = image_checksum(data)
    return bytes([0xaa, 0x55, 0x10, 0x80, 0x28, 0x00, chk & 0xFF, chk >> 8,
                  0x00, 0x00, 0x02, 0x01, button_idx])


def main_descriptor(data):
    """`aa55 10 80 7e 01 <chk> 00 00 02 01 00` for the 240x204 display."""
    chk = image_checksum(data)
    return bytes([0xaa, 0x55, 0x10, 0x80, 0x7e, 0x01, chk & 0xFF, chk >> 8,
                  0x00, 0x00, 0x02, 0x01, 0x00])


def erase_done(report):
    return report[2:4] == bytes([0x80, 0xfa])


def committed(report):
    """True for `10 fa`: the descriptor or chunk was taken."""
    return report[2:4] == bytes([0x10, 0xfa])


def committed_offset(report):
    """The offset the keyboard wants next, from a `10 fa` reply."""
    return int.from_bytes(report[4:6], "little")


def display_updated(report):
    return report[:2] == bytes([0xff, 0xaa])


# ── What an interface works with ────────────────────────────────────────────

# (id, name, has_speed, has_brightness, has_color1, has_color2, has_direction)
# The name is what rgb_settings.json has always stored, so 3.x reads it.
RGB_EFFECTS = (
    ("static",            "Static",            False, True,  True,  False, False),
    ("breathing",         "Breathing",         True,  True,  True,  False, False),
    ("breathing-rainbow", "Breathing Rainbow", True,  True,  False, False, False),
    ("breathing-dual",    "Breathing Dual",    True,  True,  True,  True,  False),
    ("wave",              "Wave",              True,  True,  True,  False, True),
    ("wave-rainbow",      "Wave Rainbow",      True,  True,  False, False, True),
    ("tornado",           "Tornado",           True,  True,  True,  False, True),
    ("tornado-rainbow",   "Tornado Rainbow",   True,  True,  False, False, True),
    ("reactive",          "Reactive",          True,  True,  True,  True,  False),
    ("yeti",              "Yeti",              True,  True,  True,  True,  False),
    ("matrix",            "Matrix",            True,  True,  True,  True,  False),
    ("off",               "Off",               False, False, False, False, False),
)
RGB_EFFECT_IDS = tuple(e[0] for e in RGB_EFFECTS)

# Directions as (label, value). The labels are what rgb_settings.json stores.
WAVE_DIRECTIONS = (("→ L→R", 0), ("↓ T→B", 2), ("← R→L", 4), ("↑ B→T", 6))
TORNADO_DIRECTIONS = (("↻ CW", 9), ("↺ CCW", 10))
DIRECTION_VALUES = dict(WAVE_DIRECTIONS + TORNADO_DIRECTIONS)
DIRECTION_LABELS = {v: k for k, v in DIRECTION_VALUES.items()}


def effect_info(effect):
    """Capabilities of an effect (by id or name) as a dict, or None."""
    for eid, name, hs, hb, hc1, hc2, hd in RGB_EFFECTS:
        if effect in (eid, name):
            return {"id": eid, "name": name, "speed": hs, "brightness": hb,
                    "color1": hc1, "color2": hc2, "direction": hd,
                    "directions": [lbl for lbl, _ in
                                   (TORNADO_DIRECTIONS if "tornado" in eid
                                    else WAVE_DIRECTIONS)] if hd else []}
    return None


def hex_color(rgb):
    """(r, g, b) -> 'rrggbb'."""
    r, g, b = rgb
    return "%02x%02x%02x" % (r, g, b)


def parse_color(value):
    """'rrggbb', '#rrggbb' or a 3-sequence -> (r, g, b); ValueError otherwise."""
    if isinstance(value, str):
        h = value.lstrip("#")
        if len(h) != 6:
            raise ValueError("not a colour: %r" % value)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    r, g, b = value
    return (int(r), int(g), int(b))


def rgb_args(effect, speed, brightness, color1, color2, direction):
    """The controller's `rgb` command line for these settings."""
    return ["rgb", effect, str(int(speed)), str(int(brightness)),
            hex_color(color1), hex_color(color2), str(int(direction))]


def per_key_payload(leds, side=None, brightness=100, effects=None,
                    effect_params=None):
    """The JSON the controller's `per-key-rgb` takes; no ring when side is
    None. `effects` (a name per key) and `effect_params` ({name: settings})
    are only in it when effects is given, so a colour-only payload is what it
    always was."""
    d = {"leds": [list(c) for c in leds], "brightness": brightness}
    if side is not None:
        d["side"] = [list(c) for c in side]
    if effects is not None:
        d["effects"] = list(effects)
        d["effect_params"] = dict(effect_params or {})
    return json.dumps(d)


def parse_per_key_payload(text):
    """-> (leds, side or None, brightness), as the controller reads it."""
    data = json.loads(text)
    leds = [tuple(c) for c in data.get("leds", [])]
    side_raw = data.get("side", [])
    side = [tuple(c) for c in side_raw] if side_raw else None
    return leds, side, int(data.get("brightness", 100))


def parse_per_key_effects(text):
    """-> (effects or None, effect_params) of a `per-key-rgb` payload."""
    data = json.loads(text)
    effects = data.get("effects")
    params = data.get("effect_params")
    return (list(effects) if isinstance(effects, list) else None,
            params if isinstance(params, dict) else {})


# ── What is attached: the answer to `11 14` (FW_EXTEND_INFO) ────────────────
#
# The keyboard answers `11 14` with 29 bytes of its own account of what is
# plugged into it, starting at offset 4 of the answer (after the echoed
# command). The field order is the struct in the Windows service. Monitor Mode
# sends `11 14` every 0.2 s as its keepalive, so the answer is there already.
# tools/everest_probe.py carries a standalone copy of the decoder for users who
# run it without the application; tools/test_everest_max_modules.py keeps the
# two equal.

EXTEND_INFO_LEN = 29
EXTEND_INFO_OFFSET = 4


def is_extend_reply(data):
    """True when `data` looks like the answer to `11 14` (it echoes the command)."""
    return (data is not None and len(data) >= EXTEND_INFO_OFFSET + EXTEND_INFO_LEN
            and data[0] == 0x11 and data[1] == 0x14)


def decode_extend_info(data):
    """The keyboard's own account of what is attached to it, or None."""
    if not data or len(data) < EXTEND_INFO_OFFSET + EXTEND_INFO_LEN:
        return None
    body = bytes(data[EXTEND_INFO_OFFSET:EXTEND_INFO_OFFSET + EXTEND_INFO_LEN])
    return {
        "mm_dock_plugged": bool(body[0]),
        "mm_dock_show_menu": body[1],
        "mm_dock_menu_index": body[2],
        "mm_dock_color": list(body[3:6]),
        "mm_dock_screen_setup": body[6],
        "mm_dock_screensaver_s": int.from_bytes(body[7:9], "little"),
        "mm_dock_turn_off_s": int.from_bytes(body[9:11], "little"),
        "mm_dock_show_profile": list(body[11:16]),
        "numpad_plugged": bool(body[16]),
        "pixel_shift_time": body[17],
        "mm_dock_double_click": body[18],
        "brightness": [{"dock": body[19 + i * 2], "numpad": body[20 + i * 2]}
                       for i in range(5)],
        "raw": " ".join("%02x" % b for b in body),
    }


def modules_of(data):
    """{"numpad": bool, "dock": bool} from an answer to `11 14`, or None when
    `data` is not such an answer."""
    if not is_extend_reply(data):
        return None
    info = decode_extend_info(data)
    return {"numpad": info["numpad_plugged"], "dock": info["mm_dock_plugged"]}


# ── Writing FW_EXTEND_INFO: the media dock's settings ───────────────────────
#
# `11 14 00 01 <29 bytes>` writes the same struct the answer to `11 14`
# carries; the keyboard echoes the packet. The rule, as Mountain's own service
# does it: read first and change only the fields the user touched. So
# encode_extend_info() has no default base: a write always starts from the
# bytes the keyboard sent, never from a guess.

#: The dial's menus as bits of byMMDockShowMenu (byte 1), bit 0 first.
#: Bit 0 (clock) and bit 1 (profile) were verified by toggling them in
#: Mountain's Base Camp; bits 2-7 on the keyboard under Linux (2026-11-23,
#: three rounds of three menus off, each item's pattern unique): an item whose
#: bit is off stays in the dial's menu but cannot be chosen.
DOCK_MENUS = ("clock", "profile", "rgb", "volume", "brightness", "pcinfo",
              "apm", "custom")
#: byMMDockScreenSetup (byte 6): the high nibble is the display the dial shows
#: now (coded like MAIN_DISPLAY_MODES); bit 0 turns the screensaver on, bit 1
#: the turn-off (both verified).
DOCK_SCREENSAVER_BIT = 0x01
DOCK_TURN_OFF_BIT = 0x02
DOCK_SECONDS_MIN = 1
DOCK_SECONDS_MAX = 3600
#: What the screensaver shows (byMMDockMenuIndex, byte 2) with the display
#: nibble the original writes to byte 6 beside it, read from Base Camp's
#: service log (everest-max-sdk-captures.md). "image" is not captured (the
#: original opens a file dialog): 0x01 and nibble 0 (MAIN_DISPLAY_MODES'
#: image) are a guess that has to be checked on the keyboard.
DOCK_CONTENT = {
    "image":         (0x01, 0x0),
    "clock_digital": (0x15, 0x1),
    "clock_analog":  (0x14, 0x1),
    "stopwatch":     (0x16, 0x3),
    "timer":         (0x17, 0x4),
    "volume":        (0x41, 0x7),
    "brightness":    (0x51, 0x8),
    "cpu":           (0x61, 0x9),
    "gpu":           (0x62, 0xA),
    "hd":            (0x63, 0xB),
    "network":       (0x64, 0xC),
    "ram":           (0x65, 0xD),
    "apm":           (0x71, 0xE),
}
#: exBrightness (bytes 19-28): for each of the 5 profiles the brightness of the
#: media dock's display and of the numpad's D-key displays (byMMDockBrightness,
#: byNumpadBrightness). Measured through Base Camp's service log on 2026-11-23
#: ("Displays synchronisieren"): 0 = the displays follow the backlight (the box
#: checked), 0x80 | percent = a brightness of their own (75 % = 203). Base
#: Camp wrote profile 1's pair only; we have no profiles, so all five get the
#: same and profile 1's pair is what is read.
#: The numpad's D-key displays take only 0, 25, 50, 75 and 100 % (0x80, 0x99,
#: 0xb2, 0xcb, 0xe4) and ignore any other value; the dial takes every percent.
#: Mountain's SDK rounds both to the nearest step before it writes (seen
#: on 2026-11-23: 20 % went out as 25 %, 10 % as 0 %), and so
#: do we (DISPLAYS_STEP).
DISPLAYS_OWN = 0x80
DISPLAYS_STEP = 25
DISPLAYS_OFFSET = 19
DISPLAYS_PROFILES = 5


def extend_body(data):
    """The 29 bytes of FW_EXTEND_INFO from an answer to `11 14` (or the 29
    bytes themselves); None for anything else."""
    if data is None:
        return None
    data = bytes(data)
    if len(data) == EXTEND_INFO_LEN:
        return data
    if is_extend_reply(data):
        return data[EXTEND_INFO_OFFSET:EXTEND_INFO_OFFSET + EXTEND_INFO_LEN]
    return None


def _u8(value, name):
    v = int(value)
    if not 0 <= v <= 0xFF:
        raise ValueError("%s out of range: %r" % (name, value))
    return v


def _u16(value, name):
    v = int(value)
    if not 0 <= v <= 0xFFFF:
        raise ValueError("%s out of range: %r" % (name, value))
    return v.to_bytes(2, "little")


def _flag(value, old):
    """A plugged flag: the byte as read while the meaning stays, else 1/0."""
    return old if bool(old) == bool(value) else (1 if value else 0)


def encode_extend_info(fields, base):
    """The inverse of decode_extend_info(): the 29 bytes to write.

    `fields` uses decode_extend_info()'s names ("raw" is ignored); every
    field it has is written, every other byte is the one in `base`, the 29
    bytes (or the whole answer) read from the keyboard. There is no default
    base on purpose. ValueError for a base that is not one, or a value that
    does not fit.
    """
    body = extend_body(base)
    if body is None:
        raise ValueError("no FW_EXTEND_INFO to start from: read 11 14 first")
    b = bytearray(body)
    f = fields or {}
    if "mm_dock_plugged" in f:
        b[0] = _flag(f["mm_dock_plugged"], b[0])
    if "mm_dock_show_menu" in f:
        b[1] = _u8(f["mm_dock_show_menu"], "mm_dock_show_menu")
    if "mm_dock_menu_index" in f:
        b[2] = _u8(f["mm_dock_menu_index"], "mm_dock_menu_index")
    if "mm_dock_color" in f:
        c = list(f["mm_dock_color"])
        if len(c) != 3:
            raise ValueError("mm_dock_color needs 3 values")
        b[3:6] = bytes(_u8(v, "mm_dock_color") for v in c)
    if "mm_dock_screen_setup" in f:
        b[6] = _u8(f["mm_dock_screen_setup"], "mm_dock_screen_setup")
    if "mm_dock_screensaver_s" in f:
        b[7:9] = _u16(f["mm_dock_screensaver_s"], "mm_dock_screensaver_s")
    if "mm_dock_turn_off_s" in f:
        b[9:11] = _u16(f["mm_dock_turn_off_s"], "mm_dock_turn_off_s")
    if "mm_dock_show_profile" in f:
        p = list(f["mm_dock_show_profile"])
        if len(p) != 5:
            raise ValueError("mm_dock_show_profile needs 5 values")
        b[11:16] = bytes(_u8(v, "mm_dock_show_profile") for v in p)
    if "numpad_plugged" in f:
        b[16] = _flag(f["numpad_plugged"], b[16])
    if "pixel_shift_time" in f:
        b[17] = _u8(f["pixel_shift_time"], "pixel_shift_time")
    if "mm_dock_double_click" in f:
        b[18] = _u8(f["mm_dock_double_click"], "mm_dock_double_click")
    if "brightness" in f:
        bri = list(f["brightness"])
        if len(bri) != 5:
            raise ValueError("brightness needs 5 profiles")
        for i, e in enumerate(bri):
            b[19 + i * 2] = _u8(e["dock"], "brightness")
            b[20 + i * 2] = _u8(e["numpad"], "brightness")
    return bytes(b)


def display_mode_body(base, mode):
    """FW_EXTEND_INFO with the dial switched to `mode` (MAIN_DISPLAY_MODES;
    an unknown mode is the clock): only byte 6's high nibble (the display)
    changes. Its low bits (screensaver, turn-off) and every other byte stay
    as read, so a mode switch keeps the Display Dial's settings."""
    body = extend_body(base)
    if body is None:
        raise ValueError("no FW_EXTEND_INFO to start from: read 11 14 first")
    setup = (MAIN_DISPLAY_MODES.get(mode, 0x11) & 0xF0) | (body[6] & 0x0F)
    return encode_extend_info({"mm_dock_screen_setup": setup}, body)


def extend_write_packet(body):
    """`11 14 00 01 <29 bytes>`: write FW_EXTEND_INFO."""
    body = extend_body(body)
    if body is None:
        raise ValueError("FW_EXTEND_INFO is 29 bytes")
    pkt = bytearray(PKT_SIZE)
    pkt[0] = 0x11; pkt[1] = 0x14; pkt[3] = 0x01
    pkt[EXTEND_INFO_OFFSET:EXTEND_INFO_OFFSET + EXTEND_INFO_LEN] = body
    return bytes(pkt)


def clamp_dock_seconds(value):
    """A screensaver or turn-off delay, clamped to 1..3600 s."""
    return max(DOCK_SECONDS_MIN, min(DOCK_SECONDS_MAX, int(value)))


def displays_byte(synced, percent=None):
    """One exBrightness byte: 0 when the displays follow the backlight, else
    0x80 | percent, clamped to 0..100 and rounded to the nearest DISPLAYS_STEP
    (the D-key displays ignore anything else; Mountain's SDK rounds the same)."""
    if synced:
        return 0
    p = max(0, min(100, int(percent)))
    return DISPLAYS_OWN | (p + DISPLAYS_STEP // 2) // DISPLAYS_STEP * DISPLAYS_STEP


def displays_of(data):
    """{synced, percent} of the displays' brightness from FW_EXTEND_INFO
    (profile 1's pair; percent is None while they follow the backlight).
    A pair where only one display has its own brightness counts as own."""
    b = extend_body(data)
    pair = b[DISPLAYS_OFFSET:DISPLAYS_OFFSET + 2]
    own = [v for v in pair if v & DISPLAYS_OWN]
    if not own:
        return {"synced": True, "percent": None}
    return {"synced": False, "percent": min(100, own[0] & 0x7F)}


def dock_content_of(byte2):
    """The DOCK_CONTENT name for byte 2 as read, None for a code not in the table."""
    for name, (code, _nibble) in DOCK_CONTENT.items():
        if code == byte2:
            return name
    return None


def dock_settings_of(data):
    """The media dock's settings as an interface shows them, from an answer
    to `11 14` or its 29 bytes; None when there is nothing to read.

    {plugged, menus: {clock, profile, rgb, volume, brightness, pcinfo, apm,
    custom: bool}, color: "#rrggbb", screensaver: {on, seconds}, turn_off:
    {on, seconds}, display (byte 6's high nibble), screensaver_content
    (byte 2 as read), content (its DOCK_CONTENT name or None), displays: {synced, percent} (displays_of), raw}.
    """
    b = extend_body(data)
    if b is None:
        return None
    return {
        "plugged": bool(b[0]),
        "menus": {name: bool(b[1] >> i & 1) for i, name in enumerate(DOCK_MENUS)},
        "color": "#" + hex_color(b[3:6]),
        "screensaver": {"on": bool(b[6] & DOCK_SCREENSAVER_BIT),
                        "seconds": int.from_bytes(b[7:9], "little")},
        "turn_off": {"on": bool(b[6] & DOCK_TURN_OFF_BIT),
                     "seconds": int.from_bytes(b[9:11], "little")},
        "display": b[6] >> 4,
        "screensaver_content": b[2],
        "content": dock_content_of(b[2]),
        "displays": displays_of(b),
        "raw": b.hex(),
    }


def apply_dock_settings(base, menus=None, color=None, screensaver=None,
                        turn_off=None, displays=None, content=None):
    """The 29 bytes with the user's changes on top of `base` (what was read).

    menus: {name: bool} for any of DOCK_MENUS; color: '#rrggbb'; screensaver
    and turn_off: {on?: bool, seconds?: int}, the seconds clamped to
    1..3600. Byte 6 keeps its high nibble (the display shown now) and only
    bit 0 / bit 1 change, unless `content` (a DOCK_CONTENT name) is given:
    then byte 2 gets its code and byte 6's high nibble the display that goes
    with it, as the original writes them. Without `content` byte 2 stays as
    read.
    displays: {synced: bool, percent: 0..100 (with synced false)}, written
    to both displays of all five profiles (displays_byte).
    """
    body = extend_body(base)
    if body is None:
        raise ValueError("no FW_EXTEND_INFO to start from: read 11 14 first")
    fields = {}
    if menus:
        mask = body[1]
        for name, on in dict(menus).items():
            if name not in DOCK_MENUS:
                raise ValueError("unknown dial menu: %r" % (name,))
            bit = 1 << DOCK_MENUS.index(name)
            mask = (mask | bit) if on else (mask & ~bit & 0xFF)
        fields["mm_dock_show_menu"] = mask
    if color is not None:
        fields["mm_dock_color"] = list(parse_color(color))
    setup = body[6]
    for spec, bit, key in ((screensaver, DOCK_SCREENSAVER_BIT, "mm_dock_screensaver_s"),
                           (turn_off, DOCK_TURN_OFF_BIT, "mm_dock_turn_off_s")):
        if not spec:
            continue
        if spec.get("on") is not None:
            setup = (setup | bit) if spec["on"] else (setup & ~bit & 0xFF)
        if spec.get("seconds") is not None:
            fields[key] = clamp_dock_seconds(spec["seconds"])
    if content is not None:
        if content not in DOCK_CONTENT:
            raise ValueError("unknown screensaver content: %r" % (content,))
        code, nibble = DOCK_CONTENT[content]
        fields["mm_dock_menu_index"] = code
        setup = (setup & 0x0F) | (nibble << 4)
    if setup != body[6]:
        fields["mm_dock_screen_setup"] = setup
    if displays is not None:
        v = displays_byte(displays.get("synced"), displays.get("percent"))
        fields["brightness"] = [{"dock": v, "numpad": v}] * DISPLAYS_PROFILES
    return encode_extend_info(fields, body)


# ── Settings page: game mode and indicator LEDs ────────────────────────────
#
# Base Camp's Einstellungen page (captured from Mountain's SDK):
# `12 06` reads the game mode
# mask, `12 06 00 01 <mask>` writes it; `12 07` reads the indicator LEDs,
# `12 07 00 01 <0|1>` writes them. The answer to a read is `12 <sub> 00 00
# <value>`. All four bits checked on the keyboard under Linux (2026-11-23):
# 1 Shift+Tab, 2 Alt+F4, 4 Windows key, 8 Alt+Tab. The mask only says what
# game mode blocks; game mode itself is switched on the keyboard with
# Fn + Pause (an LED on the media dock shows it; `12 09` reads it).

SETTING_GAME_MODE = 0x06
SETTING_INDICATOR_LEDS = 0x07
#: The game mode's keys in the page's order; the bit of each is 1 << index.
GAME_MODE_KEYS = ("shift_tab", "alt_f4", "win", "alt_tab")


def setting_read_packet(sub):
    """`12 <sub>`: ask for one of the page's settings."""
    return make_packet(0x12, sub)


def setting_write_packet(sub, value):
    """`12 <sub> 00 01 <value>`: write it."""
    return make_packet(0x12, sub, 0x00, 0x01, value & 0xFF)


def is_setting_reply(data, sub):
    """True for the keyboard's answer to `12 <sub>` (`12 <sub> 00 00 <value>`),
    not for the echo of a write (`... 00 01 ...`) or any other packet."""
    return (data is not None and len(data) >= 5 and data[0] == 0x12 and data[1] == sub
            and data[2] == 0x00 and data[3] == 0x00)


def setting_value_of(data, sub):
    """The value byte of an answer to `12 <sub>`, or None."""
    return data[4] if is_setting_reply(data, sub) else None


def game_mode_of(mask):
    """{shift_tab, alt_f4, win, alt_tab: bool} from the mask. Bits above the
    four are ignored here and kept by apply_game_mode."""
    return {k: bool(mask >> i & 1) for i, k in enumerate(GAME_MODE_KEYS)}


def apply_game_mode(mask, flags):
    """The mask with `flags` ({key: bool}) on top; every other bit stays."""
    for key, on in dict(flags).items():
        if key not in GAME_MODE_KEYS:
            raise ValueError("unknown game mode key: %r" % (key,))
        bit = 1 << GAME_MODE_KEYS.index(key)
        mask = (mask | bit) if on else (mask & ~bit)
    return mask & 0xFF


# ── Firmware versions (feature reports) ─────────────────────────────────────
#
# `aa 55 80` answers `aa 55 80 fa <vid LE> <pid LE> <firmware LE> <bootloader
# LE>`; on this keyboard `aa 55 80 fa 82 32 01 00 57 00 05 00`. A sub-device
# (1 the media dock, 2 the numpad) is selected with `aa 55 21 <n>` first and
# deselected with `aa 55 21` afterwards; its answer has the same layout
# (dock `… fa 0b 06 1f 7a 24`: firmware 0x24). Mountain's DLL shows the
# numbers as decimal BCD: 0x57 is version 57. The same channel carries the
# image uploads, so nothing here may run while one does.

DEVICE_INFO = bytes([0xaa, 0x55, 0x80])
SUB_DEVICE_DESELECT = bytes([0xaa, 0x55, 0x21])
SUB_DEVICES = (("dock", 1), ("numpad", 2))


def sub_device_packet(n):
    """`aa 55 21 <n>`: talk to sub-device n (1 dock, 2 numpad)."""
    return bytes([0xaa, 0x55, 0x21, n])


def parse_device_info(report):
    """{vid, pid, firmware, bootloader} from an answer to `aa 55 80`, else None."""
    if report is None or len(report) < 12:
        return None
    r = bytes(report)
    if r[:4] != b"\xaa\x55\x80\xfa":
        return None
    return {"vid": int.from_bytes(r[4:6], "little"),
            "pid": int.from_bytes(r[6:8], "little"),
            "firmware": int.from_bytes(r[8:10], "little"),
            "bootloader": int.from_bytes(r[10:12], "little")}


def sub_device_firmware(report):
    """A sub-device's firmware from its `aa 55 80` answer, or None: no
    answer, firmware 0, or the keyboard answering for itself (no such
    module)."""
    info = parse_device_info(report)
    if not info or not info["firmware"] or (info["vid"], info["pid"]) == (VID, PID):
        return None
    return info["firmware"]


def version_text(value):
    """A version number as Mountain's tools show it (decimal BCD: 0x57 ->
    "57"), or None."""
    if value is None:
        return None
    return "%x" % int(value)


# ── Key remapping (captured from Mountain's SDK) ────────────────────────────
# `14 20 <src> <target hi> <target lo>` makes key `src` send `target` (the
# SDK's ChangeKey): target = src is the key's own function ("Standard"),
# target 0 disables it, and the codes of Base Camp's firmware functions
# (media, mouse, F13-F24, profiles, a few OS keys) are targets as well.
# `14 21 <src> 00 <key> <mods>` makes it send a key with modifiers
# (ChangeShortcutKey). Keys are named by their SDK numbers (layout.KEY_IDS).
# The keyboard echoes both.

REMAP_CMD = 0x20
SHORTCUT_CMD = 0x21
REMAP_DISABLED = 0
#: Modifier bits of `14 21`, in the order the original shows its checkboxes.
SHORTCUT_MODS = (("ctrl", 0x01), ("shift", 0x02), ("alt", 0x04), ("win", 0x08))


def _key_byte(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 0xff:
        raise ValueError("%s must be a key number 1..255" % name)
    return value


def change_key_packet(src, target):
    """`14 20 <src> <target hi> <target lo>`: key `src` sends `target`
    (`src` itself: its own function; 0: nothing)."""
    _key_byte(src, "src")
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= 0xffff:
        raise ValueError("target must be 0..65535")
    return make_packet(0x14, REMAP_CMD, src, target >> 8, target & 0xff)


def shortcut_mods_byte(mods):
    """The modifier byte of `14 21` for names out of SHORTCUT_MODS."""
    bits = dict(SHORTCUT_MODS)
    unknown = [m for m in mods if m not in bits]
    if unknown:
        raise ValueError("unknown modifier: %s" % ", ".join(map(str, unknown)))
    out = 0
    for m in mods:
        out |= bits[m]
    return out


def shortcut_key_packet(src, key, mods=()):
    """`14 21 <src> 00 <key> <mods>`: key `src` sends `key` with the
    modifiers named in `mods` (ctrl, shift, alt, win)."""
    return make_packet(0x14, SHORTCUT_CMD, _key_byte(src, "src"), 0x00,
                       _key_byte(key, "key"), shortcut_mods_byte(mods))


# Key macros, confirmed on the keyboard (captures doc, "Key macros"): a
# header `14 18 <type> 00 <times>`, then the events in chunks of up to 15,
# `15 <src> <n in this chunk> <chunk index> <n x event>`. An event is
# `<SDK key number> <(page << 4) | action> <timer lo> <timer hi>`, page 0 the
# keyboard and 1 the mouse (key 1: left button), action 2 press and 1
# release; the list ends with `ff ff 00 00`. Base Camp writes type 1 and
# timer 1 everywhere; the timer's unit has not been measured. Writing a
# macro binds the key to it; Standard (`14 20 src 00 src`) takes it off.
MACRO_CMD = 0x18
MACRO_CHUNK_CMD = 0x15
MACRO_TYPE = 1
MACRO_CHUNK_EVENTS = 15
MACRO_PRESS, MACRO_RELEASE = 2, 1
MACRO_PAGE_KEYS, MACRO_PAGE_MOUSE = 0, 1
MACRO_MOUSE_LEFT = 1
MACRO_END = bytes([0xff, 0xff, 0x00, 0x00])


def macro_event(key, page, action, timer):
    """One event: 4 bytes."""
    if not (0 <= key <= 0xff and 0 <= page <= 0x0f and 0 <= action <= 0x0f
            and 0 <= timer <= 0xffff):
        raise ValueError("macro event out of range: %r" % ((key, page, action, timer),))
    return bytes([key, (page << 4) | action, timer & 0xff, timer >> 8])


def macro_packets(src, events, times=1):
    """`14 18` and the `15` chunks for a key macro on key `src`. `events` are
    (key, page, action, timer); the end marker is added here."""
    _key_byte(src, "src")
    if not 1 <= times <= 0xff:
        raise ValueError("times must be 1..255")
    body = [macro_event(*e) for e in events] + [MACRO_END]
    out = [make_packet(0x14, MACRO_CMD, MACRO_TYPE, 0x00, times)]
    for idx in range(0, len(body), MACRO_CHUNK_EVENTS):
        chunk = body[idx:idx + MACRO_CHUNK_EVENTS]
        out.append(make_packet(MACRO_CHUNK_CMD, src, len(chunk), idx // MACRO_CHUNK_EVENTS,
                               *b"".join(chunk)))
    return out


def remap_packets(entry):
    """The packets of one entry of the controller's `remap` verb: {src,
    target} for `14 20`, {src, key, mods} for `14 21`, {src, macro: {times,
    events: [[key, page, action, timer], ...]}} for a key macro."""
    if not isinstance(entry, dict) or "src" not in entry:
        raise ValueError("a remap entry is {src, target}, {src, key, mods} or {src, macro}")
    if "macro" in entry:
        m = entry["macro"]
        if not isinstance(m, dict) or not isinstance(m.get("events"), list):
            raise ValueError("macro must be {times, events}")
        try:
            events = [tuple(int(v) for v in e) for e in m["events"]]
        except (TypeError, ValueError):
            raise ValueError("a macro event is [key, page, action, timer]") from None
        if not all(len(e) == 4 for e in events):
            raise ValueError("a macro event is [key, page, action, timer]")
        return macro_packets(entry["src"], events, int(m.get("times", 1)))
    if "key" in entry:
        return [shortcut_key_packet(entry["src"], entry["key"], entry.get("mods") or ())]
    return [change_key_packet(entry["src"], entry.get("target"))]


def is_remap_echo(report, pkt):
    """True when `report` is the keyboard's echo of the remap packet `pkt`."""
    return report is not None and len(report) >= 3 and bytes(report[:3]) == bytes(pkt[:3])
