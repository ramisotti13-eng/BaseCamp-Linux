"""Where the Everest Max's keys and ring LEDs are, as plain data.

The per-key colour editor draws the keyboard from this, and a web interface
can draw the same picture from it: every key is (label, led_index, x, y, w, h)
in the editor's canvas coordinates, led_index None for a key without its own
LED. The colour indices are those of the custom-colour packets (§11.1).
"""

SCALE = 0.82
NUM_LEDS = 126
NUM_SIDE_LEDS = 45


def build_layout(shape="ansi"):
    """Return a list of (label, led_idx_or_None, x, y, w, h) for all keys."""
    SC = SCALE
    KH = int(30 * SC)
    RS = int(32 * SC)
    IW = int(510 * SC)
    FW = int(616 * SC)
    OX = 14 + int(26 * SC)
    OY = 14 + int(12 * SC)
    NP_X0 = 14 + int(642 * SC) + 32
    NPS   = int(33 * SC)
    NPG   = int(7 * SC)
    MS  = 25
    MG  = 4
    MX  = OX + IW + 8
    NPTAL = KH + RS

    def sbet(specs, inner_w, y):
        total = sum(int(w * SC) for _, _, w in specs)
        gap   = (inner_w - total) / max(1, len(specs) - 1)
        res, x = [], OX
        for lbl, idx, cw in specs:
            pw = int(cw * SC)
            res.append((lbl, idx, int(x), y, pw, KH))
            x += pw + gap
        return res

    L = []
    L += sbet([
        ('ESC',0,30),('F1',9,30),('F2',18,30),('F3',27,30),
        ('F4',36,30),('F5',45,30),('F6',54,30),('F7',63,30),
        ('F8',72,30),('F9',81,30),('F10',90,30),('F11',99,30),
        ('F12',108,30),('PrtSc',117,30),('ScrLk',114,30),('Pause',123,30),
    ], FW, OY)

    y1 = OY + RS
    L += sbet([
        ('`',1,30),('1',10,30),('2',19,30),('3',28,30),('4',37,30),
        ('5',46,30),('6',55,30),('7',64,30),('8',73,30),('9',82,30),
        ('0',91,30),('-',100,30),('=',109,30),('⌫',87,68),
    ], IW, y1)
    L += [('Ins',96,MX+MS+MG,y1,MS,KH), ('Del',88,MX+2*(MS+MG),y1,MS,KH)]

    y2 = OY + 2 * RS
    L += sbet([
        ('Tab',2,50),('Q',11,30),('W',20,30),('E',29,30),('R',38,30),
        ('T',47,30),('Y',56,30),('U',65,30),('I',74,30),('O',83,30),
        ('P',92,30),('[',101,30),(']',110,30),('\\',119,50),
    ], IW, y2)
    L += [('Home',105,MX+MS+MG,y2,MS,KH), ('PgUp',115,MX+2*(MS+MG),y2,MS,KH)]

    y3 = OY + 3 * RS
    L += sbet([
        ('Caps',3,60),('A',12,30),('S',21,30),('D',30,30),('F',39,30),
        ('G',48,30),('H',57,30),('J',66,30),('K',75,30),('L',84,30),
        (';',93,30),("'",102,30),('↵',120,73),
    ], IW, y3)
    L += [('End',97,MX+MS+MG,y3,MS,KH), ('PgDn',106,MX+2*(MS+MG),y3,MS,KH)]

    y4 = OY + 4 * RS
    L += sbet([
        ('⇧',4,80),('Z',22,30),('X',31,30),('C',40,30),('V',49,30),
        ('B',58,30),('N',67,30),('M',76,30),(',',85,30),('.',94,30),
        ('/',103,30),('⇧',121,88),
    ], IW, y4)
    L.append(('↑', 124, MX + MS + MG, y4, MS, KH))

    # Right of the space bar the keyboard has AltGr, Fn, Menu and Ctrl, no
    # second Windows key (lit one at a time on an ISO-DE Everest Max: 68, 77,
    # 86, 95).
    y5 = OY + 5 * RS
    L += sbet([
        ('Ctrl',5,42),('⊞',14,42),('Alt',23,42),(' ',41,210),
        ('Alt',68,42),('FN',77,42),('≡',86,42),('Ctrl',95,42),
    ], IW, y5)
    L += [
        ('←',104,MX,y5,MS,KH),
        ('↓',113,MX+MS+MG,y5,MS,KH),
        ('→',122,MX+2*(MS+MG),y5,MS,KH),
    ]

    npx = NP_X0 + int(5 * SC)
    npy = OY
    for i, (lbl, idx) in enumerate([('NumLk',6),('/',24),('*',16),('-',15)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy, NPS, KH))
    for i, (lbl, idx) in enumerate([('7',61),('8',69),('9',70)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+RS, NPS, KH))
    L.append(('+', 7, npx + 3*(NPS+NPG), npy+RS, NPS, NPTAL))
    for i, (lbl, idx) in enumerate([('4',51),('5',52),('6',60)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+2*RS, NPS, KH))
    for i, (lbl, idx) in enumerate([('1',34),('2',42),('3',43)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+3*RS, NPS, KH))
    L.append(('↵', 33, npx + 3*(NPS+NPG), npy+3*RS, NPS, NPTAL))
    L.append(('0', 78, npx, npy+4*RS, 2*NPS+NPG, KH))
    L.append(('.', 79, npx + 2*NPS+2*NPG, npy+4*RS, NPS, KH))

    return _to_iso(L) if shape == "iso" else L


