"""EverestMaxService: the Everest Max, with one owner of the keyboard at a time.

Everything that talks to the keyboard runs the controller command line
(backend.py) on the service's own worker thread, one command after another.
The worker is the only thread that ever starts or stops a controller process,
so Monitor Mode and an upload can never hold the keyboard at the same time,
and no wait for a controller to let go of it runs on the interface thread
any more.

Monitor Mode is a long-running controller (`cpu <style>`) that holds the
keyboard's interface. Its *wanted* state (on/off, what the user and the jobs
asked for) is kept apart from whether the process runs:

- A job that needs the keyboard pauses Monitor Mode: the worker stops the
  process and waits until it has let go, runs the job, and starts it again
  afterwards if it is still wanted. Jobs queued back to back share one pause.
- start_monitor()/stop_monitor() only change the wanted state; the worker
  makes the process follow whenever no job holds the keyboard.
- A Monitor Mode process that ends by itself (keyboard unplugged, crash) is
  noticed within a second and is not restarted, as before.

Every job publishes `everest_max.job_started`, `job_progress` and `job_done`
with its id and kind; the lighting jobs also publish `rgb_applied`. Monitor
state changes publish `monitor`, settings `clock`, `main_mode` and
`buttons_changed`; a read or write of the media dock's settings publishes
`dock_settings`, a read of the firmware versions `versions`. Payloads carry
codes and values, never translated text.

Device state that used to live in the panel lives here: the main display
mode, "an image was just uploaded" (the keyboard is busy writing it for a
while), "the dial was reset", and the queue of plugin pictures for D1-D4.
"""
import collections
import contextlib
import copy
import hashlib
import json
import logging
import os
import threading
import time

from core import CommandError, Service
from core.devices import keyboard_layouts
from core.devices.everest_max import keybindings as KB, keymacro, layout, protocol as P
from core.devices.everest_max.backend import ControllerBackend, wait_for_exit
from core.devices.everest_max.keytrigger import EvdevTrigger
from core.devices.everest_max.metrics import MeterSampler, watch_volume

log = logging.getLogger("basecamp.core.everest_max")

MAIN_UPLOAD_TRIES = 3
DBUTTON_PUSH_TIMEOUT = 180
COMMAND_TIMEOUT = 30
POLL_S = 1.0            # how often an idle worker looks at Monitor Mode
RECENT_JOBS = 64


def _cfg():
    """shared.config, imported when first needed: importing it creates the
    configuration directory, which importing the core must not do."""
    from shared import config
    return config


def _read_text(path, default):
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


class Job:
    def __init__(self, seq, kind, fn, keyboard=True, info=None):
        self.id = seq
        self.kind = kind
        self.fn = fn
        self.keyboard = keyboard
        self.info = dict(info or {})
        self.state = "queued"
        self.result = None
        self.done = threading.Event()


class JobContext:
    """What a running job knows about the pause it runs in."""

    def __init__(self, service, job, fresh):
        self._service = service
        self.job = job
        # Monitor Mode let go of the keyboard for this pause and nothing has
        # claimed it since: the next claim should give it a moment.
        self.fresh = fresh

    def sleep(self, seconds):
        if seconds:
            self._service._sleep(seconds)

    def settle(self, after_monitor, otherwise=0.0):
        """Wait before claiming the keyboard: `after_monitor` seconds when
        Monitor Mode just let go of it, else `otherwise`."""
        delay = after_monitor if self.fresh else otherwise
        self.fresh = False
        self.sleep(delay)

    def progress(self, pct=None, **extra):
        payload = dict(self.job.info)
        payload.update(extra)
        payload.update(id=self.job.id, kind=self.job.kind, pct=pct)
        self._service.publish("job_progress", **payload)


