"""What a paired phone may do: the allowlist, the shell and lock boxes.

The HTTP server asks `check_call()` (through RemoteService.check) before
every command a phone sends and passes every reply and event payload through
`redact()` before it leaves the PC.

- Only REMOTE_COMMANDS; only events under REMOTE_TOPIC_PREFIXES.
- Commands (SHELL_TYPES: "shell" and "app", both run through bash -c, and
  every plugin action type: any type outside the built-in ACTION_TYPES
  plus "back" and "none", since a plugin may run what its value says, as
  Pipe Text runs "<pipe>;<shell command>"): while the box "Phones may set
  shell commands" is off, a phone cannot set one, in a key's type, its
  "also on press" chain, its double press or a `set_key` action that would
  write one (its JSON, read recursively), and it never sees one's value (a
  command line may hold a password): every action dict of such a type
  that has an "action" or "value" comes back with them "" and redacted
  True (a shell or app dict is marked even without them). A set_key action
  that would write one is hidden the same way. Macro steps of other types
  than the key actions' are hidden too, should one ever reach a phone.
- Presses while the session is locked are refused ("locked") unless the
  box "Phones may press keys while the PC is locked" is ticked.
- Pictures: every path given as a picture must pass `image_ok`, the
  predicate the server passes: the same one it serves /api/image by
  (RemoteService.image_ok, the pictures DisplayPad keys carry now and
  vpads.pictures()).
"""
import json

from core.devices.displaypad.actions import ACTION_TYPES

REMOTE_COMMANDS = frozenset(
    ["displaypad." + n for n in (
        "status", "list_pages", "keys", "switch_page", "press_key", "set_action",
        "set_image", "set_key_text", "clear_key", "library", "fonts", "action_types")]
    + ["vpads." + n for n in (
        "list", "create", "rename", "delete", "set_grid", "keys", "set_key",
        "clear_key", "press", "switch_page", "create_page", "rename_page",
        "delete_page")]
    # macros.names, not macros.list: full macros carry their steps' command
    # lines (under "value").
    + ["macros.names", "obs.scenes", "obs.state", "remote.whoami"])

REMOTE_TOPIC_PREFIXES = ("displaypad.", "vpads.")

#: Action types that run a command line the phone typed: "app" too, which
#: the key action dispatcher runs through bash -c like "shell".
SHELL_TYPES = ("shell", "app")
#: The built-in action types that run no command line: everything else (a
#: plugin's type) is taken to be able to (Ruling 16).
SAFE_TYPES = frozenset(t for t in list(ACTION_TYPES) + ["back", "none"]
                       if t not in SHELL_TYPES)
#: Commands whose action may carry a shell command.
_SETS_ACTION = ("displaypad.set_action", "vpads.set_key")
_PRESSES = ("displaypad.press_key", "vpads.press")
#: The parameters that name a picture file, per command.
IMAGE_PARAMS = {
    "displaypad.set_image": ("path",),
    "displaypad.set_action": ("icon",),       # a page key's own picture
    "vpads.set_key": ("image",),
}
#: Deeper than this an action is taken to hold a shell command.
_MAX_DEPTH = 16


def _set_key_target(value):
    """The action a set_key action's JSON writes, as a dict, or None."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        d = json.loads(value)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def command_type(t):
    """True for an action type that may run a command: shell, app, any
    type that is not a string, and every plugin type. No type (None or
    "") runs nothing."""
    if t is None or t == "":
        return False
    if not isinstance(t, str):
        return True
    return t not in SAFE_TYPES


def has_shell(action, _depth=0):
    """True when `action` (an action dict, or a list of them) would set or
    run a command anywhere (see command_type): its type, its `actions`
    (or `extra`) chain, its `double` press, or the action a `set_key`
    writes."""
    if _depth > _MAX_DEPTH:
        return True
    if isinstance(action, (list, tuple)):
        return any(has_shell(a, _depth + 1) for a in action)
    if not isinstance(action, dict):
        return False
    t = action.get("type")
    if command_type(t):
        return True
    for chain in ("actions", "extra"):
        if has_shell(action.get(chain), _depth + 1):
            return True
    if has_shell(action.get("double"), _depth + 1):
        return True
    if t == "set_key":
        target = _set_key_target(action.get("action"))
        if target is not None and has_shell(target, _depth + 1):
            return True
    return False


#: The fields that hold an action's command line: "action" in a key's
#: action, "value" in a macro step.
_TEXT_FIELDS = ("action", "value")


def redact(obj, allow_shell):
    """A deep copy of `obj` (dicts and lists copied, tuples become lists)
    in which, unless `allow_shell` is set, no command's text is left: every
    dict of a shell type (and every set_key that would write one) has its
    "action" and "value" (those it has) set to "" and redacted True, and so
    has every dict of another command type (a plugin's) that has an
    "action" or "value" (others, which are not actions, are left alone)."""
    return _copy(obj, not allow_shell)


def _copy(obj, hide):
    if isinstance(obj, dict):
        out = {k: _copy(v, hide) for k, v in obj.items()}
        t = obj.get("type")
        if hide and (t in SHELL_TYPES or (t == "set_key" and has_shell(obj))
                     or (command_type(t) and any(f in obj for f in _TEXT_FIELDS))):
            for field in _TEXT_FIELDS:
                if field in out:
                    out[field] = ""
            out["redacted"] = True
        return out
    if isinstance(obj, (list, tuple)):
        return [_copy(v, hide) for v in obj]
    return obj


def _action_of(cmd, params):
    """The action a set command would store, in the stored shape."""
    if cmd == "displaypad.set_action":
        return {"type": params.get("type", "none"), "action": params.get("action", ""),
                "actions": params.get("extra"), "double": params.get("double")}
    return params.get("action")


def check_call(cmd, params, *, allow_shell, allow_locked, locked, image_ok):
    """Why a phone may not call `cmd` with `params`, or None when it may."""
    if cmd not in REMOTE_COMMANDS:
        return "not allowed from a phone: %s" % cmd
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return "the parameters must be an object"
    if not allow_shell and cmd in _SETS_ACTION and has_shell(_action_of(cmd, params)):
        return "shell commands cannot be set from a phone"
    if cmd in _PRESSES and locked and not allow_locked:
        return "locked"
    for name in IMAGE_PARAMS.get(cmd, ()):
        path = params.get(name)
        if path is None or path == "":
            continue
        if not isinstance(path, str) or image_ok is None or not image_ok(path):
            return "that picture cannot be used from a phone"
    return None
