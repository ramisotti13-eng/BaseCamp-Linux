"""MakaluService: the Makalu 67 / Makalu Max, owned by the core.

What the screen used to do itself, minus the widgets: detect the model and
what follows from it (buttons, DPI minimum, remap defaults), lighting,
the eight custom LEDs and their presets, the five DPI levels, button
remapping with the left-button safety revert, and the four sensor settings.

The left button is special: remapping it away from "left" can leave the
person without a click to undo it, so the change is kept only if confirmed
within REMAP_CONFIRM_S seconds; otherwise it is reverted. The countdown runs
on the core's scheduler, not in a dialog, so closing the window does not
stop it, and stopping the core with a confirmation pending reverts at once.

Lighting and sensor settings are saved when the mouse took them
(makalu_settings.json, ids not labels), so a screen can show what is set;
3.x stored neither. Every controller call goes through ControllerBackend:
one at a time, off the scheduler thread, with a timeout.

The Makalu Max (#115) does not take these commands; it gets them anyway, as
it always has, and the CLI refuses its buttons 7 and 8. What differs for the
Max here is only what the screen showed: its name, buttons, DPI minimum and
remap defaults.

Events (topic `makalu.<event>`):
    presence         {present, model}
    result           {op, ok, detail}   op: lighting | custom | dpi | remap |
                     polling_rate | debounce | lift_off | angle_snapping
                     (remap also carries button, assignment, pending)
    dpi              {levels, active, source}   source: "read" | "set"
    remap_pending    {button, assignment, previous, seconds_left}
                     (seconds_left 0: the time is up, the revert starts)
    remap_confirmed  {button, assignment}
    remap_reverted   {button, ok, assignment}
    remap_changed    {assignments}               (after a reset)
"""
import logging

from core.commands import CommandError
from core.service import Service
from core.devices.controller_backend import ControllerBackend
from core.devices.makalu import device
from core.devices.makalu.dpi_watch import DpiWatcher
from core.devices.makalu import protocol as P

log = logging.getLogger("basecamp.core.makalu")


class ConfigStore:
    """The service's files, through shared.config (imported when first used:
    importing it creates the configuration directory)."""

    @staticmethod
    def _cfg():
        import shared.config as cfg
        return cfg

    def load_leds(self):
        return self._cfg()._load_makalu_leds()

    def save_leds(self, leds, brightness, preset=""):
        self._cfg()._save_makalu_leds(leds, brightness, preset)

    def load_presets(self):
        return self._cfg()._load_makalu_presets()

    def save_presets(self, presets):
        self._cfg()._save_makalu_presets(presets)

    def load_dpi(self):
        return self._cfg()._load_makalu_dpi()

    def save_dpi(self, levels):
        self._cfg()._save_makalu_dpi(levels)

    def dpi_defaults(self):
        return list(self._cfg().DPI_DEFAULTS)

    def load_remap(self, defaults):
        return self._cfg()._load_makalu_remap(defaults)

    def save_remap(self, assignments):
        self._cfg()._save_makalu_remap(assignments)

    def load_settings(self):
        return self._cfg()._load_makalu_settings()

    def save_settings(self, data):
        self._cfg()._save_makalu_settings(data)

    # makalu_lighting: "effect" or "custom", a live file of the profile.
    def _lighting_file(self):
        import os
        return os.path.join(self._cfg().CONFIG_DIR, "makalu_lighting")

    def load_lighting_mode(self):
        try:
            with open(self._lighting_file()) as f:
                value = f.read().strip()
        except OSError:
            return None
        return value if value in ("effect", "custom") else None

    def save_lighting_mode(self, mode):
        with open(self._lighting_file(), "w") as f:
            f.write(mode)


