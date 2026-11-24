"""Pressing keys, typing and moving the mouse on the user's desktop.

Key actions, text actions and macros all end here. Keys are named in one
vocabulary across the application ("ctrl", "f13", "pageup", "a"; see
KEY_CHOICES), and an InputInjector turns them into whatever the desktop
needs:

    inj = injector()             # None when no tool is installed
    inj.key_combo("ctrl+shift+m")
    inj.type_text("kind regards")
    inj.mouse_click("right")

On Linux that is xdotool (X11, and XWayland) or ydotool (Wayland, through
uinput), whichever is installed, xdotool first. The tool is looked up on
every call rather than once, so installing one while the application runs
takes effect at once. A Windows port adds an injector on SendInput and
returns it from injector(); nothing that calls this module changes.

Under sudo the tools run as the invoking user (see launcher.user_env), since
root's X authority is not the desktop's.
"""
import os
import shutil
import subprocess

from core.platform import launcher

# Friendly key name -> xdotool keysym
KEY_MAP = {
    "ctrl": "Control_L", "ctrl_r": "Control_R",
    "shift": "Shift_L", "shift_r": "Shift_R",
    "alt": "Alt_L", "alt_r": "Alt_R",
    "win": "Super_L", "super": "Super_L",
    "enter": "Return", "return": "Return",
    "tab": "Tab", "esc": "Escape", "escape": "Escape",
    "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next",
    "insert": "Insert", "print": "Print",
    "capslock": "Caps_Lock", "numlock": "Num_Lock", "scrolllock": "Scroll_Lock",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    # F13-F24 have no key on the board but exist as keysyms, so they are the
    # usual target for a macro key that must not collide with a real shortcut.
    "f13": "F13", "f14": "F14", "f15": "F15", "f16": "F16",
    "f17": "F17", "f18": "F18", "f19": "F19", "f20": "F20",
    "f21": "F21", "f22": "F22", "f23": "F23", "f24": "F24",
    "pause": "Pause", "menu": "Menu",
}

# Friendly names for dropdown display (sorted by category)
KEY_CHOICES = [
    # Modifiers
    "ctrl", "shift", "alt", "win", "ctrl_r", "shift_r", "alt_r",
    # Navigation
    "enter", "tab", "esc", "space", "backspace", "delete",
    "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown", "insert",
    # F-keys
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20",
    "f21", "f22", "f23", "f24",
    # Letters
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    # Numbers
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    # Punctuation
    "minus", "equal", "bracketleft", "bracketright", "backslash",
    "semicolon", "apostrophe", "grave", "comma", "period", "slash",
]

# Reverse map: X/Tk keysym (lower case) → friendly name, for recording a key
# from a key event. Tk reports X keysyms on Linux; a web interface would map
# DOM key codes onto the same friendly names.
KEYSYM_TO_FRIENDLY = {}
for _friendly, _xkey in KEY_MAP.items():
    KEYSYM_TO_FRIENDLY[_xkey.lower()] = _friendly
# Add identity mappings for single-char keys
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    KEYSYM_TO_FRIENDLY[_c] = _c
# Common tkinter keysyms that differ from xdotool names
KEYSYM_TO_FRIENDLY.update({
    "control_l": "ctrl", "control_r": "ctrl_r",
    "shift_l": "shift", "shift_r": "shift_r",
    "alt_l": "alt", "alt_r": "alt_r",
    "super_l": "win", "super_r": "win",
    "return": "enter", "escape": "esc",
    "backspace": "backspace", "delete": "delete",
    "tab": "tab", "space": "space",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end",
    "prior": "pageup", "next": "pagedown",
    "insert": "insert", "print": "print",
    "caps_lock": "capslock", "num_lock": "numlock", "scroll_lock": "scrolllock",
    "pause": "pause", "menu": "menu",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
    "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
    "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
    "minus": "minus", "equal": "equal",
    "bracketleft": "bracketleft", "bracketright": "bracketright",
    "backslash": "backslash", "semicolon": "semicolon",
    "apostrophe": "apostrophe", "grave": "grave",
    "comma": "comma", "period": "period", "slash": "slash",
})