def _to_iso(keys):
    """The ISO shape of the main block: a tall Enter where the ANSI backslash
    and Enter keys are, a short key below its top ("ISO#"), a short left Shift
    and the extra key beside it ("ISO<").

    The LEDs, lit one at a time on an ISO-DE Everest Max: the tall Enter keeps
    the ANSI Enter's LED (120), "ISO#" is 111 (the column of "]", one row
    lower), "ISO<" is 13 (the column after left Shift's 4). The ANSI
    backslash's LED (119) lights nothing on the ISO board."""
    hw = int(25 * SCALE)
    by = {k[1]: k for k in keys if k[1] is not None}
    bs, ent, lsh = by[119], by[120], by[4]
    out = []
    for k in keys:
        if k is bs:
            nx = ent[2] + hw + 3
            out.append(("↵", 120, nx, bs[3], bs[2] + bs[4] - nx, bs[5] + int(32 * SCALE)))
        elif k is ent:
            out.append(("ISO#", 111, ent[2], ent[3], hw, ent[5]))
        elif k is lsh:
            sw = lsh[4] - hw - 3
            out.append((lsh[0], 4, lsh[2], lsh[3], sw, lsh[5]))
            out.append(("ISO<", 13, lsh[2] + sw + 3, lsh[3], hw, lsh[5]))
        else:
            out.append(k)
    return out


LAYOUT = build_layout()
CANVAS_W = 14 + int(642 * SCALE) + 32 + int(166 * SCALE) + 14
CANVAS_H = (14 + int(12 * SCALE)) + 5 * int(32 * SCALE) + int(30 * SCALE) + 18 + 24
SIDE_LED_SIZE = 9       # the ring's LEDs, drawn as squares of this size
SIDE_OFFSET = 12        # how far outside the key area the ring is drawn

# The numpad module carries the four display keys D1-D4 on top; the media dock
# with the dial sits beyond it. Same canvas coordinates as the keys (the D-keys
# lie above the numpad's first row, so their y is smaller than the keys' top).
_NPS = int(33 * SCALE)
_NPG = int(7 * SCALE)
_NP_X = 14 + int(642 * SCALE) + 32 + int(5 * SCALE)          # the numpad's left edge
_NP_W = 4 * _NPS + 3 * _NPG
DKEY_SIZE = _NPS
DKEY_Y = 14 + int(12 * SCALE) - DKEY_SIZE - 12                # one row above NumLk
DKEYS = [("D%d" % (i + 1), _NP_X + i * (_NPS + _NPG), DKEY_Y, DKEY_SIZE, DKEY_SIZE)
         for i in range(4)]
