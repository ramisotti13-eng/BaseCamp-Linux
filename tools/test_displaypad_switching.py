#!/usr/bin/env python3
"""
Plays page switches against a simulated DisplayPad and checks what each key
ends up showing.

    python3 tools/test_displaypad_switching.py

Issues #104, #107, #112 and #99 all describe the pad (or the editor's grid)
showing the wrong thing after a page switch: a widget key left black until its
value happened to change, keys of the page just left still on the pad, and a
replugged pad with half its keys empty. None of that needs real hardware to
see. The panel's USB helpers are swapped for a twelve-key memory, the real
widget plugins run against it (from the basecamp-plugins checkout beside this
one, when there is one), and after each step the memory is compared with what
the page says each key should carry.

It needs a display, and no DisplayPad. Its config goes to a temporary
directory, and Monitor Mode is kept from starting, so a keyboard attached to
the machine is never touched.
"""
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
PLUGIN_REPO = os.environ.get(
    "BASECAMP_PLUGINS", os.path.join(os.path.dirname(REPO), "basecamp-plugins"))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("no display, skipping the page switch checks")
    sys.exit(0)

_TMP_HOME = tempfile.mkdtemp(prefix="basecamp-dp-switch-test-")
os.environ["HOME"] = _TMP_HOME
# The control socket would otherwise land beside a running application's.
_RUN = tempfile.mkdtemp(prefix="dpsw-", dir="/tmp")
os.environ["XDG_RUNTIME_DIR"] = _RUN

from PIL import Image                              # noqa: E402

import gui                                         # noqa: E402
from devices.displaypad import panel as dpp        # noqa: E402
from devices.everest_max import panel as emp       # noqa: E402
from shared import hid_compat                      # noqa: E402
from shared.config import (CONFIG_DIR, FRAME_DIR,             # noqa: E402
                           _save_displaypad_page_record)

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-62s %s" % (name, detail), flush=True)
    if not ok:
        failures.append("%s: %s" % (name, detail))


# ── Keep the keyboard out of it ───────────────────────────────────────────────

emp.EverestMaxPanel._start_cpu_auto = lambda self: None
emp.EverestMaxPanel._start_cpu_auto_clean = lambda self: None
emp.EverestMaxPanel._toggle_cpu = lambda self: None
if hasattr(gui.App, "_start_cpu_auto_clean"):
    gui.App._start_cpu_auto_clean = lambda self: None


# ── The simulated pad ─────────────────────────────────────────────────────────

class FakePad:
    """Twelve keys of pixel memory, and a plug that can be pulled."""

    def __init__(self):
        self.lock = threading.Lock()
        self.keys = [None] * 12
        self.present = True
        self.fail_uploads = 0       # this many key writes raise, then it recovers
        self.fail_in = None         # only writes made from this function fail
        self.writes = 0
        self.log = []               # (time, caller, key, outcome) for a failure report

    def power_cycle(self):
        with self.lock:
            self.keys = [None] * 12


PAD = FakePad()
_FAKE_PATH = b"/fake/displaypad"


class _FakeHid:
    nonblocking = False

    def read(self, _size, timeout=None):
        time.sleep((timeout or 0) / 1000.0)
        return []

    def write(self, data):
        return len(data)

    def close(self):
        pass


class _FakeUsb:
    pass


_real_enumerate = hid_compat.enumerate
_real_open_path = hid_compat.open_path


def _fake_enumerate(vid=0, pid=0, *a, **kw):
    if vid == dpp.VID and pid == dpp.PID:
        return ([{"interface_number": 3, "path": _FAKE_PATH,
                  "vendor_id": vid, "product_id": pid}] if PAD.present else [])
    return _real_enumerate(vid, pid, *a, **kw)


def _fake_open_path(path, *a, **kw):
    if path == _FAKE_PATH:
        if not PAD.present:
            raise OSError("open failed")
        return _FakeHid()
    return _real_open_path(path, *a, **kw)


def _fake_open_interfaces():
    if not PAD.present:
        raise RuntimeError("DisplayPad Interface 3 not found")
    return _FakeUsb(), _FakeHid()