# ── Keys from a web page ─────────────────────────────────────────────────────
# KeyboardEvent.code (the key's place on the board) -> friendly name, for keys
# that do not type a character. Printable keys are named by the character the
# event typed, so a German or French layout records what the user pressed.
DOM_CODE_TO_FRIENDLY = {
    "ControlLeft": "ctrl", "ControlRight": "ctrl_r",
    "ShiftLeft": "shift", "ShiftRight": "shift_r",
    "AltLeft": "alt", "AltRight": "alt_r",
    "MetaLeft": "win", "MetaRight": "win", "OSLeft": "win", "OSRight": "win",
    "Enter": "enter", "NumpadEnter": "enter", "Tab": "tab", "Escape": "esc",
    "Space": "space", "Backspace": "backspace", "Delete": "delete",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
    "Insert": "insert", "PrintScreen": "print", "CapsLock": "capslock",
    "NumLock": "numlock", "ScrollLock": "scrolllock", "Pause": "pause",
    "ContextMenu": "menu",
    "Minus": "minus", "Equal": "equal", "BracketLeft": "bracketleft",
    "BracketRight": "bracketright", "Backslash": "backslash",
    "Semicolon": "semicolon", "Quote": "apostrophe", "Backquote": "grave",
    "Comma": "comma", "Period": "period", "Slash": "slash",
}
_DOM_CHAR_TO_FRIENDLY = {
    " ": "space", "-": "minus", "=": "equal", "[": "bracketleft",
    "]": "bracketright", "\\": "backslash", ";": "semicolon", "'": "apostrophe",
    "`": "grave", ",": "comma", ".": "period", "/": "slash",
}


def key_from_dom_code(code, key=""):
    """The friendly key name for a browser key event, or None for a key the
    macro vocabulary has no name for.

    `code` is KeyboardEvent.code ("KeyA", "F5", "ControlLeft"), `key` is
    KeyboardEvent.key ("a", "A", "-", "Dead"). A letter or digit is named by
    `key` (lower case), so the name follows the user's layout; everything else
    by `code`. Numpad digits count as digits.
    """
    code, key = code or "", key or ""
    if len(key) == 1:
        low = key.lower()
        if low in "abcdefghijklmnopqrstuvwxyz0123456789":
            return low
        if key in _DOM_CHAR_TO_FRIENDLY:
            return _DOM_CHAR_TO_FRIENDLY[key]
    if code.startswith("Key") and len(code) == 4 and code[3].isalpha():
        return code[3].lower()
    if code.startswith("Digit") and len(code) == 6 and code[5].isdigit():
        return code[5]
    if code.startswith("Numpad") and len(code) == 7 and code[6].isdigit():
        return code[6]
    if code.startswith("F") and code[1:].isdigit() and 1 <= int(code[1:]) <= 24:
        return code.lower()
    return DOM_CODE_TO_FRIENDLY.get(code)


MOUSE_CLICK_BUTTONS = {
    "left": "1", "middle": "2", "right": "3",
    "back": "8", "forward": "9",
    "button10": "10", "button11": "11", "button12": "12",
}
MOUSE_SCROLL_DIRS = {"up": "4", "down": "5"}

# ydotool uses Linux input event key codes (from input-event-codes.h)
YDOTOOL_KEY_MAP = {
    "ctrl": "29", "ctrl_r": "97",
    "shift": "42", "shift_r": "54",
    "alt": "56", "alt_r": "100",
    "win": "125", "super": "125",
    "enter": "28", "return": "28",
    "tab": "15", "esc": "1", "escape": "1",
    "space": "57", "backspace": "14", "delete": "111",
    "up": "103", "down": "108", "left": "105", "right": "106",
    "home": "102", "end": "107",
    "pageup": "104", "pagedown": "109",
    "insert": "110", "print": "99",
    "capslock": "58", "numlock": "69", "scrolllock": "70",
    "f1": "59", "f2": "60", "f3": "61", "f4": "62",
    "f5": "63", "f6": "64", "f7": "65", "f8": "66",
    "f9": "67", "f10": "68", "f11": "87", "f12": "88",
    "f13": "183", "f14": "184", "f15": "185", "f16": "186",
    "f17": "187", "f18": "188", "f19": "189", "f20": "190",
    "f21": "191", "f22": "192", "f23": "193", "f24": "194",
    "pause": "119", "menu": "139",
    "a": "30", "b": "48", "c": "46", "d": "32", "e": "18",
    "f": "33", "g": "34", "h": "35", "i": "23", "j": "36",
    "k": "37", "l": "38", "m": "50", "n": "49", "o": "24",
    "p": "25", "q": "16", "r": "19", "s": "31", "t": "20",
    "u": "22", "v": "47", "w": "17", "x": "45", "y": "21", "z": "44",
    "1": "2", "2": "3", "3": "4", "4": "5", "5": "6",
    "6": "7", "7": "8", "8": "9", "9": "10", "0": "11",
    "minus": "12", "equal": "13",
    "bracketleft": "26", "bracketright": "27",
    "backslash": "43", "semicolon": "39",
    "apostrophe": "40", "grave": "41",
    "comma": "51", "period": "52", "slash": "53",
}

