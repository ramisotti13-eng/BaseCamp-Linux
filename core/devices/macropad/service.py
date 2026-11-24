"""MacroPadService: the Mountain MacroPad, owned by one thread.

Same chassis as the DisplayPad, twelve plain keys with per-key RGB. Key
presses come in over the vendor HID collection (decoded by
devices/macropad/controller.py, measured in #85); this service turns them
into presses and runs what each key is bound to.

One thread owns the device. Commands from any interface go through a queue
and are sent by that same thread between reads. The DisplayPad learned this
the hard way (#26 to #28): a second opener of the one command interface makes
key events and uploads eat each other.

    svc = app.service("macropad")
    svc.set_present(True)                      # the device scan's verdict
    svc.set_action(4, "shell", "echo hallo")   # M5
    svc.update_lighting(effect=mp.EFFECT_WAVE, speed=80)
    svc.apply_lighting()                       # False: no pad to send it to

Events (codes, never translated text):
    macropad.presence          {present}
    macropad.status            {code, detail}
        code: ready, open_failed (detail: the error), failed (detail),
              applied, saved, job_failed (detail)
    macropad.key               {index}       a key went down (after debounce)
    macropad.actions_changed   {index}
    macropad.lighting_changed  {fields}
"""
import copy
import logging
import queue
import threading
import time

from core.commands import CommandError
from core.service import Service
from devices.macropad import controller as mp

log = logging.getLogger("basecamp.core.macropad")

NUM_KEYS = mp.NUM_KEYS

#: Action types this device offers: the DisplayPad's minus everything that
#: needs a screen or a page (no image, no page switch, no key redefinition).
ACTION_TYPES = ["none", "shell", "url", "folder", "app", "obs", "macro",
                "keypress", "text"]

#: Which controls an effect reads: (speed, colour 1, colour 2, direction).
#: Sending a colour to an effect that ignores it is harmless, but offering a
#: control that changes nothing is not.
EFFECT_CONTROLS = {
    mp.EFFECT_STATIC:     (False, True,  False, False),
    mp.EFFECT_BREATHING:  (True,  True,  True,  False),
    mp.EFFECT_WAVE:       (True,  True,  False, True),
    mp.EFFECT_TORNADO:    (True,  True,  False, True),
    mp.EFFECT_MATRIX:     (True,  True,  True,  False),
    mp.EFFECT_YETI:       (True,  True,  True,  False),
    mp.EFFECT_REACTIVE_A: (True,  True,  True,  False),
    mp.EFFECT_REACTIVE_B: (True,  True,  True,  False),
    mp.EFFECT_REACTIVE_C: (True,  True,  True,  False),
    mp.EFFECT_CUSTOM:     (False, False, False, False),
    mp.EFFECT_OFF:        (False, False, False, False),
}
_DEFAULT_CONTROLS = (True, True, True, False)

#: How many directions an effect turns: Wave four ways, Tornado two. The wire
#: values behind them are not 0 to n-1; the controller maps them (#85).
DIRECTION_COUNTS = {mp.EFFECT_WAVE: 4, mp.EFFECT_TORNADO: 2}

_LIGHTING_FIELDS = ("effect", "brightness", "speed", "direction", "color1", "color2")
_EFFECT_IDS = [effect for effect, _key in mp.EFFECTS]


def _config():
    from shared import config
    return config


def _clamp_int(value, low, high):
    return max(low, min(high, int(value)))


def _color(value):
    r, g, b = value
    return [_clamp_int(c, 0, 255) for c in (r, g, b)]