def _fake_upload_button(_usb, _hid, key_index, bgr, key_events=None):
    time.sleep(0.01)
    caller = sys._getframe(1).f_code.co_name
    with PAD.lock:
        if not PAD.present:
            PAD.log.append((time.monotonic(), caller, key_index, "gone"))
            raise RuntimeError("[Errno 19] No such device (it may have been disconnected)")
        if PAD.fail_uploads > 0 and PAD.fail_in in (None, caller):
            PAD.fail_uploads -= 1
            PAD.log.append((time.monotonic(), caller, key_index, "failed"))
            raise RuntimeError(f"No ready response for key {key_index}")
        PAD.keys[key_index] = bytes(bgr)
        PAD.writes += 1
        PAD.log.append((time.monotonic(), caller, key_index,
                        STATIC.get(bytes(bgr), "widget" if any(bgr) else "black")))


# DPSW_REAL=1 runs it against a DisplayPad that is plugged in: the USB
# helpers stay real, and only what each key is sent gets noted, so the same
# comparison works. Unplugging cannot be played there and is left out.
REAL = bool(os.environ.get("DPSW_REAL"))
if REAL:
    _real_upload_button = dpp._upload_button

    def _noted_upload_button(usb_dev, hid_dev, key_index, bgr, key_events=None):
        caller = sys._getframe(1).f_code.co_name
        with PAD.lock:
            if PAD.fail_uploads > 0 and PAD.fail_in in (None, caller):
                PAD.fail_uploads -= 1
                PAD.log.append((time.monotonic(), caller, key_index, "failed"))
                raise RuntimeError(f"No ready response for key {key_index}")
        _real_upload_button(usb_dev, hid_dev, key_index, bgr, key_events)
        with PAD.lock:
            PAD.keys[key_index] = bytes(bgr)
            PAD.log.append((time.monotonic(), caller, key_index,
                            STATIC.get(bytes(bgr), "widget" if any(bgr) else "black")))
    dpp._upload_button = _noted_upload_button
else:
    hid_compat.enumerate = _fake_enumerate
    hid_compat.open_path = _fake_open_path
    dpp._open_interfaces = _fake_open_interfaces
    dpp._init_device = lambda _hid: None
    dpp._close_interfaces = lambda _usb, _hid: None
    dpp._upload_button = _fake_upload_button
if os.environ.get("DPSW_TRACE_SAVE"):
    import shared.config as _cfg
    _real_record = _cfg._save_displaypad_page_record

    def _traced_record(page_id, record, _real=_real_record):
        frames = {k: os.path.basename(v) for k, v in (record.get("buttons") or {}).items()
                  if v and _FRAME_NAME.search(os.path.basename(v))}
        if frames:
            print(f"      SAVED frames as icons on page {page_id}: {frames}")
            traceback.print_stack(limit=8)
        return _real(page_id, record)
    _cfg._save_displaypad_page_record = _traced_record

# It only looks, but during the simulated unplug it would find a real pad.
dpp.DisplayPadPanel._maybe_warn_usb_quirk = lambda self: None


# ── A configuration shaped like FransM's (#104) ───────────────────────────────

ICON_DIR = os.path.join(_TMP_HOME, "icons")
os.makedirs(ICON_DIR, exist_ok=True)
PIPE_DIR = os.path.join(_RUN, "pipes")
os.makedirs(PIPE_DIR, exist_ok=True)
PRODUCER = os.path.join(_TMP_HOME, "producer.py")
with open(PRODUCER, "w") as f:
    # Writes the same text every second, the way battery_status.py does: the
    # text only changes when the thing it reports does.
    f.write("import sys, time\n"
            "text, path = sys.argv[1], sys.argv[2]\n"
            "while True:\n"
            "    with open(path, 'w') as p:\n"
            "        p.write(text + '\\n')\n"
            "    time.sleep(1)\n")

STATIC = {}       # bytes -> "S:<page>:<key>"


def _static_icon(page, key):
    colour = (10 + 19 * key, 30 + 70 * page, 230 - 15 * key)
    path = os.path.join(ICON_DIR, f"s_p{page}_k{key}.png")
    Image.new("RGB", (102, 102), colour).save(path)
    STATIC[dpp._image_to_bgr102(path, 0)] = f"S:{page}:{key}"
    return path


def _pipe(name, text):
    return {"type": "pipe_text",
            "action": f"{PIPE_DIR}/{name}.pipe;{sys.executable} {PRODUCER} {text}"}


NONE = {"type": "none", "action": ""}

