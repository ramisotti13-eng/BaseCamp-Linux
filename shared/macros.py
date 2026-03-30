"""Macro execution engine for BaseCamp Linux.

Macros are sequences of actions (key press/release, delays, text, shell commands)
executed via xdotool (X11) or ydotool (Wayland).
"""
import os
import subprocess
import time
import uuid

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
    # Letters
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    # Numbers
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    # Punctuation
    "minus", "equal", "bracketleft", "bracketright", "backslash",
    "semicolon", "apostrophe", "grave", "comma", "period", "slash",
]

# Reverse map: tkinter keysym → friendly name (for record button)
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

ACTION_TYPES = [
    "key_down", "key_up", "key_tap", "delay", "text",
    "mouse_click", "mouse_move", "mouse_path", "mouse_scroll",
    "shell", "url", "folder",
]

ACTION_LABELS = {
    "key_down":      "Key Down",
    "key_up":        "Key Up",
    "key_tap":       "Key Tap",
    "delay":         "Delay (ms)",
    "text":          "Type Text",
    "mouse_click":   "Mouse Click",
    "mouse_move":    "Mouse Move",
    "mouse_path":    "Mouse Path",
    "mouse_scroll":  "Mouse Scroll",
    "shell":         "Shell",
    "url":           "URL",
    "folder":        "Folder",
}

MOUSE_CLICK_BUTTONS = {
    "left": "1", "middle": "2", "right": "3",
    "back": "8", "forward": "9",
    "button10": "10", "button11": "11", "button12": "12",
}
MOUSE_SCROLL_DIRS = {"up": "4", "down": "5"}

REPEAT_MODES = ["once", "repeat", "toggle"]


def generate_macro_id():
    return str(uuid.uuid4())[:8]


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


def _resolve_key(name):
    """Map a friendly key name to an xdotool keysym."""
    return KEY_MAP.get(name.lower(), name)


def _resolve_key_ydotool(name):
    """Map a friendly key name to a ydotool key code."""
    return YDOTOOL_KEY_MAP.get(name.lower(), name)


def _detect_session():
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


def _find_tool():
    """Find the best available input automation tool.
    Returns ('xdotool', path) or ('ydotool', path) or (None, None).
    """
    import shutil
    for tool in ("xdotool", "ydotool"):
        path = shutil.which(tool)
        if path:
            return tool, path
    return None, None


def _build_env():
    """Build environment for input tools, running as real user if under sudo."""
    env = os.environ.copy()
    sudo_user = env.get("SUDO_USER")
    if sudo_user:
        import pwd
        pw = pwd.getpwnam(sudo_user)
        uid = pw.pw_uid
        env["HOME"] = pw.pw_dir
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        if os.path.exists(f"/run/user/{uid}/wayland-0"):
            env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    return env, sudo_user


def _run_tool(tool_name, *args):
    """Run xdotool or ydotool with correct user environment."""
    env, sudo_user = _build_env()
    if sudo_user:
        cmd = ["sudo", "-u", sudo_user, "-E", tool_name] + list(args)
    else:
        cmd = [tool_name] + list(args)
    try:
        subprocess.run(cmd, env=env, timeout=5, capture_output=True)
    except Exception:
        pass


def _run_tool_output(tool_name, *args):
    """Run tool and return stdout."""
    env, sudo_user = _build_env()
    if sudo_user:
        cmd = ["sudo", "-u", sudo_user, "-E", tool_name] + list(args)
    else:
        cmd = [tool_name] + list(args)
    try:
        r = subprocess.run(cmd, env=env, timeout=5, capture_output=True)
        return r.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _xdotool(*args):
    """Run xdotool with the correct user environment."""
    _run_tool("xdotool", *args)


def _run_xdg_open(target):
    """Open a URL or folder via xdg-open."""
    env, sudo_user = _build_env()
    if sudo_user:
        cmd = ["sudo", "-u", sudo_user, "-E", "xdg-open", target]
    else:
        cmd = ["xdg-open", target]
    try:
        subprocess.Popen(cmd, env=env)
    except Exception:
        pass


