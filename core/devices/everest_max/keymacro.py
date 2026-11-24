"""Our macros as the Everest Max's own key macros, where that is possible.

A macro that only presses keys, types text, waits and clicks the left mouse
button can live in the keyboard (protocol.macro_packets): it then runs with
no application at all, as Base Camp's do. Everything else (shell, URL and
folder steps, mouse moves and paths, scrolling, other buttons, a toggle
macro, which runs until its key is pressed again) stays with the
application, which the key reaches through an F13-F24 (keybindings.py).

The keyboard sends key positions, so a key or a character is looked up in
the keyboard layout the application uses (core/devices/keyboard_layouts): on
a German layout "z" is the key US boards call Y. A character that needs
Shift or AltGr gets that key held around it.
"""
from core.devices.everest_max import layout, protocol as P

#: The macro editor's key names (core/platform/input.py) that are not a
#: character, with their SDK numbers (captures doc, "Key remapping").
NAMED_KEYS = {
    "ctrl": 58, "ctrl_r": 64, "shift": 44, "shift_r": 57, "alt": 60, "alt_r": 62,
    "win": 59, "super": 59, "enter": 43, "return": 43, "tab": 16, "esc": 110,
    "escape": 110, "space": 61, "backspace": 15, "delete": 76, "up": 83, "down": 84,
    "left": 79, "right": 89, "home": 80, "end": 81, "pageup": 85, "pagedown": 86,
    "insert": 75, "print": 124, "capslock": 30, "numlock": 90, "scrolllock": 125,
    "pause": 126, "menu": 63,
}
NAMED_KEYS.update({"f%d" % n: 111 + n for n in range(1, 13)})      # F1 112 .. F12 123
NAMED_KEYS.update({"f%d" % n: 217 + n for n in range(13, 25)})     # F13 230 .. F24 241

#: Key names that stand for the character on the key.
CHAR_NAMES = {"minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]",
              "backslash": "\\", "semicolon": ";", "apostrophe": "'", "grave": "`",
              "comma": ",", "period": ".", "slash": "/"}
#: Characters typed with a key of their own, whatever the layout.
TEXT_KEYS = {" ": 61, "\n": 43, "\t": 16}
MOD_KEYS = {"shift": 44, "altgr": 62}

#: The longest macro written to the keyboard: two chunks of 15 events, the
#: longest captured (30 events). How much more the keyboard holds is not
#: known; a longer macro runs from the application.
MAX_EVENTS = 30


def char_keys(legends, shape="ansi"):
    """{character: (key number, (modifier, ...))} for a layout's legends
    (keyboard_layouts.legends): what to press for each character."""
    by_label = {}
    for lbl, led, *_ in layout.build_layout(shape):
        kid = layout.KEY_IDS.get(led)
        if kid is not None:
            by_label.setdefault(lbl, kid)
    out = {}
    for name, (base, shift, altgr) in (legends or {}).items():
        kid = by_label.get(name)
        if kid is None:
            continue
        if len(base) == 1 and base.isalpha() and not shift:
            # a letter key: the legend is its capital
            out.setdefault(base.lower(), (kid, ()))
            out.setdefault(base.upper(), (kid, ("shift",)))
            pairs = ((altgr, ("altgr",)),)
        else:
            pairs = ((base, ()), (shift, ("shift",)), (altgr, ("altgr",)))
        for ch, mods in pairs:
            if len(ch) == 1:
                out.setdefault(ch, (kid, mods))
    return out


def key_number(name, chars):
    """The SDK number of a macro key name, or None. A character key is the
    one that types it without a modifier."""
    n = str(name).strip().lower()
    if n in NAMED_KEYS:
        return NAMED_KEYS[n]
    hit = chars.get(CHAR_NAMES.get(n, n))
    return hit[0] if hit and not hit[1] else None


def hardware_macro(macro, legends, shape="ansi", dead=""):
    """{times, events} for protocol.macro_packets, or None when the macro
    has to run in the application.

    A character of `dead` (the layout's dead keys, keyboard_layouts.DEAD_KEYS)
    is typed as its key and a space, which gives the character itself on
    every layout (the key alone only arms an accent; xdotool under Wayland
    cannot type it at all, seen 2026-11-23).

    Each event's timer is the wait after it in milliseconds (measured on the
    keyboard, 2026-11-23: a 500 ms delay step waits 500 ms), a step's delay
    and delay steps; 1 where there is none, as Base Camp writes."""
    if not isinstance(macro, dict):
        return None
    mode = macro.get("repeat_mode", "once")
    if mode == "once":
        times = 1
    elif mode == "repeat":
        try:
            times = int(macro.get("repeat_count", 1))
        except (TypeError, ValueError):
            return None
        if not 1 <= times <= 0xff:
            return None
    else:
        return None
    chars = char_keys(legends, shape)
    events = []                     # [key, page, action, ms to wait after it]

    def add(key, action, page=P.MACRO_PAGE_KEYS):
        events.append([key, page, action, 0])

    def wait(ms):
        if ms <= 0:
            return True
        if not events:
            return False            # nothing to wait after: only the application can
        events[-1][3] += ms
        return True

    for step in macro.get("actions") or []:
        if not isinstance(step, dict):
            return None
        kind = step.get("type", "")
        value = str(step.get("value", ""))
        try:
            delay = int(step.get("delay", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        if kind == "delay":
            try:
                if not wait(int(float(value))):
                    return None
            except ValueError:
                pass                    # the engine skips it too
            continue
        if kind in ("key_down", "key_up", "key_tap"):
            # "ctrl+c" taps the keys together, as xdotool does
            keys = [key_number(k, chars) for k in value.split("+")] if value.strip() else [None]
            if None in keys:
                return None
            if kind != "key_up":
                for k in keys:
                    add(k, P.MACRO_PRESS)
            if kind != "key_down":
                for k in reversed(keys):
                    add(k, P.MACRO_RELEASE)
        elif kind == "text":
            for ch in value:
                if ch in TEXT_KEYS:
                    hit = (TEXT_KEYS[ch], ())
                else:
                    hit = chars.get(ch)
                if hit is None:
                    return None
                kid, mods = hit
                for m in mods:
                    add(MOD_KEYS[m], P.MACRO_PRESS)
                add(kid, P.MACRO_PRESS)
                add(kid, P.MACRO_RELEASE)
                for m in reversed(mods):
                    add(MOD_KEYS[m], P.MACRO_RELEASE)
                if ch in dead:
                    add(TEXT_KEYS[" "], P.MACRO_PRESS)
                    add(TEXT_KEYS[" "], P.MACRO_RELEASE)
        elif kind == "mouse_click" and value.strip().lower() in ("", "left"):
            add(P.MACRO_MOUSE_LEFT, P.MACRO_PRESS, P.MACRO_PAGE_MOUSE)
            add(P.MACRO_MOUSE_LEFT, P.MACRO_RELEASE, P.MACRO_PAGE_MOUSE)
        else:
            return None
        if delay > 0 and not wait(delay):
            return None
    if not events or len(events) + 1 > MAX_EVENTS:         # + the end marker
        return None
    for e in events:
        e[3] = max(1, e[3])
        if e[3] > 0xffff:
            return None
    return {"times": times, "events": events}