MAIN, CORES, KEYS = 0, 1, 2
PLUGIN_KEYS = {
    MAIN: {1, 2, 3, 5},
    CORES: {0, 1, 2, 3},
    KEYS: set(),
}
_main_actions = [NONE,
                 {"type": "clock_display", "action": "date,sec"},
                 _pipe("battery", "BATT"),
                 {"type": "mon_ram", "action": ""},
                 {"type": "page", "action": "Cores", "target": "Cores"},
                 {"type": "mon_disk", "action": "/"},
                 {"type": "page", "action": "Keys", "target": "Keys"},
                 NONE, NONE, NONE, NONE, NONE]
_cores_actions = [_pipe("core0", "C0"), _pipe("core1", "C1"),
                  _pipe("core2", "C2"), _pipe("core3", "C3")] + [NONE] * 8
_keys_actions = [{"type": "keypress", "action": f"F{i + 1}"} for i in range(12)]


_BLANK = os.path.join(CONFIG_DIR, "dp_blank.png")
os.makedirs(CONFIG_DIR, exist_ok=True)
Image.new("RGB", (102, 102), (0, 0, 0)).save(_BLANK)


def _page(pid, name, actions, timeout=None):
    # A widget key keeps the icon it had before the widget was put on it. For
    # a key that had been cleared that is the blank one, which is what
    # FransM's Main page stores on both of its widget keys K1 and K3.
    buttons = {str(k): (_BLANK if k in PLUGIN_KEYS[pid] else _static_icon(pid, k))
               for k in range(12)}
    _save_displaypad_page_record(pid, {
        "name": name, "v": 2, "actions": actions, "buttons": buttons,
        "fullscreen": None, "timeout": timeout})


_page(MAIN, "Main", _main_actions)
_page(CORES, "Cores", _cores_actions,
      timeout={"mode": "after", "seconds": 4, "target": "Main"})
_page(KEYS, "Keys", _keys_actions)

_plugins_dir = os.path.join(CONFIG_DIR, "plugins")
os.makedirs(_plugins_dir, exist_ok=True)
HAVE_PLUGINS = all(os.path.isdir(os.path.join(PLUGIN_REPO, p))
                   for p in ("dp_clock", "dp_pipe_text", "system_monitor"))
if HAVE_PLUGINS:
    for p in ("dp_clock", "dp_pipe_text", "system_monitor"):
        shutil.copytree(os.path.join(PLUGIN_REPO, p), os.path.join(_plugins_dir, p))
else:
    print(f"no plugin checkout at {PLUGIN_REPO}, skipping the widget checks")
    sys.exit(0)


# ── Reading the pad back ──────────────────────────────────────────────────────

# Widgets whose picture changes by itself while the test runs: the file a
# frame came from may already hold the next one, so on these keys any widget
# picture that is not another key's counts. The pipe widgets write the same
# text for ever, so theirs must be exactly the frame of this page and key.
LIVE = {(MAIN, 1), (MAIN, 3), (MAIN, 5)}
_FRAME_NAME = re.compile(r"dp_([a-z_]+?)_p(\d+)_k(\d+)\.png$")


def _frames():
    """Every widget frame there is, by its pixels: bytes -> "F:<page>:<key>".
    In memory since #105, in the config directory before."""
    out = {}
    for where in (CONFIG_DIR, FRAME_DIR):
        for name in os.listdir(where):
            m = _FRAME_NAME.match(name)
            if not m:
                continue
            try:
                out[dpp._image_to_bgr102(os.path.join(where, name), 0)] = \
                    f"F:{int(m.group(2))}:{int(m.group(3))}"
            except Exception:
                pass
    return out


def shows(k, frames=None):
    with PAD.lock:
        b = PAD.keys[k]
    if b is None:
        return "nothing"
    if not any(b):
        return "black"
    if b in STATIC:
        return STATIC[b]
    return (frames if frames is not None else _frames()).get(b, "widget")


def pad_matches(page):
    """Every key of `page` as it should be: its own icon, or its own widget."""
    frames = _frames()
    wrong = {}
    for k in range(12):
        got = shows(k, frames)
        if k not in PLUGIN_KEYS[page]:
            want = f"S:{page}:{k}"
            ok = got == want
        else:
            want = f"F:{page}:{k}"
            ok = got == want or ((page, k) in LIVE and got == "widget")
        if not ok:
            wrong[k + 1] = f"{got} (want {want})"
    return wrong