def _run_shell(command):
    """Run a shell command."""
    env, sudo_user = _build_env()
    if sudo_user:
        cmd = ["sudo", "-u", sudo_user, "-E", "bash", "-c", command]
    else:
        cmd = command
    try:
        subprocess.Popen(cmd, shell=not sudo_user, env=env)
    except Exception:
        pass


def check_macro_tools():
    """Check if required tools are available. Returns (tool_name, session_type) or (None, session_type)."""
    session = _detect_session()
    tool, _ = _find_tool()
    return tool, session


def execute_macro(macro_data, stop_event=None):
    """Execute a macro's action sequence.

    macro_data: dict with 'actions', 'repeat_mode', 'repeat_count'
    stop_event: threading.Event for toggle mode cancellation
    """
    repeat_mode = macro_data.get("repeat_mode", "once")
    repeat_count = macro_data.get("repeat_count", 1)

    if repeat_mode == "once":
        _run_actions(macro_data.get("actions", []), stop_event)
    elif repeat_mode == "repeat":
        for _ in range(int(repeat_count)):
            if stop_event and stop_event.is_set():
                break
            _run_actions(macro_data.get("actions", []), stop_event)
    elif repeat_mode == "toggle":
        while not (stop_event and stop_event.is_set()):
            _run_actions(macro_data.get("actions", []), stop_event)


def _run_actions(actions, stop_event=None):
    tool, _ = _find_tool()
    if not tool:
        print("[Macro] ERROR: No input tool found (install xdotool or ydotool)")
        return

    for act in actions:
        if stop_event and stop_event.is_set():
            return
        atype = act.get("type", "")
        value = str(act.get("value", ""))
        delay = int(act.get("delay", 0))

        if atype in ("key_down", "key_up", "key_tap"):
            _exec_key(tool, atype, value)
        elif atype == "delay":
            try:
                _sleep_ms(int(float(value)), stop_event)
            except (ValueError, TypeError):
                pass
        elif atype == "text":
            _exec_text(tool, value)
        elif atype == "mouse_click":
            _exec_mouse_click(tool, value)
        elif atype == "mouse_move":
            _exec_mouse_move(tool, value)
        elif atype == "mouse_path":
            _exec_mouse_path(tool, value, stop_event)
        elif atype == "mouse_scroll":
            _exec_mouse_scroll(tool, value)
        elif atype == "shell":
            _run_shell(value)
        elif atype == "url":
            _run_xdg_open(value)
        elif atype == "folder":
            _run_xdg_open(value)

        if delay > 0 and atype != "delay":
            _sleep_ms(delay, stop_event)


# ── Tool-specific execution ──────────────────────────────────────────────────

def _exec_key(tool, atype, value):
    if tool == "xdotool":
        keysym = _resolve_key(value)
        if atype == "key_down":
            _run_tool("xdotool", "keydown", keysym)
        elif atype == "key_up":
            _run_tool("xdotool", "keyup", keysym)
        else:
            _run_tool("xdotool", "key", keysym)
    elif tool == "ydotool":
        code = _resolve_key_ydotool(value)
        if atype == "key_down":
            _run_tool("ydotool", "key", f"{code}:1")
        elif atype == "key_up":
            _run_tool("ydotool", "key", f"{code}:0")
        else:
            _run_tool("ydotool", "key", f"{code}:1", f"{code}:0")


def _exec_text(tool, value):
    if tool == "xdotool":
        _run_tool("xdotool", "type", "--clearmodifiers", "--delay", "12", value)
    elif tool == "ydotool":
        _run_tool("ydotool", "type", "--key-delay", "12", value)


def _exec_mouse_click(tool, value):
    btn_num = MOUSE_CLICK_BUTTONS.get(value.lower().strip(), "1")
    if tool == "xdotool":
        _run_tool("xdotool", "click", btn_num)
    elif tool == "ydotool":
        # ydotool click: 0=left, 1=right, 2=middle
        ydotool_btn = {"1": "0xC0", "2": "0xC2", "3": "0xC1",
                       "8": "0xC7", "9": "0xC8"}.get(btn_num, "0xC0")
        _run_tool("ydotool", "click", ydotool_btn)


