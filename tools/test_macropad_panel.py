#!/usr/bin/env python3
"""
Drives the MacroPad screen without a MacroPad.

    python3 tools/test_macropad_panel.py

Nobody on the team owns the device, so this is as close to trying it as we
get: it builds the real application, tells it a pad is plugged in, and then
does what a person would do. The key presses it feeds in are the reports two
owners actually captured in issue #85.

It needs a display, because the screen is Tk widgets and half of what is worth
checking is which of them are on screen. With no display it says so and exits
0, so it can sit in the same run as the hardware-free checks.

Its config goes to a temporary directory, so running it never touches the
config of whoever runs it.
"""
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("no display, skipping the screen checks")
    sys.exit(0)

# Before anything reads CONFIG_DIR.
_TMP_HOME = tempfile.mkdtemp(prefix="basecamp-macropad-test-")
os.environ["HOME"] = _TMP_HOME
# Widget frames live in the runtime directory (#105): never in the one of
# the application you are running.
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="basecamp-test-run-")

import gui                                      # noqa: E402
from devices.macropad import panel as mpp       # noqa: E402
from devices.macropad import controller as mp   # noqa: E402
from shared import config as cfg                # noqa: E402

# The screen's worker would spend the test trying to open a device that is not
# there. Everything it would carry is fed in by hand below, and the last block
# puts the real one back against a stand-in pad.
_REAL_START_WORKER = mpp.MacroPadPanel._start_worker
mpp.MacroPadPanel._start_worker = lambda self: None


class FakePad:
    """Stands in for controller.MacroPad: records what is sent, hands out
    whatever reports the test puts in the queue."""

    def __init__(self):
        self.dev = self                 # the worker reads through pad.dev
        self.sent = []
        self.reports = queue.Queue()
        self.key_events = []
        self.closed = False

    def init(self):
        self.sent.append("init")

    def read(self, _size, timeout=None):
        try:
            item = self.reports.get(timeout=(timeout or 0) / 1000.0)
        except queue.Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def drain_key_events(self):
        events, self.key_events = self.key_events, []
        return events

    def set_effect(self, effect, **kwargs):
        self.sent.append(("effect", effect, kwargs))

    def set_effect_auto(self, effect, **kwargs):
        # Wave and Tornado go out on a different command (#85); the panel
        # leaves that choice to the controller.
        self.sent.append(("effect", effect, kwargs))

    def set_key_colors(self, colors, brightness=0):
        self.sent.append(("colors", len(colors), brightness))

    def save(self, slot=0):
        self.sent.append("save")

    def close(self):
        self.closed = True


