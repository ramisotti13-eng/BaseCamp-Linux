"""The DisplayPad's energy saving and lock screen.

Two settings, as in the original's "Ausschalten nach" and "Bildschirm
sperren", stored in CONFIG_DIR/displaypad_power.json:

    {"auto_off": {"enabled": false, "seconds": 10},
     "lock": {"mode": "none", "picture": null, "hours": 24}}

  auto_off  after `seconds` without a key press the keys go dark (brightness
            0). The next press brings the brightness back and does nothing
            else.
  lock      what the pad shows while the session is locked or the computer
            sleeps: "none" (the pages, as before), "clock" (the time across
            the pad, `hours` 24 or 12), "picture" (`picture` split over the
            twelve keys), "logo" (the BaseCamp Linux logo) or "off" (dark).
            Unlocking or waking brings the page on the pad back. While the
            lock screen holds the pad its keys do nothing.

PadPower decides; the service does. It runs on the core scheduler and tells
the service through three callbacks: on_dark(bool) for the brightness,
on_screen(picture or None) for the lock screen (a 612 x 204 picture, or None
for the page), and on_change() after any change, for an event. The live keys
are paused while the pad is dark or locked. The session's lock and sleep
come from core/platform/session.py, watched only while a lock mode other
than "none" is set. Every timer is the scheduler's, and stop() ends them and
the watcher.
"""
import json
import os
import time

from PIL import Image

from core.devices.displaypad import live as dpl

LOCK_MODES = ("none", "clock", "picture", "logo", "off")
SCREEN_MODES = ("clock", "picture", "logo")
AUTO_OFF_MAX = 99 * 3600 + 59 * 60 + 59

DEFAULTS = {"auto_off": {"enabled": False, "seconds": 10},
            "lock": {"mode": "none", "picture": None, "hours": 24}}


def settings_file():
    from shared.config import CONFIG_DIR
    return os.path.join(CONFIG_DIR, "displaypad_power.json")


def normalize(data):
    """A complete, valid settings dict from whatever was stored or sent."""
    data = data if isinstance(data, dict) else {}
    ao = data.get("auto_off") if isinstance(data.get("auto_off"), dict) else {}
    lk = data.get("lock") if isinstance(data.get("lock"), dict) else {}
    try:
        secs = int(float(ao.get("seconds", DEFAULTS["auto_off"]["seconds"])))
    except (TypeError, ValueError):
        secs = DEFAULTS["auto_off"]["seconds"]
    mode = lk.get("mode") if lk.get("mode") in LOCK_MODES else "none"
    pic = lk.get("picture") if isinstance(lk.get("picture"), str) and lk.get("picture") else None
    try:
        hours = 12 if int(lk.get("hours", 24)) == 12 else 24
    except (TypeError, ValueError):
        hours = 24
    return {"auto_off": {"enabled": bool(ao.get("enabled", False)),
                         "seconds": max(1, min(AUTO_OFF_MAX, secs))},
            "lock": {"mode": mode, "picture": pic, "hours": hours}}


def load_settings(path=None):
    try:
        with open(path or settings_file(), encoding="utf-8") as f:
            return normalize(json.load(f))
    except (OSError, ValueError):
        return normalize(None)


def save_settings(data, path=None):
    path = path or settings_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(normalize(data), f, indent=2)
    os.replace(tmp, path)