class MakaluService(Service):
    name = "makalu"
    #: The device's id in the application and on the control socket.
    device_id = "makalu67"
    TIMEOUT_S = 5
    REMAP_CONFIRM_S = 10

    def __init__(self, app, backend=None, store=None):
        super().__init__(app)
        self.backend = backend or ControllerBackend(app, self.device_id,
                                                    timeout=self.TIMEOUT_S)
        self.store = store or ConfigStore()
        self._model = P.MAKALU_67
        self._found = False
        self._present = False
        self._lighting_busy = False
        self._dpi = None            # {"levels", "active"}, loaded when first asked
        self._remap = None          # assignments, loaded when first asked
        self._pending = None        # the left-button confirmation, if any
        self._lighting_mode = None  # "effect" | "custom": what was last sent
        self._pending_apply = None  # a profile state to write on plug-in
        self._tick = None
        #: Follows the DPI button while the mouse is plugged in (dpi_watch.py);
        #: a test passes its own factory, or None for no watcher.
        self.dpi_watch_factory = DpiWatcher
        self._dpi_watch = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self):
        self.backend.reopen()

    def stop(self):
        """Release the timer; a confirmation still pending is reverted now,
        synchronously: nobody is left to confirm it."""
        pending = self._pending
        self._end_pending()
        self._stop_dpi_watch()
        self.backend.close()            # nothing waiting runs after this
        if pending is not None:
            try:
                res = self.backend.run_now(P.remap_args(pending["button"], pending["previous"]))
                if res.said_ok:
                    self._assignments()[pending["button"]] = pending["previous"]
                    self.store.save_remap(self._assignments())
            except Exception:
                log.exception("reverting the pending remap on stop failed")

    def commands(self):
        return {
            "detect": self.detect,
            "status": self.status,
            "effects": self.effects,
            "lighting": self.lighting,
            "set_lighting": self.set_lighting,
            "custom_leds": self.custom_leds,
            "set_custom_leds": self.set_custom_leds,
            "presets": self.presets,
            "save_preset": self.save_preset,
            "load_preset": self.load_preset,
            "delete_preset": self.delete_preset,
            "dpi": self.dpi,
            "read_dpi": self.read_dpi,
            "set_dpi": self.set_dpi,
            "remap_catalog": self.remap_catalog,
            "assignments": self.assignments,
            "remap": self.remap,
            "confirm_remap": self.confirm_remap,
            "revert_remap": self.revert_remap,
            "reset_remap": self.reset_remap,
            "sensor": self.sensor,
            "set_polling_rate": self.set_polling_rate,
            "set_debounce": self.set_debounce,
            "set_lift_off": self.set_lift_off,
            "set_angle_snapping": self.set_angle_snapping,
            "reset_settings": self.reset_settings,
        }

    def use_command(self, builder):
        """Build controller command lines with `builder(*args)` (the
        CustomTkinter interface passes app._cmd_for_device for the mouse)."""
        self.backend.set_command(builder)

    # ── model and presence ─────────────────────────────────────────────────
    def detect(self):
        """Look for the mouse now and take its model (the Makalu 67 when none
        is found, as the screen did). Returns found plus the model."""
        pid, _name = device.detect()
        model = P.model_for(pid)
        if model is not self._model:
            self._model = model
            self._remap = None      # the other model's buttons and defaults
        self._found = pid is not None
        out = P.model_dict(model)
        out["found"] = self._found
        return out

    @property
    def model(self):
        return self._model

    @property
    def present(self):
        return self._present

    def status(self):
        out = P.model_dict(self._model)
        out.update(present=self._present, found=self._found, busy=self.backend.busy,
                   remap_pending=self._pending_info())
        return out

    def set_present(self, present):
        present = bool(present)
        if present == self._present:
            return
        self._present = present
        self.publish("presence", present=present, model=self._model.name)
        if present:
            self._start_dpi_watch()
            if self._pending_apply is not None:
                self.app.scheduler.call_soon(self._run_pending_apply)
        else:
            self._stop_dpi_watch()

    # ── the DPI button on the mouse ────────────────────────────────────────
    def _start_dpi_watch(self):
        if self._dpi_watch is None and self.dpi_watch_factory is not None:
            self._dpi_watch = self.dpi_watch_factory(
                lambda level: self.app.scheduler.call_soon(self._level_from_mouse, level))
            self._dpi_watch.start()

    def _stop_dpi_watch(self):
        w, self._dpi_watch = self._dpi_watch, None
        if w is not None:
            w.stop(wait=False)

    def _level_from_mouse(self, level):
        """The DPI button changed the active level (0-based). A `dpi` event
        (source "mouse") follows; before the levels were ever read they are
        read now, so the event carries the mouse's own."""
        if self._dpi is None:
            self.read_dpi()
            return
        if self._dpi["active"] == level:
            return
        self._dpi["active"] = level
        self.publish("dpi", levels=list(self._dpi["levels"]), active=level, source="mouse")

    # ── lighting ───────────────────────────────────────────────────────────
    def effects(self):
        return {
            "effects": [e._asdict() for e in P.RGB_EFFECTS],
            "speeds": [{"id": s[0], "label": s[1]} for s in P.SPEEDS],
            "directions": [{"id": d[0], "label": d[1]} for d in P.DIRECTIONS],
            "brightness_levels": list(P.BRIGHTNESS_LEVELS),
        }

    def _settings(self):
        try:
            data = self.store.load_settings()
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def _save_setting(self, key, value):
        data = self._settings()
        data[key] = value
        try:
            self.store.save_settings(data)
        except Exception:
            log.exception("saving makalu_settings.json failed")

    def lighting(self):
        """The lighting last applied (defaults before the first apply)."""
        return P.normalize_lighting(self._settings().get("lighting"))

    def set_lighting(self, settings=None, **fields):
        """Apply an effect: effect, speed (slow/medium/fast), brightness
        (0..100), direction (left/right), color1, color2. Ids or the screen's
        labels. One at a time: while one is on its way another is dropped
        ({"sent": False, "busy": True})."""
        if self._lighting_busy:
            return {"sent": False, "busy": True}
        s = P.normalize_lighting(settings, **fields)
        args = P.lighting_args(s)
        self._lighting_busy = True

        def done(res):
            self._lighting_busy = False
            if res.said_ok:
                self._save_setting("lighting", s)
                self._note_lighting_mode("effect")
            self._result("lighting", res)
        self.backend.submit(args, done)
        return {"sent": True, "args": args}

    def _note_lighting_mode(self, mode):
        self._lighting_mode = mode
        try:
            self.store.save_lighting_mode(mode)
        except Exception:
            log.exception("saving makalu_lighting failed")

    def custom_leds(self):
        """The eight custom LEDs, their brightness and the preset they came from."""
        leds, bri, preset = self.store.load_leds()
        return {"leds": [list(c) for c in leds], "brightness": bri, "preset": preset}

    def set_custom_leds(self, leds, brightness=100, preset=""):
        """Light the eight LEDs one by one. They are saved before the mouse
        answers, as the editor always did."""
        leds = [tuple(int(v) for v in c) for c in list(leds)[:P.NUM_LEDS]]
        if len(leds) != P.NUM_LEDS:
            raise CommandError("leds: eight [r, g, b] needed")
        brightness = int(brightness)
        self.store.save_leds(leds, brightness, preset or "")
        args = P.custom_args(leds, brightness)

        def done(res):
            if res.said_ok:
                self._note_lighting_mode("custom")
            self._result("custom", res)
        self.backend.submit(args, done)
        return {"sent": True}

    def presets(self):
        return {name: {"leds": [list(c) for c in p.get("leds", [])],
                       "brightness": int(p.get("brightness", 100))}
                for name, p in self.store.load_presets().items() if isinstance(p, dict)}

    def save_preset(self, preset, leds, brightness=100):
        """Save the LEDs as a named preset, and as the current ones."""
        name = (preset or "").strip()
        if not name:
            raise CommandError("a preset needs a name")
        leds = [tuple(c) for c in leds]
        presets = self.store.load_presets()
        presets[name] = {"leds": [list(c) for c in leds], "brightness": int(brightness)}
        self.store.save_presets(presets)
        self.store.save_leds(leds, int(brightness), name)
        return {"names": sorted(presets)}

    def load_preset(self, preset):
        """A preset's LEDs (padded to eight) and brightness, saved as the
        current ones. Nothing is sent to the mouse."""
        name = (preset or "").strip()
        presets = self.store.load_presets()
        if name not in presets:
            raise CommandError("preset not found: %s" % name)
        d = presets[name]
        leds = [tuple(c) for c in d.get("leds", [])]
        leds = (leds + [(20, 20, 20)] * P.NUM_LEDS)[:P.NUM_LEDS]
        bri = int(d.get("brightness", 100))
        self.store.save_leds(leds, bri, name)
        return {"leds": [list(c) for c in leds], "brightness": bri, "preset": name}

    def delete_preset(self, preset):
        name = (preset or "").strip()
        presets = self.store.load_presets()
        if name not in presets:
            raise CommandError("preset not found: %s" % name)
        del presets[name]
        self.store.save_presets(presets)
        return {"names": sorted(presets)}

    # ── DPI ────────────────────────────────────────────────────────────────
    def _dpi_state(self):
        if self._dpi is None:
            self._dpi = {"levels": list(self.store.load_dpi()), "active": 0}
        return self._dpi

    def dpi(self):
        """The five levels and the active one (0-based) as last read, set or
        saved, plus the range this model takes."""
        st = self._dpi_state()
        return {"levels": list(st["levels"]), "active": st["active"],
                "defaults": self.store.dpi_defaults(), "min": self._model.dpi_min,
                "max": P.DPI_MAX, "step": P.DPI_STEP}

    def snap_dpi(self, value):
        return P.snap_dpi(value, self._model.dpi_min)

    def read_dpi(self):
        """Ask the mouse for its levels; a `dpi` event (source "read") follows
        if it answers. Nothing happens when it does not."""
        def done(res):
            if res.error is not None or res.returncode != 0:
                return
            parts = res.stdout.strip().split()
            if len(parts) != 6:
                return
            try:
                values = [int(p) for p in parts]
            except ValueError:
                return
            self._dpi = {"levels": values[:5], "active": values[5]}
            self.publish("dpi", levels=values[:5], active=values[5], source="read")
        self.backend.submit(["dpi", "get"], done)

    def set_dpi(self, levels, active=0):
        """Write the five levels and the active one (0-based); saved when the
        mouse took them."""
        try:
            levels = [int(v) for v in levels]
            active = int(active)
        except (TypeError, ValueError):
            raise CommandError("levels: five numbers; active: 0..4") from None
        if len(levels) != P.DPI_LEVELS or not 0 <= active < P.DPI_LEVELS:
            raise CommandError("levels: five numbers; active: 0..4")
        args = ["dpi"] + [str(v) for v in levels] + [str(active + 1)]

        def done(res):
            if res.said_ok:
                self.store.save_dpi(levels)
                self._dpi = {"levels": list(levels), "active": active}
                self.publish("dpi", levels=list(levels), active=active, source="set")
            self._result("dpi", res)
        self.backend.submit(args, done)
        return {"sent": True, "args": args}

    # ── buttons ────────────────────────────────────────────────────────────
    def remap_catalog(self):
        return {
            "buttons": [b._asdict() for b in self._model.buttons],
            "categories": [{"id": c, "functions": list(fns)} for c, fns in P.REMAP_CATEGORIES],
            "defaults": dict(self._model.remap_defaults),
            "sniper": {"min": self._model.dpi_min, "max": P.DPI_MAX,
                       "default": P.SNIPER_DEFAULT_DPI},
        }

    def _assignments(self):
        if self._remap is None:
            self._remap = self.store.load_remap(dict(self._model.remap_defaults))
        return self._remap

    def assignments(self):
        """Button id -> function ("left", ..., "sniper:<dpi>")."""
        return dict(self._assignments())

    def remap(self, button, function, sniper_dpi=None):
        """Give a button a function; "sniper" takes sniper_dpi. Saved when the
        mouse took it. Taking "left" away from button 1 has to be confirmed
        (confirm_remap) within REMAP_CONFIRM_S seconds or it is reverted."""
        return self._remap_button(button, function, sniper_dpi, keep=False)

    def _remap_button(self, button, function, sniper_dpi, keep):
        """remap(); `keep` skips the left-button confirmation (a profile
        switch restores what the user chose and confirmed before)."""
        key = str(button)
        if key not in {b.id for b in self._model.buttons}:
            raise CommandError("no button %s on the %s" % (key, self._model.name))
        if function == "sniper":
            dpi = P.SNIPER_DEFAULT_DPI if sniper_dpi is None else sniper_dpi
            try:
                assignment = "sniper:%d" % self.snap_dpi(dpi)
            except (TypeError, ValueError):
                raise CommandError("sniper_dpi must be a number") from None
        elif function in P.REMAP_FUNCTIONS:
            assignment = function
        else:
            raise CommandError("unknown function: %s" % function)
        previous = self._assignments().get(key, key)
        args = P.remap_args(key, assignment)

        def done(res):
            if not res.said_ok:
                self._result("remap", res, button=key, assignment=previous, pending=False)
                return
            self._assignments()[key] = assignment
            self.store.save_remap(self._assignments())
            pending = key == "1" and function != "left" and not keep
            self._result("remap", res, button=key, assignment=assignment, pending=pending)
            if pending:
                self._start_pending(key, assignment, previous)
        self.backend.submit(args, done)
        return {"sent": True, "args": args}

    def _pending_info(self):
        p = self._pending
        if p is None:
            return None
        return {k: p[k] for k in ("button", "assignment", "previous", "seconds_left")}

    def _start_pending(self, key, assignment, previous):
        self._end_pending()
        self._pending = {"button": key, "assignment": assignment, "previous": previous,
                         "seconds_left": self.REMAP_CONFIRM_S}
        self.publish("remap_pending", **self._pending_info())
        self._tick = self.app.scheduler.call_later(1000, self._count_down)

    def _end_pending(self):
        self.app.scheduler.cancel(self._tick)
        self._tick = None
        self._pending = None

    def _count_down(self):
        self._tick = None
        if self._pending is None:
            return
        self._pending["seconds_left"] -= 1
        if self._pending["seconds_left"] <= 0:
            self.publish("remap_pending", **self._pending_info())
            self.revert_remap()
            return
        self.publish("remap_pending", **self._pending_info())
        self._tick = self.app.scheduler.call_later(1000, self._count_down)

    def confirm_remap(self):
        """Keep the pending left-button change."""
        p = self._pending
        if p is None:
            return {"pending": False}
        self._end_pending()
        self.publish("remap_confirmed", button=p["button"], assignment=p["assignment"])
        return {"pending": False, "button": p["button"], "assignment": p["assignment"]}

    def revert_remap(self):
        """Put the pending button's previous function back now."""
        p = self._pending
        if p is None:
            return {"pending": False}
        self._end_pending()
        key, previous = p["button"], p["previous"]

        def done(res):
            if res.said_ok:
                self._assignments()[key] = previous
                self.store.save_remap(self._assignments())
            self.publish("remap_reverted", button=key, ok=res.said_ok,
                         assignment=self._assignments().get(key, key))
        self.backend.submit(P.remap_args(key, previous), done)
        return {"pending": False, "button": key, "reverting_to": previous}

    def reset_remap(self):
        """Every button back to this model's defaults, in the saved file. As
        before, nothing is sent to the mouse."""
        self._remap = dict(self._model.remap_defaults)
        self.store.save_remap(self._remap)
        self.publish("remap_changed", assignments=dict(self._remap))
        return self.assignments()

    # ── sensor ─────────────────────────────────────────────────────────────
    def sensor(self):
        """Polling rate (Hz), debounce (ms), lift-off ("low"/"high") and angle
        snapping (bool) as last applied."""
        return P.normalize_sensor(self._settings().get("sensor"))

    def _apply_sensor(self, op, args, key, value):
        def done(res):
            if res.said_ok:
                s = self.sensor()
                s[key] = value
                self._save_setting("sensor", s)
            self._result(op, res)
        self.backend.submit(args, done)
        return {"sent": True, "args": args}

    def set_polling_rate(self, hz):
        try:
            hz = int(hz)
        except (TypeError, ValueError):
            hz = None
        if hz not in P.POLLING_RATE_MAP:
            raise CommandError("polling rate: one of 125, 250, 500, 1000")
        return self._apply_sensor("polling_rate", ["polling-rate", str(hz)], "polling_rate", hz)

    def set_debounce(self, ms):
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            ms = None
        if ms not in P.DEBOUNCE_VALUES:
            raise CommandError("debounce: one of 2, 4, 6, 8, 10, 12 ms")
        return self._apply_sensor("debounce", ["debounce", str(ms)], "debounce", ms)

    def set_lift_off(self, high):
        if high in ("high", "low"):
            high = high == "high"
        elif not isinstance(high, bool):
            raise CommandError("lift-off: high or low")
        value = "high" if high else "low"
        return self._apply_sensor("lift_off", ["lift-off", value], "lift_off", value)

    def set_angle_snapping(self, on):
        if on in ("on", "off"):
            on = on == "on"
        elif not isinstance(on, bool):
            raise CommandError("angle snapping: on or off")
        return self._apply_sensor("angle_snapping",
                                  ["angle-snapping", "on" if on else "off"],
                                  "angle_snapping", on)

    def reset_settings(self):
        """The Settings page's "Werkseinstellungen": the five DPI levels and
        the sensor back to the application's defaults, each sent and saved as
        when it is set by hand. Lighting and button assignments stay (they
        have their own pages and resets)."""
        d = P.DEFAULT_SENSOR
        levels = self.store.dpi_defaults()
        self.set_dpi(levels, 0)
        self.set_polling_rate(d["polling_rate"])
        self.set_debounce(d["debounce"])
        self.set_lift_off(d["lift_off"])
        self.set_angle_snapping(d["angle_snapping"])
        return {"sent": True, "levels": levels, "active": 0, "sensor": dict(d)}

    # ── profiles (core/profiles.py) ────────────────────────────────────────
    def profile_files(self):
        return ["makalu_leds.json", "makalu_settings.json", "makalu_dpi.json",
                "makalu_remap.json", "makalu_lighting"]

    def profile_picture_refs(self, folder):
        return []

    def capture_profile_state(self):
        try:
            mode = self.store.load_lighting_mode()
        except Exception:
            mode = None
        mode = mode or self._lighting_mode or "effect"
        try:
            active = int(self._dpi_state()["active"])
        except Exception:
            active = 0
        return {"lighting": mode, "dpi_active": active}

    def factory_profile_state(self):
        return {"lighting": "effect", "dpi_active": 0}

    def apply_profile(self, state, previous_folder):
        """The live files were swapped: forget what was cached from the old
        ones and write the mouse (now, or on the next plug-in)."""
        try:
            state = dict(state) if isinstance(state, dict) else {}
            self._dpi = None
            self._remap = None
            mode = state.get("lighting")
            self._lighting_mode = mode if mode in ("effect", "custom") else "effect"
            try:
                self.store.save_lighting_mode(self._lighting_mode)
            except Exception:
                log.exception("saving makalu_lighting failed")
            if not self._present:
                self._pending_apply = state
                return
            self._pending_apply = None
            self.app.scheduler.call_soon(self._write_profile, state, True)
        except Exception:
            log.exception("applying the profile failed")

    def _run_pending_apply(self):
        state, self._pending_apply = self._pending_apply, None
        if state is not None:
            self._write_profile(state, True)

    def _write_profile(self, state, retry):
        """On the scheduler thread. The mouse takes one lighting command at a
        time: if one is still on its way, try again once after a second."""
        try:
            if self._lighting_busy and retry:
                self.app.scheduler.call_later(1000, self._write_profile, state, False)
                return
            if state.get("lighting") == "custom":
                self.set_custom_leds(**self.custom_leds())
            else:
                self.set_lighting(self.lighting())
            levels = list(self.store.load_dpi())
            try:
                active = int(state.get("dpi_active", 0))
            except (TypeError, ValueError):
                active = 0
            if not 0 <= active < P.DPI_LEVELS:
                active = 0
            self.set_dpi(levels, active)
            for key, assignment in sorted(self._assignments().items()):
                function, _, dpi = str(assignment).partition(":")
                try:
                    self._remap_button(key, function, dpi or None, keep=True)
                except CommandError:
                    log.warning("profile: button %s: %r skipped", key, assignment)
            self.publish("remap_changed", assignments=self.assignments())
            s = self.sensor()
            self.set_polling_rate(s["polling_rate"])
            self.set_debounce(s["debounce"])
            self.set_lift_off(s["lift_off"])
            self.set_angle_snapping(s["angle_snapping"])
        except Exception:
            log.exception("writing the profile to the mouse failed")

    # ── results ────────────────────────────────────────────────────────────
    def _result(self, op, res, **extra):
        detail = res.stdout.strip() if res.error is None else res.error
        self.publish("result", op=op, ok=res.said_ok, detail=detail, **extra)