def _exec_mouse_move(tool, value):
    parts = value.replace(",", " ").split()
    if len(parts) != 2:
        return
    x, y = parts[0].strip(), parts[1].strip()
    if tool == "xdotool":
        _run_tool("xdotool", "mousemove", x, y)
    elif tool == "ydotool":
        _run_tool("ydotool", "mousemove", "-a", x, y)


def _exec_mouse_path(tool, filename, stop_event=None):
    """Play back a mouse recording file."""
    from shared.config import MOUSE_RECORDINGS_DIR
    path = os.path.join(MOUSE_RECORDINGS_DIR, filename)
    if not os.path.exists(path):
        return
    try:
        import json
        data = json.loads(open(path).read())
        positions = data.get("positions", [])
        add_click = data.get("click_at_end", False)
    except Exception:
        return
    prev_t = positions[0][2] if positions else 0
    for x, y, t in positions:
        if stop_event and stop_event.is_set():
            return
        delay_ms = int((t - prev_t) * 1000)
        if delay_ms > 5:
            _sleep_ms(delay_ms, stop_event)
        _exec_mouse_move(tool, f"{x}, {y}")
        prev_t = t
    if add_click:
        _exec_mouse_click(tool, "left")


def save_mouse_recording(name, positions, click_at_end=False):
    """Save mouse positions to a recording file. Returns filename."""
    from shared.config import MOUSE_RECORDINGS_DIR
    os.makedirs(MOUSE_RECORDINGS_DIR, exist_ok=True)
    import json
    data = {
        "name": name,
        "positions": positions,
        "click_at_end": click_at_end,
    }
    filename = name.replace(" ", "_").lower() + ".json"
    path = os.path.join(MOUSE_RECORDINGS_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return filename


def list_mouse_recordings():
    """Return list of (filename, display_name) for saved recordings."""
    from shared.config import MOUSE_RECORDINGS_DIR
    result = []
    try:
        import json
        for f in sorted(os.listdir(MOUSE_RECORDINGS_DIR)):
            if f.endswith(".json"):
                try:
                    data = json.loads(open(os.path.join(MOUSE_RECORDINGS_DIR, f)).read())
                    name = data.get("name", f)
                    n = len(data.get("positions", []))
                    result.append((f, f"{name} ({n} pts)"))
                except Exception:
                    result.append((f, f))
    except FileNotFoundError:
        pass
    return result


def _exec_mouse_scroll(tool, value):
    parts = value.lower().strip().split()
    direction = parts[0] if parts else "up"
    count = int(parts[1]) if len(parts) > 1 else 3
    if tool == "xdotool":
        btn_num = MOUSE_SCROLL_DIRS.get(direction, "4")
        _run_tool("xdotool", "click", "--repeat", str(count), btn_num)
    elif tool == "ydotool":
        pixels = count * 30
        if direction == "up":
            pixels = -pixels
        _run_tool("ydotool", "mousemove", "--wheel", "0", str(pixels))


def get_mouse_location():
    """Get current mouse position. Returns (x, y) or None.
    Tries xdotool first (fastest), then python-xlib.
    Note: On pure Wayland these only work over XWayland when cursor
    is over an X11 window. For reliable Wayland tracking, use
    get_mouse_location_tk() with a fullscreen overlay instead.
    """
    # Method 1: xdotool
    out = _run_tool_output("xdotool", "getmouselocation")
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
    return None


def _sleep_ms(ms, stop_event=None):
    """Sleep for ms milliseconds, checking stop_event every 50ms."""
    remaining = ms / 1000.0
    while remaining > 0:
        if stop_event and stop_event.is_set():
            return
        chunk = min(remaining, 0.05)
        time.sleep(chunk)
        remaining -= chunk