class PadPower:
    """Auto-off and the lock screen, on the core scheduler.

    `events` makes the session watcher (core.platform.session; a test's
    stand-in), `now` is the wall clock, `res_path` where resources/ is (the
    logo), `store` (load, save) the settings' persistence."""

    def __init__(self, scheduler, on_dark, on_screen, on_change=None, live=None,
                 events=None, now=time.time, res_path=None, store=None):
        self.scheduler = scheduler
        self.on_dark = on_dark
        self.on_screen = on_screen
        self.on_change = on_change or (lambda: None)
        self.live = live
        self._events_factory = events
        self.now = now
        self.res_path = res_path
        self._load, self._save = store or (load_settings, save_settings)
        self.settings = self._load()
        self.locked = False
        self.sleeping = False
        self.idle_dark = False
        self.watcher = None
        self.watching = False
        self.idle_handle = None
        self.clock_handle = None
        self.started = False
        self.stopped = False
        self._dark = False        # what on_dark was last told
        self._screen = None       # bytes of the lock picture last shown
        self._paused = False      # whether the live keys were paused

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def mode(self):
        return self.settings["lock"]["mode"]

    @property
    def away(self):
        """Locked or asleep."""
        return self.locked or self.sleeping

    @property
    def lock_showing(self):
        """The lock setting holds the pad (its screen, or dark)."""
        return self.away and self.mode != "none"

    @property
    def screen_active(self):
        return self.away and self.mode in SCREEN_MODES

    @property
    def dark(self):
        return self.idle_dark or (self.away and self.mode == "off")

    def state(self):
        return {"auto_off": dict(self.settings["auto_off"]),
                "lock": dict(self.settings["lock"]),
                "locked": self.locked, "sleeping": self.sleeping,
                "dark": self.dark, "screen": self.screen_active,
                "watching": self.watching}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Read the settings, arm the auto-off and watch the session if a
        lock mode is set. On the scheduler, once the pad runs."""
        if self.started or self.stopped:
            return
        self.started = True
        self._watch()
        self._arm_idle()

    def stop(self):
        self.stopped = True
        for name in ("idle_handle", "clock_handle"):
            h = getattr(self, name)
            if h is not None:
                self.scheduler.cancel(h)
                setattr(self, name, None)
        self._unwatch()

    # ── Settings ──────────────────────────────────────────────────────────────

    def set_auto_off(self, enabled, seconds):
        s = normalize(dict(self.settings, auto_off={"enabled": enabled, "seconds": seconds}))
        self.settings = s
        self._save(s)
        if not s["auto_off"]["enabled"] and self.idle_dark:
            self.idle_dark = False          # switched off while dark: light up
        self._arm_idle()
        self._update()

    def set_lock(self, mode=None, picture=None, hours=None):
        lk = dict(self.settings["lock"])
        if mode is not None:
            lk["mode"] = mode
        if picture is not None:
            lk["picture"] = picture or None
        if hours is not None:
            lk["hours"] = hours
        s = normalize(dict(self.settings, lock=lk))
        self.settings = s
        self._save(s)
        self._redraw()
        if self.started:
            self._watch()
        self._arm_idle()
        self._update()

    def reload(self):
        """Read the settings file again (a profile swap changed it under us)
        and bring the pad in line: the lock screen redrawn, the session
        watched or not, the auto-off counted from now. On the scheduler."""
        self.settings = self._load()
        self._redraw()
        if self.started:
            self._watch()
        self._arm_idle()
        self._update()

    # ── Events ────────────────────────────────────────────────────────────────

    def key_pressed(self, idx):
        """A key went down. True when the press is used up here: it woke the
        pad from its auto-off, or the lock screen holds the pad."""
        if self.lock_showing:
            return True
        if self.idle_dark:
            self.idle_dark = False
            self._arm_idle()
            self._update()
            return True
        self._arm_idle()
        return False

    def wake(self):
        """Light the pad up again if the auto-off turned it off (somebody
        changed its brightness) and count from now."""
        if self.idle_dark:
            self.idle_dark = False
            self._update()
        self._arm_idle()

    def session_event(self, kind, active):
        """From the session watcher, on the scheduler: ("lock"|"sleep", bool)."""
        if self.stopped:
            return
        was_away = self.away
        if kind == "lock":
            self.locked = bool(active)
        elif kind == "sleep":
            self.sleeping = bool(active)
        else:
            return
        if self.away == was_away and kind == "lock":
            return
        if was_away and not self.away:
            self.idle_dark = False           # whoever unlocked is back
        if kind == "sleep" and not active:
            self._redraw()                   # the time moved on while asleep
        self._arm_idle()
        self._update()

    # ── Watching the session ──────────────────────────────────────────────────

    def _watch(self):
        """Watch while a lock mode is set, and only then."""
        want = self.mode != "none"
        if want and self.watcher is None and self._events_factory is not None:
            try:
                self.watcher = self._events_factory()
                self.watching = bool(self.watcher.start(self._heard))
            except Exception as e:
                print(f"[DisplayPad] cannot follow the session lock: {e}", flush=True)
                self.watching = False
            if not self.watching:
                print("[DisplayPad] the session's lock and sleep cannot be followed "
                      "here (no gdbus?): the lock screen setting has no effect",
                      flush=True)
        elif not want and self.watcher is not None:
            self._unwatch()
            # Nobody tells us about the session any more.
            self.locked = self.sleeping = False

    def _unwatch(self):
        w, self.watcher = self.watcher, None
        self.watching = False
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass

    def _heard(self, kind, active):
        """notify() of the watcher: any thread; handed to the scheduler."""
        self.scheduler.call_soon(self.session_event, kind, active)

    # ── Timers ────────────────────────────────────────────────────────────────

    def _arm_idle(self):
        if self.idle_handle is not None:
            self.scheduler.cancel(self.idle_handle)
            self.idle_handle = None
        ao = self.settings["auto_off"]
        if (self.stopped or not self.started or not ao["enabled"] or self.idle_dark
                or self.lock_showing):
            return
        self.idle_handle = self.scheduler.call_later(ao["seconds"] * 1000, self._idle_fire)

    def _idle_fire(self):
        self.idle_handle = None
        if self.stopped or self.lock_showing or not self.settings["auto_off"]["enabled"]:
            return
        self.idle_dark = True
        self._update()

    def _arm_clock(self):
        if self.clock_handle is not None:
            self.scheduler.cancel(self.clock_handle)
            self.clock_handle = None
        if self.stopped or not (self.screen_active and self.mode == "clock"):
            return
        now = self.now()
        delay = dpl.next_due(("clock", {"seconds": False}), now) - now
        self.clock_handle = self.scheduler.call_later(
            max(0, int(round(delay * 1000))) + dpl.LiveKeys.SLACK_MS, self._clock_tick)

    def _clock_tick(self):
        self.clock_handle = None
        self._update()

    # ── Telling the service ───────────────────────────────────────────────────

    def screen_picture(self, now=None, lock=None):
        """The 612 x 204 picture of the lock setting (or of `lock`, for a
        preview), or None: none and off have no picture, and a picture that
        cannot be read has none either (the pad then shows black)."""
        lk = lock or self.settings["lock"]
        mode = lk["mode"]
        if mode == "clock":
            # A hair early is the time it was armed for.
            t = self.now() if now is None else now
            return dpl.render_clock_screen(t + dpl.LiveKeys.EARLY, lk["hours"])
        if mode == "logo":
            from core.devices.displaypad.pages import default_res_path
            return dpl.render_logo_screen(self.res_path or default_res_path())
        if mode == "picture" and lk["picture"]:
            return dpl.render_picture_screen(lk["picture"])
        return None

    def _redraw(self):
        """Have the next _update() send the lock screen again if it is up
        (b"" matches no picture; None would mean it is not up)."""
        if self._screen is not None:
            self._screen = b""

    def _update(self):
        """Bring the pad in line with the state: live keys, brightness, the
        lock screen. In that order: the keys are drawn before the page they
        are on is sent again."""
        if self.stopped:
            return
        screen = self.screen_active
        paused = self.dark or self.lock_showing
        if paused != self._paused and self.live is not None:
            self._paused = paused
            if paused:
                self.live.pause()
            else:
                # Leaving the lock screen sends the whole page: no extra push.
                self.live.resume(push=self._screen is None)
        if screen:
            pic = self.screen_picture()
            if pic is None:
                pic = Image.new("RGB", (dpl.SCREEN_W, dpl.SCREEN_H), (0, 0, 0))
            data = pic.tobytes()
            if data != self._screen:
                self._screen = data
                self.on_screen(pic)
        elif self._screen is not None:
            self._screen = None
            self.on_screen(None)
        if self.dark != self._dark:
            self._dark = self.dark
            self.on_dark(self._dark)
        self._arm_clock()
        self.on_change()
