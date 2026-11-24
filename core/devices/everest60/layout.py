"""Where the Everest 60's keys and side-ring LEDs are, as plain data.

The per-key editor of a web interface draws the keyboard from this. Every key
is (label, led, x, y, w, h): `led` is the key's position in the per-key colour
list (ESC is 0; the same order as protocol.LEDIDX, whose entry is the
firmware's LED address), x, y, w, h are in canvas units (one key unit = UNIT).

The side ring is 44 points around the key area, in the order of the ring's
LEDs: it starts above ESC and goes clockwise (protocol.SIDE_LED_INDICES).

The ISO board has the same 64 LEDs as the ANSI one (the LED table knows no
extra key), so both draw the same.
"""
from core.devices.everest60 import protocol as P

UNIT = 44                     # canvas units per key unit
COLS = 15                     # the board is 15 key units wide
ROWS = 5
MARGIN = 26                   # room around the keys for the side ring
CANVAS_W = COLS * UNIT + 2 * MARGIN
CANVAS_H = ROWS * UNIT + 2 * MARGIN

# (label, width in key units) per row; the widths of every row add up to COLS.
_ROWS = (
    [("Esc", 1)] + [(c, 1) for c in "1234567890-="] + [("⌫", 2)],
    [("Tab", 1.5)] + [(c, 1) for c in "QWERTYUIOP[]"] + [("\\", 1.5)],
    [("Caps", 1.75)] + [(c, 1) for c in "ASDFGHJKL;'"] + [("↵", 2.25)],
    [("⇧", 2)] + [(c, 1) for c in "ZXCVBNM,./"]
    + [("⇧", 1), ("↑", 1), ("Del", 1)],
    [("Ctrl", 1.25), ("⊞", 1.25), ("Alt", 1.25), (" ", 6.25), ("Alt", 1),
     ("Fn", 1), ("←", 1), ("↓", 1), ("→", 1)],
)


def build_layout(shape="ansi"):
    """[(label, led, x, y, w, h)] for the 64 keys, in led order. The ISO shape
    has the same 64 LEDs: the tall Enter sits on the ANSI backslash key's LED,
    the short key beside it ("ISO#") on the ANSI Enter's, and the extra key beside
    the shortened left Shift ("ISO<") has no LED (led None), so the list has 65."""
    out = []
    for r, row in enumerate(_ROWS):
        x = 0.0
        for label, w in row:
            out.append((label, len(out), MARGIN + round(x * UNIT), MARGIN + r * UNIT,
                        round(w * UNIT), UNIT))
            x += w
    if shape != "iso":
        return out
    iso = []
    for k in out:
        label, led, x, y, w, h = k
        if label == "\\":
            iso.append(("↵", led, x + UNIT // 4, y, w - UNIT // 4, 2 * UNIT))
        elif label == "↵":
            iso.append(("ISO#", led, x, y, UNIT, h))
        elif label == "⇧" and x == MARGIN:
            iso.append((label, led, x, y, UNIT, h))
            iso.append(("ISO<", None, x + UNIT, y, UNIT, h))
        else:
            iso.append(k)
    return iso


def side_points():
    """The 44 ring LEDs as (x, y), clockwise from above ESC, spread evenly
    over a rectangle just outside the keys."""
    x0, y0 = MARGIN - 12, MARGIN - 12
    x1, y1 = MARGIN + COLS * UNIT + 12, MARGIN + ROWS * UNIT + 12
    edges = ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0))
    lengths = [abs(bx - ax) + abs(by - ay) for ax, ay, bx, by in edges]
    total = sum(lengths)
    n = P.NUM_SIDE_LEDS
    pts = []
    for i in range(n):
        d = (i + 0.5) * total / n
        for (ax, ay, bx, by), ln in zip(edges, lengths):
            if d <= ln:
                f = d / ln
                pts.append((round(ax + (bx - ax) * f), round(ay + (by - ay) * f)))
                break
            d -= ln
    return pts


LAYOUT = build_layout()
SIDE_POINTS = side_points()


def as_dict(shape="ansi"):
    """The layout in a form JSON can carry. `shape` is "ansi" or "iso"."""
    shape = "iso" if shape == "iso" else "ansi"
    return {
        "shape": shape,
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "keys": [{"label": lbl, "led": led, "hw": None if led is None else P.LEDIDX[led],
                  "x": x, "y": y, "w": w, "h": h}
                 for lbl, led, x, y, w, h in (build_layout("iso") if shape == "iso" else LAYOUT)],
        "num_keys": P.NUM_KEYS,
        "side": [{"led": i, "hw": hw, "x": x, "y": y}
                 for i, (hw, (x, y)) in enumerate(zip(P.SIDE_LED_INDICES, SIDE_POINTS))],
        "side_led_size": 10,
    }