def pump(app, done, seconds=4.0):
    """Wait for a worker thread to get somewhere, keeping Tk alive meanwhile.

    The worker posts everything back through after(), so a plain sleep here
    would wait for something that cannot happen.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.update()
        if done():
            return True
        time.sleep(0.02)
    return False

_real_presence = gui._check_usb_presence
gui._check_usb_presence = (
    lambda vid, pid: True if (vid, pid) == (mp.VID, mp.PID)
    else _real_presence(vid, pid))

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-42s %s" % (name, detail))
    if not ok:
        failures.append("%s: %s" % (name, detail))


def key_report(byte, mask):
    """The report the pad sends while one key is held."""
    report = bytearray(mp.PAYLOAD_LEN)
    report[0] = 0x01
    report[byte] |= mask
    return bytes(report)


NOTHING_HELD = bytes([0x01] + [0] * (mp.PAYLOAD_LEN - 1))


def _load_actions_again():
    return cfg._load_macropad_actions()

app = gui.App()
app.geometry("1280x820")


def checks():
    app._switch_device("macropad")
    app.update()
    panel = app._macropad_panel

    # ── Binding an action to a key ───────────────────────────────────────────
    panel._show_key(4)                                  # M5
    check("selecting a key moves the inspector", panel._selected == 4, "M5")
    panel._type_var.set(app.T("action_type_shell"))
    panel._on_type_change(chosen=True)
    panel._value_var.set("echo hallo")
    panel._save_action()
    app.update()

    stored = json.load(open(os.path.join(cfg.CONFIG_DIR, "macropad_actions.json")))
    check("the binding reaches the config file",
          stored["actions"][4] == {"type": "shell", "action": "echo hallo"},
          json.dumps(stored["actions"][4]))
    check("the tile says what the key does",
          panel._tile_labels[4].cget("text") == "echo hallo",
          panel._tile_labels[4].cget("text"))

    panel._show_key(0)
    check("another key shows its own binding", panel._value_var.get() == "", "empty")
    panel._show_key(4)
    check("and coming back shows the first one again",
          panel._value_var.get() == "echo hallo", panel._value_var.get())

    # ── A press has to reach that action ─────────────────────────────────────
    # Everything below this line is the measurement from issue #85 in use.
    fired = []
    panel._run_action = lambda type_id, value: fired.append((type_id, value))

    panel._on_report(key_report(42, 0x20))              # M5
    app.update()
    check("a press runs the bound action",
          fired == [("shell", "echo hallo")], fired)

    panel._on_report(key_report(42, 0x20))
    app.update()
    check("holding it down is not a second press", len(fired) == 1, fired)

    panel._on_report(NOTHING_HELD)
    panel._on_report(key_report(42, 0x20))
    app.update()
    check("a bounce inside the debounce is dropped", len(fired) == 1, fired)

    panel._on_report(key_report(47, 0x10))              # M12
    app.update()
    check("the last key on the pad is M12", fired[-1] == ("none", ""), fired[-1])

    before = len(fired)
    panel._on_report(mp.pkt_init(True))                 # init echo
    panel._on_report(bytes([0xFF, 0xAA] + [0] * 62))    # generic ack
    app.update()
    check("command replies are not key presses", len(fired) == before, fired[before:])

    # Two keys at once: the pad sets both bits in one report, and both have to
    # come out of it. Counting matters here, so measure the growth.
    panel._on_report(NOTHING_HELD)
    panel._last_fire.clear()
    before = len(fired)
    both = bytearray(key_report(42, 0x02))              # M1
    both[47] |= 0x10                                    # and M12
    panel._on_report(bytes(both))
    app.update()
    check("two keys in one report fire twice",
          len(fired) - before == 2, "%d new" % (len(fired) - before))

    # ── Clicking, which is how anyone actually picks a key ───────────────────
    # customtkinter binds on its internal canvas and label, which is what a
    # real click lands on, so that is where the event has to be generated.
    # Generating it on the CTk widget itself reaches no binding at all and
    # would pass whether or not the tiles were clickable.
    def inner_targets(widget):
        return [getattr(widget, attr) for attr in ("_canvas", "_label")
                if getattr(widget, attr, None) is not None]

    for widget, what in ((panel._tiles[5], "tile"),
                         (panel._tile_labels[5], "summary"),
                         (panel._tile_bars[5], "colour strip")):
        for target in inner_targets(widget):
            panel._show_key(0)
            app.update()
            target.event_generate("<Button-1>", x=5, y=5)
            app.update()
            check("clicking the %s of M6 selects it" % what,
                  panel._selected == 5, "selected M%d" % (panel._selected + 1))

    # ── An edit must not vanish because the person clicked the next key ──────
    panel._show_key(2)
    panel._type_var.set(app.T("action_type_shell"))
    panel._on_type_change(chosen=True)
    panel._value_var.set("typed but never saved")
    panel._show_key(7)
    panel._show_key(2)
    app.update()
    check("an edit survives moving to another key",
          panel._value_var.get() == "typed but never saved",
          panel._value_var.get())
    check("and it is on disk",
          json.load(open(os.path.join(cfg.CONFIG_DIR, "macropad_actions.json"))
                    )["actions"][2]["action"] == "typed but never saved")

    # ── A value belongs to the type it was typed for (the #84 rule) ──────────
    panel._show_key(2)
    panel._type_var.set(app.T("action_type_folder"))
    panel._on_type_change(chosen=True)
    app.update()
    check("changing the type clears the old value",
          panel._value_var.get() == "", repr(panel._value_var.get()))

    panel._actions[3] = {"type": "shell", "action": "keep me"}
    panel._show_key(3)
    app.update()
    check("but loading a key does not clear it",
          panel._value_var.get() == "keep me", panel._value_var.get())
    check("and loading writes nothing back",
          panel._actions[3] == {"type": "shell", "action": "keep me"},
          panel._actions[3])

    # The separator is a heading. Picking it must leave the key alone.
    if "_separator" in panel._type_ids:
        panel._show_key(3)
        panel._type_var.set(panel._type_labels[panel._type_ids.index("_separator")])
        panel._on_type_change(chosen=True)
        app.update()
        check("picking the plugin heading leaves the key alone",
              panel._actions[3] == {"type": "shell", "action": "keep me"}
              and panel._type_var.get() == app.T("action_type_shell"),
              "%r / %r" % (panel._actions[3], panel._type_var.get()))

    # A key set to "does nothing" carries no leftover text.
    panel._show_key(3)
    panel._type_var.set(app.T("action_type_none"))
    panel._on_type_change(chosen=True)
    app.update()
    check("a key set to none keeps no leftover value",
          panel._actions[3] == {"type": "none", "action": ""}, panel._actions[3])

    # ── A binding to a plugin that is gone must survive being looked at ──────
    # The menu cannot show a type this installation does not have, so it falls
    # back to "does nothing". Saving that fallback would destroy the binding
    # for anyone who merely clicked the key on their way past.
    panel._actions[10] = {"type": "vanished_plugin", "action": "some value"}
    panel._show_key(10)
    app.update()
    check("a key bound to a missing type says so",
          "vanished_plugin" in panel._key_status.cget("text"),
          panel._key_status.cget("text"))
    panel._show_key(0)
    panel._show_key(10)
    app.update()
    check("and clicking past it leaves the binding alone",
          panel._actions[10] == {"type": "vanished_plugin", "action": "some value"},
          panel._actions[10])
    check("the Save button does not destroy it either",
          (panel._save_action() or
           panel._actions[10] == {"type": "vanished_plugin", "action": "some value"}),
          panel._actions[10])
    # Choosing a real type is still a real edit and must go through.
    panel._show_key(10)
    panel._type_var.set(app.T("action_type_url"))
    panel._on_type_change(chosen=True)
    panel._value_var.set("https://example.org")
    panel._commit()
    app.update()
    check("but deliberately re-typing it does save",
          panel._actions[10] == {"type": "url", "action": "https://example.org"},
          panel._actions[10])

    # ── The action types have to reach the same helpers the DisplayPad uses ──
    # Above this point _run_action was replaced by a recorder, so nothing had
    # actually run yet. Put the real one back and watch where it dispatches.
    panel._run_action = mpp.MacroPadPanel._run_action.__get__(panel)
    import shared.macros as macros_mod

    calls = []
    real = {name: getattr(macros_mod, name) for name in
            ("execute_macro", "simulate_keypress", "simulate_text",
             "_run_shell", "_run_xdg_open")}
    for name in real:
        setattr(macros_mod, name,
                lambda *a, _n=name, **kw: calls.append((_n, a)))
    real_load = cfg.load_macros
    cfg.load_macros = lambda: {"macros": {"Build and test": {"actions": [],
                                                             "repeat_mode": "once"}}}
    try:
        # A macro made on the Macros screen, bound to a MacroPad key.
        panel._actions[0] = {"type": "macro", "action": "Build and test"}
        panel._fire(0)
        pump(app, lambda: any(c[0] == "execute_macro" for c in calls), 2.0)
        check("a key runs a macro from the Macros screen",
              any(c[0] == "execute_macro" for c in calls), [c[0] for c in calls])

        for kind, value, helper in (("keypress", "ctrl+shift+m", "simulate_keypress"),
                                    ("text", "kind regards", "simulate_text"),
                                    ("shell", "echo hi", "_run_shell"),
                                    ("url", "https://example.org", "_run_xdg_open"),
                                    ("folder", "/tmp", "_run_xdg_open"),
                                    ("app", "discord", "_run_shell")):
            calls.clear()
            panel._actions[1] = {"type": kind, "action": value}
            panel._fire(1)
            pump(app, lambda: any(c[0] == helper for c in calls), 2.0)
            check("a %s key reaches %s" % (kind, helper),
                  any(c[0] == helper and value in c[1] for c in calls),
                  [c[0] for c in calls])

        # OBS goes through the OBS screen, the same way the DisplayPad does.
        obs_calls = []
        real_obs = app._obs_panel.execute_action
        app._obs_panel.execute_action = lambda *a: obs_calls.append(a)
        try:
            for value, expected in (("scene:Gaming", ("scene", "Gaming")),
                                    ("record", ("record",)),
                                    ("stream", ("stream",))):
                obs_calls.clear()
                panel._actions[1] = {"type": "obs", "action": value}
                panel._fire(1)
                app.update()
                check("an obs key sends %s" % value, obs_calls == [expected], obs_calls)
        finally:
            app._obs_panel.execute_action = real_obs

        # An unbound key must reach nothing at all.
        calls.clear()
        panel._actions[2] = {"type": "none", "action": ""}
        panel._fire(2)
        app.update()
        check("an unbound key runs nothing", not calls, calls)

        # A key still bound to a plugin type whose plugin is gone must not
        # have its stored value run as a shell command: that value was written
        # for the plugin, not for a shell.
        calls.clear()
        panel._actions[2] = {"type": "monitor_widget", "action": "CPU: all cores"}
        panel._fire(2)
        app.update()
        check("a removed plugin type runs nothing", not calls, calls)
    finally:
        for name, func in real.items():
            setattr(macros_mod, name, func)
        cfg.load_macros = real_load
        panel._run_action = lambda type_id, value: fired.append((type_id, value))
        panel._actions = _load_actions_again()

    # ── Lighting ─────────────────────────────────────────────────────────────
    panel._effect_var.set(app.T("mp_effect_wave"))
    panel._on_effect_change()
    app.update()
    check("the effect id follows the menu",
          panel._rgb["effect"] == mp.EFFECT_WAVE, panel._rgb["effect"])
    check("wave offers a speed", panel._speed_row.winfo_ismapped())
    check("wave hides the second colour",
          not panel._color_rows[2][0].winfo_ismapped())

    panel._effect_var.set(app.T("mp_effect_static"))
    panel._on_effect_change()
    app.update()
    check("static has no speed", not panel._speed_row.winfo_ismapped())
    check("static keeps a colour", panel._color_rows[1][0].winfo_ismapped())

    panel._effect_var.set(app.T("mp_effect_custom"))
    panel._on_effect_change()
    app.update()
    check("custom explains where its colours come from",
          panel._custom_hint.winfo_ismapped())

    # Back to an effect that uses everything. pack() appends, so a row that was
    # hidden and shown again has to be put back in its place, not under the
    # Apply button.
    panel._effect_var.set(app.T("mp_effect_breathing"))
    panel._on_effect_change()
    app.update_idletasks()
    order = [panel._bri_row.winfo_y(), panel._speed_row.winfo_y(),
             panel._color_rows[1][0].winfo_y(), panel._color_rows[2][0].winfo_y(),
             panel._rgb_buttons.winfo_y()]
    check("the rows come back in the right order",
          order == sorted(order) and len(set(order)) == len(order), order)

    # Applying with no pad must not queue anything for a thread that is not
    # running, and the screen has to say why nothing happened.
    panel._connected = False
    panel._apply_lighting()
    check("apply without a pad queues nothing", panel._jobs.qsize() == 0)
    check("and says the pad is not there",
          "MacroPad" in panel._rgb_status.cget("text"),
          panel._rgb_status.cget("text"))

    panel._connected = True
    panel._effect_var.set(app.T("mp_effect_static"))
    panel._on_effect_change()
    panel._apply_lighting()
    job = panel._jobs.get_nowait()
    check("static queues one effect, with one colour",
          job["kind"] == "effect" and job["effect"] == mp.EFFECT_STATIC
          and "color1" in job["kwargs"] and "color2" not in job["kwargs"],
          json.dumps(job, default=str))

    panel._effect_var.set(app.T("mp_effect_custom"))
    panel._on_effect_change()
    panel._apply_lighting()
    job = panel._jobs.get_nowait()
    check("custom queues twelve colours",
          job["kind"] == "colors" and len(job["colors"]) == mp.NUM_KEYS,
          str(job["colors"][:2]))

    # Sending the key colours puts the pad into Custom, so the menu has to
    # follow. It said Static while the pad ran Custom.
    panel._effect_var.set(app.T("mp_effect_wave"))
    panel._on_effect_change()
    panel._apply_key_colors()
    panel._jobs.get_nowait()
    app.update()
    check("applying key colours moves the menu to Custom",
          panel._effect_var.get() == app.T("mp_effect_custom")
          and panel._rgb["effect"] == mp.EFFECT_CUSTOM,
          panel._effect_var.get())

    # That button lives in the inspector, so its refusal has to show up there.
    panel._connected = False
    panel._apply_key_colors()
    app.update()
    check("apply colours without a pad says so in the inspector",
          "MacroPad" in panel._key_status.cget("text"),
          panel._key_status.cget("text"))
    panel._connected = True

    # ── Language ─────────────────────────────────────────────────────────────
    for code in ("en", "de", "en"):
        app._load_lang_code(code)
        app._apply_lang()
        app.update()
    check("both languages apply without a crash", True,
          panel._insp_title.cget("text"))
    check("the effect menu is translated in place",
          panel._effect_var.get() in panel._effect_names, panel._effect_var.get())
    check("the binding survives a language change",
          panel._tile_labels[4].cget("text") == "echo hallo",
          panel._tile_labels[4].cget("text"))

    # ── The thread that owns the pad ─────────────────────────────────────────
    # Everything above fed reports straight into the panel. This runs the real
    # worker against a stand-in device, which is the only way to check the
    # part that will actually carry a key press on someone's desk.
    fake = FakePad()
    real_macropad = mp.MacroPad
    mp.MacroPad = lambda *_a, **_kw: fake
    mpp.MacroPadPanel._start_worker = _REAL_START_WORKER
    fired.clear()
    panel._last_fire.clear()
    panel._held.clear()
    try:
        panel._connected = True
        panel._start_worker()
        check("the worker opens the pad and says hello",
              pump(app, lambda: "init" in fake.sent), fake.sent[:1])
        check("and reports itself ready",
              pump(app, lambda: panel._rgb_status.cget("text") == app.T("mp_ready")),
              panel._rgb_status.cget("text"))

        fake.reports.put(key_report(42, 0x02))          # M1
        check("a report from the pad reaches the action",
              pump(app, lambda: len(fired) == 1), fired)

        panel._effect_var.set(app.T("mp_effect_static"))
        panel._on_effect_change()
        panel._apply_lighting()
        check("a queued effect is sent by that same thread",
              pump(app, lambda: any(s[0] == "effect" for s in fake.sent
                                    if isinstance(s, tuple))),
              [s for s in fake.sent if isinstance(s, tuple)])

        panel._save_to_device()
        check("save to pad reaches the device",
              pump(app, lambda: "save" in fake.sent), fake.sent)

        panel.set_connected(False)
        check("unplugging ends the thread and closes the pad",
              pump(app, lambda: fake.closed and not panel._worker.is_alive()),
              "alive: %s" % panel._worker.is_alive())
    finally:
        mp.MacroPad = real_macropad
        mpp.MacroPadPanel._start_worker = lambda self: None

    # ── The worker has to survive a pad that misbehaves ──────────────────────
    # A cable pulled mid read, a command that fails, junk on the wire: none of
    # these may leave the screen with a dead thread and no key presses.
    slow, mpp._RETRY_S = mpp._RETRY_S, 0.05
    real_macropad = mp.MacroPad
    fired.clear()
    panel._run_action = lambda type_id, value: fired.append((type_id, value))
    mpp.MacroPadPanel._start_worker = _REAL_START_WORKER
    try:
        opened = []

        def flaky(*_a, **_kw):
            # The first pad hands out one key press and then breaks; every
            # pad after that is quiet.
            pad = FakePad()
            if not opened:
                pad.reports.put(key_report(42, 0x02))
                pad.reports.put(OSError("read error [Errno 19] No such device"))
            opened.append(pad)
            return pad

        mp.MacroPad = flaky
        panel._connected = True
        panel._start_worker()
        check("a read that fails makes the worker open the pad again",
              pump(app, lambda: len(opened) >= 2, 4.0), "%d opens" % len(opened))
        check("and the broken one was closed", opened[0].closed)
        check("the press before the failure still ran", fired == [("none", "")], fired)
        panel.set_connected(False)
        pump(app, lambda: not panel._worker.is_alive(), 3.0)

        # A command that raises must be reported, not swallowed, and must not
        # take the thread down with it.
        class RefusingPad(FakePad):
            def set_effect_auto(self, *_a, **_kw):
                raise OSError("write failed")

        refusing = []

        def refuse(*_a, **_kw):
            pad = RefusingPad()
            refusing.append(pad)
            return pad

        mp.MacroPad = refuse
        panel._connected = True
        panel._start_worker()
        pump(app, lambda: refusing and "init" in refusing[0].sent, 3.0)
        panel._jobs.put({"kind": "effect", "effect": 0, "kwargs": {}})
        check("a command that fails is put on screen",
              pump(app, lambda: "write failed" in panel._rgb_status.cget("text"), 3.0),
              panel._rgb_status.cget("text"))
        check("and the thread keeps serving",
              panel._worker.is_alive() and len(refusing) == 1,
              "%s, %d opens" % (panel._worker.is_alive(), len(refusing)))
        panel.set_connected(False)
        pump(app, lambda: not panel._worker.is_alive(), 3.0)

        # Junk on the wire: a short read, a report with another first byte, a
        # report of nothing but zeroes. None of them is a key press and none
        # of them may stop the thread.
        fired.clear()
        noisy = []

        def noise(*_a, **_kw):
            pad = FakePad()
            if not noisy:
                for junk in (b"", b"\x01", b"\x02" * mp.PAYLOAD_LEN,
                             bytes(mp.PAYLOAD_LEN), NOTHING_HELD):
                    pad.reports.put(junk)
                pad.reports.put(key_report(47, 0x10))       # a real one, last
            noisy.append(pad)
            return pad

        mp.MacroPad = noise
        panel._connected = True
        panel._start_worker()
        check("junk on the wire does not stop the worker",
              pump(app, lambda: fired == [("none", "")], 4.0),
              "%s, alive=%s" % (fired, panel._worker.is_alive()))
        panel.set_connected(False)
        pump(app, lambda: not panel._worker.is_alive(), 3.0)
    finally:
        mp.MacroPad = real_macropad
        mpp._RETRY_S = slow
        mpp.MacroPadPanel._start_worker = lambda self: None
        panel._connected = False

    # ── Starting the device thread ───────────────────────────────────────────
    # A pad that flaps could leave a 500 ms retry timer waking the application
    # forever, one more chain per flap, because "a worker is already running"
    # booked another retry instead of ending there.
    class _Stub:
        def __init__(self, alive=True):
            self._alive = alive

        def is_alive(self):
            return self._alive

    scheduled = []
    real_after = panel.after
    panel.after = lambda ms, *a, **kw: (scheduled.append(ms),
                                        real_after(ms, *a, **kw))[1]
    start = _REAL_START_WORKER.__get__(panel)
    try:
        panel._connected = True
        panel._worker, panel._stop = _Stub(), threading.Event()   # healthy
        scheduled.clear()
        start(), start(), start()
        check("a healthy worker books no retries",
              500 not in scheduled, scheduled)

        panel._stop.set()                                          # winding down
        scheduled.clear()
        start()
        check("a worker on its way out books one retry",
              scheduled.count(500) == 1, scheduled)

        panel._connected = False
        scheduled.clear()
        start()
        check("no pad means no thread and no retry", not scheduled, scheduled)
    finally:
        panel.after = real_after
        panel._worker, panel._stop = None, threading.Event()
        panel._connected = False

    # ── Clicking Apply at a pad that will not open ───────────────────────────
    # Those jobs are meant for a device that is not answering. Keeping them
    # would fire the whole backlog at once the moment it comes back.
    slow_retry, mpp._RETRY_S = mpp._RETRY_S, 0.05
    real_macropad = mp.MacroPad

    def refuse(*_a, **_kw):
        raise RuntimeError("no permission for /dev/hidraw2")

    mp.MacroPad = refuse
    mpp.MacroPadPanel._start_worker = _REAL_START_WORKER
    try:
        panel._connected = True
        panel._start_worker()
        for _ in range(20):
            panel._jobs.put({"kind": "save"})
        check("a backlog for an unreachable pad is dropped",
              pump(app, lambda: panel._jobs.qsize() == 0, 3.0),
              "%d left" % panel._jobs.qsize())
        check("and the screen says what went wrong",
              "permission" in panel._rgb_status.cget("text"),
              panel._rgb_status.cget("text"))
    finally:
        panel.set_connected(False)
        mp.MacroPad = real_macropad
        mpp._RETRY_S = slow_retry
        mpp.MacroPadPanel._start_worker = lambda self: None

    # ── Unplugging ───────────────────────────────────────────────────────────
    app.update()
    check("the banner comes back when the pad goes",
          panel._banner.winfo_ismapped())


def run():
    try:
        checks()
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("the screen raised, see the traceback above")
    finally:
        app.after(50, app.destroy)


app.after(1200, run)
# A Tk main loop that never gets its callback would hang a test run forever.
# Daemon, and cancelled on the way out: a live timer thread would otherwise
# hold the interpreter open until it fires and then exit non-zero on a run
# where everything passed.
_watchdog = threading.Timer(90, lambda: os._exit(1))
_watchdog.daemon = True
_watchdog.start()
app.mainloop()
_watchdog.cancel()

shutil.rmtree(_TMP_HOME, ignore_errors=True)

print()
if failures:
    print("%d check(s) failed:" % len(failures))
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("all checks passed")
