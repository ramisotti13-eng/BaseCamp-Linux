"""Everest60Service: the Everest 60's lighting, owned by the core.

What the screen used to do itself, minus the widgets:

- find the board and its model (ANSI or ISO) on the HID bus;
- apply a firmware effect, saving it to rgb_settings.json first, as the
  screen did;
- replay the saved lighting 900 ms after the board appears (#42), from the
  saved settings, the Custom effect as its saved per-key map;
- light the side ring in one colour while keeping the saved key colours
  (#4), and push a whole per-key map.

Every write goes through the controller CLI (ControllerBackend): one call at
a time, never on the scheduler thread, with a timeout. Results come back as
events, never as translated text.

Events (topic `everest60.<event>`):
    presence     {present, model}
    rgb_applied  {op, ok, detail}   op: "lighting" | "saved" | "side_ring" | "per_key"
                 detail: the controller's output (stdout for lighting,
                 stderr for the side ring) or why it could not run.
"""
from core.commands import CommandError
from core.service import Service
from core.devices import keyboard_layouts
from core.devices.controller_backend import ControllerBackend
from core.devices.everest60 import device, layout
from core.devices.everest60 import protocol as P


class ConfigStore:
    """The service's files, through shared.config (looked up at call time).

    Imported lazily: shared.config creates the configuration directory when
    it is imported, which a core module must not do just by being imported.
    """

    @staticmethod
    def _cfg():
        import shared.config as cfg
        return cfg

    def config_dir(self):
        return self._cfg().CONFIG_DIR

    def load_rgb(self):
        return self._cfg().load_rgb_settings()

    def save_rgb(self, data):
        self._cfg().save_rgb_settings(data)

    def load_per_key(self):
        return self._cfg()._load_per_key_60()

    def save_per_key(self, leds, side, brightness):
        self._cfg()._save_per_key_60(leds, side, brightness)

    def load_presets(self):
        return self._cfg()._load_presets_60()

    def save_presets(self, presets):
        self._cfg()._save_presets_60(presets)


