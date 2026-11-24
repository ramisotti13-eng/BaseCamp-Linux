"""What a key does when it is pressed: the one dispatcher for key actions.

A key on the MacroPad, the DisplayPad or the Everest Max is bound to an
action, a (type, value) pair such as ("shell", "discord") or
("macro", "3f2a9c1e"). Each device had its own copy of the code that runs
one, and the copies had drifted; this is the one they share:

    actions = app.service("macros").actions          # an ActionDispatcher
    actions.run("shell", "discord", owner="macropad")

`owner` says whose key it was ("macropad", "displaypad", "everest_max"). A
toggle macro is stopped by the next press of the same owner, and handlers an
owner registers for its own types (the DisplayPad's page switch, the Everest
daemon's page and set_key over the control socket) are only asked for its
keys.

What run() does, in the order it is checked (pinned by tools/test_actions.py):

 1. A handler the owner registered for this type gets the value, on the
    caller's thread.
 2. obs with a value: "scene:<name>", "record" or "stream" go to the OBS
    executor, on the caller's thread (it starts its own); nothing when OBS
    is not available.
 3. macro with a value: the macro service's toggle(): start it, or stop the
    toggle run this owner started.
 4. keypress and text with a value: on a daemon thread each, since the input
    tools take a moment and the caller is usually the interface thread.
 5. A plugin action type: its handler on a daemon thread, with the value as
    it is (also an empty one).
 6. none, or an empty value: nothing.
 7. shell and app run as a shell command, url and folder are opened, on the
    caller's thread (they only start a process); a failure is swallowed.
 8. Any other type: nothing. A key still bound to a plugin that has been
    removed must not have its stored value run as a shell command.

Nothing here imports an interface. The two things only the application
knows, OBS and the plugins, are handed in as lookups:

    actions.set_obs(lambda: obs_screen.execute_action)   # or None
    actions.set_plugin_lookup(plugin_manager.get_action_handler)

and the desktop is reached through an ActionEnv, which a test, the Everest
daemon (which starts programs with its own sudo environment) or a Windows
port can replace.
"""
import logging
import threading

log = logging.getLogger("basecamp.core.actions")

#: Types the dispatcher knows itself, in no particular order.
BUILTIN_TYPES = ("none", "obs", "macro", "keypress", "text",
                 "shell", "app", "url", "folder")
#: The built-in types that start a program.
LAUNCH_TYPES = ("shell", "app", "url", "folder")

_OWNER_NAMES = {"macropad": "MacroPad", "displaypad": "DisplayPad",
                "everest_max": "Everest Max"}


class ActionEnv:
    """How the dispatcher reaches the desktop.

    This default goes through the 3.x names in shared.macros, looked up on
    every call: the key dispatchers always called them there, and the tests
    (and anything else that wants to see what a key would do) replace them
    there. Those names are thin faces over core/platform, so the work is
    done in the core either way.
    """

    @staticmethod
    def _m():
        import shared.macros
        return shared.macros

    def keypress(self, combo):
        self._m().simulate_keypress(combo)

    def text(self, text):
        self._m().simulate_text(text)

    def run_shell(self, command):
        self._m()._run_shell(command)

    def open_target(self, target):
        self._m()._run_xdg_open(target)


def _spawn_daemon(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


class ActionDispatcher:
    def __init__(self, macros=None, env=None, obs=None, plugin_handler=None,
                 spawn=None):
        """
        macros          has toggle(macro_id, owner) -> str (MacroService or a
                        core.macros.service.MacroRunner); default: a runner
                        of its own on macros.json
        env             an ActionEnv
        obs             () -> callable(kind, *args) or None
        plugin_handler  (type_id) -> callable(value) or None
        spawn           (fn, *args) -> starts fn on a daemon thread
        """
        self._macros = macros
        self.env = env or ActionEnv()
        self._obs = obs
        self._plugin_handler = plugin_handler
        self._spawn = spawn or _spawn_daemon
        self._lock = threading.Lock()
        self._owner_types = {}       # (owner, type_id) -> handler(value)

    # ── Wiring ───────────────────────────────────────────────────────────────

    @property
    def macros(self):
        if self._macros is None:
            from core.macros.service import MacroRunner
            self._macros = MacroRunner()
        return self._macros

    def set_obs(self, lookup):
        """lookup() returns the OBS executor, fn("scene", name) / fn("record")
        / fn("stream"), or None while OBS is not available."""
        self._obs = lookup

    def set_plugin_lookup(self, lookup):
        """lookup(type_id) returns a plugin's handler(value), or None."""
        self._plugin_handler = lookup

    @property
    def has_obs(self):
        return self._obs is not None

    @property
    def has_plugin_lookup(self):
        return self._plugin_handler is not None

    def register(self, type_id, handler, owner=None):
        """Let `owner` handle its own action type (page, back, set_key...):
        handler(value) is called on the caller's thread, before anything else
        is looked at."""
        with self._lock:
            self._owner_types[(owner, type_id)] = handler

    def unregister(self, type_id, owner=None):
        with self._lock:
            self._owner_types.pop((owner, type_id), None)

    def plugin_handler(self, type_id):
        lookup = self._plugin_handler
        if lookup is None:
            return None
        try:
            return lookup(type_id)
        except Exception:
            log.exception("plugin lookup for %r failed", type_id)
            return None

    # ── Running ──────────────────────────────────────────────────────────────

    def run(self, type_id, value, *, owner=None):
        """Run one action. Returns what happened, as a short code:

        handled, obs, obs_unavailable, macro_started, macro_stopped,
        macro_missing, keypress, text, plugin, none, launched, unknown
        """
        with self._lock:
            own = self._owner_types.get((owner, type_id))
        if own is not None:
            own(value)
            return "handled"
        if type_id == "obs" and value:
            return self._run_obs(value)
        if type_id == "macro" and value:
            return "macro_" + self.macros.toggle(value, owner)
        if type_id == "keypress" and value:
            self._spawn(self.env.keypress, value)
            return "keypress"
        if type_id == "text" and value:
            self._spawn(self.env.text, value)
            return "text"
        handler = self.plugin_handler(type_id)
        if handler:
            self._spawn(handler, value)
            return "plugin"
        if type_id == "none" or not value:
            return "none"
        if type_id not in LAUNCH_TYPES:
            print("[%s] key action type %r is not installed, doing nothing"
                  % (_OWNER_NAMES.get(owner, owner or "Key"), type_id), flush=True)
            return "unknown"
        try:
            if type_id in ("url", "folder"):
                self.env.open_target(value)
            else:
                self.env.run_shell(value)
        except Exception:
            pass
        return "launched"

    def _run_obs(self, value):
        lookup = self._obs
        execute = lookup() if lookup is not None else None
        if execute is None:
            return "obs_unavailable"
        if value.startswith("scene:"):
            execute("scene", value[6:])
        elif value in ("record", "stream"):
            execute(value)
        return "obs"
