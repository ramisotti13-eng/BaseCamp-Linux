"""DisplayPadService: the Mountain DisplayPad, from the USB bus to the pages.

It owns the page model (pages.py), the action runner (actions.py) and the
device: the monitor that notices the pad come and go, the device worker that
holds the pad while idle (widget frames and key presses), the upload worker
that sends a page and runs its GIFs, the retries, the brightness, and the
settings. An interface shows what the events say and asks through the
methods and commands; under the CustomTkinter interface that is
devices/displaypad/panel.py.

Threads (the rule of the whole core, here in detail): every method below
that is not a worker runs on the core scheduler, one at a time, which under
the CustomTkinter interface is the Tk main loop itself. That is what the
panel's logic always relied on: plain flags (uploading, animating,
page_switch_waiting) set before an upload is scheduled, read by the workers
without a lock. The three workers (_monitor_loop, _plugin_upload_worker,
_worker) touch only those flags, the queue, the USB lock and the events, and
hand everything else to the scheduler with call_soon(). Widget plugins push
frames and write into the model's image maps from their own threads, as
they always have.

Moved here from devices/displaypad/panel.py (4.0), method by method and
unchanged in what it does, with one exception: the brightness is written
under the USB lock, by whoever holds the pad (it used to open interface 3 a
second time beside the worker, #26-style).

Events (topic displaypad.<name>), all JSON:
  device_connected {has_content}   device_disconnected {busy}   quirk {}
  upload_started {}   upload_progress {key, n, total}   animating {count}
  upload_finished {ok, error}   upload_empty {}
  key_frame {idx}                  a widget's frame arrived for a key
  settings_changed {rotation, brightness, debounce, min_ms}
  page_switched {page, prev, show_hints}
  key_changed {page, idx, origin}  pages_changed {origin}
  page_cleared {page, origin}      page_deleted {page}
  hints_show {page, name, note, keys, show}   hints_hide {}
  power_changed {auto_off, lock, locked, sleeping, dark, screen, watching}

The clock and PC-info keys (live.py), the auto-off and the lock screen
(power.py) run on the scheduler too. A live key's picture goes the way a
widget's frame goes; the lock screen holds the pad through `screen_tiles`:
while it is set, start_upload() sends those twelve pictures instead of the
page, the device worker drops every queued frame, and a key press only
reaches PadPower.

`origin` is whatever the caller of a change passed: an interface that has
already shown its own change passes its own and leaves those events alone.
"""
import os
import queue
import threading
import time

from core.commands import CommandError
from core.service import Service
from core.devices.displaypad import images as dpi
from core.devices.displaypad import library as dplib
from core.devices.displaypad import protocol
from core.devices.displaypad.actions import (
    ActionRunner, DispatcherActionEnv, parse_set_key,
    action_type_entries, label_key, SECONDARY_TYPES, SEPARATOR,
)
from core.devices.displaypad.live import (
    LiveKeys, render_startup_logo as dpl_render_startup_logo, save_png, split_screen)
from core.devices.displaypad.pages import PageModel
from core.devices.displaypad.power import LOCK_MODES, PadPower
import shared.config as _cfg
from shared.config import (
    FRAME_DIR,
    _load_displaypad_page_hints, _save_displaypad_page_hints,
    _save_hint_box_settings, _save_to_dp_fs_library,
    _save_to_dp_library, _save_displaypad_buttons, _save_displaypad_fullscreen,
    _load_displaypad_rotation, _save_displaypad_rotation,
    _load_displaypad_brightness, _save_displaypad_brightness,
    _load_displaypad_debounce, _save_displaypad_debounce,
    _load_displaypad_min_ms, _save_displaypad_min_ms,
)

NUM_KEYS = protocol.NUM_KEYS