class Everest60Service(Service):
    name = "everest60"
    #: The device's id in the application and on the control socket.
    device_id = "everest60"
    REPLAY_DELAY_MS = 900       # let the HID interface finish enumerating
    TIMEOUT_S = 8

    def __init__(self, app, backend=None, store=None):
        super().__init__(app)
        self.backend = backend or ControllerBackend(app, self.device_id,
                                                    timeout=self.TIMEOUT_S)
        self.store = store or ConfigStore()
        self._present = False
        self._pid = None
        self._model = P.DEFAULT_MODEL
        self._replay = None
        # The side-ring colour is not saved (it never was); it lives here for
        # as long as the application runs. Mountain magenta by default.
        self._side = {"color": [255, 0, 67], "brightness": 100}

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self):
        self.backend.reopen()

    def stop(self):
        self.app.scheduler.cancel(self._replay)
        self._replay = None
        self.backend.close()

    def commands(self):
        return {
            "detect": self.detect,
            "status": self.status,
            "effects": self.effects,
            "lighting": self.lighting,
            "apply_lighting": self.apply_lighting,
            "save_effect": self.save_effect,
            "apply_saved": self.apply_saved,
            "side_ring": self.side_ring,
            "set_side_ring": self.set_side_ring,
            "apply_per_key": self.apply_per_key,
            "layout": self._cmd_layout,
            "keyboard_layout": self._cmd_keyboard_layout,
            "set_keyboard_layout": self._cmd_set_keyboard_layout,
            "per_key": self.per_key,
        }

    # ── keyboard layout: the shape from the USB id, the legends from the system ─
    def _config_dir(self):
        fn = getattr(self.store, "config_dir", None)
        if fn:
            return fn()
        import tempfile
        return tempfile.gettempdir()

    def _layout_info(self, lang=None, legends=False):
        shape = "iso" if self._pid == P.PID_ISO or "iso" in str(self._model).lower() else \
            ("ansi" if self._pid == P.PID_ANSI else None)
        return keyboard_layouts.info("everest60", self._config_dir(), language=lang,
                                     pid_shape=shape, with_legends=legends)

    def _cmd_layout(self, lang=None):
        kb = self._layout_info(lang, legends=True)
        d = layout.as_dict(kb["shape"])
        d["keyboard"] = kb
        return d

    def _cmd_keyboard_layout(self, lang=None):
        return self._layout_info(lang)

    def _cmd_set_keyboard_layout(self, layout_id, lang=None):
        """Store the legend set ("auto" or one of keyboard_layouts.LAYOUT_IDS);
        the shape always follows the board's USB id."""
        try:
            keyboard_layouts.set_choice("everest60", layout_id, self._config_dir())
        except ValueError as e:
            raise CommandError(str(e))
        self.publish("keyboard_layout", layout=layout_id)
        return self._layout_info(lang)

    def use_command(self, builder):
        """Build controller command lines with `builder(*args)` (the
        CustomTkinter interface passes app._cmd_for_device for the board)."""
        self.backend.set_command(builder)

    # ── device ─────────────────────────────────────────────────────────────
    def detect(self):
        """Look for the board on the HID bus now. Returns {found, pid, model}."""
        pid, name = device.detect()
        if pid:
            self._pid, self._model = pid, name
        return {"found": pid is not None, "pid": pid, "model": name or P.DEFAULT_MODEL}

    @property
    def model(self):
        return self._model

    @property
    def present(self):
        return self._present

    def status(self):
        return {"present": self._present, "pid": self._pid, "model": self._model,
                "busy": self.backend.busy}

    def set_present(self, present):
        """The board came or went (the device scan says so). On arrival the
        saved lighting is replayed after REPLAY_DELAY_MS."""
        present = bool(present)
        if present == self._present:
            return
        self._present = present
        self.app.scheduler.cancel(self._replay)
        self._replay = None
        if present:
            self._replay = self.app.scheduler.call_later(
                self.REPLAY_DELAY_MS, self._replay_saved)
        self.publish("presence", present=present, model=self._model)

    def _replay_saved(self):
        self._replay = None
        try:
            self.apply_saved()
        except Exception:
            pass

    # ── lighting ───────────────────────────────────────────────────────────
    def effects(self):
        """The effects, colour modes and directions the board offers."""
        return {
            "effects": [e._asdict() for e in P.RGB_EFFECTS],
            "color_modes": list(P.COLOR_MODES),
            "directions": {kind: [{"id": d[0], "label": d[1]} for d in dirs]
                           for kind, dirs in P.DIRECTIONS.items()},
        }

    def lighting(self):
        """The saved lighting settings, in the core's form (ids)."""
        return P.normalize(self._load_rgb())

    def _load_rgb(self):
        try:
            data = self.store.load_rgb()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def save_effect(self, effect):
        """Remember the chosen effect at once, before any apply (#34): the
        Custom choice has no Apply, and a replay must know it."""
        spec = P.effect(effect)
        if spec is None:
            raise CommandError("unknown effect: %s" % effect)
        try:
            data = self._load_rgb()
            data["effect"] = spec.name
            self.store.save_rgb(data)
        except Exception:
            pass

    def apply_lighting(self, settings=None, **fields):
        """Apply a firmware effect and save it.

        Takes the settings as a dict or as keywords: effect, color_mode,
        speed, brightness (0..100), color1, color2 ([r, g, b]), direction
        (ids, or the names/labels rgb_settings.json holds). Custom sends
        nothing (the per-key editor applies it). The result arrives as an
        `rgb_applied` event with op "lighting".
        """
        s = P.normalize(settings, **fields)
        args = P.rgb_args(s)
        if args is None:
            return {"sent": False}
        self.store.save_rgb(P.to_saved(s))       # saved before it is sent
        self._run(args, "lighting")
        return {"sent": True, "args": args}

    def apply_saved(self):
        """Push the saved lighting to the board; nothing when nothing was
        saved yet, so a board nobody configured keeps its own lighting."""
        data = self._load_rgb()
        if not data.get("effect"):
            return {"sent": False}
        s = P.normalize(data)
        if s["effect"] == "custom":
            return self._apply_saved_custom()
        args = P.rgb_args(s)
        if args is None:
            return {"sent": False}
        self._run(args, "saved")
        return {"sent": True, "args": args}

    def _apply_saved_custom(self):
        try:
            leds, side, bri = self.store.load_per_key()
        except Exception:
            return {"sent": False}
        if not leds:
            return {"sent": False}
        self._run(["per-key-rgb", P.per_key_payload(leds, side, bri)], "saved")
        return {"sent": True}

    def side_ring(self):
        """The side-ring colour and brightness last asked for."""
        return {"color": list(self._side["color"]),
                "brightness": self._side["brightness"]}

    def set_side_ring(self, color=None, brightness=None):
        """Light the 44 side LEDs in one colour, keeping the saved key colours
        (white when none are saved). Result: `rgb_applied` op "side_ring"."""
        if color is not None:
            try:
                r, g, b = (int(c) for c in color)
            except (TypeError, ValueError):
                raise CommandError("color must be [r, g, b]") from None
            self._side["color"] = [r, g, b]
        if brightness is not None:
            self._side["brightness"] = max(0, min(100, int(brightness)))
        try:
            leds, _side, _bri = self.store.load_per_key()
            leds = [list(c) for c in leds]
        except Exception:
            leds = []
        payload = P.side_ring_payload(leds, self._side["color"], self._side["brightness"])
        self._run(["per-key-rgb", payload], "side_ring")
        return self.side_ring()

    def per_key(self):
        """The saved per-key colours: {leds: 64 x [r, g, b], side: 44 x
        [r, g, b], brightness}, what the editor starts from."""
        try:
            leds, side, bri = self.store.load_per_key()
        except Exception:
            leds, side, bri = [], [], 100
        return {"leds": [list(c) for c in leds], "side": [list(c) for c in side],
                "brightness": bri}

    def apply_per_key(self, leds, side=None, brightness=100):
        """Paint the keys (64 x [r, g, b], ESC first) and optionally the side
        ring (44 x [r, g, b]); the map is saved first, so the replay and the
        editor find it. Result: `rgb_applied` op "per_key"."""
        try:
            self.store.save_per_key([list(c) for c in leds],
                                    [list(c) for c in (side or [])], int(brightness))
        except Exception:
            pass
        self._run(["per-key-rgb", P.per_key_payload(leds, side, brightness)], "per_key")

    # ── controller ─────────────────────────────────────────────────────────
    def _run(self, args, op):
        def done(res):
            if op == "side_ring":
                # The screen ran this one with check=True: the exit code
                # decides, and on failure stderr is what it shows.
                ok = res.exited_ok
                detail = res.stderr if res.error is None else res.error
            else:
                ok = res.said_ok
                detail = res.stdout.strip() if res.error is None else res.error
            self.publish("rgb_applied", op=op, ok=ok, detail=detail)
        self.backend.submit(args, done)
