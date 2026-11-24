"""What a macro is, as stored in macros.json.

    {"macros": {<id>: {"name": "Build and test",
                       "actions": [{"type": "key_tap", "value": "a", "delay": 10}, ...],
                       "repeat_mode": "once" | "repeat" | "toggle",
                       "repeat_count": 1}}}

Keys and their dispatchers refer to a macro by its id, never by its name, so
a macro can be renamed without touching the keys it sits on.
"""
import copy
import uuid

ACTION_TYPES = [
    "key_down", "key_up", "key_tap", "delay", "text",
    "mouse_click", "mouse_move", "mouse_path", "mouse_scroll",
    "shell", "url", "folder",
]

# English labels of the step types. The 3.x editor shows these and maps them
# back to the type, so they are identifiers as much as text; kept as they are.
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

REPEAT_MODES = ["once", "repeat", "toggle"]

DEFAULT_NAME = "Macro"


def generate_macro_id():
    return str(uuid.uuid4())[:8]


def next_macro_name(macros):
    """"Macro", or "Macro N" with the first N not taken, for a new macro."""
    existing = {m.get("name", "") for m in macros.values()}
    base = DEFAULT_NAME
    if base not in existing:
        return base
    n = 1
    while f"{base} {n}" in existing:
        n += 1
    return f"{base} {n}"


def new_macro(macros):
    """The macro the New button creates: one key tap, run once."""
    return {
        "name": next_macro_name(macros),
        "actions": [{"type": "key_tap", "value": "a", "delay": 10}],
        "repeat_mode": "once",
        "repeat_count": 1,
    }


def normalise_step(step):
    """A step as the editor saves it: type and value as given, delay an int
    (0 when it is not a number)."""
    step = step or {}
    try:
        delay = int(step.get("delay", 0))
    except (TypeError, ValueError):
        delay = 0
    return {"type": step.get("type", ""), "value": step.get("value", ""),
            "delay": delay}


def apply_fields(macro, name=None, repeat_mode=None, repeat_count=None,
                 actions=None):
    """Write editor fields into `macro` with the editor's rules: a blank name
    is "Macro", an unknown mode is "once", the count is at least 1 (1 when it
    is not a number). Fields left at None are not touched."""
    if name is not None:
        macro["name"] = str(name).strip() or DEFAULT_NAME
    if repeat_mode is not None:
        macro["repeat_mode"] = repeat_mode if repeat_mode in REPEAT_MODES else "once"
    if repeat_count is not None:
        try:
            macro["repeat_count"] = max(1, int(repeat_count))
        except (TypeError, ValueError):
            macro["repeat_count"] = 1
    if actions is not None:
        macro["actions"] = [normalise_step(a) for a in actions]
    return macro


def duplicate(macro):
    """A copy of `macro` named "<name> (Copy)"."""
    m = copy.deepcopy(macro)
    m["name"] = m["name"] + " (Copy)"
    return m


def is_importable(data):
    """True for a file that looks like one exported macro."""
    return isinstance(data, dict) and "name" in data and "actions" in data
