"""What the Everest Max's keys are bound to (Base Camp's "Tastenbelegung").

Pure functions and a small file store; nothing here talks to the keyboard.

A binding names a function of the original's catalogue and its value:

    {"function": "media", "value": "play_pause"}
    {"function": "shortcut", "value": 31, "mods": ["ctrl"]}     (31: the A key)
    {"function": "url", "value": "https://example.org"}

How each is done:

- Firmware functions (Standard, Deaktivieren, Medien, Maus, Hotkey-Wechsel,
  Profil, four of the OS commands) are a target code for `14 20`, a key
  combination is `14 21`.
- "Macro ausführen" with a macro made only of key steps, delays and left
  clicks is written into the keyboard as its own key macro (keymacro.py).
- Everything else runs in the application (a URL, a program, a folder, OBS,
  a plugin's action, a macro it cannot write, three OS commands): the
  keyboard has no code for it, so the key is made to send a free F13-F24
  (stored with the binding as "fkey") and the application runs the action
  when it hears that key (keytrigger.py).

The file, everest_max_keybindings.json:

    {"version": 1,
     "keys": {"<SDK key number>": binding, ...},      Standard keys are left out
     "dkeys": {"0": {"picture": <library file>, "text": {...}}, ...}}

D1-D4 and the media dock's buttons keep an action that runs in the
application where it always was (buttons.json, everest_max_dock.json), with
the press detection they have; only their firmware functions are kept here.
"""
import json
import os
import sys

from core.devices.everest_max import layout, protocol as P

FILE_NAME = "everest_max_keybindings.json"
VERSION = 1

#: F13-F24 as the keyboard names them (SDK numbers 230-241).
FKEYS = tuple(range(230, 242))