class EverestMaxService(Service):
    name = "everest_max"

    def __init__(self, app, backend=None, sleep=None):
        super().__init__(app)
        self.backend = backend or ControllerBackend()
        self._sleep = sleep or time.sleep
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._jobs = collections.deque()
        self._recent = collections.OrderedDict()
        self._seq = 0
        self._worker = None
        self._closing = False

        # Monitor Mode
        self._monitor = None            # the running controller, or None
        self._monitor_wanted = False
        self._monitor_error = ""
        self._monitor_code = None       # exit code when it ended by itself
        self._exiting = []              # told to stop, not yet gone
        self._session = None            # {"fresh": bool} while a job holds it

        # Settings and device state
        self._style, self._clock_format, self._main_mode = self._read_settings()
        self._main_just_uploaded = False    # the keyboard is still writing it
        self._after_dial_reset = False

        # Pictures on D1-D4 from plugins
        self._dpush_lock = threading.Lock()
        self._dpush_pending = {}     # key -> (file, digest), the latest only
        self._dpush_inflight = {}    # key -> digest being uploaded right now
        self._dpush_sent = {}        # key -> digest of what the key shows
        self._dpush_busy = False

        self._meters = MeterSampler()

        self._modules = None             # (numpad, dock) as the keyboard last said
        self._modules_at = None

        self._dock_settings = None       # the media dock's settings as last read
        self._board_settings = None      # game mode and indicator LEDs as last read
        self._versions = None            # firmware versions as last read
        self._upload_end = None          # monotonic time the last image upload ended

        # Key binding (keybindings.py)
        self._present = None             # the keyboard is plugged in; None: not told
        self._keymap_lock = threading.RLock()     # load-modify-save of the bindings file
        self._keymap_dirty = set()       # keys whose remap waits for the "keymap" job
        self._keymap_job = None          # that job's id while it is queued
        #: Makes the software trigger (keytrigger.KeyTrigger); a test or the
        #: demo hands in its own.
        self.key_trigger_factory = EvdevTrigger
        self._trigger = None
        self._trigger_lock = threading.Lock()
        # True after a per-key write went through, False after an effect, None
        # when not known (start, unplug, a failed write): the controller then
        # sends the custom-mode packet twice, which a switch from an effect needs.
        self._in_custom = None
        self._unsub_macros = None
        # Device profiles (apply_profile): which RGB state is active, and a
        # switch that waits for the keyboard to be plugged in.
        self._lighting = self._read_lighting()
        self._profile_pending = None    # {"state", "mode", "style"} or None

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def start(self):
        with self._lock:
            self._closing = False
            self._ensure_worker()
        if self._unsub_macros is None:
            self._unsub_macros = self.app.bus.subscribe("macros.changed", self._on_macros_changed)
        self._update_trigger()

    def stop(self):
        """Stop the worker and Monitor Mode. Does not wait for either: the
        application is closing, and the controller releases the keyboard on
        its way out by itself."""
        if self._unsub_macros is not None:
            self._unsub_macros()
            self._unsub_macros = None
        self._stop_trigger()
        with self._lock:
            self._closing = True
            pending = list(self._jobs)
            self._jobs.clear()
            proc = self._monitor
            self._wake.notify_all()
            worker = self._worker
        for job in pending:
            self._finish(job, {"ok": False, "error": "stopped"}, publish=False)
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        if worker is not None and worker is not threading.current_thread():
            worker.join(0.5)

    def commands(self):
        return {
            "status": self.status,
            "start_monitor": lambda: (self.start_monitor(), self.monitor_state())[1],
            "stop_monitor": lambda: (self.stop_monitor(), self.monitor_state())[1],
            "toggle_monitor": lambda: (self.toggle_monitor(), self.monitor_state())[1],
            "set_style": lambda style: {"job": self.set_style(style)},
            "sync_time": lambda style=None: {"job": self.sync_time(style)},
            "set_clock_format": lambda format: self.set_clock_format(format),
            "set_main_mode": lambda mode: {"job": self.set_main_mode(mode)},
            "upload_main_image": self._cmd_upload_main,
            "reset_dial": lambda: {"job": self.reset_dial()},
            "factory_reset": lambda: {"job": self.factory_reset()},
            "get_buttons": lambda: {"buttons": self.get_buttons()},
            "set_button": self._cmd_set_button,
            "reset_buttons_flash": lambda: {"job": self.reset_buttons_flash()},
            "get_dock_buttons": lambda: {"buttons": self.get_dock_buttons()},
            "set_dock_button": self._cmd_set_dock_button,
            "upload_dbutton": self._cmd_upload_dbutton,
            "rgb_settings": self.rgb_settings,
            "apply_rgb": self._cmd_apply_rgb,
            "apply_per_key": self._cmd_apply_per_key,
            "effects": lambda: {"effects": [P.effect_info(e) for e in P.RGB_EFFECT_IDS]},
            "layout": self._cmd_layout,
            "keyboard_layout": self._cmd_keyboard_layout,
            "set_keyboard_layout": self._cmd_set_keyboard_layout,
            "modules": self.modules,
            "set_modules_setting": self._cmd_set_modules_setting,
            "per_key": self.per_key,
            "preview_per_key": self._cmd_preview_per_key,
            "dock_settings": lambda refresh=True: self.dock_settings(bool(refresh)),
            "set_dock_settings": self._cmd_set_dock_settings,
            "board_settings": lambda refresh=True: self.board_settings(bool(refresh)),
            "set_board_settings": self._cmd_set_board_settings,
            "versions": lambda refresh=False: self.versions(bool(refresh)),
            "key_functions": self.key_functions,
            "keybindings": self.keybindings,
            "set_keybinding": self._cmd_set_keybinding,
            "set_dkey_text": self._cmd_set_dkey_text,
            "pictures": self.pictures,
            "meters": self.meters,
            "job": self.job_info,
            "run": lambda args, timeout=COMMAND_TIMEOUT: self.run_command(
                [str(a) for a in args], timeout=timeout),
        }

    # ── Keyboard layout (ANSI/ISO shape and the legends) ───────────────────
    def _layout_info(self, lang=None, legends=False):
        return keyboard_layouts.info("everest_max", _cfg().CONFIG_DIR, language=lang,
                                     with_legends=legends)

    def _cmd_layout(self, lang=None):
        """The keys' geometry for the keyboard's shape, with the legends."""
        kb = self._layout_info(lang, legends=True)
        d = layout.as_dict(kb["shape"])
        d["keyboard"] = kb
        return d

    def _cmd_keyboard_layout(self, lang=None):
        return self._layout_info(lang)

    def _cmd_set_keyboard_layout(self, layout_id, lang=None):
        """Store the layout ("auto" or one of keyboard_layouts.LAYOUT_IDS)."""
        try:
            keyboard_layouts.set_choice("everest_max", layout_id, _cfg().CONFIG_DIR)
        except ValueError as e:
            raise CommandError(str(e))
        self.publish("keyboard_layout", layout=layout_id)
        # A macro in the keyboard types characters by their keys, which
        # depend on the layout.
        self._on_macros_changed("macros.changed", {"id": None})
        return self._layout_info(lang)

    # ── Attached modules (numpad, media dock) ──────────────────────────────
    # Three sources, in this order: the user's setting for each module
    # ("on"/"off"), the keyboard's own answer to `11 14` (reported by Monitor
    # Mode), and the assumption that both are there. The query is not reliable
    # (Mountain's own service gets no answer on some systems), so a module is
    # never hidden because a query failed: only an answer that reports at
    # least one module counts, an all-zero answer is taken as "no answer".
    #: How long an answer of the keyboard counts as current.
    MODULES_TTL = 30.0
    MODULE_NAMES = ("numpad", "dock")

    @staticmethod
    def _modules_file():
        return os.path.join(_cfg().CONFIG_DIR, "everest_max_modules.json")

    def modules_setting(self):
        """{numpad, dock}: "auto", "on" (attached) or "off" (not attached)."""
        import json
        try:
            with open(self._modules_file(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        return {n: (data.get(n) if data.get(n) in ("on", "off") else "auto") for n in self.MODULE_NAMES}

    def _cmd_set_modules_setting(self, numpad=None, dock=None):
        """Store the manual override; a name that is left out keeps its value."""
        import json
        cur = self.modules_setting()
        for name, value in (("numpad", numpad), ("dock", dock)):
            if value is None:
                continue
            if value not in ("auto", "on", "off"):
                raise CommandError("modules setting: %s must be auto, on or off" % name)
            cur[name] = value
        with open(self._modules_file(), "w", encoding="utf-8") as f:
            json.dump(cur, f)
        r = self.modules()
        self.publish("modules", **{k: r[k] for k in ("numpad", "dock", "source", "known")})
        return r

    def set_modules(self, numpad, dock, now=None):
        """What the keyboard says is attached (Monitor Mode reads it from the
        answer to its `11 14` keepalive and reports it here). Publishes
        ``everest_max.modules`` when the effective answer changes."""
        now = time.monotonic() if now is None else now
        cur = (bool(numpad), bool(dock))
        if not any(cur):                       # not an answer we can trust, see above
            return self.modules(now)
        before = self.modules(now)
        with self._lock:
            self._modules = cur
            self._modules_at = now
        after = self.modules(now)
        if before != after:
            self.publish("modules", **{k: after[k] for k in ("numpad", "dock", "source", "known")})
        return after

    def modules(self, now=None):
        """{numpad, dock, source, known, setting}: what to draw. `source` is
        "setting" when a manual override decides at least one module, else
        "device" when the keyboard answered recently, else "assumed" (both).
        `known` is True when the keyboard answered."""
        now = time.monotonic() if now is None else now
        with self._lock:
            cur, at = self._modules, self._modules_at
        known = cur is not None and at is not None and now - at <= self.MODULES_TTL
        setting = self.modules_setting()
        out, manual = {}, False
        for i, name in enumerate(self.MODULE_NAMES):
            if setting[name] != "auto":
                out[name] = setting[name] == "on"
                manual = True
            else:
                out[name] = cur[i] if known else True
        out["source"] = "setting" if manual else ("device" if known else "assumed")
        out["known"] = known
        out["setting"] = setting
        return out

    # ── State ──────────────────────────────────────────────────────────────
    @property
    def style(self):
        return self._style

    @property
    def clock_format(self):
        return self._clock_format

    @property
    def main_mode(self):
        return self._main_mode

    @property
    def monitor_wanted(self):
        """Monitor Mode is on: running, or paused for a job and coming back."""
        return self._monitor_wanted

    @property
    def monitor_running(self):
        proc = self._monitor
        return proc is not None and proc.poll() is None

    @property
    def monitor_process(self):
        """The running Monitor Mode controller (a Popen), or None."""
        proc = self._monitor
        return proc if proc is not None and proc.poll() is None else None

    @staticmethod
    def _read_settings():
        cfg = _cfg()
        style = cfg.load_config()
        fmt = _read_text(os.path.join(cfg.CONFIG_DIR, "clock_format"), "24H")
        mode = _read_text(cfg.MAIN_MODE_FILE, "clock")
        return (style if style in P.STYLES else "analog", fmt,
                mode if mode in P.MAIN_MODES else "clock")

    def reload_settings(self):
        """Read clock style, clock format and main display mode from their
        files again: a restored backup changes them while the application
        runs. The screen calls it when it is built, which is when it used to
        read them itself."""
        style, fmt, mode = self._read_settings()
        with self._lock:
            clock = (style, fmt) != (self._style, self._clock_format)
            main = mode != self._main_mode
            self._style, self._clock_format, self._main_mode = style, fmt, mode
        if clock:
            self._publish_clock()
        if main:
            self.publish("main_mode", mode=mode)

    def monitor_state(self):
        with self._lock:
            return {"active": self.monitor_running,
                    "wanted": self._monitor_wanted,
                    "paused": self._session is not None,
                    "error": self._monitor_error,
                    "code": self._monitor_code}

    def status(self):
        with self._lock:
            busy = [{"id": j.id, "kind": j.kind, "state": j.state}
                    for j in self._recent.values() if j.state != "done"]
        return {"monitor": self.monitor_state(), "style": self._style,
                "clock_format": self._clock_format, "main_mode": self._main_mode,
                "buttons": self.get_buttons(), "rgb": self.rgb_settings(),
                "jobs": busy}

    def job_info(self, id):
        with self._lock:
            job = self._recent.get(id)
        if job is None:
            raise CommandError("no such job: %s" % id)
        return {"id": job.id, "kind": job.kind, "state": job.state,
                "result": job.result}

    def wait(self, job_id, timeout=None):
        """Block until a job is done and return its result (None on timeout).
        Not for the interface thread."""
        with self._lock:
            job = self._recent.get(job_id)
        if job is None or not job.done.wait(timeout):
            return None
        return job.result

    # ── Monitor Mode ───────────────────────────────────────────────────────
    def start_monitor(self, clean=False):
        """Turn Monitor Mode on. With `clean`, first end Monitor Mode
        processes left by a crash or another instance (they hold the
        keyboard), as the application does at start-up."""
        if clean:
            return self._submit("clean_start", self._job_clean_start)
        self._set_wanted(True)
        return None

    def stop_monitor(self):
        """Turn Monitor Mode off; True if it was on. Returns at once: the
        worker stops the process, and anything that needs the keyboard next
        waits until it has let go."""
        return self._set_wanted(False)

    def toggle_monitor(self):
        """Turn Monitor Mode on or off; returns whether it is on now."""
        with self._lock:
            self._poll_monitor_locked()
            want = not self._monitor_wanted
        self._set_wanted(want)
        return want

    def _job_clean_start(self, ctx):
        self.backend.kill_strays()
        ctx.sleep(0.4)
        with self._lock:
            self._monitor_wanted = True
            self._monitor_error = ""
            self._monitor_code = None
        return {"ok": True}

    def _set_wanted(self, want):
        with self._lock:
            # One that ended by itself and has not been noticed yet is not
            # running: asking for Monitor Mode must start a new one.
            died = self._poll_monitor_locked()
            was = self._monitor_wanted
            self._monitor_wanted = want
            if want:
                self._monitor_error = ""
                self._monitor_code = None
            self._ensure_worker()
            self._wake.notify_all()
        if was != want or died:
            self._publish_monitor()
        return was

    @contextlib.contextmanager
    def paused(self, timeout=None):
        """Hold the keyboard for the block: Monitor Mode is stopped (and has
        let go) and no job runs until the block ends; Monitor Mode comes back
        afterwards if it was on. Yields whether it was running.

        For a caller that runs its own controller command (the control
        socket). It blocks the calling thread until the keyboard is free, so
        it is not for the interface thread.
        """
        if threading.current_thread() is self._worker:
            yield False
            return
        entered = threading.Event()
        leave = threading.Event()
        holder = {}

        def hold(ctx):
            holder["was_running"] = ctx.fresh
            entered.set()
            leave.wait()
            return {"ok": True}
        self._submit("hold", hold)
        if not entered.wait(timeout):
            leave.set()
            raise CommandError("the keyboard is busy")
        try:
            yield holder.get("was_running", False)
        finally:
            leave.set()

    def run_command(self, args, timeout=COMMAND_TIMEOUT, env=None, settle=None):
        """Run one controller command line with the keyboard held, wait for
        it and return {ok, code, stdout, stderr}. Not for the interface
        thread.

        `settle` is (after_monitor, otherwise): seconds to give the keyboard
        before the command claims it, as an upload does (JobContext.settle).
        """
        if threading.current_thread() is self._worker:
            raise CommandError("run_command from inside a job")

        def run(ctx):
            if settle:
                ctx.settle(*settle)
            return self.backend.run(*args, timeout=timeout, env=env).as_dict()
        job_id = self._submit("command", run)
        result = self.wait(job_id)
        if result is None:
            return {"ok": False, "error": "stopped"}
        result = dict(result)
        if not result.get("error"):
            result.pop("error", None)       # the socket's reply has none then
        return result

    # ── Clock ──────────────────────────────────────────────────────────────
    def set_style(self, style):
        """Analog or digital dial: saved, then sent with a time sync."""
        if style not in P.STYLES:
            raise CommandError("style must be one of: %s" % ", ".join(P.STYLES))
        _cfg().save_config(style)
        with self._lock:
            self._style = style
        self._publish_clock()
        return self.sync_time(style)

    def sync_time(self, style=None):
        style = style or self._style
        if style not in P.STYLES:
            raise CommandError("style must be one of: %s" % ", ".join(P.STYLES))

        def run(ctx):
            r = self.backend.run(style)
            return {"ok": r.ok, "error": r.error}
        return self._submit("time_sync", run, info={"style": style})

    def set_clock_format(self, fmt):
        """24H or 12H. Monitor Mode reads it from its file every two
        seconds, so saving it is enough."""
        if fmt not in P.CLOCK_FORMATS:
            raise CommandError("format must be one of: %s" % ", ".join(P.CLOCK_FORMATS))
        with open(os.path.join(_cfg().CONFIG_DIR, "clock_format"), "w") as f:
            f.write(fmt)
        with self._lock:
            self._clock_format = fmt
        self._publish_clock()

    # ── Main display ───────────────────────────────────────────────────────
    def set_main_mode(self, mode):
        """Switch the main display (image, clock, volume, cpu, ...).

        Every mode but "image" turns Monitor Mode on afterwards, since it is
        what feeds the display; "image" and a failed switch leave it off.
        """
        if mode not in P.MAIN_MODES:
            raise CommandError("mode must be one of: %s" % ", ".join(P.MAIN_MODES))
        with self._lock:
            self._main_mode = mode
            just_uploaded = self._main_just_uploaded
            self._main_just_uploaded = False
        with open(_cfg().MAIN_MODE_FILE, "w") as f:
            f.write(mode)
        self.publish("main_mode", mode=mode)

        def run(ctx):
            if just_uploaded:
                # The keyboard is still writing the picture to its flash.
                ctx.progress(stage="waiting")
                ctx.sleep(2.0)
                ctx.fresh = False
            else:
                ctx.settle(0.8, 0.5)
            self.backend.kill_strays()
            ctx.sleep(0.3)
            r = self.backend.run("main-mode", mode)
            self._log_main_mode(r)
            if not r.ok:
                with self._lock:
                    self._monitor_wanted = False
                return {"ok": False, "error": r.error}
            if mode != "image":
                ctx.sleep(0.3)
            with self._lock:
                self._monitor_wanted = mode != "image"
                if mode != "image":
                    self._monitor_error = ""
            return {"ok": True}
        return self._submit("main_mode", run, info={"mode": mode})

    @staticmethod
    def _log_main_mode(r):
        """The controller says on stderr whether the switch was a
        read-modify-write of 11 14 or, without an answer, the fixed packet."""
        for line in (getattr(r, "stderr", "") or "").splitlines():
            if line.startswith("main-mode "):
                log.info("%s", line)

    def main_image_unchanged(self, path, frame=0, thumb=None):
        """True if the display shows this picture already (by library name)."""
        cfg = _cfg()
        stored = cfg._load_icon_last().get("main")
        resolved = thumb or cfg._compute_main_lib_hash(path, frame)
        return bool(resolved) and resolved == stored

    def upload_main_image(self, path, frame=0, thumb=None):
        """Put a picture on the main display. Returns the job id, or None
        when the display shows that picture already. Monitor Mode stays off
        afterwards (the display shows the image, not the numbers)."""
        if self.main_image_unchanged(path, frame, thumb):
            return None

        def run(ctx):
            ctx.settle(2.5, 0.5)
            with self._lock:
                need_switch = self._main_mode != "image"
                after_reset = self._after_dial_reset
                self._after_dial_reset = False
            if need_switch and not after_reset:
                with self._lock:
                    self._main_mode = "image"
                self.publish("main_mode", mode="image")
                self._log_main_mode(self.backend.run("main-mode", "image"))
                ctx.sleep(0.3)
            extras = ["--frame", str(frame)] if frame else []
            if after_reset:
                extras.append("--activate-custom")
            ok, err = False, ""
            for attempt in range(MAIN_UPLOAD_TRIES):
                if attempt > 0:
                    ctx.progress(stage="retry", attempt=attempt,
                                 tries=MAIN_UPLOAD_TRIES - 1)
                    ctx.sleep(2.0)
                    ctx.progress(0)
                r = self.backend.stream("upload-main", path, *extras,
                                        on_progress=ctx.progress, echo=True)
                ok, err = r.ok, r.error
                if ok:
                    break
            if ok:
                cfg = _cfg()
                new = thumb or cfg._save_to_main_library(path, frame)
                if new:
                    cfg._save_icon_last("main", new)
                cfg.save_main_source(path, frame)   # what a profile uploads again
            with self._lock:
                self._monitor_wanted = False
                if ok:
                    self._main_mode = "image"
                    self._main_just_uploaded = True
            if ok:
                self.publish("main_mode", mode="image")
            return {"ok": ok, "error": err}
        return self._submit("main_upload", run, info={"path": path})

    def reset_dial(self):
        """Back to the factory Mountain logo on the dial. The next main
        display upload then skips its switch to image mode."""
        def run(ctx):
            ctx.settle(2.5, 0.0)
            r = self.backend.run("reset-dial")
            with self._lock:
                self._after_dial_reset = True
            return {"ok": r.ok, "error": r.error}
        return self._submit("dial_reset", run)

    # ── The keyboard's factory reset ───────────────────────────────────────
    #: Live files of the Everest Max the factory reset deletes: the keyboard
    #: has nothing of them any more.
    FACTORY_RESET_FILES = ("everest_max_keybindings.json", "buttons.json",
                           "everest_max_dock.json", "per_key_colors.json",
                           "everest_max_main.json", "everest_max_custom_unsaved")
    FACTORY_RESET_TIMEOUT_S = 35.0      # the controller waits up to 15 s for the echo
    #: What the keyboard runs after a reset: the rainbow wave (id, speed, brightness).
    FACTORY_EFFECT = ("wave-rainbow", 100, 100)

    def factory_reset(self):
        """The keyboard's own factory reset (ResetFlash). On success the
        application's files, caches and key listener match the keyboard's
        factory state. Returns the job id."""
        if self._present is False:
            raise CommandError("the keyboard is not connected", code="not_connected")

        def run(ctx):
            ctx.settle(2.5, 0.0)
            r = self.backend.run("factory-reset", timeout=self.FACTORY_RESET_TIMEOUT_S)
            # Even without the echo the keyboard may have reset: not in custom mode then.
            self._in_custom = None
            if r.ok:
                try:
                    self._after_factory_reset()
                except Exception:
                    log.exception("everest_max: after the factory reset")
            self.publish("factory_reset", ok=r.ok, error=r.error)
            return {"ok": r.ok, "error": r.error}
        return self._submit("factory_reset", run)

    def _after_factory_reset(self):
        cfg = _cfg()
        for name in self.FACTORY_RESET_FILES:
            try:
                os.remove(os.path.join(cfg.CONFIG_DIR, name))
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning("could not remove %s: %s", name, e)
        eid, speed, brightness = self.FACTORY_EFFECT
        cfg.save_rgb_settings({
            "effect": P.effect_info(eid)["name"], "speed": speed,
            "brightness": brightness, "color1": [255, 0, 0], "color2": [0, 0, 255],
            "direction": P.WAVE_DIRECTIONS[0][0],
        })
        self._set_lighting("effect")
        last = cfg._load_icon_last()
        for slot in ("0", "1", "2", "3", "main"):
            last.pop(slot, None)
        try:
            with open(cfg.ICON_LAST_FILE, "w") as f:
                json.dump(last, f, indent=2)
        except OSError as e:
            log.warning("could not save icon_last: %s", e)
        with self._lock:
            self._in_custom = None
            self._dock_settings = None
            self._board_settings = None
            self._keymap_dirty.clear()
            self._profile_pending = None
            self._after_dial_reset = True       # the dial shows the logo now
        self._sync_macro_bindings()
        self._update_trigger()
        self.publish("keybindings_changed", keys=self.keybindings()["keys"], key=None)
        self.publish("buttons_changed", buttons=cfg.load_buttons(), button=None)
        self.publish("dock_buttons_changed", buttons=self.get_dock_buttons(), button=None)
        self.publish("rgb_applied", job=None, kind="effect", ok=True, error="",
                     effect=self.FACTORY_EFFECT[0])

    # ── D1-D4 ──────────────────────────────────────────────────────────────
    def get_buttons(self):
        """The four D-keys: [{icon, type, action, ...}] from buttons.json."""
        return _cfg().load_buttons()

    def set_button(self, button, type=None, action=None, strip=True):
        """Save what a D-key does. Monitor Mode reads buttons.json every two
        seconds, so saving is enough for it to take effect.

        The control socket's set_key saves the action as it was sent
        (strip=False), as it always did."""
        if not (isinstance(button, int) and 0 <= button <= 3):
            raise CommandError("button must be 0..3 (D1..D4)")
        cfg = _cfg()
        buttons = cfg.load_buttons()
        if action is not None:
            buttons[button]["action"] = action.strip() if strip else action
        if type is not None:
            buttons[button]["type"] = type
        cfg.save_buttons(buttons)
        self.publish("buttons_changed", buttons=buttons, button=button)
        return buttons

    # ── Media dock buttons ─────────────────────────────────────────────────
    # The dock's four media buttons and the dial's two scroll directions can be
    # bound to the same actions as D1-D4. The type "none" keeps the button's
    # own media function. Detecting the presses is NOT done here yet (the USB
    # report that carries them is still being researched): whatever detects a
    # press calls `on_dock_button(name)`.
    DOCK_BUTTON_NAMES = tuple(b[0] for b in layout.DOCK_BUTTONS) + tuple(b[0] for b in layout.DOCK_SCROLL)
    #: Types a dock button cannot have (they only make sense on a pad with pages).
    DOCK_REFUSED_TYPES = ("page", "set_key")

    @staticmethod
    def _dock_file():
        return os.path.join(_cfg().CONFIG_DIR, "everest_max_dock.json")

    def get_dock_buttons(self):
        """{name: {type, action}} for every dock element; unset ones are "none"."""
        import json
        try:
            with open(self._dock_file(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        out = {}
        for name in self.DOCK_BUTTON_NAMES:
            e = data.get(name)
            e = e if isinstance(e, dict) else {}
            t = e.get("type")
            t = t if isinstance(t, str) and t else "none"
            a = e.get("action")
            out[name] = {"type": t, "action": a if isinstance(a, str) and t != "none" else ""}
        return out

    def set_dock_button(self, name, type=None, action=None):
        """Save what a dock element does; a part that is left out keeps its value."""
        import json
        if name not in self.DOCK_BUTTON_NAMES:
            raise CommandError("dock button must be one of: %s" % ", ".join(self.DOCK_BUTTON_NAMES))
        if type in self.DOCK_REFUSED_TYPES:
            raise CommandError("a dock button cannot have the type %r" % type)
        buttons = self.get_dock_buttons()
        cur = buttons[name]
        if type is not None:
            cur["type"] = str(type) or "none"
        if action is not None:
            cur["action"] = str(action).strip()
        if cur["type"] == "none":
            cur["action"] = ""
        with open(self._dock_file(), "w", encoding="utf-8") as f:
            json.dump(buttons, f)
        self.publish("dock_buttons_changed", buttons=buttons, button=name)
        return buttons

    def on_dock_button(self, name):
        """HOOK for press detection: a dock button was pressed (or the dial
        turned: scroll_l / scroll_r). Runs the binding through the shared
        action dispatcher, as MacroPad keys do. Returns the dispatcher's
        result code, or "none" when the button keeps its media function."""
        if name not in self.DOCK_BUTTON_NAMES:
            raise CommandError("unknown dock button %r" % (name,))
        b = self.get_dock_buttons()[name]
        if b["type"] == "none":
            return "none"
        self.publish("dock_button", name=name)
        return self._dispatcher().run(b["type"], b["action"], owner=self.name)

    def _dispatcher(self):
        macros = self.app.service("macros")
        actions = getattr(macros, "actions", None)
        if actions is not None:
            return actions
        if getattr(self, "_own_actions", None) is None:
            from core.actions import ActionDispatcher
            self._own_actions = ActionDispatcher()
        return self._own_actions

    def reset_buttons_flash(self):
        """Write every D-key's action into the keyboard's flash again."""
        def run(ctx):
            ctx.sleep(0.5)
            ctx.fresh = False
            r = self.backend.run("reset-buttons")
            return {"ok": r.ok, "error": r.error}
        return self._submit("reset_buttons", run)

    def upload_dbutton(self, button, path, frame=0, thumb=None):
        """Put a picture on a D-key (45-60 s of flash writing), with the
        key's text drawn on when it has one (set_dkey_text). Skipped, without
        touching the keyboard, when the key shows it already."""
        if not (isinstance(button, int) and 0 <= button <= 3):
            raise CommandError("button must be 0..3 (D1..D4)")

        def run(ctx):
            cfg = _cfg()
            up_path, up_frame, up_thumb = path, frame, thumb
            # The picture without the text, kept for the next change of the text.
            source = cfg._save_to_library(path, frame) if path else None
            text = self._dkey_entry(button).get("text")
            if text or not path:
                up_path = self._compose_dkey(button, self._library_file(source), text)
                up_frame, up_thumb = 0, None
            resolved = up_thumb or cfg._compute_lib_hash(up_path, up_frame)
            if resolved and resolved == cfg._load_icon_last().get(str(button)):
                self._set_dkey_entry(button, picture=source)
                return {"ok": True, "skipped": True}
            ctx.progress(stage="uploading")
            ctx.settle(2.5, 0.5)
            args = ["upload", str(button), up_path]
            if up_frame:
                args += ["--frame", str(up_frame)]
            r = self.backend.stream(*args, on_progress=ctx.progress)
            if r.ok:
                self._set_dkey_entry(button, picture=source)
                new = up_thumb or cfg._save_to_library(up_path, up_frame)
                if new:
                    cfg._save_icon_last(button, new)
                # Whatever a plugin put there is gone; its next picture has
                # to be written even if it is the one it sent before.
                with self._dpush_lock:
                    self._dpush_sent.pop(button, None)
            return {"ok": r.ok, "error": r.error}
        return self._submit("dbutton_upload", run, info={"button": button})

    def push_dbutton_image(self, idx, pil_image):
        """Put a picture on D1-D4 (idx 0-3) for a plugin. Thread-safe and
        returns at once; whether the picture was taken.

        The keys keep their pictures in the keyboard's flash, so every one is
        an erase and a rewrite of two sectors, and Monitor Mode has to let go
        of the keyboard for it: measured on an Everest Max, 45 to 60 seconds
        per picture, the same as uploading one by hand. Base Camp for Windows
        does the same for its Spotify covers, and only when the song changes.
        So here as well: a picture the key already shows is not written
        again, pictures that arrive while one is being written go in the same
        pause, and per key only the latest is written.
        """
        if not (isinstance(idx, int) and 0 <= idx <= 3):
            return False
        try:
            from PIL import Image
            img = pil_image.convert("RGB").resize((P.ICON_W, P.ICON_H),
                                                  Image.LANCZOS)
        except Exception:
            return False
        digest = hashlib.sha1(img.tobytes()).hexdigest()
        path = os.path.join(_cfg().CONFIG_DIR, f"everest_plugin_d{idx + 1}.png")
        with self._dpush_lock:
            on_key = self._dpush_inflight.get(idx, self._dpush_sent.get(idx))
            if digest == on_key:
                self._dpush_pending.pop(idx, None)
                return True
            queued = self._dpush_pending.get(idx)
            if queued and queued[1] == digest:
                return True
            # Replaced whole, so an upload reading the previous picture keeps
            # reading the previous picture.
            tmp = path + ".tmp"
            img.save(tmp, "PNG")
            os.replace(tmp, path)
            self._dpush_pending[idx] = (path, digest)
            if self._dpush_busy:
                return True
            self._dpush_busy = True
        try:
            self._submit("dbutton_push", self._job_dpush)
        except Exception:
            with self._dpush_lock:
                self._dpush_busy = False
            return False
        return True

    def _job_dpush(self, ctx):
        # The controller hands the keyboard back on its way out; the manual
        # upload gives it the same moment before claiming it again.
        ctx.settle(2.5, 0.5)
        uploaded = []
        while True:
            with self._dpush_lock:
                batch = dict(self._dpush_pending)
                self._dpush_pending.clear()
                self._dpush_inflight = {i: d for i, (_f, d) in batch.items()}
                if not batch:
                    # A push that arrives from now on queues a job of its own;
                    # the worker keeps the pause for it if it is in time.
                    self._dpush_busy = False
                    return {"ok": True, "buttons": uploaded}
            for idx, (path, digest) in sorted(batch.items()):
                r = self.backend.run("upload", str(idx), path,
                                     timeout=DBUTTON_PUSH_TIMEOUT)
                with self._dpush_lock:
                    self._dpush_inflight.pop(idx, None)
                    if r.ok:
                        self._dpush_sent[idx] = digest
                    else:
                        self._dpush_sent.pop(idx, None)
                if r.ok:
                    uploaded.append(idx)
                    # The key shows the plugin's picture now, not the library
                    # one on record: uploading that again must not be refused
                    # as unchanged.
                    _cfg()._save_icon_last(idx, None)
                else:
                    print(f"[Everest] D{idx + 1}: plugin picture not uploaded: "
                          f"{r.error}", flush=True)

    # ── Lighting ───────────────────────────────────────────────────────────
    def rgb_settings(self):
        """The saved effect settings, as ids and values.

        rgb_settings.json stores the effect's name and the direction's label
        (what 3.x reads); an id stored there is accepted too.
        """
        saved = _cfg().load_rgb_settings()
        out = {"effect": "static", "speed": 50, "brightness": 100,
               "color1": (255, 0, 0), "color2": (0, 0, 255), "direction": 0}
        info = P.effect_info(saved.get("effect"))
        if info:
            out["effect"] = info["id"]
        for key in ("speed", "brightness"):
            if key in saved:
                out[key] = saved[key]
        for key in ("color1", "color2"):
            c = saved.get(key)
            if isinstance(c, (list, tuple)) and len(c) == 3:
                out[key] = tuple(c)
        if saved.get("direction") in P.DIRECTION_VALUES:
            out["direction"] = P.DIRECTION_VALUES[saved["direction"]]
        out["direction_label"] = P.DIRECTION_LABELS.get(out["direction"],
                                                       P.WAVE_DIRECTIONS[0][0])
        return out

    def apply_rgb(self, effect, speed=50, brightness=100, color1=(255, 0, 0),
                  color2=(0, 0, 255), direction=0):
        """Send an effect; saved to rgb_settings.json once the keyboard took it."""
        info = P.effect_info(effect)
        if info is None:
            raise CommandError("unknown effect: %s" % effect)
        try:
            c1, c2 = P.parse_color(color1), P.parse_color(color2)
            speed, brightness = int(speed), int(brightness)
        except (TypeError, ValueError) as e:
            raise CommandError(str(e)) from None
        if isinstance(direction, str):
            if direction not in P.DIRECTION_VALUES:
                raise CommandError("unknown direction: %s" % direction)
            direction = P.DIRECTION_VALUES[direction]
        direction = int(direction)
        eid = info["id"]

        def run(ctx):
            r = self.backend.run(*P.rgb_args(eid, speed, brightness, c1, c2,
                                             direction))
            self._in_custom = None          # an effect now (or not known)
            if r.ok:
                self._set_lighting("effect")
                _cfg().save_rgb_settings({
                    "effect": info["name"], "speed": speed,
                    "brightness": brightness, "color1": list(c1),
                    "color2": list(c2),
                    "direction": P.DIRECTION_LABELS.get(
                        direction, P.WAVE_DIRECTIONS[0][0]),
                })
            self.publish("rgb_applied", job=ctx.job.id, kind="effect",
                         ok=r.ok, error=r.error, effect=eid)
            return {"ok": r.ok, "error": r.error}
        return self._submit("rgb", run, info={"effect": eid})

    @staticmethod
    def _key_effects(effects, effect_params, brightness):
        """(effects, effect_params) checked: a name of P.PER_KEY_EFFECT_IDS
        per key (126) and {name: P.effect_settings()}. ValueError otherwise."""
        if not isinstance(effects, (list, tuple)) or len(effects) > P.NUM_LEDS:
            raise ValueError("effects must be a list of up to %d effect names" % P.NUM_LEDS)
        names = [e if e else "static" for e in effects]
        bad = sorted({e for e in names if e not in P.PER_KEY_EFFECT_IDS}, key=str)
        if bad:
            raise ValueError("unknown per-key effect: %s" % ", ".join(map(str, bad)))
        names += ["static"] * (P.NUM_LEDS - len(names))
        if effect_params is not None and not isinstance(effect_params, dict):
            raise ValueError("effect_params must be an object")
        params = {name: P.effect_settings(name, p, brightness)
                  for name, p in (effect_params or {}).items()}
        return names, params

    def apply_per_key(self, leds, side=None, brightness=100, persist=False,
                      effects=None, effect_params=None):
        """Send per-key colours (126 keys, 45 ring LEDs); with `persist`,
        also commit them to the keyboard's custom slot. Saved to
        per_key_colors.json: always for a plain apply, only on success for
        a persist (as the editor always did).

        `effects` gives each key an effect of its own (P.PER_KEY_EFFECTS,
        a name per colour index) and `effect_params` the settings of each
        effect ({name: {effect, speed, brightness, color1, color2,
        direction}}, shared by every key with that effect). The keyboard
        only runs them from its saved custom slot, so a key with an effect
        other than static always saves it. Without effects (or all static)
        the keyboard gets exactly what it always got.

        Every write carries the whole picture, so a newer one replaces a
        waiting (not yet running) write: an editor that writes at once
        (Individuell, ~300 ms after the last change) never builds up a
        queue, and the newest state wins. A persist replaces waiting plain
        writes too (it shows the same picture and saves it); a plain write
        replaces only waiting plain writes, never a waiting persist (the
        save would be lost). The editor shows plain writes and saves once
        when it is done. Monitor Mode is
        paused and given back as for any job."""
        leds = [tuple(c) for c in leds]
        side = None if side is None else [tuple(c) for c in side]
        brightness = int(brightness)
        if effects is not None:
            effects, effect_params = self._key_effects(effects, effect_params, brightness)
        uses = effects is not None and P.has_key_effects(effects)
        payload = (P.per_key_payload(leds, side, brightness, effects, effect_params)
                   if uses else P.per_key_payload(leds, side, brightness))

        def run(ctx):
            args = ["per-key-rgb", payload] + (["--persist"] if persist else [])
            if self._in_custom:
                args.append("--in-custom")      # one mode packet, not two (no extra blink)
            r = self.backend.run(*args)
            self._in_custom = True if r.ok else None
            if r.ok:
                self._set_lighting("custom")
                self._mark_unsaved(not persist)
            if r.ok or not persist:
                _cfg()._save_per_key(leds, side or [], brightness, effects, effect_params)
            self.publish("rgb_applied", job=ctx.job.id,
                         kind="persist" if persist else "per_key",
                         ok=r.ok, error=r.error)
            return {"ok": r.ok, "error": r.error}
        info = {"persist": bool(persist)}
        if uses:
            info["effects"] = True
        with self._lock:
            for j in [j for j in self._jobs
                      if j.kind == "per_key" and (persist or not j.info["persist"])]:
                self._jobs.remove(j)
                self._finish(j, {"ok": False, "skipped": True, "replaced": True},
                             publish=False)
            return self._submit("per_key", run, info=info)

    # Individuell shows its changes with plain writes and saves the picture
    # into the keyboard's custom slot once when it is done. A marker file
    # remembers a picture the slot does not have yet (the app quit or the
    # keyboard was unplugged before the save): it is saved when the keyboard
    # is there again.
    def _unsaved_path(self):
        return os.path.join(_cfg().CONFIG_DIR, "everest_max_custom_unsaved")

    def _mark_unsaved(self, unsaved):
        path = self._unsaved_path()
        try:
            if unsaved:
                if not os.path.exists(path):
                    with open(path, "w"):
                        pass
            else:
                os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("custom slot marker: %s", e)

    def _save_unsaved(self):
        """The keyboard is there: save a picture its slot is missing."""
        if self._lighting != "custom" or not os.path.exists(self._unsaved_path()):
            return
        pk = self.per_key()
        self.apply_per_key(pk["leds"], pk["side"], pk["brightness"], persist=True,
                           effects=pk["effects"], effect_params=pk["effect_params"])

    def per_key(self):
        """The saved per-key colours and effects, for an editor to start
        from: {leds: [[r, g, b] x 126], side: [[r, g, b] x 45], brightness,
        effects: [name x 126], effect_params: {name: settings and the
        direction's label}}."""
        cfg = _cfg()
        leds, side, brightness = cfg._load_per_key()
        effects, raw_params = cfg._load_per_key_effects()
        effects = [e if e in P.PER_KEY_EFFECT_IDS else "static" for e in effects]
        params = {}
        for name, p in raw_params.items():
            try:
                params[name] = P.effect_settings(name, p, brightness)
            except (TypeError, ValueError):
                continue                # a hand-edited entry: the defaults then
            info = P.effect_info(params[name]["effect"])
            # The label an editor's direction list shows (rgb_settings does the same).
            params[name]["direction_label"] = (
                P.DIRECTION_LABELS.get(params[name]["direction"], "")
                if info and info["direction"] else "")
        return {"leds": [list(c) for c in leds], "side": [list(c) for c in side],
                "brightness": brightness, "effects": effects, "effect_params": params}

    def pictures(self):
        """What the keyboard shows, as files of the picture libraries a
        screen can load as thumbnails: {dbuttons: [path or None x 4], main:
        path or None}. None where nothing is on record (never uploaded, or
        a plugin put its own picture there)."""
        cfg = _cfg()
        last = cfg._load_icon_last()

        def find(folder, name):
            path = os.path.join(folder, name) if isinstance(name, str) else None
            return path if path and os.path.isfile(path) else None
        return {"dbuttons": [find(cfg.ICON_LIBRARY_DIR, last.get(str(i)))
                             for i in range(4)],
                "main": find(cfg.MAIN_LIBRARY_DIR, last.get("main"))}

    def preview_per_key(self, leds, side=None, brightness=100):
        """Show per-key colours while they are being edited (the brightness
        slider). Not saved, does not pause Monitor Mode: while it runs the
        keyboard is not free and the preview is skipped. Only the latest of
        several waiting previews is sent."""
        payload = P.per_key_payload([tuple(c) for c in leds],
                                    None if side is None else [tuple(c) for c in side],
                                    int(brightness))

        def run(ctx):
            if self._monitor is not None:
                return {"ok": False, "skipped": True}
            args = ["per-key-rgb", payload] + (["--in-custom"] if self._in_custom else [])
            r = self.backend.run(*args)
            self._in_custom = True if r.ok else None
            return {"ok": r.ok, "error": r.error}
        with self._lock:
            for j in [j for j in self._jobs if j.kind == "per_key_preview"]:
                self._jobs.remove(j)
                self._finish(j, {"ok": False, "skipped": True}, publish=False)
        return self._submit("per_key_preview", run, keyboard=False)

    # ── Media dock settings (the Display Dial page) ────────────────────────
    # The dial's shown menus, menu colour, screensaver and turn-off, and the
    # displays' brightness ("Displays synchronisieren" in Individuell), live in
    # the keyboard's FW_EXTEND_INFO (`11 14`). Writing is read-modify-write
    # in one controller run (`dock-settings --set`): it reads the 29 bytes,
    # changes only what was asked and writes them back, and writes nothing
    # when the keyboard does not answer the read. Results: `job_done` with
    # `settings`, and `everest_max.dock_settings {settings}`.

    def dock_settings(self, refresh=True):
        """{settings: as last read (None before the first read), job: the id
        of a read that was started (None with refresh=False)}."""
        with self._lock:
            cached = self._dock_settings
        job = self._submit("dock_read", self._job_dock_read) if refresh else None
        return {"settings": cached, "job": job}

    def _job_dock_read(self, ctx):
        ctx.settle(0.5)
        return self._dock_result(self.backend.run("dock-settings", timeout=COMMAND_TIMEOUT))

    @staticmethod
    def _dock_change(menus=None, color=None, screensaver=None, turn_off=None,
                     displays=None, content=None):
        """The changes in the controller's shape, checked; CommandError for
        anything else. Seconds are clamped to 1..3600, a display brightness
        to 0..100 %."""
        out = {}
        if menus is not None:
            if not isinstance(menus, dict) or not menus:
                raise CommandError("menus must be an object of {name: bool}")
            bad = [n for n in menus if n not in P.DOCK_MENUS]
            if bad:
                raise CommandError("unknown dial menu: %s (known: %s)"
                                   % (", ".join(map(str, bad)), ", ".join(P.DOCK_MENUS)))
            if not all(isinstance(v, bool) for v in menus.values()):
                raise CommandError("menus: every value must be true or false")
            out["menus"] = dict(menus)
        if color is not None:
            try:
                out["color"] = "#" + P.hex_color(P.parse_color(color))
            except (TypeError, ValueError):
                raise CommandError("color must be #rrggbb") from None
        for key, spec in (("screensaver", screensaver), ("turn_off", turn_off)):
            if spec is None:
                continue
            if not isinstance(spec, dict) or not ({"on", "seconds"} & set(spec)):
                raise CommandError("%s must be {on: bool, seconds}" % key)
            s = {}
            if spec.get("on") is not None:
                if not isinstance(spec["on"], bool):
                    raise CommandError("%s.on must be true or false" % key)
                s["on"] = spec["on"]
            if spec.get("seconds") is not None:
                try:
                    s["seconds"] = P.clamp_dock_seconds(spec["seconds"])
                except (TypeError, ValueError):
                    raise CommandError("%s.seconds must be a number" % key) from None
            out[key] = s
        if displays is not None:
            # "Displays synchronisieren": {synced: true}, or {synced: false,
            # percent} for a brightness of their own (P.displays_byte).
            if not isinstance(displays, dict) or not isinstance(displays.get("synced"), bool):
                raise CommandError("displays must be {synced: bool, percent}")
            if displays["synced"]:
                out["displays"] = {"synced": True}
            else:
                try:
                    pct = max(0, min(100, int(displays.get("percent"))))
                except (TypeError, ValueError):
                    raise CommandError("displays.percent must be a number") from None
                out["displays"] = {"synced": False, "percent": pct}
        if content is not None:
            if content not in P.DOCK_CONTENT:
                raise CommandError("unknown screensaver content: %s (known: %s)"
                                   % (content, ", ".join(P.DOCK_CONTENT)))
            out["content"] = content
        if not out:
            raise CommandError("nothing to change")
        return out

    def set_dock_settings(self, menus=None, color=None, screensaver=None, turn_off=None,
                          displays=None, content=None):
        """Change some of the dock's settings; the rest stays as the keyboard
        has it. Returns the job id."""
        change = self._dock_change(menus, color, screensaver, turn_off, displays, content)
        import json

        def run(ctx):
            ctx.settle(0.5)
            r = self.backend.run("dock-settings", "--set", json.dumps(change),
                                 timeout=COMMAND_TIMEOUT)
            return self._dock_result(r)
        return self._submit("dock_write", run, info={"change": sorted(change)})

    def _dock_result(self, r):
        """A controller's `dock-settings` run as a job result."""
        import json
        if not r.ok:
            return {"ok": False, "error": r.error or "the keyboard did not answer"}
        settings = None
        for line in reversed((r.stdout or "").strip().splitlines()):
            try:
                data = json.loads(line)
                settings = P.dock_settings_of(bytes.fromhex(data["raw"]))
            except (ValueError, KeyError, TypeError):
                continue
            break
        if settings is None:
            return {"ok": False, "error": "no dock settings in the controller's answer"}
        with self._lock:
            self._dock_settings = settings
        self.publish("dock_settings", settings=settings)
        return {"ok": True, "settings": settings}

    # ── Settings page: game mode and indicator LEDs ────────────────────────
    # Base Camp's Einstellungen page. Read-modify-write in one controller run
    # (`board-settings [--set]`), like the dock's settings: a write changes
    # only the keys asked for and nothing is written when the keyboard does
    # not answer the read. Results: `job_done` with `settings`, and
    # `everest_max.board_settings {settings}` where settings is
    # {game_mode: {shift_tab, alt_f4, win, alt_tab: bool}, game_mode_mask,
    # leds: bool}.

    def board_settings(self, refresh=True):
        """{settings: as last read (None before the first read), job: the id
        of a read that was started (None with refresh=False)}."""
        with self._lock:
            cached = self._board_settings
        job = self._submit("board_read", self._job_board_read) if refresh else None
        return {"settings": cached, "job": job}

    def _job_board_read(self, ctx):
        ctx.settle(0.5)
        return self._board_result(self.backend.run("board-settings", timeout=COMMAND_TIMEOUT))

    @staticmethod
    def _board_change(game_mode=None, leds=None):
        """The changes in the controller's shape, checked; CommandError for
        anything else."""
        out = {}
        if game_mode is not None:
            if not isinstance(game_mode, dict) or not game_mode:
                raise CommandError("game_mode must be an object of {key: bool}")
            bad = [k for k in game_mode if k not in P.GAME_MODE_KEYS]
            if bad:
                raise CommandError("unknown game mode key: %s (known: %s)"
                                   % (", ".join(map(str, bad)), ", ".join(P.GAME_MODE_KEYS)))
            if not all(isinstance(v, bool) for v in game_mode.values()):
                raise CommandError("game_mode: every value must be true or false")
            out["game_mode"] = dict(game_mode)
        if leds is not None:
            if not isinstance(leds, bool):
                raise CommandError("leds must be true or false")
            out["leds"] = leds
        if not out:
            raise CommandError("nothing to change")
        return out

    def set_board_settings(self, game_mode=None, leds=None):
        """Change the game mode keys and/or the indicator LEDs; the rest stays
        as the keyboard has it. Returns the job id."""
        change = self._board_change(game_mode, leds)
        import json

        def run(ctx):
            ctx.settle(0.5)
            r = self.backend.run("board-settings", "--set", json.dumps(change),
                                 timeout=COMMAND_TIMEOUT)
            return self._board_result(r)
        return self._submit("board_write", run, info={"change": sorted(change)})

    def _board_result(self, r):
        """A controller's `board-settings` run as a job result."""
        import json
        if not r.ok:
            return {"ok": False, "error": r.error or "the keyboard did not answer"}
        settings = None
        for line in reversed((r.stdout or "").strip().splitlines()):
            try:
                data = json.loads(line)
                mask = int(data["game_mode"])
                settings = {"game_mode": P.game_mode_of(mask), "game_mode_mask": mask,
                            "leds": bool(data["leds"])}
            except (ValueError, KeyError, TypeError):
                continue
            break
        if settings is None:
            return {"ok": False, "error": "no settings in the controller's answer"}
        with self._lock:
            self._board_settings = settings
        self.publish("board_settings", settings=settings)
        return {"ok": True, "settings": settings}

    # ── Firmware versions ──────────────────────────────────────────────────
    # Read over the feature reports the image uploads use, so never during
    # one: the worker runs one job at a time, and a read right after an
    # upload is refused while the keyboard may still be writing its flash.
    VERSION_KEYS = ("keyboard", "bootloader", "dock", "numpad")
    UPLOAD_KINDS = ("main_upload", "dbutton_upload", "dbutton_push")
    #: After an upload the keyboard keeps writing its flash (about 32 s).
    UPLOAD_QUIET_S = 35.0

    def _upload_busy(self):
        """True while an image upload is queued or running, or ended less than
        UPLOAD_QUIET_S ago."""
        with self._lock:
            pending = any(j.kind in self.UPLOAD_KINDS and j.state != "done"
                          for j in list(self._recent.values()))
            end = self._upload_end
        return pending or (end is not None and time.monotonic() - end < self.UPLOAD_QUIET_S)

    def versions(self, refresh=False):
        """{keyboard, bootloader, dock, numpad} as last read (None where
        unknown) with {job, busy}. Starts a read when none was made yet or
        `refresh` asks for one, unless an image upload is busy (busy: True,
        job: None)."""
        with self._lock:
            cached = dict(self._versions) if self._versions else None
        out = dict(cached) if cached else {k: None for k in self.VERSION_KEYS}
        busy = self._upload_busy()
        job = None
        if (refresh or cached is None) and not busy:
            job = self._submit("versions", self._job_versions)
        out.update(job=job, busy=busy)
        return out

    def _job_versions(self, ctx):
        # Jobs run one at a time, so no upload runs now; one that just ended
        # may still be writing the keyboard's flash.
        with self._lock:
            end = self._upload_end
        if end is not None and time.monotonic() - end < self.UPLOAD_QUIET_S:
            return {"ok": False, "busy": True,
                    "error": "an image upload just ended; read the versions again in a minute"}
        ctx.settle(0.5)
        r = self.backend.run("versions", timeout=COMMAND_TIMEOUT)
        if not r.ok:
            return {"ok": False, "error": r.error}
        import json
        data = None
        for line in reversed((r.stdout or "").strip().splitlines()):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                break
            data = None
        if data is None:
            return {"ok": False, "error": "no versions in the controller's answer"}
        found = {}
        for k in self.VERSION_KEYS:
            v = data.get(k)
            found[k] = P.version_text(v) if isinstance(v, int) and not isinstance(v, bool) else None
        # A module the keyboard says is not attached has no version to show.
        mods = self.modules()
        for name in ("dock", "numpad"):
            if mods.get(name) is False:
                found[name] = None
        with self._lock:
            self._versions = dict(found)
        self.publish("versions", **found)
        return dict(found, ok=True)

    # ── Meters ─────────────────────────────────────────────────────────────
    def meters(self):
        """{cpu, ram, disk, net_mbs, volume}: what Monitor Mode sends."""
        return self._meters.sample()

    def watch_volume(self):
        watch_volume()

    # ── Key binding (Tastenbelegung) ───────────────────────────────────────
    # Every key can be bound like in Base Camp (keybindings.py says how each
    # function is done). The bindings live in everest_max_keybindings.json
    # and are written to the keyboard by the controller's `remap` verb in the
    # "keymap" job; changes queued while that job waits go with it. The
    # keyboard gets them all again when it is plugged in and when the
    # application starts (set_present), and the software trigger runs while a
    # key sends an F13-F24 for the application. Events: `keybindings_changed
    # {keys, key}`, `key_action {fkey, function, result}`.
    DKEY_IDS = layout.DKEY_IDS
    DOCK_IDS = {kid: name for name, kid in layout.DOCK_KEY_IDS.items()}

    @staticmethod
    def _keymap_file():
        return os.path.join(_cfg().CONFIG_DIR, KB.FILE_NAME)

    def _keymap(self):
        return KB.load(self._keymap_file())

    def key_functions(self):
        """The catalogue for an editor: the functions in the original's order
        with their choices (`app`: run by the application), the modifiers of
        a key combination, how many keys can run something in the
        application, the key numbers of D1-D4 and the dock's buttons."""
        return {"functions": [{"id": f, "choices": [c for c, _code in KB.CHOICES.get(f, ())],
                               "app": f in KB.APP_FUNCTIONS} for f in KB.FUNCTIONS],
                "app_choices": {"os": list(KB.OS_ACTIONS)},
                "modifiers": [m for m, _bit in P.SHORTCUT_MODS],
                "fkeys": len(KB.TRIGGER_FKEYS),
                "dkeys": list(self.DKEY_IDS),
                "dock": dict(layout.DOCK_KEY_IDS)}

    def keybindings(self):
        """{keys: {key number: binding}, dkeys: {0-3: {text}}, listener,
        free_fkeys}. D1-D4 and the dock's buttons show their action from
        buttons.json / everest_max_dock.json unless a firmware function is
        set. A macro written into the keyboard is marked `in_keyboard`."""
        data = self._keymap()
        keys = {}
        for kid, b in data["keys"].items():
            v = dict(b)
            if b["function"] == "macro" and b.get("fkey") is None:
                v["in_keyboard"] = True
            keys[str(kid)] = v
        buttons = self.get_buttons()
        for i, kid in enumerate(self.DKEY_IDS):
            if kid not in data["keys"] and i < len(buttons):
                b = KB.from_action(buttons[i].get("type"), buttons[i].get("action", ""))
                if b:
                    keys[str(kid)] = b
        dock = self.get_dock_buttons()
        for kid, name in self.DOCK_IDS.items():
            if kid not in data["keys"]:
                b = KB.from_action(dock[name]["type"], dock[name]["action"])
                if b:
                    keys[str(kid)] = b
        return {"keys": keys,
                "dkeys": {str(i): {"text": e.get("text")} for i, e in data["dkeys"].items()},
                "listener": self.listener_state(),
                "free_fkeys": len(KB.TRIGGER_FKEYS) - len(KB.used_fkeys(data["keys"]) & set(KB.TRIGGER_FKEYS))}

    def _plugin_types(self):
        pm = getattr(self.app.service("plugins"), "manager", None)
        try:
            return {tid for tid, _label in pm.get_action_type_labels()} if pm is not None else set()
        except Exception:
            return set()

    def _hw_macro(self, binding):
        """{times, events} of a macro binding the keyboard can run by itself,
        else None (keymacro.py)."""
        if not binding or binding.get("function") != "macro" or not binding.get("value"):
            return None
        macros = self.app.service("macros")
        macro = macros.get(binding["value"]) if macros is not None else None
        if macro is None:
            return None
        kb = self._layout_info(legends=True)
        return keymacro.hardware_macro(macro, kb.get("legends"), kb.get("shape", "ansi"),
                                       keyboard_layouts.DEAD_KEYS.get(kb.get("id"), ""))

    def _entries(self, kid, keys):
        """The remap entries that make the keyboard do what `keys` says for
        key `kid`."""
        b = keys.get(kid)
        macro = self._hw_macro(b) if b and b.get("fkey") is None else None
        return KB.remap_entries(kid, b, macro)

    def set_keybinding(self, kid, binding):
        """Bind key `kid` (its SDK number, layout.KEY_IDS) as `binding` says
        ({function, value, mods}, keybindings.normalize). Saved at once; the
        keyboard is written in the "keymap" job when its state changes.
        Returns {keys, listener, free_fkeys, job}."""
        if kid not in layout.bindable_ids():
            raise CommandError("no key with the number %r" % (kid,))
        own = kid in self.DKEY_IDS or kid in self.DOCK_IDS
        try:
            b = KB.normalize(binding, self._plugin_types(), own=own)
        except ValueError as e:
            raise CommandError(str(e)) from None
        with self._keymap_lock:
            data = self._keymap()
            old = dict(data["keys"])
            if own and KB.runs_in_app(b):
                # An action of D1-D4 or a dock button stays where its press
                # detection reads it; only a firmware function is kept here.
                keys, changed = {k: v for k, v in old.items() if k != kid}, [kid]
            else:
                needs_fkey = not own and KB.runs_in_app(b) and self._hw_macro(b) is None
                try:
                    keys, changed = KB.place(old, kid, b, needs_fkey=needs_fkey)
                except KB.NoFreeFKey as e:
                    raise CommandError(str(e), code="no_free_fkey") from None
            data["keys"] = keys
            KB.save(self._keymap_file(), data)
        if own:
            t, a = KB.action_of(b) if KB.runs_in_app(b) else ("none", "")
            if kid in self.DKEY_IDS:
                self.set_button(self.DKEY_IDS.index(kid), type=t, action=a)
            else:
                self.set_dock_button(self.DOCK_IDS[kid], type=t, action=a)
        # Typing a URL changes no packet: only what the keyboard must change is written.
        dirty = [k for k in changed if self._entries(k, old) != self._entries(k, keys)]
        if dirty and self._present is False:
            with self._lock:            # written by set_present(True), with the saved ones
                self._keymap_dirty.update(dirty)
        job = self._queue_keymap(dirty) if dirty and self._present is not False else None
        self._update_trigger()
        view = self.keybindings()
        self.publish("keybindings_changed", keys=view["keys"], key=kid)
        return {"keys": view["keys"], "listener": view["listener"], "free_fkeys": view["free_fkeys"],
                "job": job}

    def _queue_keymap(self, kids):
        """Write the remaps of `kids` in the "keymap" job; one that is queued
        and not running yet takes them along. Returns its id."""
        with self._lock:
            self._keymap_dirty.update(kids)
            if self._keymap_job is not None:
                return self._keymap_job
            self._keymap_job = self._submit("keymap", self._job_keymap)
            return self._keymap_job

    def _job_keymap(self, ctx):
        import json
        with self._lock:
            kids = sorted(self._keymap_dirty)
            self._keymap_dirty.clear()
            self._keymap_job = None
        keys = self._keymap()["keys"]
        entries = [e for k in kids for e in self._entries(k, keys)]
        if not entries:
            return {"ok": True, "skipped": True}
        ctx.settle(0.5)
        r = self.backend.run("remap", json.dumps(entries), timeout=COMMAND_TIMEOUT)
        if not r.ok:
            with self._lock:            # tried again when the keyboard comes back
                self._keymap_dirty.update(kids)
        return {"ok": r.ok, "error": r.error, "keys": kids}

    def _sync_macro_bindings(self, macro_id=None):
        """Keys bound to macro `macro_id` (every macro for None) after the
        macro changed: written into the keyboard or run through an F-key,
        whichever the macro allows now. Returns those keys."""
        touched = []
        with self._keymap_lock:
            data = self._keymap()
            keys = data["keys"]
            for kid, b in list(keys.items()):
                if b["function"] != "macro" or (macro_id and b.get("value") != macro_id):
                    continue
                touched.append(kid)
                in_keyboard = self._hw_macro(b) is not None
                if in_keyboard == (b.get("fkey") is None):
                    continue
                try:
                    keys, _changed = KB.place(keys, kid, b, needs_fkey=not in_keyboard)
                except KB.NoFreeFKey:
                    log.warning("key %d: its macro needs the application now, but "
                                "F13-F24 are all in use", kid)
            if touched:
                data["keys"] = keys
                KB.save(self._keymap_file(), data)
        return touched

    def _on_macros_changed(self, _topic, payload):
        touched = self._sync_macro_bindings((payload or {}).get("id"))
        if not touched:
            return
        if self._present is not False:
            self._queue_keymap(touched)
        self._update_trigger()
        self.publish("keybindings_changed", keys=self.keybindings()["keys"], key=None)

    # ── Device profiles (core/profiles.py) ─────────────────────────────────
    LIGHTING_FILE = "everest_max_lighting"
    #: Keys of dock_settings() that set_dock_settings takes back.
    DOCK_STATE_KEYS = ("menus", "color", "screensaver", "turn_off", "displays", "content")
    PROFILE_READ_S = 8.0        # capture_profile_state waits this long for the keyboard

    def _read_lighting(self):
        kind = _read_text(os.path.join(_cfg().CONFIG_DIR, self.LIGHTING_FILE), "effect")
        return kind if kind in ("effect", "custom") else "effect"

    def _set_lighting(self, kind):
        """Remember which RGB state the keyboard is in, in memory and in the
        live file, so a restart (and a profile) knows it."""
        with self._lock:
            self._lighting = kind
        try:
            with open(os.path.join(_cfg().CONFIG_DIR, self.LIGHTING_FILE), "w") as f:
                f.write(kind)
        except OSError as e:
            log.warning("could not save the lighting kind: %s", e)

    def profile_files(self):
        return ["style", "clock_format", "main_display_mode", "buttons.json",
                "everest_max_dock.json", "everest_max_keybindings.json",
                "rgb_settings.json", "per_key_colors.json", "everest_max_main.json",
                self.LIGHTING_FILE]

    def profile_picture_refs(self, folder):
        """Library pictures of the D-keys and the main display's source file
        that the profile in `folder` names."""
        refs = []

        def add(path):
            if path and path not in refs:
                refs.append(path)
        try:
            for n, entry in sorted(KB.load(os.path.join(folder, KB.FILE_NAME))["dkeys"].items()):
                add(self._library_file(entry.get("picture")))
            with open(os.path.join(folder, "buttons.json"), encoding="utf-8") as f:
                for entry in json.load(f):
                    for v in (entry.values() if isinstance(entry, dict) else ()):
                        if isinstance(v, str) and v:
                            add(self._library_file(os.path.basename(v)))
            src = _cfg().load_main_source(folder)
            if src:
                add(src[0])
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return refs

    def capture_profile_state(self):
        out = {"lighting": self._lighting}
        try:
            with self._lock:
                pending = self._profile_pending
            if pending:         # written when the keyboard is plugged in: that is the state
                dock, board = pending["state"].get("dock"), pending["state"].get("board")
            else:
                if self._present is not False and not self._upload_busy():
                    jobs = [self.dock_settings(True)["job"], self.board_settings(True)["job"]]
                    end = time.monotonic() + self.PROFILE_READ_S
                    for job in jobs:
                        self.wait(job, max(0.1, end - time.monotonic()))
                with self._lock:
                    dock, board = self._dock_settings, self._board_settings
            if isinstance(dock, dict):
                dock = {k: copy.deepcopy(dock[k]) for k in self.DOCK_STATE_KEYS
                        if dock.get(k) is not None}
                if dock:
                    out["dock"] = dock
            if isinstance(board, dict):
                board = {k: copy.deepcopy(board[k]) for k in ("game_mode", "leds")
                         if board.get(k) is not None}
                if board:
                    out["board"] = board
        except Exception:
            log.exception("everest_max: capture_profile_state")
        return out

    def factory_profile_state(self):
        return {"lighting": "effect",
                "dock": {"menus": {name: True for name in P.DOCK_MENUS},
                         "color": "#f0cc20", "content": "clock_analog",
                         "screensaver": {"on": True, "seconds": 30},
                         "turn_off": {"on": True, "seconds": 30},
                         "displays": {"synced": True}},
                "board": {"game_mode": {k: False for k in P.GAME_MODE_KEYS},
                          "leds": False}}

    def apply_profile(self, state, previous_folder):
        try:
            self._apply_profile(state if isinstance(state, dict) else {}, previous_folder)
        except Exception:
            log.exception("everest_max: apply_profile")

    def _apply_profile(self, state, previous_folder):
        with self._lock:
            old_mode, old_style = self._main_mode, self._style
            pending = self._profile_pending
        if pending:         # nothing was written since: the keyboard still has the old ones
            old_mode, old_style = pending["mode"], pending["style"]
        self.reload_settings()
        self._in_custom = None
        lighting = state.get("lighting")
        self._set_lighting(lighting if lighting in ("effect", "custom") else self._read_lighting())
        # Keys the outgoing profile bound and the new one does not go back to
        # Standard: the remap job gets the union.
        before = (KB.load(os.path.join(previous_folder, KB.FILE_NAME))["keys"]
                  if previous_folder else {})
        with self._keymap_lock:
            after = self._keymap()["keys"]
        kids = sorted(set(before) | set(after))
        if self._present is False:
            with self._lock:        # written by set_present(True)
                self._keymap_dirty.update(kids)
                self._profile_pending = {"state": copy.deepcopy(state),
                                         "mode": old_mode, "style": old_style}
            self._sync_macro_bindings()
            self._update_trigger()
            return
        with self._lock:
            self._profile_pending = None
        if kids:
            self._queue_keymap(kids)
        self._sync_macro_bindings()
        self._update_trigger()
        self._profile_write(state, old_mode, old_style)

    def _profile_write(self, state, old_mode, old_style):
        """Everything but the key remaps, in the order of the spec; the
        pictures last, as ordinary queued jobs."""
        cfg = _cfg()
        try:
            if self._lighting == "custom":
                pk = self.per_key()
                params = {n: {k: v for k, v in p.items() if k != "direction_label"}
                          for n, p in pk["effect_params"].items()}
                self.apply_per_key(pk["leds"], pk["side"], pk["brightness"], persist=True,
                                   effects=pk["effects"], effect_params=params)
            else:
                r = self.rgb_settings()
                self.apply_rgb(r["effect"], r["speed"], r["brightness"],
                               r["color1"], r["color2"], r["direction"])
        except (CommandError, ValueError, TypeError) as e:
            log.warning("profile: lighting not applied: %s", e)
        for name, fn, keys in (("dock", self.set_dock_settings, self.DOCK_STATE_KEYS),
                               ("board", self.set_board_settings, ("game_mode", "leds"))):
            values = state.get(name)
            if not isinstance(values, dict):
                continue
            values = {k: values[k] for k in keys if values.get(k) is not None}
            if not values:
                continue
            try:
                fn(**values)
            except CommandError as e:
                log.warning("profile: %s settings not applied: %s", name, e)
        if self._main_mode != old_mode:
            self.set_main_mode(self._main_mode)
        if self._style != old_style:
            self.sync_time()
        self.reset_buttons_flash()
        last = cfg._load_icon_last()
        dkeys = self._keymap()["dkeys"]
        for n in range(4):
            entry = dkeys.get(n) or {}
            pic = entry.get("picture")
            path = self._library_file(pic)
            if not path:
                continue            # nothing to put there (a key cannot be cleared)
            if not entry.get("text") and last.get(str(n)) == pic:
                continue            # the key shows it already
            try:
                self.upload_dbutton(n, path)
            except CommandError as e:
                log.warning("profile: D%d picture not queued: %s", n + 1, e)
        if self._main_mode == "image":
            src = cfg.load_main_source()
            if src:
                try:
                    self.upload_main_image(src[0], src[1])    # None: shown already
                except CommandError as e:
                    log.warning("profile: main picture not queued: %s", e)

    # Presence (core/devices/registry.wire_presence).
    def set_present(self, present):
        """The keyboard was plugged in (or the application started with it)
        or unplugged: the bindings are written again, the trigger follows."""
        present = bool(present)
        if present == self._present:
            return
        self._present = present
        self._in_custom = None                  # what a replugged keyboard shows is not known
        if present:
            self._sync_macro_bindings()
            keys = self._keymap()["keys"]
            with self._lock:
                waiting = bool(self._keymap_dirty)
            if keys or waiting:
                self._queue_keymap(list(keys))
            pending, self._profile_pending = self._profile_pending, None
            if pending:             # a profile was switched while it was unplugged
                self._profile_write(pending["state"], pending["mode"], pending["style"])
            else:
                self._save_unsaved()
        self._update_trigger()

    # The software trigger (keytrigger.py).
    def _needs_trigger(self):
        return any(b.get("fkey") is not None for b in self._keymap()["keys"].values())

    def listener_state(self):
        """{state: keytrigger.STATES, needed: some key sends an F-key for the
        application}."""
        t = self._trigger
        return {"state": t.state if t is not None else "off", "needed": self._needs_trigger()}

    def _update_trigger(self):
        # Called from the command thread, the macros' events and presence: the
        # lock keeps two callers from starting two triggers (every F-key would
        # then run its action twice).
        with self._trigger_lock:
            want = self._present is not False and self._needs_trigger()
            if want and self._trigger is None and self.key_trigger_factory is not None:
                self._trigger = self.key_trigger_factory()
                self._trigger.start(lambda fkey: self.app.scheduler.call_soon(self._on_fkey, fkey))
                return
            if want:
                return
        self._stop_trigger()

    def _stop_trigger(self):
        with self._trigger_lock:
            t, self._trigger = self._trigger, None
        if t is not None:
            t.stop()

    def _on_fkey(self, fkey):
        """The keyboard sent F13-F24 `fkey`: run what the key that sends it is
        bound to. Returns the dispatcher's result code, or None."""
        b = next((b for b in self._keymap()["keys"].values() if b.get("fkey") == fkey), None)
        if b is None:
            return None
        t, value = KB.action_of(b)
        result = self._dispatcher().run(t, value, owner=self.name)
        self.publish("key_action", fkey=fkey, function=b["function"], result=result)
        return result

    # Text over the pictures of D1-D4 (the "T" menu, as on the DisplayPad).
    def _dkey_entry(self, button):
        return dict(self._keymap()["dkeys"].get(button) or {})

    def _set_dkey_entry(self, button, **fields):
        """Change fields of a D-key's entry; None takes a field away."""
        with self._keymap_lock:
            data = self._keymap()
            e = dict(data["dkeys"].get(button) or {})
            for k, v in fields.items():
                if v:
                    e[k] = v
                else:
                    e.pop(k, None)
            data["dkeys"][button] = e
            KB.save(self._keymap_file(), data)

    @staticmethod
    def _library_file(name):
        path = os.path.join(_cfg().ICON_LIBRARY_DIR, name) if name else None
        return path if path and os.path.isfile(path) else None

    @staticmethod
    def _compose_dkey(button, source, text):
        """D-key `button`'s picture (a black tile without one) with `text`
        drawn on as on the DisplayPad, saved in the configuration folder."""
        from PIL import Image
        from core.devices.displaypad import images as dpi
        img = None
        if source:
            try:
                img = Image.open(source).convert("RGB").resize((P.ICON_W, P.ICON_H), Image.LANCZOS)
            except Exception:
                img = None
        if img is None:
            img = Image.new("RGB", (P.ICON_W, P.ICON_H), (0, 0, 0))
        dpi.draw_text_overlay(img, text)
        out = os.path.join(_cfg().CONFIG_DIR, "everest_dkey_text_d%d.png" % (button + 1))
        img.save(out + ".tmp", "PNG")
        os.replace(out + ".tmp", out)
        return out

    def set_dkey_text(self, button, text):
        """Text over D-key `button`'s picture ({text, pos, font, size_pt,
        bold, italic, underline, color}, as the DisplayPad's; None takes it
        away). The key is written again with it (45-60 s). Returns {text,
        job}; job None when the key has neither picture nor text."""
        from core.devices.displaypad import images as dpi
        if not (isinstance(button, int) and 0 <= button <= 3):
            raise CommandError("button must be 0..3 (D1..D4)")
        try:
            cfg_text = dpi.clean_text_overlay(text)
        except ValueError as e:
            raise CommandError("set_dkey_text: %s" % e) from None
        entry = self._dkey_entry(button)
        source = self._library_file(entry.get("picture"))
        if source is None:
            # A picture put there before the text was known: it is the one without text.
            source = self.pictures()["dbuttons"][button]
        self._set_dkey_entry(button, text=cfg_text)
        job = self.upload_dbutton(button, source) if source or cfg_text else None
        self.publish("keybindings_changed", keys=self.keybindings()["keys"], key=self.DKEY_IDS[button])
        return {"text": cfg_text, "job": job}

    # ── Command wrappers (keyword arguments from the wire) ─────────────────
    def _cmd_set_keybinding(self, key, function, value=None, mods=None):
        try:
            kid = int(key)
        except (TypeError, ValueError):
            raise CommandError("key must be a key number") from None
        b = {"function": function, "value": value}
        if mods is not None:
            b["mods"] = mods
        return self.set_keybinding(kid, b)

    def _cmd_set_dkey_text(self, button, text=None):
        try:
            button = int(button)
        except (TypeError, ValueError):
            raise CommandError("button must be 0..3 (D1..D4)") from None
        return self.set_dkey_text(button, text)

    def _cmd_upload_main(self, path, frame=0):
        if not os.path.isfile(path):
            raise CommandError("no such file: %s" % path)
        job = self.upload_main_image(path, int(frame))
        return {"job": job, "skipped": job is None}

    def _cmd_upload_dbutton(self, button, path, frame=0):
        if not os.path.isfile(path):
            raise CommandError("no such file: %s" % path)
        return {"job": self.upload_dbutton(int(button), path, int(frame))}

    def _cmd_set_button(self, button, type=None, action=None):
        try:
            button = int(button)
        except (TypeError, ValueError):
            raise CommandError("button must be 0..3 (D1..D4)") from None
        return {"buttons": self.set_button(button, type, action)}

    def _cmd_set_dock_button(self, name, type=None, action=None):
        return {"buttons": self.set_dock_button(name, type, action)}

    def _cmd_apply_rgb(self, effect, speed=50, brightness=100, color1="ff0000",
                       color2="0000ff", direction=0):
        return {"job": self.apply_rgb(effect, speed, brightness, color1, color2,
                                      direction)}

    def _cmd_preview_per_key(self, leds, side=None, brightness=100):
        try:
            if (not isinstance(leds, (list, tuple)) or len(leds) != P.NUM_LEDS
                    or (side is not None and (not isinstance(side, (list, tuple))
                                              or len(side) != P.NUM_SIDE_LEDS))):
                raise ValueError("leds must be %d colours and side %d"
                                 % (P.NUM_LEDS, P.NUM_SIDE_LEDS))
            return {"job": self.preview_per_key(leds, side, brightness)}
        except (TypeError, ValueError) as e:
            raise CommandError(str(e)) from None

    def _cmd_apply_per_key(self, leds, side=None, brightness=100, persist=False,
                           effects=None, effect_params=None):
        try:
            return {"job": self.apply_per_key(leds, side, brightness, bool(persist),
                                              effects, effect_params)}
        except (TypeError, ValueError) as e:
            raise CommandError(str(e)) from None

    def _cmd_set_dock_settings(self, menus=None, color=None, screensaver=None,
                               turn_off=None, displays=None, content=None):
        return {"job": self.set_dock_settings(menus, color, screensaver, turn_off,
                                              displays, content)}

    def _cmd_set_board_settings(self, game_mode=None, leds=None):
        return {"job": self.set_board_settings(game_mode, leds)}

    # ── The worker ─────────────────────────────────────────────────────────
    def _submit(self, kind, fn, keyboard=True, info=None):
        with self._lock:
            if self._closing:
                raise CommandError("the application is shutting down")
            self._seq += 1
            job = Job(self._seq, kind, fn, keyboard, info)
            self._jobs.append(job)
            self._recent[job.id] = job
            while len(self._recent) > RECENT_JOBS:
                self._recent.popitem(last=False)
            self._ensure_worker()
            self._wake.notify_all()
        return job.id

    def _ensure_worker(self):
        if self._closing:
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work,
                                            name="everest_max-worker",
                                            daemon=True)
            self._worker.start()

    def _work(self):
        while True:
            events = []
            with self._lock:
                while True:
                    if self._closing:
                        return
                    if self._poll_monitor_locked():
                        events.append(self._monitor_state_locked())
                    if self._jobs:
                        job = self._jobs.popleft()
                        break
                    if self._reconcile_due_locked():
                        job = None
                        break
                    if events:
                        job = None
                        break
                    self._wake.wait(POLL_S)
            for state in events:
                self.publish("monitor", **state)
            try:
                if job is not None:
                    self._execute(job)
                else:
                    with self._lock:
                        due = self._reconcile_due_locked()
                    if due:
                        self._reconcile()
            except Exception:
                log.exception("Everest Max worker")

    def _execute(self, job):
        if job.keyboard:
            if self._session is None:
                self._begin_session()
            session = self._session
            fresh = session["fresh"]
        else:
            session, fresh = None, False
        job.state = "running"
        ctx = JobContext(self, job, fresh)
        self.publish("job_started", **self._job_payload(job))
        try:
            result = job.fn(ctx)
        except Exception as e:
            log.exception("Everest Max job %s failed", job.kind)
            result = {"ok": False, "error": str(e) or type(e).__name__}
        if not isinstance(result, dict):
            result = {"ok": True}
        result.setdefault("ok", True)
        result.setdefault("error", "")
        if job.kind in self.UPLOAD_KINDS and not result.get("skipped"):
            with self._lock:
                self._upload_end = time.monotonic()     # see _job_versions
        if session is not None:
            session["fresh"] = ctx.fresh and bool(result.get("skipped"))
        self._finish(job, result)
        if session is not None:
            with self._lock:
                more = any(j.keyboard for j in self._jobs) and not self._closing
            if not more:
                self._end_session()

    def _finish(self, job, result, publish=True):
        job.result = result
        job.state = "done"
        if publish:
            payload = self._job_payload(job)
            for k, v in result.items():
                payload.setdefault(k, v)
            self.publish("job_done", **payload)
        job.done.set()

    @staticmethod
    def _job_payload(job):
        payload = dict(job.info)
        payload.update(id=job.id, kind=job.kind)
        return payload

    def _begin_session(self):
        """Take the keyboard from Monitor Mode (worker thread)."""
        with self._lock:
            proc = self._monitor
            self._monitor = None
            self._session = {"fresh": False}
            died = proc is not None and proc.poll() is not None
            if died:
                # It ended by itself before we got to it: not coming back.
                self._monitor_wanted = False
                self._monitor_code = proc.returncode
        fresh = False
        if proc is not None and not died:
            try:
                proc.terminate()
            except Exception:
                pass
            fresh = True
        self._publish_monitor()
        if proc is not None and not died:
            wait_for_exit(proc)
        self._wait_exiting()
        self._session["fresh"] = fresh

    def _end_session(self):
        with self._lock:
            self._session = None
        self._publish_monitor()

    def _reconcile_due_locked(self):
        if self._session is not None or self._closing:
            return False
        if self._monitor_wanted:
            return self._monitor is None
        return self._monitor is not None

    def _reconcile(self):
        """Make Monitor Mode follow its wanted state (worker thread)."""
        with self._lock:
            want = self._monitor_wanted
            proc = self._monitor
            if not want and proc is not None:
                self._monitor = None
                self._exiting.append(proc)
        if not want:
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:
                    pass
                self._publish_monitor()
            return
        if proc is not None:
            return
        self._wait_exiting()
        with self._lock:
            if (not self._monitor_wanted or self._monitor is not None
                    or self._session is not None or self._closing):
                return
            style = self._style
        try:
            proc = self.backend.spawn_monitor(
                style, os.path.join(_cfg().CONFIG_DIR, "controller_error.log"))
        except Exception as e:
            with self._lock:
                self._monitor_wanted = False
                self._monitor_error = str(e) or type(e).__name__
            self._publish_monitor()
            return
        with self._lock:
            self._monitor = proc
            self._monitor_error = ""
            self._monitor_code = None
        self._publish_monitor()

    def _poll_monitor_locked(self):
        """Notice a Monitor Mode that ended by itself. True if it did."""
        self._exiting = [p for p in self._exiting if p.poll() is None]
        proc = self._monitor
        if proc is None or proc.poll() is None:
            return False
        self._monitor = None
        self._monitor_wanted = False
        self._monitor_code = proc.returncode
        return True

    def _wait_exiting(self):
        with self._lock:
            procs = list(self._exiting)
        for proc in procs:
            wait_for_exit(proc)
        with self._lock:
            self._exiting = [p for p in self._exiting if p not in procs]

    def _monitor_state_locked(self):
        return {"active": self.monitor_running, "wanted": self._monitor_wanted,
                "paused": self._session is not None,
                "error": self._monitor_error, "code": self._monitor_code}

    def _publish_monitor(self):
        self.publish("monitor", **self.monitor_state())

    def _publish_clock(self):
        self.publish("clock", style=self._style, format=self._clock_format)