class MacroPadService(Service):
    name = "macropad"

    #: How long a key stays deaf after it fired, so one press is one action.
    DEBOUNCE_S = 0.25
    #: How long the worker waits for a report before looking at the job queue.
    READ_MS = 120
    #: After a failed open, wait this long before trying again. The pad
    #: re-enumerates on its own now and then, and a tight loop would spam.
    RETRY_S = 2.0
    #: start_worker() while the previous worker is still winding down
    #: checks again after this long.
    RESTART_MS = 500

    def __init__(self, app, opener=None):
        super().__init__(app)
        cfg = _config()
        self._present = False
        self._actions = cfg._load_macropad_actions()
        self._rgb = cfg._load_macropad_rgb()
        # The device thread and the jobs handed to it.
        self._jobs = queue.Queue()
        self._stop = threading.Event()
        self._worker = None
        self._held = set()          # keys currently down, for edge detection
        self._last_fire = {}        # key -> monotonic time it last fired
        self._opener = opener       # None: controller.MacroPad(), looked up then
        #: Seconds between open attempts, or a callable returning them.
        self.retry_s = self.RETRY_S
        self._press_handler = None
        self._own_actions = None
        #: set_present() starts and stops the worker itself; an interface
        #: that does that on the presence event turns this off.
        self.worker_follows_presence = True

    def stop(self):
        self.stop_worker()

    # ── Presence and the worker ──────────────────────────────────────────────

    @property
    def present(self):
        return self._present

    @property
    def worker_alive(self):
        return self._worker is not None and self._worker.is_alive()

    def set_present(self, present, manage_worker=None):
        """The device scan's verdict (the device registry calls it). Owning
        the pad is what makes key presses arrive, so the worker runs whenever
        the pad is here, not only while some screen shows it. Returns True
        when that was news.

        manage_worker=False only records it: the Tk screen starts and stops
        the worker through members of its own, which the tests reach, when
        it hears the presence event. None means `worker_follows_presence`,
        which that screen turns off while it is there.
        """
        if manage_worker is None:
            manage_worker = self.worker_follows_presence
        present = bool(present)
        if present == self._present:
            return False
        self._present = present
        self.publish("presence", present=present)
        if manage_worker:
            if present:
                self.start_worker()
            else:
                self.stop_worker()
        return True

    def start_worker(self):
        """Start the device thread, waiting out any predecessor first.

        A pad unplugged and plugged back in within a second or two would
        otherwise leave two threads on the one command interface: the
        outgoing one has not reached its next stop check yet. Each worker owns
        its own stop event, so a stopped one can never be revived, and the
        retry runs on the scheduler rather than blocking it on a join.

        The "already serving" case ends the chain rather than booking another
        retry, or a pad flapping twice would leave a timer waking the
        application forever, one more chain per flap.
        """
        if not self._present:
            return
        previous = self._worker
        if previous is not None and previous.is_alive():
            if not self._stop.is_set():
                return                      # serving already, nothing to do
            self.app.scheduler.call_later(self.RESTART_MS, self.start_worker)
            return
        stop = threading.Event()
        self._stop = stop
        self._worker = threading.Thread(target=self._worker_loop, args=(stop,),
                                        daemon=True, name="macropad-worker")
        self._worker.start()

    def stop_worker(self):
        # The reference stays, so start_worker can see the thread is still on
        # its way out. is_alive() is what tells the two apart.
        self._stop.set()
        self._held.clear()

    def use_opener(self, opener):
        """Open the pad with `opener()` instead of controller.MacroPad() (a
        stand-in pad: tests, the web interface's demo mode)."""
        self._opener = opener

    def _open(self):
        if self._opener is not None:
            return self._opener()
        return mp.MacroPad()        # looked up now, so a stand-in can replace it

    def _retry_delay(self):
        r = self.retry_s
        return r() if callable(r) else r

    def _worker_loop(self, stop):
        while not stop.is_set():
            pad = None
            try:
                pad = self._open()
                pad.init()
            except Exception as exc:
                if pad is not None:
                    pad.close()
                self._post_status("open_failed", str(exc))
                # Whatever was queued was meant for a pad that is not
                # answering. Dropping it keeps a person clicking Apply at an
                # unreachable pad from building a backlog that all fires at
                # once the moment it comes back.
                self._drop_jobs()
                # A pad that is present but not readable is almost always the
                # udev rule; the application says so once, elsewhere.
                stop.wait(self._retry_delay())
                continue
            self._post_status("ready")
            try:
                self._serve(pad, stop)
            except Exception as exc:
                self._post_status("failed", str(exc))
                stop.wait(self._retry_delay())
            finally:
                pad.close()
                self._held.clear()

    def _drop_jobs(self):
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                return

    def _serve(self, pad, stop):
        """Send what was queued, read what the pad says, until told to stop."""
        while not stop.is_set():
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                self._run_job(pad, job)
            data = pad.dev.read(mp.PAYLOAD_LEN, timeout=self.READ_MS)
            if data:
                self.feed_report(data)
            # A command reply arriving with key reports behind it parks them
            # on the device object rather than dropping them.
            for event in pad.drain_key_events():
                self.feed_report(event)

    def _run_job(self, pad, job):
        kind = job.get("kind")
        try:
            if kind == "effect":
                # Wave and Tornado ride a different command with a different
                # struct; the controller picks (#85).
                pad.set_effect_auto(job["effect"], **job["kwargs"])
                self._post_status("applied")
            elif kind == "colors":
                pad.set_key_colors(job["colors"], brightness=job["brightness"])
                self._post_status("applied")
            elif kind == "save":
                pad.save()
                self._post_status("saved")
        except Exception as exc:
            self._post_status("job_failed", str(exc))

    def _post_status(self, code, detail=""):
        """Status from the worker thread, published on the scheduler."""
        self.app.scheduler.call_soon(
            lambda: self.publish("status", code=code, detail=detail))

    # ── Key presses ──────────────────────────────────────────────────────────

    def feed_report(self, data):
        """One input report: a press for every key that just went down.

        Runs on the worker thread (or wherever a test feeds reports from); the
        press itself is handed to the scheduler.
        """
        if not mp.is_key_event(data):
            return
        pressed = mp.decode_key_event(data)
        new = pressed - self._held
        self._held = pressed
        now = time.monotonic()
        for index in sorted(new):
            if now - self._last_fire.get(index, 0.0) < self.DEBOUNCE_S:
                continue
            self._last_fire[index] = now
            try:
                self.app.scheduler.call_soon(self._key_down, index)
            except Exception:
                # The interface is going away underneath us. Reports arriving
                # in that moment are not worth taking the thread down for.
                return

    def set_press_handler(self, handler):
        """Let an interface take over key presses: handler(index) is called on
        the scheduler instead of run_key(index), and is then expected to run
        the action itself (the Tk screen lights the key's tile first). None
        gives presses back to the service."""
        self._press_handler = handler

    def _key_down(self, index):
        self.publish("key", index=index)
        handler = self._press_handler
        if handler is not None:
            handler(index)
        else:
            self.run_key(index)

    def run_key(self, index):
        """Run what key `index` is bound to, as if it had been pressed."""
        action = self._actions[index] if 0 <= index < len(self._actions) else {}
        return self.run_action(action.get("type", "none"), action.get("action", ""))

    def run_action(self, type_id, value):
        """Run one action as a key of this pad, through the shared dispatcher."""
        return self._dispatcher().run(type_id, value, owner=self.name)

    def _dispatcher(self):
        macros = self.app.service("macros")
        actions = getattr(macros, "actions", None)
        if actions is not None:
            return actions
        if self._own_actions is None:
            from core.actions import ActionDispatcher
            self._own_actions = ActionDispatcher()
        return self._own_actions

    # ── Key actions ──────────────────────────────────────────────────────────

    def actions(self):
        """[{"type", "action"}] for M1 to M12 (a copy)."""
        return [dict(a) for a in self._actions]

    def set_action(self, index, type_id, value=""):
        """Bind key `index` (0-11). A key that does nothing carries no
        leftover text; the value is trimmed.

        Returns True when the binding changed (it is then saved), False when
        it was already that. A failed save raises, with the new binding in
        place, as the screen always had it.
        """
        if not 0 <= int(index) < NUM_KEYS:
            raise IndexError("MacroPad key %r does not exist" % (index,))
        index = int(index)
        type_id = str(type_id or "none")
        pending = {"type": type_id,
                   "action": "" if type_id == "none" else str(value or "").strip()}
        if pending == self._actions[index]:
            return False
        self._actions[index] = pending
        _config()._save_macropad_actions(self._actions)
        self.publish("actions_changed", index=index)
        return True

    # ── Lighting ─────────────────────────────────────────────────────────────

    def lighting(self):
        """Effect, brightness, speed, direction, color1, color2 and the 12
        per-key colours of the Custom effect (a copy)."""
        return copy.deepcopy(self._rgb)

    def effect(self):
        """The stored effect id; Static for one the pad does not have (a hand
        edited file), which is also what the screen's menu shows then."""
        try:
            effect = int(self._rgb.get("effect", mp.EFFECT_STATIC))
        except (TypeError, ValueError):
            return mp.EFFECT_STATIC
        return effect if effect in _EFFECT_IDS else mp.EFFECT_STATIC

    @staticmethod
    def effect_controls(effect):
        """(speed, colour 1, colour 2, direction) the effect reads."""
        return EFFECT_CONTROLS.get(effect, _DEFAULT_CONTROLS)

    @staticmethod
    def direction_count(effect):
        return DIRECTION_COUNTS.get(effect, 0)

    def update_lighting(self, **fields):
        """Change lighting fields and save them. A failed save raises, with
        the change in place."""
        unknown = sorted(set(fields) - set(_LIGHTING_FIELDS))
        if unknown:
            raise ValueError("unknown lighting field(s): %s" % ", ".join(unknown))
        for key, value in fields.items():
            if key in ("color1", "color2"):
                self._rgb[key] = _color(value)
            elif key in ("brightness", "speed"):
                self._rgb[key] = _clamp_int(value, 0, 100)
            else:
                self._rgb[key] = _clamp_int(value, 0, 255)
        self.save_lighting(sorted(fields))

    def set_key_color(self, index, color):
        """The colour key `index` gets from the Custom effect; saved."""
        if not 0 <= int(index) < NUM_KEYS:
            raise IndexError("MacroPad key %r does not exist" % (index,))
        self._rgb["colors"][int(index)] = _color(color)
        self.save_lighting(["colors"])

    def save_lighting(self, fields=()):
        _config()._save_macropad_rgb(self._rgb)
        self.publish("lighting_changed", fields=list(fields))

    def apply_lighting(self):
        """Send the current effect. Custom sends the 12 key colours instead.
        False when there is no pad to send it to."""
        effect = self.effect()
        if effect == mp.EFFECT_CUSTOM:
            return self.apply_key_colors()
        _speed, has_c1, has_c2, has_dir = self.effect_controls(effect)
        kwargs = {"brightness": int(self._rgb["brightness"]),
                  "speed": int(self._rgb["speed"])}
        if has_c1:
            kwargs["color1"] = tuple(self._rgb["color1"])
        if has_c2:
            kwargs["color2"] = tuple(self._rgb["color2"])
        if has_dir:
            kwargs["direction"] = int(self._rgb.get("direction", 0))
        return self.submit("effect", effect=effect, kwargs=kwargs)

    def apply_key_colors(self):
        """Send the 12 key colours. The pad switches to the Custom effect to
        show them, so the stored effect follows, or an interface would claim
        one effect while the pad runs another."""
        if self.effect() != mp.EFFECT_CUSTOM:
            try:
                self.update_lighting(effect=mp.EFFECT_CUSTOM)
            except Exception:
                log.exception("saving the MacroPad lighting failed")
        return self.submit("colors",
                           colors=[tuple(c) for c in self._rgb["colors"]],
                           brightness=int(self._rgb["brightness"]))

    def save_to_device(self):
        """Write the current state into the pad's flash, so it survives a
        replug without the application running."""
        return self.submit("save")

    def submit(self, kind, **payload):
        """Hand a job to the device thread. False when there is no pad."""
        if not self._present:
            return False
        payload["kind"] = kind
        self._jobs.put(payload)
        return True

    # ── Commands ─────────────────────────────────────────────────────────────

    def _need_pad(self, queued):
        if not queued:
            raise CommandError("the MacroPad is not connected")

    def commands(self):
        def set_action(index, type_id, value=""):
            try:
                return {"changed": self.set_action(index, type_id, value)}
            except IndexError as e:
                raise CommandError(str(e)) from None

        def update_lighting(effect=None, brightness=None, speed=None,
                            direction=None, color1=None, color2=None):
            fields = {k: v for k, v in (("effect", effect), ("brightness", brightness),
                                        ("speed", speed), ("direction", direction),
                                        ("color1", color1), ("color2", color2))
                      if v is not None}
            try:
                self.update_lighting(**fields)
            except (TypeError, ValueError) as e:
                raise CommandError("bad lighting value: %s" % e) from None
            return {"lighting": self.lighting()}

        def set_key_color(index, color):
            try:
                self.set_key_color(index, color)
            except (IndexError, TypeError, ValueError) as e:
                raise CommandError(str(e)) from None

        def press(index):
            if not 0 <= int(index) < NUM_KEYS:
                raise CommandError("MacroPad key %r does not exist" % (index,))
            return {"result": self.run_key(int(index))}

        return {
            "status": lambda: {"present": self._present, "worker": self.worker_alive},
            "actions": lambda: {"actions": self.actions(), "types": list(ACTION_TYPES)},
            "set_action": set_action,
            "lighting": lambda: {"lighting": self.lighting(),
                                 "effects": [{"id": e, "key": k} for e, k in mp.EFFECTS],
                                 "controls": {str(e): list(self.effect_controls(e))
                                              for e, _k in mp.EFFECTS},
                                 "directions": {str(e): n for e, n
                                                in DIRECTION_COUNTS.items()}},
            "update_lighting": update_lighting,
            "set_key_color": set_key_color,
            "apply_lighting": lambda: self._need_pad(self.apply_lighting()),
            "apply_key_colors": lambda: self._need_pad(self.apply_key_colors()),
            "save_to_device": lambda: self._need_pad(self.save_to_device()),
            "press": press,
        }