def grid_foreign(panel, page):
    """Keys whose editor tile is a frame file another page's widget wrote."""
    out = {}
    for k, path in panel._images.items():
        base = os.path.basename(path or "")
        if base.startswith("dp_") and "_p" in base:
            try:
                fpage = int(base.split("_p")[1].split("_")[0])
            except ValueError:
                continue
            if fpage != page:
                out[int(k) + 1] = base
    return out


app = gui.App()
app.geometry("1200x800")
panel = app._displaypad_panel


def dump_log(since, keys=None):
    for t, caller, k, what in PAD.log:
        if t >= since and (keys is None or k in keys):
            print(f"      {t - since:+6.2f}s {caller:<22} key {k + 1:<2} {what}")


def wait_until(cond, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        yield 0.1
    return cond()


def scenario():
    app._switch_device("displaypad")
    check("the device monitor outlived a slow start (#106)",
          any("_monitor_loop" in t.name for t in threading.enumerate()),
          [t.name for t in threading.enumerate()])
    ok = yield from wait_until(lambda: not pad_matches(MAIN), 12)
    check("start-up: Main is on the pad, widgets included", ok, pad_matches(MAIN))

    # A manual switch to a page with no widgets and back (#104, first report).
    panel._switch_to_page(KEYS)
    ok = yield from wait_until(lambda: not pad_matches(KEYS), 8)
    check("Keys page: every key carries its own icon", ok, pad_matches(KEYS))
    check("Keys page: no widget frame in the editor grid",
          not grid_foreign(panel, KEYS), grid_foreign(panel, KEYS))
    panel._switch_to_page(MAIN)
    ok = yield from wait_until(lambda: not pad_matches(MAIN), 10)
    check("back on Main: unchanged widgets (disk, battery) are drawn", ok,
          pad_matches(MAIN))
    check("back on Main: no other page's frame in the editor grid",
          not grid_foreign(panel, MAIN), grid_foreign(panel, MAIN))

    # To a page of pipe widgets, and back by its timeout (#104, 18 Sep).
    mark = time.monotonic()
    if os.environ.get("DPSW_LOG"):
        print("      before Cores: live[2]=", os.path.basename(panel._images.get("2", "")),
              "page0[2]=", os.path.basename(panel._page_images.get(0, {}).get("2", "")),
              "marked0=", sorted(panel._plugin_frame_keys.get(0, ())),
              "stored0=", {k: os.path.basename(v) for k, v in __import__("shared.config", fromlist=["x"])._load_displaypad_buttons().items()})
    panel._switch_to_page(CORES)
    if os.environ.get("DPSW_LOG"):
        print("      after switch: page0[2]=",
              os.path.basename(panel._page_images.get(0, {}).get("2", "")))
    ok = yield from wait_until(lambda: not pad_matches(CORES), 8)
    check("Cores page: its pipe widgets are drawn", ok, pad_matches(CORES))
    ok = yield from wait_until(lambda: panel._current_page == MAIN, 10)
    check("the Cores timeout took the pad back to Main", ok, panel._current_page)
    ok = yield from wait_until(lambda: not pad_matches(MAIN), 10)
    check("after the timeout: the battery key is drawn again", ok, pad_matches(MAIN))
    # Not just for a moment: the pipe widget re-reads its keys every two
    # seconds, and what it does then is what FransM saw.
    yield 5
    check("and still is a few seconds later", not pad_matches(MAIN), pad_matches(MAIN))
    if pad_matches(MAIN) or os.environ.get("DPSW_LOG"):
        dump_log(mark, keys={2})

    # Unplugged and plugged back in (#104, reconnect).
    if not REAL:
        PAD.present = False
        yield from wait_until(lambda: not panel._device_present, 6)
        PAD.power_cycle()
        PAD.present = True
        ok = yield from wait_until(lambda: not pad_matches(MAIN), 15)
        check("after a replug: every key of Main is back", ok, pad_matches(MAIN))

    # A widget key given another action, and later the widget back (#99).
    disk = os.path.join(FRAME_DIR, "dp_mon_p0_k5.png")
    # A widget that has nothing to draw yet, like Now Playing with nothing
    # playing: FransM moved a key from a disk monitor to it.
    app._plugin_manager._action_types["quiet_widget"] = {
        "label": "Quiet Widget", "handler": lambda _v: None, "owner": "test",
        "value_options": None}
    panel._save_page_action(MAIN, 5, "quiet_widget", "")
    ok = yield from wait_until(lambda: shows(5) == "black", 8)
    check("a widget key handed to another widget drops the first one's picture",
          ok, shows(5))
    panel._save_page_action(MAIN, 5, "none", "")
    ok = yield from wait_until(lambda: shows(5) == "black", 8)
    check("a widget key set to nothing stops showing the widget", ok, shows(5))
    check("in the editor as well",
          os.path.basename(panel._images.get("5", "")) == "dp_blank.png",
          os.path.basename(panel._images.get("5", "")))
    yield 3      # long enough for every widget to have had a pass
    panel._save_page_action(MAIN, 5, "mon_disk", "/")
    ok = yield from wait_until(lambda: shows(5) == "F:0:5", 10)
    check("given the same widget back, it draws again", ok, shows(5))

    # A frame composed for the page just left and filed a moment after the
    # switch, the way a widget that reads the page late used to (#99, #107).
    panel._switch_to_page(KEYS)
    yield from wait_until(lambda: not pad_matches(KEYS), 8)
    late = os.path.join(FRAME_DIR, f"dp_mon_p{KEYS}_k5.png")
    shutil.copy(disk, late)
    panel._images["5"] = late
    panel._page_images.setdefault(KEYS, {})["5"] = late
    panel.push_plugin_image(5, Image.open(late))
    ok = yield from wait_until(
        lambda: os.path.basename(panel._images.get("5", "")) == f"s_p{KEYS}_k5.png", 3)
    check("a late frame on a key no widget owns is put right in the editor",
          ok, os.path.basename(panel._images.get("5", "")))
    yield 1
    check("and never reaches the pad", shows(5) == f"S:{KEYS}:5", shows(5))
    os.remove(late)
    panel._switch_to_page(MAIN)
    yield from wait_until(lambda: not pad_matches(MAIN), 10)

    # An upload that fails part way through a switch (#112).
    PAD.fail_in = "_worker"
    PAD.fail_uploads = 1
    mark = time.monotonic()
    panel._switch_to_page(KEYS)
    ok = yield from wait_until(lambda: not pad_matches(KEYS), 15)
    check("a switch whose upload failed once still lands", ok, pad_matches(KEYS))
    if not ok or os.environ.get("DPSW_LOG"):
        dump_log(mark - 0.5)
    panel._switch_to_page(MAIN)
    ok = yield from wait_until(lambda: not pad_matches(MAIN), 10)
    check("and Main comes back whole after it", ok, pad_matches(MAIN))

    yield from hidden_scenario()


def hidden_scenario():
    """Nothing is drawn for a grid nobody can see (#105)."""
    calls = []
    real = panel._refresh_panel_tile

    def counted(idx):
        calls.append(idx)
        return real(idx)

    panel._refresh_panel_tile = counted
    try:
        app.withdraw()
        # Counted from the moment Tk knows the window is gone, not from the
        # call: a tile drawn in the milliseconds between is no work saved or
        # lost, and counting it made this check fail now and then.
        yield from wait_until(lambda: not panel._grid_on_screen(), 2)
        yield 0.2
        del calls[:]
        yield 3.5                       # the clock pushes about three times
        hidden = list(calls)
        app.deiconify()
        ok = yield from wait_until(lambda: 1 in calls[len(hidden):], 4)
        check("no key tile is redrawn while the window is hidden (#105)",
              hidden == [], hidden)
        check("and the clock's tile is brought up to date when it shows again",
              ok, calls[len(hidden):])
    finally:
        panel._refresh_panel_tile = real


def _finish():
    try:
        app.destroy()
    except Exception:
        pass


def _run(gen):
    def step():
        try:
            delay = next(gen)
        except StopIteration:
            app.after(0, _finish)
            return
        except Exception:
            traceback.print_exc()
            failures.append("scenario raised")
            app.after(0, _finish)
            return
        app.after(int(delay * 1000), step)
    app.after(1500, step)


_run(scenario())
# The window takes its time, as it does on a slower machine loading several
# plugins: the pad is found before the main loop runs, and Tk refuses to be
# told so until it does (#106).
time.sleep(2.5)
app.mainloop()
shutil.rmtree(_TMP_HOME, ignore_errors=True)
shutil.rmtree(_RUN, ignore_errors=True)
if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
