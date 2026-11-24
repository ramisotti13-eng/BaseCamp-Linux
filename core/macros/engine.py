"""Plays a macro: its steps in order, each followed by its delay, repeated as
its mode says.

    execute_macro(macro)                       # once / repeat N
    execute_macro(macro, stop_event)           # toggle: until stop_event is set

Blocking: callers run it on a thread of their own (MacroService does). Keys,
text and the mouse go through core/platform/input, shell, URL and folder
steps through core/platform/launcher, so the engine reads the same on every
OS. A stop event is honoured between steps and inside every wait, so a
toggle macro stops within 50 ms even in the middle of a long delay.
"""
import time

from core.macros import recordings
from core.platform import input as _input
from core.platform import launcher as _launcher


def execute_macro(macro_data, stop_event=None):
    """Execute a macro's action sequence.

    macro_data: dict with 'actions', 'repeat_mode', 'repeat_count'
    stop_event: threading.Event for toggle mode cancellation
    """
    repeat_mode = macro_data.get("repeat_mode", "once")
    repeat_count = macro_data.get("repeat_count", 1)

    if repeat_mode == "once":
        run_actions(macro_data.get("actions", []), stop_event)
    elif repeat_mode == "repeat":
        for _ in range(int(repeat_count)):
            if stop_event and stop_event.is_set():
                break
            run_actions(macro_data.get("actions", []), stop_event)
    elif repeat_mode == "toggle":
        while not (stop_event and stop_event.is_set()):
            run_actions(macro_data.get("actions", []), stop_event)


def run_actions(actions, stop_event=None):
    inj = _input.injector()
    if inj is None:
        print("[Macro] ERROR: No input tool found (install xdotool or ydotool)")
        return

    for act in actions:
        if stop_event and stop_event.is_set():
            return
        atype = act.get("type", "")
        value = str(act.get("value", ""))
        delay = int(act.get("delay", 0))

        run_step(inj, atype, value, stop_event)

        if delay > 0 and atype != "delay":
            sleep_ms(delay, stop_event)


def run_step(inj, atype, value, stop_event=None):
    """One step of a macro, with the injector the macro runs on."""
    if atype in ("key_down", "key_up", "key_tap"):
        inj.key(atype, value)
    elif atype == "delay":
        try:
            sleep_ms(int(float(value)), stop_event)
        except (ValueError, TypeError):
            pass
    elif atype == "text":
        inj.type_text(value)
    elif atype == "mouse_click":
        inj.mouse_click(value)
    elif atype == "mouse_move":
        mouse_move_step(inj, value)
    elif atype == "mouse_path":
        play_mouse_path(inj, value, stop_event)
    elif atype == "mouse_scroll":
        mouse_scroll_step(inj, value)
    elif atype == "shell":
        _launcher.run_shell(value)
    elif atype in ("url", "folder"):
        _launcher.open_target(value)


def mouse_move_step(inj, value):
    """"x, y" or "x y"; anything else is ignored."""
    parts = value.replace(",", " ").split()
    if len(parts) != 2:
        return
    inj.mouse_move(parts[0].strip(), parts[1].strip())


def mouse_scroll_step(inj, value):
    """"up 3", "down 5"; the direction defaults to up and the count to 3."""
    parts = value.lower().strip().split()
    direction = parts[0] if parts else "up"
    count = int(parts[1]) if len(parts) > 1 else 3
    inj.mouse_scroll(direction, count)


def play_mouse_path(inj, filename, stop_event=None):
    """Play back a mouse recording file."""
    loaded = recordings.load_recording(filename)
    if loaded is None:
        return
    positions, add_click = loaded
    prev_t = positions[0][2] if positions else 0
    for x, y, t in positions:
        if stop_event and stop_event.is_set():
            return
        delay_ms = int((t - prev_t) * 1000)
        if delay_ms > 5:
            sleep_ms(delay_ms, stop_event)
        inj.mouse_move(str(x), str(y))
        prev_t = t
    if add_click:
        inj.mouse_click("left")


def sleep_ms(ms, stop_event=None):
    """Sleep for ms milliseconds, checking stop_event every 50ms."""
    remaining = ms / 1000.0
    while remaining > 0:
        if stop_event and stop_event.is_set():
            return
        chunk = min(remaining, 0.05)
        time.sleep(chunk)
        remaining -= chunk
