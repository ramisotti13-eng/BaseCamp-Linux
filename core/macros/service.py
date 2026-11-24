"""MacroService: the saved macros, and running them.

Everything the Macros screen does that is not drawing goes through here, so
another interface (a web page, the control socket) edits and runs macros by
the same rules:

    svc = app.service("macros")
    uid = svc.create()                                  # "Macro", "Macro 1", ...
    svc.update(uid, name="Build", actions=[...], repeat_mode="toggle")
    svc.toggle(uid, owner="macropad")                   # what a key press does
    svc.find_assignments(uid)                           # which keys run it

The store is macros.json (shared.config.load_macros/save_macros), read on
every call: the key dispatchers read it the same way, so what a key runs is
always what was saved last, and a profile switch or a restored backup is seen
without telling anyone.

Events:
    macros.changed  {id, change}   created / updated / deleted / imported;
                                   id is None for recordings
    macros.started  {id, owner, mode}
    macros.stopped  {id, owner, stopped}   stopped: ended by a stop event
"""
import copy
import json
import logging
import threading

from core.commands import CommandError
from core.macros import model, recordings
from core.service import Service

log = logging.getLogger("basecamp.core.macros")

#: stop(owner=ANY) stops a macro whoever started it.
ANY = object()


def _config():
    from shared import config
    return config


def _execute_through_shared(macro, stop_event):
    """Play a macro through its 3.x name, looked up now.

    shared.macros.execute_macro is the name the tests replace (and 3.x code
    calls); going through it keeps them working until they move to the core.
    """
    import shared.macros
    shared.macros.execute_macro(macro, stop_event)


def _input_module():
    from core.platform import input as _input
    return _input


def _load_saved():
    """{id: macro} from macros.json, through shared.config.load_macros now."""
    data = _config().load_macros() or {}
    macros = data.get("macros") if isinstance(data, dict) else None
    return macros if isinstance(macros, dict) else {}