# ydotool click codes for the xdotool button numbers (0xC0 = left down+up).
_YDOTOOL_BUTTONS = {"1": "0xC0", "2": "0xC2", "3": "0xC1", "8": "0xC7", "9": "0xC8"}


def friendly_key(keysym):
    """The friendly name for an X/Tk keysym, or the keysym itself."""
    keysym = (keysym or "").lower()
    return KEYSYM_TO_FRIENDLY.get(keysym, keysym)


def resolve_key(name):
    """Map a friendly key name to an xdotool keysym."""
    return KEY_MAP.get(name.lower(), name)


def resolve_key_ydotool(name):
    """Map a friendly key name to a ydotool key code."""
    return YDOTOOL_KEY_MAP.get(name.lower(), name)


# ── Finding the tool, running it ─────────────────────────────────────────────

def detect_session():
    """Detect display server: 'wayland' or 'x11'."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    xdg = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if xdg == "wayland":
        return "wayland"
    if xdg == "x11":
        return "x11"
    # Check for sudo user's session
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        uid = pwd.getpwnam(sudo_user).pw_uid
        if os.path.exists(f"/run/user/{uid}/wayland-0"):
            return "wayland"
    return "x11"


def find_tool():
    """Find the best available input automation tool.
    Returns ('xdotool', path) or ('ydotool', path) or (None, None).
    """
    for tool in ("xdotool", "ydotool"):
        path = shutil.which(tool)
        if path:
            return tool, path
    return None, None


def _tool_cmd(tool_name, args):
    env, sudo_user = launcher.user_env()
    return env, launcher.sudo_prefix(sudo_user) + [tool_name] + list(args)


def run_tool(tool_name, *args):
    """Run xdotool or ydotool with correct user environment."""
    env, cmd = _tool_cmd(tool_name, args)
    try:
        subprocess.run(cmd, env=env, timeout=5, capture_output=True)
    except Exception:
        pass


def run_tool_output(tool_name, *args):
    """Run tool and return stdout."""
    env, cmd = _tool_cmd(tool_name, args)
    try:
        r = subprocess.run(cmd, env=env, timeout=5, capture_output=True)
        return r.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


# ── Injectors ────────────────────────────────────────────────────────────────

class InputInjector:
    """Synthesises input on the desktop. Keys use the friendly names above.

    Every method is fire and forget: a tool that fails is not something a key
    press can report anywhere, so failures are swallowed by run_tool().
    """
    name = ""

    def key_combo(self, combo):
        """Press and release a combination such as "ctrl+shift+m"."""
        raise NotImplementedError

    def key(self, action, key):
        """action is "key_down", "key_up" or "key_tap" (the macro step types)."""
        raise NotImplementedError

    def type_text(self, text):
        raise NotImplementedError

    def mouse_click(self, button):
        """button is a MOUSE_CLICK_BUTTONS name; anything else is "left"."""
        raise NotImplementedError

    def mouse_move(self, x, y):
        """Move to absolute screen coordinates (strings or numbers)."""
        raise NotImplementedError

    def mouse_scroll(self, direction, count):
        """Scroll "up" or "down" by `count` notches."""
        raise NotImplementedError


def _button_number(button):
    return MOUSE_CLICK_BUTTONS.get(button.lower().strip(), "1")


class XdotoolInjector(InputInjector):
    name = "xdotool"

    def key_combo(self, combo):
        keys = "+".join(resolve_key(k) for k in combo.split("+"))
        run_tool("xdotool", "key", "--clearmodifiers", keys)

    def key(self, action, key):
        keysym = resolve_key(key)
        if action == "key_down":
            run_tool("xdotool", "keydown", keysym)
        elif action == "key_up":
            run_tool("xdotool", "keyup", keysym)
        else:
            run_tool("xdotool", "key", keysym)

    def type_text(self, text):
        run_tool("xdotool", "type", "--clearmodifiers", "--delay", "12", text)

    def mouse_click(self, button):
        run_tool("xdotool", "click", _button_number(button))

    def mouse_move(self, x, y):
        run_tool("xdotool", "mousemove", str(x), str(y))

    def mouse_scroll(self, direction, count):
        btn_num = MOUSE_SCROLL_DIRS.get(direction, "4")
        run_tool("xdotool", "click", "--repeat", str(count), btn_num)


class YdotoolInjector(InputInjector):
    name = "ydotool"

    def key_combo(self, combo):
        parts = [resolve_key_ydotool(k) for k in combo.split("+")]
        for code in parts:
            run_tool("ydotool", "key", f"{code}:1")
        for code in reversed(parts):
            run_tool("ydotool", "key", f"{code}:0")

    def key(self, action, key):
        code = resolve_key_ydotool(key)
        if action == "key_down":
            run_tool("ydotool", "key", f"{code}:1")
        elif action == "key_up":
            run_tool("ydotool", "key", f"{code}:0")
        else:
            run_tool("ydotool", "key", f"{code}:1", f"{code}:0")

    def type_text(self, text):
        run_tool("ydotool", "type", "--key-delay", "12", text)

    def mouse_click(self, button):
        # ydotool click: 0=left, 1=right, 2=middle
        run_tool("ydotool", "click", _YDOTOOL_BUTTONS.get(_button_number(button), "0xC0"))

    def mouse_move(self, x, y):
        run_tool("ydotool", "mousemove", "-a", str(x), str(y))

    def mouse_scroll(self, direction, count):
        pixels = count * 30
        if direction == "up":
            pixels = -pixels
        run_tool("ydotool", "mousemove", "--wheel", "0", str(pixels))


_INJECTORS = {"xdotool": XdotoolInjector, "ydotool": YdotoolInjector}


def injector_for(tool):
    """The injector for a tool name, or None for an unknown one."""
    cls = _INJECTORS.get(tool)
    return cls() if cls else None


def injector():
    """The injector for this desktop, or None when no tool is installed."""
    tool, _ = find_tool()
    return injector_for(tool) if tool else None


# ── The two calls key actions use ────────────────────────────────────────────

def simulate_keypress(key_name):
    """Simulate a single keypress (or combo like 'ctrl+c') via xdotool or ydotool.
    Picks the available tool automatically — works on X11 and Wayland."""
    if not key_name:
        return
    inj = injector()
    if inj is None:
        print("[Keypress] No input tool found (install xdotool or ydotool)")
        return
    inj.key_combo(key_name)


def simulate_text(text):
    """Type out text via xdotool or ydotool. Works on X11 and Wayland."""
    if not text:
        return
    inj = injector()
    if inj is None:
        print("[Text] No input tool found (install xdotool or ydotool)")
        return
    inj.type_text(text)


def cursor_pos():
    """Get current mouse position. Returns (x, y) or None.
    Tries xdotool first (fastest), then python-xlib.
    Note: on pure Wayland these only work over XWayland when the cursor
    is over an X11 window."""
    # Method 1: xdotool
    out = run_tool_output("xdotool", "getmouselocation")
    if out:
        parts = dict(p.split(":") for p in out.split() if ":" in p)
        try:
            return int(parts["x"]), int(parts["y"])
        except (KeyError, ValueError):
            pass

    # Method 2: python-xlib
    try:
        from Xlib import display as _xdisplay
        d = _xdisplay.Display()
        data = d.screen().root.query_pointer()
        d.close()
        return data.root_x, data.root_y
    except Exception:
        pass

    return None


def tools_status():
    """(tool name or None, session type) for a "macros need xdotool" hint."""
    session = detect_session()
    tool, _ = find_tool()
    return tool, session