# Set BASECAMP_PAGE_DEBUG=1 in the environment to trace page-switch/upload
# decisions (also toggles matching trace lines in shared/plugins.py and
# page-bound widget plugins). Off by default.
_PAGE_DEBUG = os.environ.get("BASECAMP_PAGE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(msg):
    if _PAGE_DEBUG:
        print(msg, flush=True)


class DisplayPadService(Service):
    name = "displaypad"

    UPLOAD_RETRIES = 5

    def __init__(self, app, transport=None, env=None, translate=None,
                 plugin_types=None, res_path=None, clock=None, metrics=None,
                 session_events=None):
        super().__init__(app)
        # Where the pad is reached: core/devices/displaypad/protocol.py, or
        # anything with its names (a test's stand-in pad; the panel hands in
        # its own module during the move, so the tests' patches there run).
        self.transport = transport or protocol
        self.model = PageModel(translate=translate, plugin_types=plugin_types,
                               res_path=res_path)
        # What a key does that is not the pad's own (a page, set_key) runs
        # through the application's one key action dispatcher.
        self.runner = ActionRunner(
            self.model, app.scheduler, env or DispatcherActionEnv(self._dispatcher),
            switch_page=self._runner_switch, set_key=self._runner_set_key)
        self._own_actions = None

        # Hooks an interface fills in (attach_host).
        self.sync_services_hook = None  # (page) -> None: widget services for a page
        self.quirk_check = None         # () -> None: replaces maybe_warn_usb_quirk

        # Settings, as stored.
        self.rotation = _load_displaypad_rotation()
        self.brightness = _load_displaypad_brightness()
        self.debounce = _load_displaypad_debounce()
        # Minimum milliseconds per GIF frame, as typed (#73): parsed when an
        # upload starts, as the entry it came from always was.
        self.min_ms = str(_load_displaypad_min_ms())
        self.min_frame_ms = 50

        # Session state. Plain flags, read by the workers without a lock and
        # set on the scheduler before an upload is scheduled (see the module
        # docstring).
        self.uploading = False
        self.animating = False
        self.startup_busy = False  # the start-up picture is being written
        self._upload_deferred = False   # a start_upload waits for the running worker
        self.panel_shown = False   # the panel holds a fullscreen picture (its gaps need blanking)
        # Set by a page switch while it waits for the device worker to yield
        # the pad; cleared in _finish(), or where a switch has nothing to send.
        self.page_switch_waiting = False
        self.device_present = False
        # hidraw path of interface 3 for the currently-present pad. A quick
        # unplug/replug re-enumerates the pad under a new path without the
        # presence flag ever toggling within one poll window; comparing paths
        # catches that so we still re-init (issue #44).
        self.dp_path = None
        # True only after a successful INIT on the current connection. Plugin
        # image uploads wait for this so they don't stream pixels to a pad that
        # has enumerated but not finished booting (the "mountain logo not shown
        # yet" state that timed out the first plugin upload, issue #43).
        self.pad_ready = False
        # One-time hint when the DisplayPad is on the USB bus but its command
        # interface (3) never enumerates — the usbhid interface-order quirk
        # FransM hit on Ubuntu/Mint (issue #36).
        self.warned_quirk = False
        self.anim_thread = None
        self.anim_stop = threading.Event()
        self.upload_queue = queue.Queue()
        # Serialises every USB session (manual upload, animation, plugin worker)
        # so two of them can never hold interface 1/3 at once. Without this the
        # check-and-set on the uploading/animating bools races between the
        # scheduler and worker threads and a second open_interfaces() hits the
        # kernel while the first still owns the device -> [Errno 16] Resource
        # busy (#26).
        self.usb_lock = threading.Lock()
        # Set whenever the device worker is NOT holding interface 3, so a
        # manual upload/animation can wait for it to free up before opening.
        self.key_released = threading.Event()
        self.key_released.set()
        self.plugin_worker_stop = threading.Event()
        self.monitor_stop = threading.Event()
        # Set the moment the application starts going away, so a worker
        # about to open the device does not do it into an interpreter that
        # is already tearing down: libusb aborts the process on that
        # ("libusb_ref_device: Assertion `refcnt >= 2' failed").
        self.closing = threading.Event()
        self.last_plugin_error = None
        self.svc_sync_handle = None      # pending widget service sync (#97)
        self.hint_pages_quiet = set()    # pages told to stay quiet this session (#93)
        # A brightness waiting to be written by whoever holds the pad.
        self._brightness_lock = threading.Lock()
        self._pending_brightness = None

        self._running = False
        self._threads = []

        # Live keys, auto-off and the lock screen (P2-DPLIVE). `clock` (wall
        # time), `metrics` (a core.metrics.SystemSampler) and
        # `session_events` (() -> a core.platform.session.SessionEvents)
        # are seams for the tests.
        self.screen_tiles = None    # {key: tile path} while the lock screen holds the pad
        self._live_unsubs = []
        now = clock or time.time
        self.live = LiveKeys(self.model, app.scheduler, self._push_live_frame,
                             now=now, sampler=metrics)
        if session_events is None:
            def session_events():
                from core.platform.session import session_events as make
                return make(app.scheduler)
        self.power = PadPower(app.scheduler, on_dark=self._power_dark,
                              on_screen=self._power_screen,
                              on_change=self._power_changed, live=self.live,
                              events=session_events, now=now,
                              res_path=self.model.res_path)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        # Not straight away: an interface built after the core started hands
        # in its hooks first (attach_host), and that is what starts the pad.
        # Without one, this runs the pad on its own.
        self.app.scheduler.call_soon(self._ensure_running)

    def attach_host(self, transport=None, translate=None, plugin_types=None,
                    env=None, sync_services=None, quirk_check=None, start=True):
        """An interface takes the pad: what it hands in replaces the
        defaults, and the pad starts if it has not yet. `start=False` only
        hands them in (core.bootstrap, before the core starts; start() then
        starts the pad)."""
        if transport is not None:
            self.transport = transport
        if translate is not None:
            self.model.translate = translate
        if plugin_types is not None:
            self.model.plugin_types = plugin_types
        if env is not None:
            self.runner.env = env
        self.sync_services_hook = sync_services
        self.quirk_check = quirk_check
        if start:
            self._ensure_running()

    def _ensure_running(self):
        """Load the pages and start the pad, once."""
        if self._running or self.closing.is_set():
            return
        self._running = True
        # Every page from the configuration, old ones brought up to date
        # (#30, #95, #111), with Main on the pad.
        self.model.load()
        self._start_live()
        if self.model.images or self.model.gif_frames:
            self.app.scheduler.call_later(200, self.start_upload)
        # The device presence monitor and the persistent device worker (owns
        # interface 1/3: plugin uploads AND key-event listening). Their names
        # say what runs in them.
        for target in (self._monitor_loop, self._plugin_upload_worker):
            t = threading.Thread(target=target, daemon=True,
                                 name="displaypad.%s" % target.__name__)
            t.start()
            self._threads.append(t)
        # Arm the auto-timeout for the start page, if it has one (#45).
        self.app.scheduler.call_later(
            800, lambda: self.runner.arm_page_timeout(self.model.current_page))

    def _start_live(self):
        """Main's clock and PC-info keys drawn (before Main is sent), the
        live keys following the page from here on, the auto-off armed and
        the session watched if a lock screen is set."""
        self.live.refresh(push=False)
        for topic in ("page_switched", "key_changed", "page_cleared"):
            self._live_unsubs.append(self.bus.subscribe(
                "displaypad." + topic, self._follow_for_live))
        self.power.start()

    @property
    def running(self):
        return self._running

    def _dispatcher(self):
        """The shared key action dispatcher: the macro service's, else one of
        this service's own (no macro service installed)."""
        actions = getattr(self.app.service("macros"), "actions", None)
        if actions is not None:
            return actions
        if self._own_actions is None:
            from core.actions import ActionDispatcher
            self._own_actions = ActionDispatcher()
        return self._own_actions

    def stop(self):
        """What the application did for the pad on its way out: no worker
        opens the pad from here on, the loops end, an animation stops, and
        the USB lock is taken once as proof that nobody is inside libusb
        any more (tearing down while one is aborts the process)."""
        self.closing.set()
        self.plugin_worker_stop.set()
        self.monitor_stop.set()
        self.anim_stop.set()
        self.runner.cancel_double_clicks()
        if self.runner.timeout_handle is not None:
            self.app.scheduler.cancel(self.runner.timeout_handle)
            self.runner.timeout_handle = None
        # The live keys' timer, the auto-off, the lock clock, the session
        # watcher and its processes.
        for unsub in self._live_unsubs:
            unsub()
        self._live_unsubs = []
        self.live.stop()
        self.power.stop()
        if self.usb_lock.acquire(timeout=3.0):
            self.usb_lock.release()

    # ── Commands ──────────────────────────────────────────────────────────────

    def commands(self):
        return {
            "status": self.status,
            "list_pages": self.list_pages,
            "keys": self._cmd_keys,
            "switch_page": self._cmd_switch_page,
            "press_key": self._cmd_press_key,
            "set_action": self._cmd_set_action,
            "set_image": self._cmd_set_image,
            "set_key_text": self._cmd_set_key_text,
            "fonts": self._cmd_fonts,
            "library": self.library,
            "action_types": self.action_types,
            "clear_key": self._cmd_clear_key,
            "clear_page": self._cmd_clear_page,
            "set_fullscreen": self._cmd_set_fullscreen,
            "clear_fullscreen": self._cmd_clear_fullscreen,
            "create_page": self._cmd_create_page,
            "rename_page": self._cmd_rename_page,
            "delete_page": self._cmd_delete_page,
            "page_references": self._cmd_page_references,
            "set_page_timeout": self._cmd_set_page_timeout,
            "upload": self._cmd_upload,
            "set_brightness": self._cmd_set_brightness,
            "set_rotation": self._cmd_set_rotation,
            "set_debounce": self._cmd_set_debounce,
            "set_min_frame_ms": self._cmd_set_min_frame_ms,
            "power": self._cmd_power,
            "set_auto_off": self._cmd_set_auto_off,
            "set_lock_screen": self._cmd_set_lock_screen,
            "preview_lock_screen": self._cmd_preview_lock_screen,
            "startup_logo": self._cmd_startup_logo,
            "set_startup_picture": self._cmd_set_startup_picture,
        }

    def _on_scheduler(self, fn, *args, timeout=5.0):
        """Run `fn` on the scheduler and return what it returned: at once
        when called there, else posted and waited for."""
        if self.app.scheduler.in_scheduler_thread():
            return fn(*args)
        done = threading.Event()
        box = {}

        def run():
            try:
                box["result"] = fn(*args)
            except Exception as e:       # handed back to the caller
                box["error"] = e
            finally:
                done.set()
        self.app.scheduler.call_soon(run)
        if not done.wait(timeout):
            raise CommandError("the DisplayPad did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def status(self):
        """The pad and its session, for a script or an interface."""
        return {"present": self.device_present, "ready": self.pad_ready,
                "busy": self.busy, "uploading": self.uploading,
                "animating": self.animating,
                "page": self.model.current_page, "prev_page": self.model.prev_page,
                "settings": self.settings(), "power": self.power.state()}

    def settings(self):
        return {"rotation": self.rotation, "brightness": self.brightness,
                "debounce": self.debounce, "min_ms": self.min_ms}

    @property
    def busy(self):
        """An upload or an animation holds the pad."""
        return bool(self.uploading or self.animating)

    def _cmd_upload(self):
        self._on_scheduler(self.start_upload)

    def _cmd_set_brightness(self, percent):
        try:
            pct = int(percent)
        except (TypeError, ValueError):
            raise CommandError("set_brightness: a percentage is needed") from None
        def run():
            self.power.wake()             # somebody wants to see it
            self.set_brightness(pct)
        self._on_scheduler(run)
        return {"brightness": self.brightness}

    def _cmd_set_rotation(self, degrees):
        try:
            deg = int(degrees)
        except (TypeError, ValueError):
            deg = None
        if deg not in (0, 90, 180, 270):
            raise CommandError("set_rotation: 0, 90, 180 or 270")
        self._on_scheduler(self.set_rotation, deg)
        return {"rotation": self.rotation}

    def _cmd_set_debounce(self, seconds):
        try:
            sec = float(seconds)
        except (TypeError, ValueError):
            raise CommandError("set_debounce: seconds are needed") from None
        self._on_scheduler(self.set_debounce, sec)
        return {"debounce": self.debounce}

    def _cmd_set_min_frame_ms(self, ms):
        self._on_scheduler(self.set_min_frame_ms, str(ms))
        return {"min_ms": self.min_ms}

    def list_pages(self):
        """Every page, by id and name, and the page on the pad. Reads only:
        a page without a stored name is shown with the one it would get."""
        return {"pages": [{"id": p, "name": self.model.page_name(p)}
                          for p in self.model.available_pages()],
                "current": self.model.current_page}

    def resolve_page(self, want):
        """A page id from what a script or a key names: a page's name first
        (even one called "prev" or "3"), then "prev", then an id."""
        if isinstance(want, bool) or want is None or want == "":
            raise CommandError("a page is needed")
        if isinstance(want, str):
            want = want.strip()
            target = self.model.page_id_by_name(want)
            if target is not None:
                return target
            if want.lower() == "prev":
                return self.model.prev_page
        try:
            target = int(want)
        except (TypeError, ValueError):
            raise CommandError("no page named '%s'" % want) from None
        if target not in self.model.all_page_ids():
            raise CommandError("no page with id %d" % target)
        return target

    def _page_arg(self, page):
        return self.model.current_page if page is None else self.resolve_page(page)

    def _key_arg(self, key):
        try:
            k = int(key)
        except (TypeError, ValueError):
            k = -1
        if not 0 <= k < NUM_KEYS:
            raise CommandError("a key from 0 to 11 is needed")
        return k

    def _cmd_switch_page(self, page, hints=True):
        def run():
            target = self.resolve_page(page)
            changed = target != self.model.current_page
            self.switch_page(target, show_hints=hints is not False)
            return {"page": target, "name": self.model.page_name(target),
                    "changed": changed}
        return self._on_scheduler(run)

    def _cmd_keys(self, page=None):
        def run():
            p = self._page_arg(page)
            imgs = (self.model.images if p == self.model.current_page
                    else self.model.page_images.get(p, {}))
            try:
                hints = _load_displaypad_page_hints(p)["keys"]
            except Exception:
                hints = {}
            fs = self.fullscreen_of(p)
            return {"page": p, "fullscreen": fs,
                    "panel": self.panel_geometry() if fs else None, "keys": [
                {"idx": i, "image": imgs.get(str(i)),
                 "action": self.model.get_action_dict(i, p),
                 "text": self.model.key_text(p, i),
                 "hint": hints.get(str(i), "")} for i in range(NUM_KEYS)]}
        return self._on_scheduler(run)

    def _cmd_press_key(self, key):
        self._on_scheduler(self._key_pressed, self._key_arg(key))

    def _cmd_set_action(self, key, type="none", action="", page=None, extra=None,
                        target=None, icon=None, double=None, hint=None):
        """One key's action. `hint` (the few words the page's box shows for
        the key, #93) is stored with it when given; None leaves it alone."""
        def run():
            p, k = self._page_arg(page), self._key_arg(key)
            if hint is not None:
                self.set_key_hint(p, k, str(hint))
            self.save_page_action(p, k, type, action, extra, target=target,
                                  icon=icon, double=double)
        self._on_scheduler(run)

    # `idx` is the name the web interface's plan gives the key; `key` is the
    # one every other DisplayPad command uses. Either is taken.
    def _cmd_set_image(self, key=None, path=None, page=None, idx=None):
        """A key's picture from a file (a still image or a GIF), on any page;
        no path (or an empty one) takes the picture away and leaves the
        action alone."""
        k = self._key_arg(idx if key is None else key)
        if path in (None, ""):
            path = None
        else:
            path = self._picture_arg(path)

        def run():
            p = self._page_arg(page)
            shown = self.set_key_image(p, k, path)
            return {"page": p, "key": k, "image": shown}
        return self._on_scheduler(run)

    def _cmd_set_key_text(self, key, page=None, text=None):
        """Text drawn over a key's picture: {text (up to 50 characters), pos
        top|bottom|center, font, size_pt 6..18, bold, italic, underline,
        color white|black}. None (or no text) takes it away. The key is
        stored, drawn again and sent."""
        k = self._key_arg(key)
        try:
            cfg = dpi.clean_text_overlay(text)
        except ValueError as e:
            raise CommandError("set_key_text: %s" % e) from None

        def run():
            p = self._page_arg(page)
            self.set_key_text(p, k, cfg)
            return {"page": p, "key": k, "text": cfg}
        return self._on_scheduler(run)

    def _cmd_fonts(self):
        """The font families a key's text can use."""
        return {"fonts": dpi.list_fonts(), "default": dpi.TEXT_DEFAULT_FONT}

    def library(self):
        """The pictures the key picker offers (the DisplayPad library):
        {folder, entries: [{path, name, pack}], packs: [names]}, the
        library's own pictures first (pack None), then each pack's. The
        icons BaseCamp ships are copied in on the first call, as the
        CustomTkinter picker does when it first opens."""
        folder = dplib.default_dir()
        try:
            dplib.ensure_bundled_icons(folder)
        except OSError:
            pass
        entries = dplib.entries(folder)
        return {"folder": folder, "entries": entries,
                "packs": sorted({e["pack"] for e in entries if e["pack"]})}

    @staticmethod
    def _picture_arg(path):
        """`path` if it names a picture PIL can read, else a refusal."""
        if not isinstance(path, str) or not os.path.isfile(path):
            raise CommandError("set_image: no such file: %s" % path)
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
        except Exception:
            raise CommandError("set_image: not a picture: %s" % path) from None
        return os.path.abspath(path)

    def _cmd_clear_key(self, key):
        self._on_scheduler(self.clear_key, self._key_arg(key))

    def _cmd_clear_page(self):
        return {"cleared": bool(self._on_scheduler(self.clear_page))}

    def _cmd_set_fullscreen(self, path, page=None):
        """One picture or GIF over all twelve keys of a page (the current one
        when `page` is not given): {page, kind: "gif"|"picture", queued}.
        `queued` is true when a running animation has to stop first."""
        try:
            path = self._picture_arg(path)
        except CommandError as e:
            raise CommandError(str(e).replace("set_image", "set_fullscreen")) from None

        def run():
            p = self._page_arg(page)
            return self.set_fullscreen(p, path)
        return self._on_scheduler(run)

    def _cmd_clear_fullscreen(self, page=None):
        """Take the fullscreen picture or GIF off a page: {page, cleared}."""
        def run():
            p = self._page_arg(page)
            return {"page": p, "cleared": self.clear_fullscreen(p)}
        return self._on_scheduler(run)

    # A page's name is `title` here: a command cannot take a parameter
    # called `name`, which CommandRegistry.call() takes for the command's.
    def _cmd_create_page(self, title):
        return {"page": self._on_scheduler(self.create_page, title)}

    def _cmd_rename_page(self, page, title):
        self._on_scheduler(lambda: self.rename_page(self.resolve_page(page), title))

    def _cmd_delete_page(self, page):
        def run():
            if not self.delete_page(self.resolve_page(page)):
                raise CommandError("that page cannot be deleted")
        self._on_scheduler(run)

    def _cmd_page_references(self, page):
        return {"references": self._on_scheduler(
            lambda: self.model.find_page_references(self.resolve_page(page)))}

    def _cmd_set_page_timeout(self, page, mode="off", seconds=10, target="prev"):
        if mode not in ("off", "after", "idle"):
            raise CommandError("set_page_timeout: mode is off, after or idle")
        try:
            secs = max(1, int(float(seconds)))
        except (TypeError, ValueError):
            secs = 10
        self._on_scheduler(lambda: self.set_page_timeout(
            self.resolve_page(page), mode, secs, target))

    # ── Settings ──────────────────────────────────────────────────────────────

    def set_brightness(self, percent):
        """Store the backlight brightness and send it to the pad (0 while
        the pad is turned off, power.py)."""
        pct = int(percent)
        self.brightness = pct
        _save_displaypad_brightness(pct)
        self._write_brightness(self._shown_brightness())
        self.publish("settings_changed", **self.settings())

    def set_debounce(self, seconds):
        self.debounce = seconds
        _save_displaypad_debounce(seconds)
        self.publish("settings_changed", **self.settings())

    def set_min_frame_ms(self, value):
        """The GIF frame floor, as typed; stored when it is a number in range
        (#73), used from the next upload on."""
        self.min_ms = value
        _save_displaypad_min_ms(value)

    def set_rotation(self, degrees):
        """Turn every key's picture. Keys without one get the blank, so the
        pad is sent all twelve again, turned."""
        self.rotation = degrees
        _save_displaypad_rotation(degrees)
        self.model.fill_blanks()
        # Re-upload with new rotation
        if not self.uploading and not self.animating:
            self.uploading = True
            self.app.scheduler.call_later(200, self.start_upload)
        self.publish("settings_changed", **self.settings())

    # ── Profiles (core/profiles.py) ───────────────────────────────────────────

    # Which generated icons are profile files: the pages refer to the labels
    # and folder captions by absolute path, and they are NOT drawn again on
    # load or upload. A label is drawn when its key is saved
    # (PageModel.maybe_auto_label_icon), a folder caption when a "page" key is
    # saved; load() only redraws a label that still exists (double captions)
    # and inject_page_icons() falls back to the shared folder icon when the
    # caption file is gone, while a missing label shows the blank. Their names
    # are only (page id, key), so two profiles collide on them: they must
    # travel with the profile. dp_blank.png is rewritten by load() when it is
    # missing; it is listed anyway because pages carry its path.
    PROFILE_SETTINGS = ["displaypad_pages/", "displaypad_rotation",
                        "displaypad_brightness", "displaypad_debounce",
                        "displaypad_min_ms", "displaypad_power.json"]

    def profile_files(self):
        """Names under CONFIG_DIR that make up the DisplayPad's profile."""
        names = list(self.PROFILE_SETTINGS)
        try:
            entries = sorted(os.listdir(_cfg.CONFIG_DIR))
        except OSError:
            entries = []
        for fn in entries:
            if ((fn.startswith(("dp_label_", "dp_folder_")) and fn.endswith(".png"))
                    or fn == "dp_blank.png"):
                names.append(fn)
        return names

    def profile_picture_refs(self, folder):
        """Pictures the pages in `folder` use that the profile does not hold:
        the key images and fullscreen GIFs the user picked, and the lock
        picture. The generated icons (in the profile), widget frames and the
        application's own resources are left out; so are missing files."""
        import json
        own = tuple(os.path.join(_cfg.CONFIG_DIR, n)
                    for n in ("dp_label_", "dp_folder_", "dp_blank.png"))
        res = os.path.join(self.model.res_path, "resources")
        pages = os.path.join(folder, "displaypad_pages")
        try:
            files = sorted(f for f in os.listdir(pages) if f.endswith(".json"))
        except OSError:
            files = []
        paths = []
        for fn in files:
            try:
                with open(os.path.join(pages, fn), encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            if isinstance(rec.get("buttons"), dict):
                paths.extend(rec["buttons"].values())
            paths.append(rec.get("fullscreen"))
        try:
            with open(os.path.join(folder, "displaypad_power.json"), encoding="utf-8") as f:
                paths.append(((json.load(f) or {}).get("lock") or {}).get("picture"))
        except (OSError, ValueError, AttributeError):
            pass
        refs = []
        for path in paths:
            if not isinstance(path, str) or not os.path.isabs(path) or path in refs:
                continue
            if path.startswith(own) or dpi.widget_frame_of(path) is not None \
                    or os.path.abspath(path).startswith(res):
                continue
            if os.path.isfile(path):
                refs.append(path)
        return refs

    def capture_profile_state(self):
        return {}

    def factory_profile_state(self):
        return {}

    def apply_profile(self, state, previous_folder):
        """The live files were swapped: drop the page cache, reload the model
        and the settings, and put the new Main on the pad. Returns at once;
        the work runs on the scheduler (the model's thread) and waits there
        for a running upload or animation, as switch_page does."""
        if self.closing.is_set():
            return
        self.app.scheduler.call_soon(self._apply_profile, 0)

    def _apply_profile(self, _tries=0):
        if self.closing.is_set():
            return
        if self.animating:
            self.stop_animation()
        if (self.uploading or self.animating) and _tries < 40:   # ~6 s
            self.page_switch_waiting = True
            self.app.scheduler.call_later(150, self._apply_profile, _tries + 1)
            return
        try:
            self._apply_profile_now()
        except Exception as e:
            print(f"[DisplayPad] applying the profile failed: {e}", flush=True)
            self.page_switch_waiting = False

    def _apply_profile_now(self):
        model = self.model
        old_page = model.current_page
        self.runner.cancel_double_clicks()
        if self.runner.timeout_handle is not None:
            self.app.scheduler.cancel(self.runner.timeout_handle)
            self.runner.timeout_handle = None
        if self.svc_sync_handle is not None:
            self.app.scheduler.cancel(self.svc_sync_handle)
            self.svc_sync_handle = None
        # The model would write its old state back over the new files on its
        # next switch or edit: start it from scratch and read the files.
        _cfg._invalidate_pages_cache()
        model.prev_page = 0
        model.deleted_page_ids = set()
        model.plugin_frame_keys = {}
        model.fullscreen_group = set()
        model.load()
        # A new profile has no page file yet: the first save would name page 0
        # "Main" (shared.config has no translations) where the list showed the
        # translated name. Store the translated name now.
        model.ensure_page_name(0)
        # Settings are read once in __init__: read them again.
        self.rotation = _load_displaypad_rotation()
        self.brightness = _load_displaypad_brightness()
        self.debounce = _load_displaypad_debounce()
        self.min_ms = str(_load_displaypad_min_ms())
        model.fill_blanks()
        self.power.reload()
        self._sync_services(0)
        self.publish("page_switched", page=0, prev=old_page, show_hints=False)
        self.publish("pages_changed", origin="profile")
        self.publish("settings_changed", **self.settings())
        if self.device_present:
            self._write_brightness(self._shown_brightness())
            self.page_switch_waiting = True
            self.uploading = True
            self.app.scheduler.call_later(200, self.start_upload)
        else:
            self.page_switch_waiting = False     # the connect path uploads
        self.runner.arm_page_timeout(0)

    # ── Brightness under the USB lock ─────────────────────────────────────────

    def _write_brightness(self, pct):
        """Write the brightness to the pad without a second session beside
        the one holding it: noted, and written by whoever holds the USB lock
        (the device worker between reads, an upload between keys), or by a
        short session of its own when nobody does."""
        with self._brightness_lock:
            self._pending_brightness = pct
        threading.Thread(target=self._brightness_session, daemon=True,
                         name="displaypad.brightness").start()

    def _take_brightness(self):
        with self._brightness_lock:
            pct, self._pending_brightness = self._pending_brightness, None
        return pct

    def _brightness_session(self):
        if not self.usb_lock.acquire(blocking=False):
            return      # the holder writes it (_write_pending_brightness)
        try:
            pct = self._take_brightness()
            if pct is None:
                return
            h = self.transport.open_command_interface()
            try:
                self.transport.set_brightness(h, pct)
            finally:
                h.close()
        except Exception:
            pass
        finally:
            self.usb_lock.release()

    def _write_pending_brightness(self, hid_dev):
        """Called by the holder of the USB lock with its open interface 3."""
        if self._pending_brightness is None:
            return
        pct = self._take_brightness()
        if pct is None:
            return
        try:
            self.transport.set_brightness(hid_dev, pct)
        except Exception:
            pass

    # ── Live keys, auto-off, lock screen (live.py, power.py) ─────────────────

    def _shown_brightness(self):
        """What the pad's backlight is to be: 0 while it is turned off."""
        return 0 if self.power.dark else self.brightness

    def _follow_for_live(self, topic, payload):
        """The live keys follow the page on the pad and its keys. These
        events come from the scheduler; a page switch is announced before
        its upload is booked, so the keys drawn here go up with it."""
        if not self.app.scheduler.in_scheduler_thread():
            self.app.scheduler.call_soon(self._follow_for_live, topic, payload)
            return
        name = topic.rsplit(".", 1)[-1]
        if name == "page_switched":
            self.live.refresh(push=False)
        elif name == "page_cleared" or payload.get("page") == self.model.current_page:
            self.live.refresh()

    def _push_live_frame(self, idx, img, send):
        """LiveKeys filed a new picture for key `idx`: tell the interfaces
        and, unless the page is about to go up whole, queue it for the pad
        the way a widget's frame is queued (push_plugin_image)."""
        self.publish("key_frame", idx=idx)
        if not send or self.screen_tiles is not None:
            return
        if not (self.device_present or self.animating or self.uploading):
            return
        self.upload_queue.put((idx, dpi.pil_to_bgr(img, self.rotation), None,
                               self.model.current_page))

    def _power_dark(self, dark):
        self._write_brightness(self._shown_brightness())

    def _power_screen(self, picture):
        """The lock screen's picture across the pad, or (None) the page back."""
        if picture is None:
            if self.screen_tiles is not None:
                self.screen_tiles = None
                self.start_upload()
            return
        tiles = {}
        try:
            for idx, tile in enumerate(split_screen(picture)):
                path = os.path.join(FRAME_DIR, "dp_screen_%d.png" % idx)
                save_png(tile, path)
                tiles[idx] = path
        except OSError as e:
            print(f"[DisplayPad] lock screen: {e}", flush=True)
            return
        self.screen_tiles = tiles
        if self.animating:
            self.stop_animation()       # its worker gives the pad up for ours
        self.start_upload()

    def _power_changed(self):
        self.publish("power_changed", **self.power.state())

    def _cmd_power(self):
        """The auto-off and lock screen settings, and what the pad is in."""
        return self._on_scheduler(self.power.state)

    def _cmd_set_auto_off(self, enabled=None, seconds=None):
        """Turn the keys off after `seconds` without a key press (enabled)."""
        if seconds is not None:
            try:
                seconds = int(float(seconds))
            except (TypeError, ValueError):
                raise CommandError("set_auto_off: seconds are needed") from None
            if seconds < 1:
                raise CommandError("set_auto_off: at least one second")

        def run():
            ao = self.power.settings["auto_off"]
            self.power.set_auto_off(ao["enabled"] if enabled is None else bool(enabled),
                                    ao["seconds"] if seconds is None else seconds)
            return self.power.state()
        return self._on_scheduler(run)

    def _lock_args(self, mode, picture, hours, cmd):
        if mode is not None and mode not in LOCK_MODES:
            raise CommandError("%s: mode is one of %s" % (cmd, ", ".join(LOCK_MODES)))
        if picture not in (None, ""):
            try:
                picture = self._picture_arg(picture)
            except CommandError as e:
                raise CommandError(str(e).replace("set_image", cmd)) from None
        if hours is not None:
            try:
                hours = int(hours)
            except (TypeError, ValueError):
                hours = None
            if hours not in (12, 24):
                raise CommandError("%s: hours is 12 or 24" % cmd)
        return mode, picture, hours

    def _cmd_set_lock_screen(self, mode=None, picture=None, hours=None):
        """What the pad shows while the session is locked or the computer
        sleeps: none, clock, picture, logo or off (power.py)."""
        mode, picture, hours = self._lock_args(mode, picture, hours, "set_lock_screen")

        def run():
            if mode == "picture" and not (picture or self.power.settings["lock"]["picture"]):
                raise CommandError("set_lock_screen: a picture is needed")
            self.power.set_lock(mode, picture, hours)
            return self.power.state()
        return self._on_scheduler(run)

    def _cmd_preview_lock_screen(self, mode=None, picture=None, hours=None):
        """{path}: the lock screen as it would look (612 x 204), or None for
        none and off. The stored setting fills in what is not given."""
        mode, picture, hours = self._lock_args(mode, picture, hours, "preview_lock_screen")

        def run():
            lock = dict(self.power.settings["lock"])
            for k, v in (("mode", mode), ("picture", picture), ("hours", hours)):
                if v is not None:
                    lock[k] = v
            pic = self.power.screen_picture(lock=lock)
            if pic is None:
                return {"path": None}
            path = os.path.join(FRAME_DIR, "dp_screen_preview.png")
            save_png(pic, path)
            return {"path": path}
        return self._on_scheduler(run)

    # ── The start-up picture (the pad's flash) ─────────────────────────────────

    def _cmd_startup_logo(self):
        """{path}: the BaseCamp Linux logo as a start-up picture (800 x 240,
        centred on black), ready to preview or to hand to set_startup_picture."""
        def run():
            from core.devices.displaypad.pages import default_res_path
            path = os.path.join(FRAME_DIR, "dp_startup_logo.png")
            save_png(dpl_render_startup_logo(self.model.res_path or default_res_path()), path)
            return {"path": path}
        return self._on_scheduler(run)

    def _cmd_set_startup_picture(self, path):
        """Store a picture (cut to 800 x 240, a still) as the pad's start-up
        picture: it is written to the pad's flash and shows from the next
        plug-in on. It cannot be undone. A job: returns {started} at once and
        publishes startup_progress (stage writing) and startup_picture
        ({ok, error}) at the end. Refused while an upload runs."""
        path = self._picture_arg(path)
        try:
            bgr = dpi.PanelSource(path)
            try:
                data = bgr.frame(0, 0)
            finally:
                bgr.close()
        except Exception as e:
            raise CommandError("set_startup_picture: not a picture: %s" % e) from None

        def run():
            return self.set_startup_picture(data, path)
        return self._on_scheduler(run)

    def set_startup_picture(self, bgr, path=None, origin=None):
        if not self.device_present:
            raise CommandError("the DisplayPad is not connected")
        if self.uploading or self.startup_busy:
            raise CommandError("the DisplayPad is busy, try again in a moment")
        self.startup_busy = True
        if self.animating:
            self.stop_animation()
            self.app.scheduler.call_later(600, self._start_startup_write, bgr, path, origin)
        else:
            self._start_startup_write(bgr, path, origin)
        return {"started": True}

    def _start_startup_write(self, bgr, path, origin):
        self.uploading = True          # the key listener and the plugins keep off the pad
        self.publish("upload_started")
        threading.Thread(target=self._startup_worker, args=(bgr, path, origin),
                         daemon=True).start()

    def _startup_worker(self, bgr, path, origin):
        ok, err = False, ""
        self.key_released.wait(timeout=1.5)
        self.usb_lock.acquire()
        try:
            if self.closing.is_set():
                err = "closing"
                return
            usb_dev, hid_dev = self.transport.open_interfaces()
            try:
                self.transport.init_device(hid_dev)
                self.publish("startup_progress", stage="writing", origin=origin)
                self.transport.upload_panel(
                    usb_dev, hid_dev, 0, 0, protocol.PANEL_W - 1, protocol.PANEL_H - 1,
                    bgr, None, True)
                ok = True
            finally:
                self.transport.close_interfaces(usb_dev, hid_dev)
        except Exception as e:
            err = str(e) or type(e).__name__
        finally:
            self.usb_lock.release()
            self.startup_busy = False
            self.panel_shown = True       # the panel may hold anything now: blank it first
            self.app.scheduler.call_soon(self._finish_startup, ok, err, path, origin)

    def _finish_startup(self, ok, err, path, origin):
        self._finish(ok, err)
        self.publish("startup_picture", ok=ok, error=err, path=path, origin=origin)
        self.app.scheduler.call_later(300, self.start_upload)    # the page back on the pad

    # ── Upload / Animation ────────────────────────────────────────────────────

    def drop_foreign_frame(self, idx, owned=None):
        """PageModel.drop_foreign_frame, traced (#99, #112). Whether it
        changed anything."""
        frame = self.model.drop_foreign_frame(idx, owned)
        if frame is None:
            return False
        _dbg(f"[DBG frames] key {idx}: dropped a frame drawn for page "
             f"{frame[0]} key {frame[1]}, now on page {self.model.current_page}")
        return True

    def start_upload(self, _retry=0):
        """Send the page on the pad to the pad, and run its GIFs. While the
        lock screen holds the pad, its pictures are sent instead.

        One worker at a time: while one still runs (or the start-up picture
        is being written) the call waits and comes again, so the newest state
        is sent once. Two workers wrote the same page twice (a GIF's first
        frame twice) and could both run their GIFs."""
        t = self.anim_thread
        if self.startup_busy or (t is not None and t.is_alive()
                                 and t is not threading.current_thread()):
            if self.animating:
                self.stop_animation()
            if not self._upload_deferred:
                self._upload_deferred = True
                self.app.scheduler.call_later(100, self._deferred_upload, _retry)
            return
        if self.screen_tiles is not None:
            self._start_screen_upload(_retry)
            return
        owned = self.model.plugin_key_slots()
        for idx in range(NUM_KEYS):
            self.drop_foreign_frame(idx, owned)
        assigned = self.model.upload_set()
        panel = self.model.panel_path
        _dbg(f"[DBG upload] page={self.model.current_page} uploading assigned={assigned}")
        if not assigned and not panel:
            if self.device_present:
                # No images to show — but the pad may still be holding stale
                # frames in its own memory (e.g. a plugin icon whose action was
                # later removed kept rendering, even across reboots — issue #41).
                # An empty-assigned worker run blanks every key and finishes.
                self.uploading = True
                self.publish("upload_started")
                self.anim_thread = threading.Thread(
                    target=self._worker, args=({}, _retry), daemon=True)
                self.anim_thread.start()
            else:
                self.uploading = False
                # This path never reaches _worker()/_finish() (no device to
                # talk to), so clear it here too -- otherwise a page switch
                # that landed here would leave the plugin worker yielding
                # the device forever for a switch that's already done.
                self.page_switch_waiting = False
                self.publish("upload_empty")
            return
        try:
            self.min_frame_ms = max(1, int(self.min_ms))
        except ValueError:
            self.min_frame_ms = 50

        has_gifs = (any(k in self.model.gif_frames for k in assigned)
                    or bool(panel and self._is_animated(panel)))
        if has_gifs:
            self.animating = True
            self.uploading = False
            self.anim_stop.clear()
        else:
            self.uploading = True

        self.publish("upload_started")
        self.anim_thread = threading.Thread(
            target=self._worker, args=(assigned, _retry), daemon=True)
        self.anim_thread.start()

    def _deferred_upload(self, _retry=0):
        self._upload_deferred = False
        if not self.closing.is_set():
            self.start_upload(_retry)

    def _start_screen_upload(self, _retry=0):
        """The lock screen's twelve pictures to the pad, by the page upload's
        own worker: no GIF runs and no key is left out."""
        tiles = {k: p for k, p in self.screen_tiles.items() if os.path.exists(p)}
        self.uploading = True
        self.publish("upload_started")
        self.anim_thread = threading.Thread(
            target=self._worker, args=(tiles, _retry, True), daemon=True)
        self.anim_thread.start()

    def stop_animation(self):
        self.anim_stop.set()

    def _key_pressed(self, idx):
        """A key went down on the pad (on the scheduler). A press that wakes
        the pad from its auto-off, or one while the lock screen holds it,
        does nothing else (power.py)."""
        if self.power.key_pressed(idx):
            return
        self.runner.key_pressed(idx)

    def _worker(self, assigned, _retry=0, screen=False):
        # Named _worker on purpose: the switching test's stand-in pad fails a
        # key write only when it comes straight from a function of this name
        # (#112), so the upload must call upload_button from right here.
        #
        # Wait for the key-event listener to release interface 3 before grabbing
        # it. The uploading/animating flag is already set by start_upload, so
        # the listener is on its way out; opening hidraw while it still holds the
        # device returns [Errno 16] Resource busy (issue #26).
        self.key_released.wait(timeout=1.5)
        self.usb_lock.acquire()
        if self.closing.is_set():
            self.usb_lock.release()
            return
        try:
            usb_dev, hid_dev = self.transport.open_interfaces()
        except Exception as e:
            self.usb_lock.release()
            # Device busy at boot: retry after a short delay, 2s, 4s, 6s, 8s,
            # 10s. The count used to start from nothing on every retry, since
            # start_upload never passed it on, so this retried for ever and
            # the error below was never reached.
            self._retry_upload(e, (2 + _retry * 2) * 1000, _retry)
            return
        try:
            self.transport.init_device(hid_dev)
            # The pad is now booted and addressable: let queued plugin uploads
            # proceed (they wait on this to avoid streaming to a not-yet-ready
            # pad, issue #43).
            self.pad_ready = True
            self._write_pending_brightness(hid_dev)

            rot = self.rotation

            # A fullscreen page is one write to the whole panel, the gaps
            # between the keys included; a page without one blanks the panel
            # first if the last page left a picture there.
            panel_path = None if screen else self.model.panel_path
            if panel_path:
                self._run_panel(usb_dev, hid_dev, panel_path, rot)
                self.app.scheduler.call_soon(self._finish, True, "")
                return
            if self.panel_shown:
                self.transport.upload_panel(usb_dev, hid_dev, 0, 0, protocol.PANEL_W - 1,
                                            protocol.PANEL_H - 1, protocol.BLANK_PANEL_BGR)
                self.panel_shown = False

            # Clear unassigned buttons so stale images from previous
            # sessions don't linger on the device.
            for k in range(NUM_KEYS):
                if self.closing.is_set():
                    return      # the application is going, the pad can wait
                if k not in assigned:
                    self.transport.upload_button(usb_dev, hid_dev, k, protocol.BLANK_BGR)

            # One unreadable file used to take the whole upload down, and the
            # pad kept whatever it was showing while a red toast repeated the
            # library's error message. A key whose image cannot be read is
            # skipped and named on the console instead; the others still go up,
            # and the key is left blank by the loop above.
            # The lock screen's pictures are still ones, whatever the page runs.
            gif_frames = {} if screen else self.model.gif_frames
            static = {}
            for k, v in assigned.items():
                if k in gif_frames:
                    continue
                if v is None:
                    txt = self.model.key_text(self.model.current_page, k)
                    if txt:     # no picture, but text: a black tile with it
                        static[k] = dpi.rotate_bgr(dpi.blank_bgr_with_text(txt), rot)
                    continue
                try:
                    static[k] = dpi.image_to_bgr(
                        v, rot, self.model.key_text(self.model.current_page, k))
                except Exception as e:
                    print(f"[DisplayPad] key {k + 1}: cannot read {v!r} ({e})")
            animated = {k: gif_frames[k] for k in assigned if k in gif_frames}

            total = len(static) + len(animated)
            for n, (key_index, bgr) in enumerate(sorted(static.items())):
                if self.closing.is_set():
                    return
                self.publish("upload_progress", key=key_index, n=n + 1, total=total)
                self.transport.upload_button(usb_dev, hid_dev, key_index, bgr)

            if not animated:
                self._write_pending_brightness(hid_dev)
                self.app.scheduler.call_soon(self._finish, True, "")
                return

            for n, (key_index, frames) in enumerate(sorted(animated.items())):
                if self.closing.is_set():
                    return
                self.publish("upload_progress", key=key_index,
                             n=n + len(static) + 1, total=total)
                fr_bgr = dpi.rotate_bgr(frames[0][0], rot)
                self.transport.upload_button(usb_dev, hid_dev, key_index, fr_bgr)

            self.publish("animating", count=len(animated))

            min_ms = self.min_frame_ms
            group = sorted(k for k in self.model.fullscreen_group if k in animated)

            _key_events = []
            # Track previous packet state for rising-edge detection so a held
            # button doesn't fire its action every GIF frame.
            _edges = protocol.KeyEdgeDecoder()

            def _dispatch_key_events(evts):
                for evt in evts:
                    for ki in _edges.feed(evt):
                        self.app.scheduler.call_soon(self._key_pressed, ki)

            if group:
                # ── Synchronized fullscreen GIF loop ──────────────────────────
                n_frames = len(animated[group[0]])
                fidx = 0
                while not self.anim_stop.is_set():
                    t0 = time.monotonic()
                    dur = animated[group[0]][fidx][1]
                    for k in group:
                        bgr, _ = animated[k][fidx % len(animated[k])]
                        bgr = dpi.rotate_bgr(bgr, rot)
                        self.transport.upload_button(usb_dev, hid_dev, k, bgr, _key_events)
                    fidx = (fidx + 1) % n_frames
                    _dispatch_key_events(_key_events)
                    _key_events.clear()
                    self._write_pending_brightness(hid_dev)
                    wait = max(0, max(dur, min_ms) / 1000.0 - (time.monotonic() - t0))
                    if wait > 0:
                        self.anim_stop.wait(timeout=wait)
            else:
                # ── Per-button animation loop ──────────────────────────────────
                frame_idx = {k: 1 % len(f) for k, f in animated.items()}
                next_time = {k: time.monotonic() + max(f[0][1], min_ms) / 1000.0
                             for k, f in animated.items()}

                while not self.anim_stop.is_set():
                    while True:
                        try:
                            qi, bgr, new_frames, qpage = self.upload_queue.get_nowait()
                        except queue.Empty:
                            break
                        if qpage is not None and qpage != self.model.current_page:
                            continue   # drawn for a page we already left (#70)
                        _allowed = self.model.plugin_key_slots()
                        if (not new_frames and _allowed is not None
                                and qi not in _allowed):
                            continue   # not a key any plugin owns here (#69)
                        if new_frames:
                            animated[qi] = new_frames
                            frame_idx[qi] = 0
                            next_time[qi] = time.monotonic()
                        else:
                            animated.pop(qi, None)
                            frame_idx.pop(qi, None)
                            next_time.pop(qi, None)
                            self.transport.upload_button(usb_dev, hid_dev, qi, bgr)

                    if not animated:
                        break

                    self._write_pending_brightness(hid_dev)
                    due_list = sorted((next_time[k], k) for k in animated)
                    t_next, key = due_list[0]
                    wait = t_next - time.monotonic()
                    if wait > 0:
                        self.anim_stop.wait(timeout=wait)
                        if self.anim_stop.is_set():
                            break
                    idx = frame_idx[key]
                    bgr, duration_ms = animated[key][idx]
                    bgr = dpi.rotate_bgr(bgr, rot)
                    self.transport.upload_button(usb_dev, hid_dev, key, bgr, _key_events)
                    frame_idx[key] = (idx + 1) % len(animated[key])
                    next_time[key] = time.monotonic() + max(duration_ms, min_ms) / 1000.0
                    _dispatch_key_events(_key_events)
                    _key_events.clear()

        except Exception as e:
            # A page that fails part way is worse than one that fails at the
            # start: the pad keeps the keys of the page it was on, the keys
            # written so far belong to the new one, and the widgets paint
            # their keys over the mixture. It was reported and never tried
            # again (#112), and after a replug the first key failing took the
            # whole page with it (#104). It is tried again, from the top.
            self._retry_upload(e, min(1000 * (1 + _retry), 5000), _retry)
        else:
            self.app.scheduler.call_soon(self._finish, True, "")
        finally:
            self.transport.close_interfaces(usb_dev, hid_dev)
            self.usb_lock.release()

    def _run_panel(self, usb_dev, hid_dev, path, rot):
        """Write a fullscreen picture to the panel; a GIF goes on frame after
        frame until the animation is stopped. Presses on the pad meanwhile
        run their keys' actions as usual."""
        full = (0, 0, protocol.PANEL_W - 1, protocol.PANEL_H - 1)
        src = dpi.PanelSource(path)
        try:
            t0 = time.monotonic()
            self.publish("upload_progress", key=0, n=1, total=1)
            self.transport.upload_panel(usb_dev, hid_dev, *full, src.frame(0, rot))
            self.panel_shown = True
            self._write_pending_brightness(hid_dev)
            if not src.animated:
                return
            self.publish("animating", count=NUM_KEYS)
            events = []
            edges = protocol.KeyEdgeDecoder()
            i = 0
            while not self.anim_stop.is_set():
                wait = max(0, max(src.duration(i), self.min_frame_ms) / 1000.0
                           - (time.monotonic() - t0))
                if wait > 0:
                    self.anim_stop.wait(timeout=wait)
                if self.anim_stop.is_set():
                    break
                i = (i + 1) % src.count
                t0 = time.monotonic()
                self.transport.upload_panel(usb_dev, hid_dev, *full, src.frame(i, rot), events)
                for evt in events:
                    for ki in edges.feed(evt):
                        self.app.scheduler.call_soon(self._key_pressed, ki)
                events.clear()
                self._write_pending_brightness(hid_dev)
        finally:
            src.close()

    def _retry_upload(self, err, delay_ms, retry):
        """Called from the upload thread when a page upload did not go
        through: try the page again in `delay_ms`, or report it.

        The flags drop while it waits so the device worker can listen for
        keys meanwhile, and the retry starts from start_upload, which reads
        the page again: a switch made in the meantime is what gets uploaded.
        A pad that has gone is not retried at all. Plugging it back in
        uploads the page anyway, and five failed tries against an empty port
        would only end in an error about a device the user just unplugged.
        """
        self.uploading = False
        self.animating = False
        if self.closing.is_set():
            return
        if not self.device_present:
            self.page_switch_waiting = False
            return
        if retry < self.UPLOAD_RETRIES:
            print(f"[DisplayPad] upload failed ({err}), trying again", flush=True)
            self.app.scheduler.call_later(delay_ms, self.start_upload, retry + 1)
            return
        self.app.scheduler.call_soon(self._finish, False, str(err))

    def _finish(self, success, err):
        self.uploading = False
        self.animating = False
        # A page switch waiting on this upload can stop yielding the device to
        # the plugin worker now that it's done.
        self.page_switch_waiting = False
        # Any plugin images that queued up while this upload held the device
        # get picked up by the persistent plugin worker on its next poll
        # (at most 0.2s later) — nothing to kick off explicitly here anymore.
        self.publish("upload_finished", ok=bool(success), error=str(err or ""))

    # ── The pad coming and going ──────────────────────────────────────────────

    def _monitor_loop(self):
        """Background thread: detect device connect/disconnect and auto-reupload.

        Nothing a single pass throws may end it. It is started while the
        window is still being built, and a pad it found then used to be
        reported with after(), which Tk refuses until the main loop runs:
        that refusal ended this thread on every start that took longer than
        a second, and from then on a replugged pad was never noticed. It came
        back with only the keys the widgets paint themselves, and the rest
        stayed dark until a page switch uploaded them (#106, #104).
        """
        while not self.monitor_stop.is_set():
            try:
                self._monitor_pass()
            except Exception as e:
                print(f"[DisplayPad] device check failed: {e}", flush=True)
            self.monitor_stop.wait(timeout=2)

    def _monitor_pass(self):
        """One look at the bus, and what follows from it."""
        path = None
        if getattr(self.transport, "HID_AVAILABLE", True):
            try:
                path = self.transport.find_command_path()
            except Exception:
                path = None
        present = path is not None

        if present and (not self.device_present or path != self.dp_path):
            # Connected, reconnected, or re-enumerated under a new path
            # without us seeing the gap: the user unplugged and replugged
            # within a single poll window, so the pad booted fresh and
            # needs INIT again even though presence never toggled (#44).
            has_content = bool(self.model.images or self.model.gif_frames)
            self.app.scheduler.call_soon(self._on_device_connected, has_content)
            self.device_present = True
            self.dp_path = path
            self.pad_ready = False
        elif not present and self.device_present:
            # Device just disconnected. Noted at once: the workers must stop
            # using a pad that is gone.
            self.device_present = False
            self.dp_path = None
            self.pad_ready = False
            self.app.scheduler.call_soon(self._on_device_disconnected)
        elif not present and not self.device_present:
            # Plugged in but interface 3 missing? Likely the usbhid quirk.
            if self.quirk_check is not None:
                self.quirk_check()
            else:
                self.maybe_warn_usb_quirk()

    def maybe_warn_usb_quirk(self):
        """If the DisplayPad is on the USB bus but its command interface (3)
        never shows up, the kernel is dropping it due to the interface-order
        quirk. Surface a clear, actionable hint once instead of silently
        showing the pad as 'not connected' (issue #36)."""
        if self.warned_quirk or not getattr(self.transport, "PYUSB_AVAILABLE", True):
            return
        try:
            dev = self.transport.find_on_bus()
        except Exception:
            return
        if dev is None:
            return  # genuinely not plugged in — nothing to warn about
        # On the bus but hidapi never listed interface 3 → quirk.
        self.warned_quirk = True
        print(
            "[DisplayPad] Detected on USB but its command interface (3) is "
            "missing — the kernel is likely dropping it (usbhid interface-order "
            "quirk). Fix: create /etc/modprobe.d/mountain-displaypad.conf with\n"
            "  options usbhid quirks=0x3282:0x0009:0x4000\n"
            "then rebuild the initramfs and reboot. See README → Known Issues.",
            flush=True)
        self.publish("quirk")

    def _on_device_connected(self, has_content, _tries=0):
        if self.uploading or self.animating:
            # A previous upload/animation session still owns the device. Don't
            # drop the reconnect (that left the pad un-initialised after a
            # replug, issue #44) — retry until the session finishes, then init.
            if _tries < 30:
                self.app.scheduler.call_later(
                    500, self._on_device_connected, has_content, _tries + 1)
            return
        self.set_brightness(self.brightness)
        self.publish("device_connected", has_content=bool(has_content))
        # Always re-run the upload on connect: with images it repaints them,
        # without any it still clears stale frames the pad kept in its own
        # memory (issue #41 — a removed plugin icon lingering across reboots).
        self.app.scheduler.call_later(2500, self.start_upload)

    def _on_device_disconnected(self):
        self.publish("device_disconnected", busy=self.busy)

    # ── Widget frames ─────────────────────────────────────────────────────────

    def push_plugin_image(self, key_index, pil_image):
        """Upload a PIL image (any size) to key `key_index` (0-11). Thread-safe;
        called by widget plugins, from their own threads, for every frame.

        Frames are always queued. The persistent device worker drains the
        queue — no new thread is spawned per push, and interface 1/3 stay
        claimed across a run of pushes instead of being released and
        reclaimed for every single image.
        """
        if not (0 <= key_index <= 11):
            return
        # Plugins put their frame into the image maps themselves so a tile and
        # a full page upload show it. Note the key, so the frame stays out of
        # the config and does not become that key's icon (#69).
        self.model.mark_plugin_frame(key_index)
        self.publish("key_frame", idx=key_index)
        _dbg(f"[DBG push_plugin_image] key={key_index} current_page={self.model.current_page}")
        bgr_bytes = dpi.pil_to_bgr(pil_image, self.rotation)

        # Don't accumulate frames for a device that nobody will drain (absent
        # and no session running) — that would grow the queue without bound.
        busy = self.animating or self.uploading
        if not (self.device_present or busy):
            return
        # Stamped with the page it was drawn for (#69/#70). A plugin renders in
        # its own thread and the queue is drained later, so without this a frame
        # composed for the page we just left gets uploaded on top of the page we
        # just switched to, and that key keeps the old widget's picture.
        self.upload_queue.put((key_index, bgr_bytes, None, self.model.current_page))

    def _plugin_upload_worker(self):
        """Persistent thread (started once, alive for the whole session) that
        owns interface 1 (pixels) + interface 3 (commands/keys) for as long as
        nothing else needs them: plugin image uploads AND key-event listening
        both happen from this one thread.

        Previously these were two separate concerns that kept trading the
        device back and forth — a standalone key-event listener that closed
        and reopened interface 3 every time a plugin (or a manual upload)
        wanted it, and a plugin worker that itself claimed/released interface
        1+3 around every burst of pushes. Since both ultimately want the same
        session, and reading key events is cheap to do "while idle" between
        pushes, they're now one thread: it opens the device once and simply
        never closes it again on its own — listening for a key IS this
        thread's idle-time job, not a reason to give the device back. It only
        yields (closes + releases the lock) the moment a manual upload,
        GIF animation, or a page switch that's waiting on one of those
        (page_switch_waiting) explicitly needs exclusive access — that code
        (_worker, above) still runs its own separate, more involved session;
        this thread just resumes once it's done.
        """
        usb_dev = hid_dev = None
        holding = False  # True while we hold usb_lock + interface 1/3
        edges = protocol.KeyEdgeDecoder(keep_short=True)
        last_fire = {}  # key_index -> monotonic time of last action
        not_ready_since = None  # when we first saw a present but un-INIT'd pad

        def _wants_device():
            # Anything that isn't us, wanting exclusive access right now.
            return self.uploading or self.animating or self.page_switch_waiting

        def _release():
            nonlocal usb_dev, hid_dev, holding
            if usb_dev is not None:
                try:
                    self.transport.close_interfaces(usb_dev, hid_dev)
                except Exception:
                    pass
                usb_dev = hid_dev = None
            if holding:
                self.key_released.set()
                try:
                    self.usb_lock.release()
                except RuntimeError:
                    pass
                holding = False

        while not (self.plugin_worker_stop.is_set() or self.closing.is_set()):
            # Yield the device immediately the moment anything else wants
            # it — don't linger. A plugin pushing frequently, or a steady
            # stream of key reads, must never starve a manual upload or a
            # pending page switch of the device.
            if holding and _wants_device():
                _release()

            # --- Is there a plugin image waiting? Grab at most one without
            # blocking long, so this loop stays responsive to key events
            # and to _wants_device() in between. ---
            try:
                item = self.upload_queue.get(timeout=0.05)
            except queue.Empty:
                item = None

            if _wants_device():
                if item is not None:
                    self.upload_queue.put(item)
                time.sleep(0.05)
                continue

            if not self.device_present:
                if item is not None:
                    self.upload_queue.put(item)  # don't lose it, drain later
                if holding:
                    _release()
                not_ready_since = None  # the grace period below is per connection
                time.sleep(0.2)
                continue

            if not self.pad_ready:
                # Normally the connect upload INITs the pad and flips this
                # flag within a couple of seconds. If that upload never
                # happens or fails, this thread must not wait forever: key
                # events live in here now, so a permanently unready flag
                # would mean a silently dead pad until the next replug. After
                # a grace period we open and INIT it ourselves, which is what
                # sets pad_ready anyway, so nothing streams pixels to a pad
                # that hasn't booted (issue #43).
                if not_ready_since is None:
                    not_ready_since = time.monotonic()
                if time.monotonic() - not_ready_since < 8.0:
                    if item is not None:
                        self.upload_queue.put(item)
                    time.sleep(0.1)
                    continue
            else:
                not_ready_since = None

            if not holding:
                # Wait for any previous session to actually let go before we
                # try to open (mirrors the old key-event listener's own wait).
                self.key_released.wait(timeout=1.5)
                # Serialise with the manual upload/animation worker so the
                # two can never open the device at the same time (#26).
                self.usb_lock.acquire()
                holding = True
                self.key_released.clear()
                if self.closing.is_set():
                    # The wait above is up to 1.5 s and the open below is
                    # seconds more; checking only at the top of the loop
                    # narrowed the teardown race rather than closing it.
                    _release()
                    return
                try:
                    usb_dev, hid_dev = self.transport.open_interfaces()
                    self.transport.init_device(hid_dev)
                    self.pad_ready = True
                    self.last_plugin_error = None
                    edges.reset()  # fresh session -- no stale key state
                except Exception as e:
                    self._log_plugin_error(e)
                    if item is not None:
                        self.upload_queue.put(item)  # don't lose this frame
                    _release()
                    time.sleep(0.5)
                    continue

            self._write_pending_brightness(hid_dev)

            if item is not None:
                try:
                    self._drain_plugin_queue(usb_dev, hid_dev, first=item)
                except Exception as e:
                    self._log_plugin_error(e)
                    _release()
                continue

            # --- Nothing to push right now: this is what used to be the
            # separate key-event listener's job. Only packets with
            # data[0] == 0x01 are key-event packets; everything else
            # (0x11 init, 0x21 image responses, etc.) is ignored. ---
            try:
                data = hid_dev.read(64, timeout=150)
            except Exception as e:
                self._log_plugin_error(e)
                _release()
                continue
            if not protocol.is_key_packet(data):
                continue
            data = list(data)
            now = time.monotonic()
            for k in edges.feed(data):
                # A double-click key uses a short anti-bounce so a
                # deliberate second tap gets through; every other key
                # keeps the user's debounce (issue #47).
                deb = (self.runner.dc_antibounce if self.model.is_double_key(k)
                       else self.debounce)
                if now - last_fire.get(k, 0) >= deb:
                    last_fire[k] = now
                    self.app.scheduler.call_soon(self._key_pressed, k)

        _release()

    def _drain_plugin_queue(self, usb_dev, hid_dev, first=None):
        """Upload every queued plugin image in this session — plus `first`,
        if given (an item the caller already popped off the queue) — keeping
        only the latest frame per key. Key presses seen during the upload
        are dispatched so the keys stay responsive while the listener is
        paused."""
        latest = {}
        allowed = self.model.plugin_key_slots()
        pending = [first] if first is not None else []
        while True:
            if pending:
                ki, bgr, frames, page = pending.pop()
            else:
                try:
                    ki, bgr, frames, page = self.upload_queue.get_nowait()
                except queue.Empty:
                    break
            if self.screen_tiles is not None:
                continue   # the lock screen holds the pad; the page comes back whole
            if frames:
                # A GIF arrived -- hand it back and let start_upload handle it.
                self.upload_queue.put((ki, bgr, frames, page))
                self.app.scheduler.call_soon(self._restart_for_gif)
                break
            if page is not None and page != self.model.current_page:
                # Drawn for a page that is no longer on the device (#69/#70).
                _dbg(f"[DBG drain] dropping stale frame for key {ki} "
                     f"(page {page}, now {self.model.current_page})")
                continue
            if allowed is not None and ki not in allowed:
                _dbg(f"[DBG drain] dropping frame for key {ki}: no plugin "
                     f"action on it on page {self.model.current_page}")
                continue
            latest[ki] = bgr
        if not latest:
            return
        key_events = []
        for ki, bgr in sorted(latest.items()):
            self.transport.upload_button(usb_dev, hid_dev, ki, bgr, key_events)
        self._dispatch_plugin_key_events(key_events)

    def _dispatch_plugin_key_events(self, events):
        """Fire actions for key-event packets captured during a plugin upload.
        Rising-edge against a fresh baseline -- the session is brief, so a held
        key fires at most once."""
        edges = protocol.KeyEdgeDecoder()
        for evt in events:
            for ki in edges.feed(evt):
                self.app.scheduler.call_soon(self._key_pressed, ki)

    def _restart_for_gif(self):
        if not self.animating and not self.uploading:
            self.app.scheduler.call_later(100, self.start_upload)

    def _log_plugin_error(self, e):
        """Print a plugin upload error only when it changes, so a persistent
        device problem doesn't flood the log with the same line."""
        msg = str(e)
        if msg != self.last_plugin_error:
            self.last_plugin_error = msg
            print(f"[Plugin] DisplayPad upload failed: {msg}", flush=True)

    # ── Pages: switching ──────────────────────────────────────────────────────

    def _runner_switch(self, page, show_hints):
        self.switch_page(page, show_hints=show_hints)

    def _runner_set_key(self, action_json, idx):
        self.apply_set_key(action_json, idx)

    def switch_page(self, page_num, show_hints=False, _retry=0):
        """Put page `page_num` on the pad.

        `show_hints` is for a switch somebody made, a key on the pad, the tray
        or a script, as opposed to one the application made (a timeout, the
        editor following its tabs). Only those bring up the page's box (#93).
        """
        if page_num == self.model.current_page:
            if show_hints:
                self.show_hints(page_num)   # asked for again: show it again
            return
        # Stop running animation before switching (check animating first,
        # because uploading may also be True during animation)
        if self.animating:
            self.stop_animation()
            self.app.scheduler.call_later(500, self.switch_page, page_num, show_hints)
            return
        # A plugin image upload (System Monitor / Clock push ~1/s) may hold the
        # device. Don't silently drop the switch — that left the panel/device on
        # the old page while the editor already showed the new one, so a fresh
        # page looked "pre-filled" with the previous page's images (issue #28).
        # Defer and retry until the short upload finishes (bounded so a stuck
        # flag can't loop forever).
        #
        # page_switch_waiting tells _plugin_upload_worker to yield the device
        # at its next loop iteration instead of only releasing it after a full
        # LINGER (2s) idle gap between queued frames — without this, a plugin
        # that keeps pushing more often than every 2s (e.g. a ~1/s System
        # Monitor push) held the device indefinitely and every retry here just
        # failed until the bound below silently gave up (this was the actual
        # bug: page switching appeared to stop working whenever such a plugin
        # was active on the current page).
        self.page_switch_waiting = True
        if self.uploading:
            if _retry < 40:  # ~6s worst case at 150ms
                self.app.scheduler.call_later(
                    150, self.switch_page, page_num, show_hints, _retry + 1)
            return
        # NOTE: page_switch_waiting stays True here — it is NOT cleared yet.
        # Clearing it as soon as this guard passes left a window (until the
        # scheduled start_upload -> _worker actually acquires usb_lock, ~200ms+
        # below) where the plugin worker saw no reason to keep yielding and
        # could grab the device again for its next push, blocking the page's
        # own static-icon upload on the same lock — static icons then simply
        # never got refreshed after a switch. It's cleared instead in
        # _finish() (reached by every _worker() upload, including the "nothing
        # assigned" blank-out case) or immediately below if this switch has
        # nothing to upload at all.
        self.runner.cancel_double_clicks()

        old_page = self.model.current_page
        if self.model.begin_switch(page_num) is None:
            _dbg(f"[DBG switch] {old_page} -> {page_num} | old_page was deleted, not saving its state")
        else:
            _dbg(f"[DBG switch] {old_page} -> {page_num} | saved page_images[{old_page}]="
                 f"{self.model.page_images[old_page]}")

        # Start/stop page-bound service plugins using their normal start()/
        # stop() lifecycle: a plugin whose button was on the page we're
        # leaving gets stop()'d (it was otherwise still polling and painting
        # over that key index on the new page, since keys are shared
        # hardware slots across pages), and a plugin whose button is on the
        # page we're entering gets start()'d right away. Before the page's
        # map is loaded: a widget that draws as it starts writes into it.
        self._sync_services(page_num)
        _dbg(f"[DBG switch] after sync_services_for_page({page_num}): "
             f"page_images[{page_num}]={self.model.page_images.get(page_num)}")

        # Load new page: its map, nav icons, blanks, GIFs, fullscreen GIF.
        self.model.load_page(page_num)
        _dbg(f"[DBG switch] loaded images for page {page_num} = {self.model.images}")
        self.publish("page_switched", page=page_num, prev=old_page,
                     show_hints=bool(show_hints))

        # Re-upload to device — set flag immediately to block key listener
        if self.model.images or self.model.gif_frames:
            self.uploading = True
            self.app.scheduler.call_later(200, self.start_upload)
        else:
            # Nothing to upload for this page at all -- start_upload/_finish
            # (which normally clear page_switch_waiting) will never run, so
            # clear it here or the plugin worker would keep yielding the
            # device forever for a switch that has already fully completed.
            self.page_switch_waiting = False

        # Start this page's auto-timeout countdown, if any (#45).
        self.runner.arm_page_timeout(page_num)
        if show_hints:
            self.show_hints(page_num)

    def _sync_services(self, page):
        if self.sync_services_hook is None:
            return
        try:
            self.sync_services_hook(page)
        except Exception as e:
            print(f"[Plugin] sync_services_for_page failed: {e}")

    def schedule_service_sync(self):
        """Ask for the page's widget services to be brought in line, shortly.

        Assigning a widget to a key has to start that widget's service, and
        clearing the last key that used it has to stop it. Both were only ever
        done on a page switch, so a widget assigned to a key on the page you
        were already looking at simply never started: nothing appeared on the
        pad or in the editor, though the key was stored and worked after a
        restart or a switch away and back (#97). Clearing one had the mirror
        problem, leaving the thread polling and painting a key nobody had
        assigned it to any more.

        Deferred and coalesced because applying the actions dialog saves all
        twelve rows in a loop, and a sync per row would stop a service on one
        row and start it again on the next.
        """
        if self.svc_sync_handle is not None:
            self.app.scheduler.cancel(self.svc_sync_handle)
        self.svc_sync_handle = self.app.scheduler.call_later(250, self.sync_page_services)

    def sync_page_services(self):
        self.svc_sync_handle = None
        self._sync_services(self.model.current_page)

    # ── The page's box: its note and its keys' hints (#93) ────────────────────

    def show_hints(self, page):
        """Bring up `page`'s box, if it has anything to say and is wanted;
        else take any box down."""
        if page in self.hint_pages_quiet:
            self.publish("hints_hide")
            return
        try:
            hints = _load_displaypad_page_hints(page)
        except Exception:
            self.publish("hints_hide")
            return
        if not hints["show"] or not (hints["note"].strip() or hints["keys"]):
            self.publish("hints_hide")      # an empty page stays silent
            return
        self.publish("hints_show", page=page, name=self.model.ensure_page_name(page),
                     note=hints["note"], keys=dict(hints["keys"]), show=hints["show"])

    def quiet_hints(self, page):
        """No box for `page` again this session."""
        self.hint_pages_quiet.add(page)

    def set_key_hint(self, page, idx, text):
        """What key `idx` of `page` does, in a few words, for the page's box.
        Kept apart from the action, so it outlives a change of action."""
        text = (text or "").strip()
        keys = _load_displaypad_page_hints(page)["keys"]
        if keys.get(str(idx), "") == text:
            return
        if text:
            keys[str(idx)] = text
        else:
            keys.pop(str(idx), None)
        _save_displaypad_page_hints(page, keys=keys)

    def set_page_note(self, page, note=None, show=None):
        """The page's note, and whether its box comes up at all."""
        _save_displaypad_page_hints(page, note=note, show=show)

    def set_hint_box_settings(self, **changes):
        """Where the box sits and how long it stays (seconds, 0 = until closed)."""
        _save_hint_box_settings(**changes)

    # ── Pages: keys ───────────────────────────────────────────────────────────

    def save_page_action(self, page, idx, btype, action, extra=None,
                         target=None, icon=None, double=None, origin=None):
        """Save one key's action (PageModel.save_page_action says what each
        argument does), send the page on the pad again if it was that page,
        and bring its widget services in line. Returns the SaveResult."""
        res = self.model.save_page_action(page, idx, btype, action, extra,
                                          target=target, icon=icon, double=double)
        # Only the visible page drives the device upload. A label drawn for a
        # key of a sub-page on the pad asks for one of its own, as it did.
        for _ in range(int(res.label_drawn) + int(res.current)):
            if not self.uploading and not self.animating:
                self.app.scheduler.call_later(200, self.start_upload)
        if res.current:
            self.schedule_service_sync()
        self.publish("key_changed", page=page, idx=idx, origin=origin)
        return res

    def apply_set_key(self, action_json, host_idx):
        """Redefine a key in response to a 'set_key' action (issue #18)."""
        parsed = parse_set_key(action_json, self.model.current_page, host_idx)
        if parsed is None:
            return
        page, key, btype, action = parsed
        self.save_page_action(page, key, btype, action)

    def set_button_image(self, key_index, path):
        """Give a key of the page on the pad a picture or a GIF, store it and
        send it. Returns the GIF's frames, or None."""
        frames = self.model.set_button_image(key_index, path)
        if self.animating:
            try:
                bgr = None if frames else dpi.image_to_bgr(
                    path, 0, self.model.key_text(self.model.current_page, key_index))
            except Exception as e:
                print(f"[DisplayPad] key {key_index + 1}: cannot read {path!r} ({e})")
            else:
                self.upload_queue.put((key_index, bgr, frames, self.model.current_page))
        elif not self.uploading:
            self.app.scheduler.call_later(100, self.start_upload)
        return frames

    def set_key_text(self, page, idx, cfg, origin=None):
        """Store the text over key `idx` of `page` (`cfg` as clean_text_overlay
        gives it, None to remove it) and, if the page is on the pad, draw the
        key again and send it. Publishes key_changed."""
        model = self.model
        model.set_key_text(page, idx, cfg)
        if page == model.current_page:
            frames = model.refresh_key_frames(idx)
            if self.animating:
                try:
                    bgr = None if frames else model.still_bgr(idx)
                except Exception as e:
                    print(f"[DisplayPad] key {idx + 1}: cannot draw text ({e})")
                else:
                    self.upload_queue.put((idx, bgr, frames, page))
            elif not self.uploading:
                self.app.scheduler.call_later(100, self.start_upload)
        self.publish("key_changed", page=page, idx=idx, origin=origin)

    def set_key_image(self, page, idx, path, origin=None):
        """Give key `idx` of `page` a picture, or take it away (`path` None).

        On the page on the pad this is set_button_image (stored, sent, a GIF
        animated); on any other page the stored map is changed and the key
        shows it when its page comes up. Like the screen's picker, a picture
        from elsewhere is also put in the DisplayPad library (best effort);
        one from the library stays as it is. Publishes key_changed and
        returns the path the key shows now."""
        model = self.model
        if path and not dplib.contains(path):
            # A picture from the library (a pack's above all) is there
            # already; a copy of it would only crowd the picker.
            try:
                _save_to_dp_library(path)
            except Exception:
                pass
        if page == model.current_page:
            if path:
                self.set_button_image(idx, path)
            else:
                model.clear_key_image(idx)
                if self.animating:
                    self.upload_queue.put((idx, protocol.BLANK_BGR, None, page))
                elif not self.uploading:
                    self.app.scheduler.call_later(100, self.start_upload)
            shown = model.images.get(str(idx))
        else:
            imgs = model.page_images.setdefault(page, {})
            imgs[str(idx)] = path or model.blank_icon
            model.take_key_back(page, idx)
            if page == 0:
                _save_displaypad_buttons(model.persistable_images(0, imgs))
            else:
                model.save_sub_pages()
            shown = imgs[str(idx)]
        self.publish("key_changed", page=page, idx=idx, origin=origin)
        return shown

    # ── What a key can do: the catalogue for an editor ───────────────────────

    #: How an editor asks for the value of each built-in type. "text" is a
    #: line of text (with a placeholder key), "select" a list of options,
    #: "page" a page picker plus the key's caption, "none" nothing.
    VALUE_KINDS = {
        "none": {"kind": "none"},
        "shell": {"kind": "text"},
        "url": {"kind": "text"},
        "folder": {"kind": "text", "browse": "folder"},
        "app": {"kind": "text", "browse": "file"},
        "page": {"kind": "page", "hint_key": "dp_page_caption_hint"},
        "obs": {"kind": "select"},
        "macro": {"kind": "select"},
        "keypress": {"kind": "text", "hint_key": "dp_keypress_hint"},
        "text": {"kind": "text", "hint_key": "action_type_text_hint"},
        "set_key": {"kind": "text", "hint_key": "action_type_set_key_hint"},
        # Live keys (live.py): a clock's options, PC info's number.
        "clock": {"kind": "clock"},
        "pc_info": {"kind": "select"},
    }

    def _plugin_manager(self):
        return getattr(self.app.service("plugins"), "manager", None)

    def _type_options(self, type_id, pm):
        """[{value, label}] for a type whose value is picked from a list, or
        None. Looked up now: OBS's scenes, the macros, a plugin's live list."""
        if type_id == "obs":
            obs = self.app.service("obs")
            scenes = list(getattr(obs, "scenes", []) or [])
            return ([{"value": "scene:%s" % s, "label": s} for s in scenes]
                    + [{"value": "record", "label": "OBS: Record"},
                       {"value": "stream", "label": "OBS: Stream"}])
        if type_id == "macro":
            macros = self.app.service("macros")
            try:
                names = macros.names() if macros is not None else {}
            except Exception:
                names = {}
            return [{"value": mid, "label": name}
                    for mid, name in sorted(names.items(), key=lambda e: str(e[1]).casefold())]
        if type_id == "page":
            return [{"value": self.model.page_name(p), "label": self.model.page_name(p),
                     "id": p} for p in self.model.available_pages()]
        if type_id == "pc_info":
            return [{"value": n, "label": self.model.translate("dp_pcinfo_" + n)}
                    for n in self.live.pc_info_names()]
        if pm is not None and type_id not in self.VALUE_KINDS:
            opts = pm.get_action_value_options(type_id)
            if opts is not None:
                return [{"value": v, "label": lbl} for lbl, v in opts]
        return None

    def action_types(self):
        """Every action a key can have, in menu order, for an editor:
        {types: [{id, label_key, label, kind, plugin, options?, hint_key?,
        browse?, editable?}], secondary: [ids], double: [ids]}.

        `label_key` is the translation key of a built-in type (None for OBS
        and for the plugins' types, whose `label` is their own name); `label`
        is in the current language. The order is the screen's (#109): None
        first, the built-in types by name, then the plugins' by name.
        `secondary` are the types an "also on press" step and a double-click
        may have. Runs on the caller's thread: a plugin's list of values may
        take a moment, and nothing here changes anything."""
        pm = self._plugin_manager()
        try:
            plugin_labels = pm.get_action_type_labels() if pm is not None else []
        except Exception:
            plugin_labels = []
        plugin_ids = {tid for tid, _l in plugin_labels}
        translate = self.model.translate
        out = []
        for tid, label in action_type_entries(translate, plugin_labels):
            if tid == SEPARATOR:
                continue
            plugin = tid in plugin_ids
            entry = {"id": tid, "label": label, "plugin": plugin,
                     "label_key": None if plugin else label_key(tid)}
            entry.update(self.VALUE_KINDS.get(tid, {"kind": "text"}))
            try:
                opts = self._type_options(tid, pm)
            except Exception:
                opts = None
            if opts is not None:
                entry["options"] = opts
                if plugin:
                    # the screen's dropdown for these is editable
                    entry["kind"] = "select"
                    entry["editable"] = True
            out.append(entry)
        return {"types": out, "secondary": list(SECONDARY_TYPES),
                "double": list(SECONDARY_TYPES)}

    def pictures(self):
        """Every picture path a key of any page shows or carries: the live
        map, the stored maps and the custom icons of page keys. An interface
        that only reads files it is allowed to asks this (a key may carry a
        picture from anywhere on disk)."""
        out = set()
        lock_pic = self.power.settings["lock"].get("picture")
        if lock_pic:
            out.add(lock_pic)     # the lock screen's own picture (power.py)
        for _ in range(3):        # the maps are written from widget threads
            try:
                maps = [dict(self.model.images)]
                maps += [dict(m) for m in list(self.model.page_images.values())
                         if isinstance(m, dict)]
                for m in maps:
                    out.update(v for v in m.values() if isinstance(v, str) and v)
                for acts in list(self.model.page_actions.values()):
                    for a in list(acts or []):
                        if isinstance(a, dict) and isinstance(a.get("icon"), str):
                            out.add(a["icon"])
                return out
            except RuntimeError:
                continue
        return out

    def clear_key(self, idx, origin=None):
        """Blank one key of the page on the pad: picture and action."""
        self.model.clear_key_image(idx)
        self.model.set_key_text(self.model.current_page, idx, None)
        self.save_page_action(self.model.current_page, idx, "none", "", origin=origin)
        self.start_upload()

    def clear_page(self, origin=None):
        """Blank every key of the page on the pad (its page keys on Main
        stay). Waits for an animation to stop; refused during an upload."""
        if self.uploading:
            return False
        if self.animating:
            self.stop_animation()
            self.app.scheduler.call_later(500, self.clear_page, origin)
            return False
        self.clear_page_now(origin)
        return True

    def clear_page_now(self, origin=None):
        page_btns, new_actions = self.model.clear_page()
        # The services follow the page's actions, and this is the one writer
        # that does not go through save_page_action, so it has to ask for
        # itself. Without it, clearing a page left a widget's thread running
        # and painting a key nobody had assigned it to (#97).
        self.schedule_service_sync()
        self.model.persist_cleared_page(page_btns, new_actions)
        # Upload blank images to device — set flag immediately to block key listener
        # before the scheduled callback fires.
        self.uploading = True
        self.app.scheduler.call_later(200, self.start_upload)
        self.publish("page_cleared", page=self.model.current_page, origin=origin)

    def apply_fullscreen(self, path, say=None, on_tile=None):
        """Put one picture or GIF over the whole panel of the page on the pad.
        `say(code, level, **params)` hears how it goes (codes are translation
        keys, level info/ok/warn/error), `on_tile(idx)` is called per key for
        anything that draws its own preview. True if something was applied.

        This is the single seam of the fullscreen pipeline: it takes the
        whole-pad picture (or GIF) and stores it with the page; the upload
        worker then writes it to the 800 x 240 panel (protocol.upload_panel),
        every frame of a GIF in turn. Nothing else needs to know how."""
        def _say(code, level="info", **kw):
            if say:
                say(code, level, **kw)

        _save_to_dp_fs_library(path)
        name = os.path.basename(path)
        try:
            src = dpi.PanelSource(path)
            animated = src.animated
            src.close()
        except Exception as e:
            _say("dp_error", "error", err=str(e))
            return False
        self.model.set_panel(path)
        for idx in range(NUM_KEYS):
            if on_tile:
                on_tile(idx)
        _say("dp_fullscreen_gif" if animated else "dp_fullscreen_static", "ok", name=name)
        return True

    # ── Fullscreen: one picture or GIF over the whole panel ───────────────────

    @staticmethod
    def _is_animated(path):
        return dpi._open_animated(path) is not None

    def fullscreen_of(self, page):
        """What covers the page: {kind: "gif"|"picture", path} or None."""
        path = self.model.page_fullscreen.get(page)
        if path and os.path.isfile(path):
            return {"kind": "gif" if self._is_animated(path) else "picture", "path": path}
        return None

    @staticmethod
    def panel_geometry():
        """The panel and where each key's window sits in it (approximate,
        protocol.KEY_PANEL_RECTS), for an interface drawing key previews."""
        return {"w": protocol.PANEL_W, "h": protocol.PANEL_H,
                "keys": [list(r) for r in protocol.KEY_PANEL_RECTS]}

    def set_fullscreen(self, page, path, origin=None):
        """Put `path` (a picture or an animated GIF, already checked) over the
        whole panel of `page`. On the page on the pad it goes through
        apply_fullscreen and is sent; on another page it is stored and shows
        when the page comes up. Returns {page, kind, queued}."""
        model = self.model
        kind = "gif" if self._is_animated(path) else "picture"
        if page != model.current_page:
            _save_to_dp_fs_library(path)
            model.page_fullscreen[page] = path
            if page == 0:
                _save_displaypad_fullscreen(path)
            else:
                model.save_sub_pages()
            self._fullscreen_changed(page, origin)
            return {"page": page, "kind": kind, "queued": False}
        if self.uploading:
            raise CommandError("the DisplayPad is busy, try again in a moment")
        queued = bool(self.animating)
        if queued:
            self.stop_animation()
            self.app.scheduler.call_later(500, self._apply_fullscreen_now, page, path, origin)
        else:
            self._apply_fullscreen_now(page, path, origin)
        return {"page": page, "kind": kind, "queued": queued}

    def _apply_fullscreen_now(self, page, path, origin=None):
        if page != self.model.current_page:
            return
        if self.apply_fullscreen(path):
            self.uploading = False
            self.app.scheduler.call_later(200, self.start_upload)
            self._fullscreen_changed(page, origin)

    def _fullscreen_changed(self, page, origin=None):
        self.publish("fullscreen_changed", page=page, origin=origin)
        self.publish("key_changed", page=page, idx=None, origin=origin)

    def clear_fullscreen(self, page, origin=None):
        """Take the fullscreen off `page`. The keys keep their own pictures
        and actions. True if there was a fullscreen."""
        model = self.model
        current = page == model.current_page
        if not model.page_fullscreen.get(page):
            return False
        if current and self.uploading:
            raise CommandError("the DisplayPad is busy, try again in a moment")
        if not model.clear_fullscreen(page):
            return False
        if current:
            if self.animating:
                self.stop_animation()
                self.app.scheduler.call_later(600, self.start_upload)
            else:
                self.app.scheduler.call_later(200, self.start_upload)
        self._fullscreen_changed(page, origin)
        return True

    # ── Pages: creating, renaming, deleting ───────────────────────────────────

    def create_page(self, name, origin=None):
        """A new page nothing points at yet (#52). Returns its id."""
        pid = self.model.create_named_page(name)
        self.publish("pages_changed", origin=origin)
        return pid

    def add_page(self, name, back_to, origin=None):
        """A new page with a back key to `back_to`. Returns its id."""
        pid = self.model.add_page(name, back_to=back_to)
        self.publish("pages_changed", origin=origin)
        return pid

    def rename_page(self, page_id, name, origin=None):
        self.model.rename_page(page_id, name)
        self.publish("pages_changed", origin=origin)

    def delete_page(self, page_id, origin=None):
        """Remove a page, leaving it first if it is on the pad."""
        ok = self.model.delete_page(page_id, switch_to=self.switch_page)
        if ok:
            self.publish("page_deleted", page=int(page_id))
            self.publish("pages_changed", origin=origin)
        return ok

    def set_page_timeout(self, page, mode, seconds, target):
        """A page's auto-timeout (#45); re-armed at once if it is on the pad."""
        self.model.set_page_timeout(page, mode, seconds, target)
        if page == self.model.current_page:
            self.runner.arm_page_timeout(page)