def _spawn_daemon(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


class MacroRunner:
    """Starts macros on daemon threads and keeps the stop events of toggle
    runs, one per (owner, macro).

    The owner is whoever pressed: "macropad", "displaypad", "everest_max".
    A toggle macro started from the MacroPad is stopped by the next MacroPad
    press, not by a DisplayPad key bound to the same macro, which is how the
    per-panel dispatchers behaved.

    Needs no CoreApp: emax_controller's daemon, a separate process, can use
    one on its own. on_event(name, **payload) hears "started" and "stopped".
    """

    def __init__(self, load=None, execute=None, spawn=None, on_event=None):
        self._load = load or _load_saved
        self._execute = execute or _execute_through_shared
        self._spawn = spawn or _spawn_daemon
        self.on_event = on_event
        self._lock = threading.Lock()
        self._toggles = {}          # (owner, macro id) -> stop Event

    def _emit(self, event, **payload):
        if self.on_event is not None:
            try:
                self.on_event(event, **payload)
            except Exception:
                log.exception("macro %s event handler failed", event)

    def get(self, macro_id):
        macro = self._load().get(macro_id)
        return macro if isinstance(macro, dict) and macro else None

    def toggle(self, macro_id, owner=None):
        """What a key press does: stop the toggle run this owner started, or
        start the macro (keeping a stop event if it is a toggle macro).

        Returns "stopped", "started" or "missing" (no such macro; then a
        running toggle is left alone, as before).
        """
        macro = self.get(macro_id)
        if macro is None:
            return "missing"
        key = (owner, macro_id)
        with self._lock:
            running = self._toggles.pop(key, None)
            stop = None
            if running is None and macro.get("repeat_mode") == "toggle":
                stop = threading.Event()
                self._toggles[key] = stop
        if running is not None:
            running.set()
            return "stopped"
        self._start(macro_id, macro, stop, owner)
        return "started"

    def run(self, macro_id, owner=None):
        """Start the macro unless a toggle run of it by this owner is going.
        Returns "started", "running" or "missing"."""
        macro = self.get(macro_id)
        if macro is None:
            return "missing"
        key = (owner, macro_id)
        with self._lock:
            if key in self._toggles:
                return "running"
            stop = None
            if macro.get("repeat_mode") == "toggle":
                stop = threading.Event()
                self._toggles[key] = stop
        self._start(macro_id, macro, stop, owner)
        return "started"

    def stop(self, macro_id=None, owner=ANY):
        """Stop toggle runs: of one macro or all, by one owner or anyone.
        Returns how many were stopped."""
        with self._lock:
            keys = [k for k in self._toggles
                    if (macro_id is None or k[1] == macro_id)
                    and (owner is ANY or k[0] == owner)]
            events = [self._toggles.pop(k) for k in keys]
        for ev in events:
            ev.set()
        return len(events)

    def running(self):
        """[{"id", "owner"}] of the toggle runs going now."""
        with self._lock:
            return [{"id": mid, "owner": owner} for owner, mid in self._toggles]

    def _start(self, macro_id, macro, stop, owner):
        key = (owner, macro_id)

        def body():
            failed = False
            try:
                self._execute(macro, stop)
            except Exception:
                failed = True
                log.exception("macro %s failed", macro_id)
            finally:
                # A toggle run that died on an error must not stay registered,
                # or the next press would only "stop" it. (A toggle run that
                # returns normally has been stopped already: the engine loops
                # until its stop event is set.)
                if failed and stop is not None:
                    with self._lock:
                        if self._toggles.get(key) is stop:
                            del self._toggles[key]
                self._emit("stopped", id=macro_id, owner=owner,
                           stopped=bool(stop is not None and stop.is_set()))

        self._emit("started", id=macro_id, owner=owner,
                   mode=macro.get("repeat_mode", "once"))
        self._spawn(body)


class MacroService(Service):
    name = "macros"

    def __init__(self, app, runner=None):
        super().__init__(app)
        self._lock = threading.RLock()      # load-modify-save of macros.json
        self.runner = runner or MacroRunner(on_event=self._runner_event)
        if runner is not None and runner.on_event is None:
            runner.on_event = self._runner_event
        #: The shared action dispatcher (core.actions), set by install().
        self.actions = None

    def stop(self):
        self.runner.stop()

    # ── Store ────────────────────────────────────────────────────────────────

    def _load(self):
        return _load_saved()

    def _save(self, macros, macro_id, change):
        _config().save_macros({"macros": macros})
        self.publish("changed", id=macro_id, change=change)

    def macros(self):
        """{id: macro} of every saved macro (a copy)."""
        return copy.deepcopy(self._load())

    def names(self):
        """{id: name}, what a macro picker shows."""
        return {uid: (m or {}).get("name", uid) if isinstance(m, dict) else uid
                for uid, m in self._load().items()}

    def get(self, macro_id):
        """The macro (a copy), or None."""
        m = self._load().get(macro_id)
        return copy.deepcopy(m) if isinstance(m, dict) else None

    def create(self, name=None, actions=None, repeat_mode=None, repeat_count=None):
        """A new macro: one key tap, run once, unless the fields say otherwise.
        Returns its id."""
        with self._lock:
            macros = self._load()
            uid = model.generate_macro_id()
            while uid in macros:
                uid = model.generate_macro_id()
            m = model.new_macro(macros)
            model.apply_fields(m, name, repeat_mode, repeat_count, actions)
            macros[uid] = m
            self._save(macros, uid, "created")
        return uid

    def update(self, macro_id, name=None, repeat_mode=None, repeat_count=None,
               actions=None):
        """Change the given fields, by the editor's rules (model.apply_fields).
        Returns the macro, or None if there is no such macro."""
        with self._lock:
            macros = self._load()
            m = macros.get(macro_id)
            if not isinstance(m, dict):
                return None
            model.apply_fields(m, name, repeat_mode, repeat_count, actions)
            self._save(macros, macro_id, "updated")
            return copy.deepcopy(m)

    def delete(self, macro_id):
        with self._lock:
            macros = self._load()
            if macros.pop(macro_id, None) is None:
                return False
            self._save(macros, macro_id, "deleted")
        return True

    def duplicate(self, macro_id):
        """A copy named "<name> (Copy)". Returns its id, or None."""
        with self._lock:
            macros = self._load()
            m = macros.get(macro_id)
            if not isinstance(m, dict):
                return None
            uid = model.generate_macro_id()
            while uid in macros:
                uid = model.generate_macro_id()
            macros[uid] = model.duplicate(m)
            self._save(macros, uid, "created")
        return uid

    def _edit_steps(self, macro_id, actions, edit):
        with self._lock:
            macros = self._load()
            m = macros.get(macro_id)
            if not isinstance(m, dict):
                return False
            steps = ([model.normalise_step(a) for a in actions]
                     if actions is not None else list(m.get("actions", [])))
            if not edit(steps):
                return False
            m["actions"] = steps
            self._save(macros, macro_id, "updated")
        return True

    def add_step(self, macro_id, step):
        """Append a step. False if there is no such macro."""
        def edit(steps):
            steps.append(dict(step))
            return True
        return self._edit_steps(macro_id, None, edit)

    def move_step(self, macro_id, index, delta, actions=None):
        """Swap step `index` with its neighbour `delta` away (-1 up, 1 down).

        `actions` is what the editor shows now, used instead of the saved
        steps. Nothing is saved when the move would leave the list."""
        def edit(steps):
            new = index + delta
            if index < 0 or index >= len(steps) or new < 0 or new >= len(steps):
                return False
            steps[index], steps[new] = steps[new], steps[index]
            return True
        return self._edit_steps(macro_id, actions, edit)

    def remove_step(self, macro_id, index, actions=None):
        def edit(steps):
            if index < 0 or index >= len(steps):
                return False
            steps.pop(index)
            return True
        return self._edit_steps(macro_id, actions, edit)

    # ── Files ────────────────────────────────────────────────────────────────

    def export_file(self, macro_id, path):
        """Write one macro to `path` as JSON. False if there is no such macro;
        a file that cannot be written raises."""
        m = self.get(macro_id)
        if m is None:
            return False
        with open(path, "w") as f:
            json.dump(m, f, indent=2)
        return True

    def import_file(self, path):
        """Add the macro in `path` under a new id. Returns the id, or None for
        a file that cannot be read or is not an exported macro."""
        try:
            with open(path) as f:
                m = json.load(f)
        except Exception:
            return None
        if not model.is_importable(m):
            return None
        with self._lock:
            macros = self._load()
            uid = model.generate_macro_id()
            while uid in macros:
                uid = model.generate_macro_id()
            macros[uid] = m
            self._save(macros, uid, "imported")
        return uid

    # ── Where a macro sits ───────────────────────────────────────────────────

    def find_assignments(self, macro_id):
        """Every key this macro sits on: [{"device", "key", "label", ...}].

        Reads the same configs the dispatchers read, so the answer cannot
        drift from what actually happens on a key press. `key` counts from 0;
        `label` is how the screen names the key ("DisplayPad K3 (Main)"),
        product and key names that are not translated.
        """
        cfg = _config()
        places = []
        try:
            for i, act in enumerate(cfg.load_buttons() or []):
                if act.get("type") == "macro" and act.get("action") == macro_id:
                    places.append({"device": "everest_max", "key": i,
                                   "label": f"Everest Max D{i + 1}"})
        except Exception:
            pass
        try:
            names = cfg._load_displaypad_page_names()
            for pid in sorted(names):
                for k, act in enumerate(cfg._load_displaypad_actions(pid) or []):
                    if not isinstance(act, dict):
                        continue
                    if act.get("type") == "macro" and act.get("action") == macro_id:
                        places.append({"device": "displaypad", "key": k, "page": pid,
                                       "page_name": names[pid],
                                       "label": f"DisplayPad K{k + 1} ({names[pid]})"})
        except Exception:
            pass
        try:
            for i, act in enumerate(cfg._load_macropad_actions() or []):
                if act.get("type") == "macro" and act.get("action") == macro_id:
                    places.append({"device": "macropad", "key": i,
                                   "label": f"MacroPad M{i + 1}"})
        except Exception:
            pass
        return places

    # ── Running ──────────────────────────────────────────────────────────────

    def toggle(self, macro_id, owner=None):
        """A key press on a key bound to the macro; see MacroRunner.toggle."""
        return self.runner.toggle(macro_id, owner)

    def run(self, macro_id, owner=None):
        return self.runner.run(macro_id, owner)

    def stop_macro(self, macro_id=None, owner=ANY):
        """Stop toggle runs (all of them with no arguments). Returns a count."""
        return self.runner.stop(macro_id, owner)

    def running(self):
        return self.runner.running()

    def _runner_event(self, event, **payload):
        # From whatever thread started or finished the macro: publish on the
        # scheduler, like every other service's worker does.
        self.app.scheduler.call_soon(lambda: self.publish(event, **payload))

    # ── Mouse recordings ─────────────────────────────────────────────────────

    def recordings(self):
        """[{"file", "name", "points"}]; points is None for an unreadable file."""
        return recordings.list_recordings()

    def save_recording(self, positions, click_at_end=False, name=None):
        """Save a recorded path as "recording_N" (or `name`). Returns the file
        name a Mouse Path step refers to."""
        if name is None:
            name = recordings.next_recording_name(
                [r["file"] for r in recordings.list_recordings()])
        filename = recordings.save_mouse_recording(name, positions, click_at_end)
        self.publish("changed", id=None, change="recordings")
        return filename

    def delete_recording(self, filename):
        ok = recordings.delete_recording(filename)
        self.publish("changed", id=None, change="recordings")
        return ok

    def tools_status(self):
        """{"tool": "xdotool"|"ydotool"|None, "session": "x11"|"wayland"}."""
        from core.platform import input as _input
        tool, session = _input.tools_status()
        return {"tool": tool, "session": session}

    # ── Commands ─────────────────────────────────────────────────────────────

    def _need(self, value, macro_id):
        if value is None or value is False:
            raise CommandError("no macro with id %r" % (macro_id,))
        return value

    _FIELDS = ("name", "actions", "repeat_mode", "repeat_count")

    def _fields(self, fields):
        """The `fields` object of create/update. (A command cannot take a
        parameter called "name": CommandRegistry.call() has one itself.)"""
        fields = fields or {}
        if not isinstance(fields, dict):
            raise CommandError("fields must be an object")
        unknown = sorted(set(fields) - set(self._FIELDS))
        if unknown:
            raise CommandError("unknown macro field(s): %s" % ", ".join(unknown))
        return fields

    def commands(self):
        return {
            "list": lambda: {"macros": self.macros()},
            "names": lambda: {"names": self.names()},
            "get": lambda macro_id: {"macro": self._need(self.get(macro_id), macro_id)},
            "create": lambda fields=None: {"id": self.create(**self._fields(fields))},
            "update": lambda macro_id, fields=None: {"macro": self._need(
                self.update(macro_id, **self._fields(fields)), macro_id)},
            "delete": lambda macro_id: self._need(self.delete(macro_id), macro_id) and None,
            "duplicate": lambda macro_id: {"id": self._need(self.duplicate(macro_id),
                                                            macro_id)},
            "import_file": self._cmd_import,
            "export_file": lambda macro_id, path: self._need(
                self.export_file(macro_id, path), macro_id) and None,
            "find_assignments": lambda macro_id: {"places": self.find_assignments(macro_id)},
            "run": lambda macro_id, owner=None: {"result": self.run(macro_id, owner)},
            "toggle": lambda macro_id, owner=None: {"result": self.toggle(macro_id, owner)},
            "stop": lambda macro_id=None: {"stopped": self.stop_macro(macro_id)},
            "running": lambda: {"running": self.running()},
            "recordings": lambda: {"recordings": self.recordings()},
            "delete_recording": lambda file: {"deleted": self.delete_recording(file)},
            "tools": self.tools_status,
            "key_choices": lambda: {"keys": list(_input_module().KEY_CHOICES)},
            "key_from_dom": lambda code="", key="": {
                "key": _input_module().key_from_dom_code(code, key)},
            "step_types": lambda: {"types": list(model.ACTION_TYPES),
                                   "repeat_modes": list(model.REPEAT_MODES)},
        }

    def _cmd_import(self, path):
        uid = self.import_file(path)
        if uid is None:
            raise CommandError("%s is not a macro file" % path)
        return {"id": uid}