# The case's top edge. Like the original, the main board has a strip above the
# F-row (the logo strip) and its top is level with the numpad module's, whose
# D-keys sit above its first row. PLATE_PAD is the plate edge the interface
# draws around the keys (PAD in keyboard-flat.js).
PLATE_PAD = 8
BOARD_TOP = DKEY_Y - PLATE_PAD
# The media dock is a horizontal pill on top of the main board, top right, as in
# the original (measured there, relative to the main board): about 36 % of the
# board's width, its right edge 3.4 % of the board width inside the board's
# right edge, 40/75 of its own height above the case's top edge, 75:232 high to
# wide. Its lower part lies on the logo strip, clear of the F-row.
_BOARD_X = 14
_BOARD_W = int(642 * SCALE)
_DK_W = round(0.36 * _BOARD_W)
_DK_H = round(_DK_W * 75 / 232)
_DK_X = _BOARD_X + _BOARD_W - round(0.034 * _BOARD_W) - _DK_W
_DK_Y = BOARD_TOP - round(_DK_H * 40 / 75)
DOCK = (_DK_X, _DK_Y, _DK_W, _DK_H)                           # x, y, w, h of the pill
_DK_R = round(_DK_H * 0.37)
DIAL = (_DK_X + round(_DK_W * 0.85), _DK_Y + _DK_H // 2, _DK_R)  # centre x, y and radius
_DK_BTN = round(_DK_W * 0.09)        # the round buttons' diameter
_DK_MENU = round(_DK_W * 0.10)       # the darker fifth button (dial menu, not bindable)
_DK_ARROW = max(12, round(_DK_W * 0.075))
# The bindable elements: (name, id in the original, x, y, w, h). The grip's
# dots are drawn by the interface; the menu button is shown but cannot be bound.
DOCK_BUTTONS = [(n, vk, _DK_X + round(_DK_W * f) - _DK_BTN // 2, _DK_Y + _DK_H // 2 - _DK_BTN // 2, _DK_BTN, _DK_BTN)
                for n, vk, f in (("prev", 177, 0.17), ("next", 176, 0.29), ("play", 179, 0.41), ("mute", 173, 0.53))]
DOCK_MENU = (_DK_X + round(_DK_W * 0.655) - _DK_MENU // 2, _DK_Y + _DK_H // 2 - _DK_MENU // 2, _DK_MENU, _DK_MENU)
DOCK_SCROLL = [("scroll_l", 262, DIAL[0] - _DK_ARROW - 2, _DK_Y - _DK_ARROW - 3, _DK_ARROW, _DK_ARROW),
               ("scroll_r", 263, DIAL[0] + 2, _DK_Y - _DK_ARROW - 3, _DK_ARROW, _DK_ARROW)]

# The keys' numbers in Mountain's SDK (Base Camp's EverestKeyBidings.DLLKeyId),
# by LED index: the key remap
# commands `14 20` / `14 21` name a key by them. They are the classic key
# position numbers. Two are not in the captured list and follow from that
# numbering: the ISO keys "ISO#" (42, between ' 41 and Enter 43) and "ISO<"
# (45, between left Shift 44 and Z 46). The capture names 63 "(R)Win"; this
# keyboard has its Menu key there (LED 86, see build_layout).
KEY_IDS = {
    0: 110, 9: 112, 18: 113, 27: 114, 36: 115, 45: 116, 54: 117, 63: 118,
    72: 119, 81: 120, 90: 121, 99: 122, 108: 123, 117: 124, 114: 125, 123: 126,
    1: 1, 10: 2, 19: 3, 28: 4, 37: 5, 46: 6, 55: 7, 64: 8, 73: 9, 82: 10,
    91: 11, 100: 12, 109: 13, 87: 15, 96: 75, 88: 76,
    2: 16, 11: 17, 20: 18, 29: 19, 38: 20, 47: 21, 56: 22, 65: 23, 74: 24,
    83: 25, 92: 26, 101: 27, 110: 28, 119: 29, 105: 80, 115: 85,
    3: 30, 12: 31, 21: 32, 30: 33, 39: 34, 48: 35, 57: 36, 66: 37, 75: 38,
    84: 39, 93: 40, 102: 41, 111: 42, 120: 43, 97: 81, 106: 86,
    4: 44, 13: 45, 22: 46, 31: 47, 40: 48, 49: 49, 58: 50, 67: 51, 76: 52,
    85: 53, 94: 54, 103: 55, 121: 57, 124: 83,
    5: 58, 14: 59, 23: 60, 41: 61, 68: 62, 86: 63, 95: 64,
    104: 79, 113: 84, 122: 89,
    6: 90, 24: 95, 16: 100, 15: 105, 61: 91, 69: 96, 70: 101, 7: 106,
    51: 92, 52: 97, 60: 102, 34: 93, 42: 98, 43: 103, 33: 108, 78: 99, 79: 104,
}
#: Fn (LED 77, SDK number 154) cannot be bound, as in the original.
FN_LED = 77
DKEY_IDS = (170, 171, 172, 173)                  # D1-D4
DOCK_KEY_IDS = {"prev": 181, "next": 180, "play": 183, "mute": 184,
                "scroll_l": 188, "scroll_r": 187}


def bindable_ids():
    """Every SDK key number a binding can be made for: the keys (both
    shapes), D1-D4 and the dock's buttons."""
    return set(KEY_IDS.values()) | set(DKEY_IDS) | set(DOCK_KEY_IDS.values())


# QWERTY -> QWERTZ label substitution (German keyboard layout)
QWERTZ_MAP = {
    'Y': 'Z', 'Z': 'Y',
    '`': '^', '-': 'ß', '=': '´',
    '[': 'ü', ']': '+', '\\': '#',
    ';': 'ö', "'": 'ä', '/': '-',
}

# Ring LED indices per edge, in drawing order.
SIDE_ZONE_INDICES = [
    [13,14,15,7,6,5,4,3,2,1,0],             # main top    (11)
    [9,8,10,11],                            # main right   (4)
    [20,21,22,23,24,25,26,27,28,29,30,12],  # main bottom (12)
    [16,17,18,19],                          # main left    (4)
    [31,44,43,42],                          # numpad top   (4)
    [41,40,39],                             # numpad right (3)
    [35,36,37,38],                          # numpad bottom(4)
    [32,33,34],                             # numpad left  (3)
]


def as_dict(shape="ansi"):
    """The layout in a form JSON can carry, for an interface that is not Python.
    `shape` is "ansi" or "iso" (the main block's keys differ, see _to_iso)."""
    shape = "iso" if shape == "iso" else "ansi"
    return {
        "shape": shape,
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        # kid: the key's SDK number for a binding, None for Fn
        "keys": [{"label": lbl, "led": idx, "x": x, "y": y, "w": w, "h": h,
                  "kid": KEY_IDS.get(idx)}
                 for lbl, idx, x, y, w, h in (build_layout(shape) if shape == "iso" else LAYOUT)],
        "num_leds": NUM_LEDS,
        "num_side_leds": NUM_SIDE_LEDS,
        "side_zones": [list(z) for z in SIDE_ZONE_INDICES],
        "side_led_size": SIDE_LED_SIZE,
        "side_offset": SIDE_OFFSET,
        "qwertz": dict(QWERTZ_MAP),
        "dkeys": [{"label": lbl, "idx": i, "x": x, "y": y, "w": w, "h": h, "kid": DKEY_IDS[i]}
                  for i, (lbl, x, y, w, h) in enumerate(DKEYS)],
        "dock": {"x": DOCK[0], "y": DOCK[1], "w": DOCK[2], "h": DOCK[3],
                 "buttons": [{"name": n, "vk": vk, "x": x, "y": y, "w": w, "h": h,
                              "kid": DOCK_KEY_IDS[n]}
                             for n, vk, x, y, w, h in DOCK_BUTTONS + DOCK_SCROLL],
                 "menu": {"x": DOCK_MENU[0], "y": DOCK_MENU[1], "w": DOCK_MENU[2], "h": DOCK_MENU[3]}},
        "dial": {"cx": DIAL[0], "cy": DIAL[1], "r": DIAL[2]},
        "numpad_x": _NP_X,
        "board_top": BOARD_TOP,
    }