def trigger_fkeys(platform=None):
    """The F-keys a key may send for the application to hear. On Linux the
    usual keymap (XKB "inet") gives F13 to XF86Tools (KDE and GNOME open
    their settings), F20 to XF86AudioMicMute and F21-F23 to the touchpad
    switches: a key sending one of those would also do that on the desktop
    (seen on Plasma 6, 2026-11-23). F14-F18 (XF86Launch5-9), F19 and F24 are
    left alone. Elsewhere all twelve."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        return (231, 232, 233, 234, 235, 236, 241)
    return FKEYS


#: The pool free_fkey() takes from (a Hotkey-Wechsel may still pick any of F13-F24).
TRIGGER_FKEYS = trigger_fkeys()

#: The original's catalogue in its order. Not offered: Multi Action (our
#: macros are that), Twitch, YouTube, Spotify, Adobe, DaVinci Resolve and
#: Zoom (no Linux counterpart). Plugins add their action types after these.
FUNCTIONS = ("default", "disable", "os", "url", "app", "folder", "macro", "media",
             "shortcut", "hotkey", "mouse", "profile", "obs")

#: The functions picked from a list: (choice, target code of `14 20`); None
#: where the keyboard has no code and the application does it.
CHOICES = {
    "os": (("taskmgr", None), ("calculator", 195), ("explorer", None), ("lock", None),
           ("shutdown", 128), ("sleep", 129), ("hibernate", 129)),
    "media": (("play_pause", 183), ("stop", 182), ("prev", 181), ("next", 180),
              ("vol_up", 187), ("vol_down", 188), ("mute", 184)),
    "hotkey": tuple(("f%d" % (k - 217), k) for k in FKEYS),
    # Scrolling left and right has no code in the original either.
    "mouse": (("left", 257), ("right", 258), ("middle", 259), ("forward", 260),
              ("back", 261), ("wheel_up", 304), ("wheel_down", 305)),
    "profile": (("p1", 160), ("p2", 161), ("p3", 162), ("p4", 163), ("p5", 164),
                ("cycle", 210), ("prev", 211), ("next", 212)),
}

#: Functions whose value is typed or picked and that the application runs;
#: their ids are the action types of the shared dispatcher (core/actions.py).
APP_FUNCTIONS = ("url", "app", "folder", "macro", "obs")

#: Action types D1-D4 and the dock buttons may already have that are not in
#: the original's catalogue; they are kept and shown as they are.
OWN_TYPES = ("shell", "keypress", "text")

# The OS commands without a code, done the Linux way: the first system
# monitor that is installed, the file manager on the home folder, the
# session's lock screen.
TASK_MANAGER = ("for p in plasma-systemmonitor gnome-system-monitor ksysguard "
                "xfce4-taskmanager mate-system-monitor lxtask; do "
                "command -v \"$p\" >/dev/null && exec \"$p\"; done")
OS_ACTIONS = {"taskmgr": ("shell", TASK_MANAGER),
              "explorer": ("shell", "xdg-open ~"),
              "lock": ("shell", "loginctl lock-session")}


class NoFreeFKey(ValueError):
    """Every one of F13-F24 is taken already."""


def shortcut_targets():
    """The key numbers a key combination may send: every key but Fn, and
    F13-F24."""
    return set(layout.KEY_IDS.values()) | set(FKEYS)


def code_of(function, value):
    """The `14 20` target of a choice, or None."""
    return dict(CHOICES.get(function, ())).get(value)


def normalize(binding, plugin_types=(), own=False):
    """A binding as it is stored: checked and cleaned. `plugin_types` are the
    action types plugins offer; `own` allows OWN_TYPES (D1-D4, dock).
    Raises ValueError."""
    if not isinstance(binding, dict):
        raise ValueError("a binding is an object with a function")
    fn = binding.get("function")
    value = binding.get("value")
    if fn in ("default", "disable"):
        return {"function": fn}
    if fn in CHOICES:
        if value not in dict(CHOICES[fn]):
            raise ValueError("%s: unknown choice %r" % (fn, value))
        return {"function": fn, "value": value}
    if fn == "shortcut":
        if isinstance(value, bool) or not isinstance(value, int) or value not in shortcut_targets():
            raise ValueError("shortcut: value must be the number of a key")
        mods = binding.get("mods") or []
        if not isinstance(mods, (list, tuple)):
            raise ValueError("shortcut: mods must be a list")
        P.shortcut_mods_byte(mods)                     # unknown names raise
        return {"function": fn, "value": value,
                "mods": [m for m, _bit in P.SHORTCUT_MODS if m in mods]}
    if fn in APP_FUNCTIONS or fn in plugin_types or (own and fn in OWN_TYPES):
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError("%s: value must be text" % fn)
        return {"function": fn, "value": value.strip()}
    raise ValueError("unknown function %r" % (fn,))


def runs_in_app(binding):
    """True for a binding the keyboard cannot do by itself."""
    fn = binding.get("function")
    if fn == "os":
        return binding.get("value") in OS_ACTIONS
    return fn not in FUNCTIONS or fn in APP_FUNCTIONS


def action_of(binding):
    """(action type, value) for the dispatcher, for a binding that runs in
    the application."""
    if binding.get("function") == "os":
        return OS_ACTIONS[binding["value"]]
    return binding["function"], binding.get("value", "")


def from_action(type_id, action):
    """The binding a D-key or dock action (buttons.json, dock file) shows as,
    or None for one that does nothing. 3.x's untouched D-key is a shell
    command without a command."""
    if not type_id or type_id == "none":
        return None
    if not action and type_id in APP_FUNCTIONS + OWN_TYPES:
        return None
    for name, (t, cmd) in OS_ACTIONS.items():
        if (type_id, action) == (t, cmd):
            return {"function": "os", "value": name}
    return {"function": type_id, "value": action or ""}


def used_fkeys(bindings):
    """F-keys taken: held by an application binding, or sent by a
    Hotkey-Wechsel binding (that key would set the action off)."""
    used = set()
    for b in bindings.values():
        if b.get("fkey") is not None:
            used.add(b["fkey"])
        if b.get("function") == "hotkey":
            used.add(code_of("hotkey", b.get("value")))
    return used


def free_fkey(bindings):
    used = used_fkeys(bindings)
    for k in TRIGGER_FKEYS:
        if k not in used:
            return k
    raise NoFreeFKey("the F-keys for the application are all in use: at most %d keys "
                     "can run something in the application" % len(TRIGGER_FKEYS))


def place(bindings, src, binding, needs_fkey=None):
    """`bindings` ({src: binding}) with key `src` bound to `binding` (a
    normalized one; Standard takes the key out). A binding that runs in the
    application gets an F-key: the one it had when that is still free, else
    the first free one. A Hotkey-Wechsel to an F-key another key holds moves
    that key to a free one. `needs_fkey` overrides runs_in_app() (a macro
    written into the keyboard needs none).

    Returns (new bindings, [keys whose binding changed]). Raises NoFreeFKey
    and leaves `bindings` as they were."""
    out = {k: dict(v) for k, v in bindings.items() if k != src}
    changed = [src]
    if binding["function"] == "default":
        return out, changed
    new = {k: v for k, v in binding.items() if k != "fkey"}
    out[src] = new
    if new["function"] == "hotkey":
        code = code_of("hotkey", new["value"])
        holder = next((k for k, v in out.items() if k != src and v.get("fkey") == code), None)
        if holder is not None:
            out[holder]["fkey"] = free_fkey(out)
            changed.append(holder)
    if runs_in_app(new) if needs_fkey is None else needs_fkey:
        keep = (bindings.get(src) or {}).get("fkey")
        new["fkey"] = keep if keep is not None and keep not in used_fkeys(out) else free_fkey(out)
    return out, changed


def remap_entries(src, binding, macro=None):
    """The controller's `remap` entries (protocol.remap_packets) that put
    `binding` on key `src`: Standard first, which also takes a key macro off
    the key, then the binding's own. None is Standard. `macro` is {times,
    events} for a macro written into the keyboard (keymacro.py)."""
    std = {"src": src, "target": src}
    if binding is None or binding["function"] == "default":
        return [std]
    fn = binding["function"]
    if binding.get("fkey") is not None:
        own = {"src": src, "target": binding["fkey"]}
    elif fn == "disable":
        own = {"src": src, "target": P.REMAP_DISABLED}
    elif fn == "shortcut":
        own = {"src": src, "key": binding["value"], "mods": list(binding.get("mods") or [])}
    elif fn == "macro" and macro is not None:
        own = {"src": src, "macro": macro}
    elif code_of(fn, binding.get("value")) is not None:
        own = {"src": src, "target": code_of(fn, binding["value"])}
    else:
        # Runs in the application but has no F-key (D1-D4, the dock: their
        # actions have their own press detection).
        return [std]
    return [std, own]


# ── the file ────────────────────────────────────────────────────────────────

def empty():
    return {"version": VERSION, "keys": {}, "dkeys": {}}


def load(path):
    """The file's content with every entry checked; junk is left out (the
    application may write this file, a person may too)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty()
    out = empty()
    if not isinstance(data, dict):
        return out
    bindable = layout.bindable_ids()
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    for k, b in keys.items():
        try:
            src = int(k)
            if src not in bindable or not isinstance(b, dict):
                continue
            nb = normalize(b, plugin_types=(b.get("function"),) if isinstance(b.get("function"), str) else ())
        except (TypeError, ValueError):
            continue
        if nb["function"] == "default":
            continue
        if b.get("fkey") in FKEYS and runs_in_app(nb):
            nb["fkey"] = b["fkey"]
        out["keys"][src] = nb
    # An F-key outside this system's pool (a file from before the Linux pool,
    # or from another system) moves to a free one of the pool; the bindings
    # are written to the keyboard again when it is plugged in or the app starts.
    for src in sorted(out["keys"]):
        b = out["keys"][src]
        if b.get("fkey") is not None and b["fkey"] not in TRIGGER_FKEYS:
            rest = {k: v for k, v in out["keys"].items() if k != src}
            try:
                b["fkey"] = free_fkey(rest)
            except NoFreeFKey:
                del b["fkey"]
    dkeys = data.get("dkeys") if isinstance(data.get("dkeys"), dict) else {}
    for k, v in dkeys.items():
        if str(k) in ("0", "1", "2", "3") and isinstance(v, dict):
            out["dkeys"][int(k)] = {n: v[n] for n in ("picture", "text") if v.get(n)}
    return out


def save(path, data):
    """Write the file whole (a reader never sees half of it)."""
    doc = {"version": VERSION,
           "keys": {str(k): b for k, b in sorted(data["keys"].items())},
           "dkeys": {str(k): v for k, v in sorted(data["dkeys"].items()) if v}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
