#!/usr/bin/env python3
"""
Checks the plugin path for pictures on the Everest Max D1-D4 keys (#113).

    python3 tools/test_everest_dbutton_push.py

No keyboard and no window: the screen's own methods run against a stand-in
for the application's event loop, and the controller that would upload to
the keyboard is replaced by a recorder. What is checked is the part that can
go wrong without anyone noticing: that Monitor Mode is paused once and given
back only if it was running, that a picture already on a key is not written
to the keyboard's flash again, that only the latest picture per key goes,
and that the plugin API refuses when there is no Everest Max.
"""
import os
import shutil
import sys
import tempfile
import threading
import time

_TMP_HOME = tempfile.mkdtemp(prefix="basecamp-dpush-test-")
os.environ["HOME"] = _TMP_HOME
# Widget frames live in the runtime directory (#105): never in the one of
# the application you are running.
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="basecamp-test-run-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image                                   # noqa: E402

from devices.everest_max import panel as emp            # noqa: E402
from shared import plugin_api                           # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-62s %s" % (name, detail), flush=True)
    if not ok:
        failures.append(name)


class Loop:
    """The interface thread: after() queues, pump() runs in order."""

    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()

    def after(self, _ms, fn):
        with self.lock:
            self.queue.append(fn)

    def pump(self, seconds=2.0):
        end = time.time() + seconds
        while time.time() < end:
            with self.lock:
                fn = self.queue.pop(0) if self.queue else None
            if fn is None:
                time.sleep(0.01)
                continue
            fn()


class FakeApp(Loop):
    def __init__(self):
        Loop.__init__(self)
        self._dev_present = {"everest_max": True}

    def _cmd(self, *args):
        return ["controller"] + list(args)


def make_panel(monitor_running=True):
    p = emp.EverestMaxPanel.__new__(emp.EverestMaxPanel)
    p._app = FakeApp()
    p._init_dbutton_push()
    p.events = []
    state = {"running": monitor_running}

    def stop():
        p.events.append("stop")
        was, state["running"] = state["running"], False
        return was

    def start():
        p.events.append("start")
        state["running"] = True

    p._stop_cpu_proc = stop
    p._start_cpu_auto = start
    p.state = state
    return p


uploads = []
fail_next = []


class Done:
    def __init__(self, rc):
        self.returncode, self.stdout, self.stderr = rc, "", "" if rc == 0 else "boom"


def fake_run(cmd, **kw):
    uploads.append(cmd)
    if fail_next:
        fail_next.pop()
        return Done(1)
    return Done(0)


emp.subprocess.run = fake_run
emp.time.sleep = lambda _s: None
cleared = []
emp._save_icon_last = lambda slot, name: cleared.append((slot, name))

red = Image.new("RGB", (200, 200), (255, 0, 0))
blue = Image.new("RGB", (200, 200), (0, 0, 255))
green = Image.new("RGB", (64, 64), (0, 255, 0))

# One picture.
p = make_panel()
ok = p.push_dbutton_image(0, red)
p._app.pump(1.0)
check("a picture for D1 is accepted", ok is True)
check("and uploaded to slot 0 by the controller",
      [c[1:3] for c in uploads] == [["upload", "0"]], uploads)
check("with Monitor Mode paused once and given back",
      p.events == ["stop", "start"], p.events)
check("the key's library record is dropped, it shows something else now",
      cleared == [(0, None)], cleared)

# The same picture again: nothing goes to the keyboard's flash.
uploads.clear(); p.events.clear()
p.push_dbutton_image(0, red.copy())
p._app.pump(0.5)
check("the same picture again is not written to flash again",
      uploads == [] and p.events == [], (uploads, p.events))

# Several at once: only the latest per key, one pause for all of them.
uploads.clear(); p.events.clear()
p.push_dbutton_image(0, blue)
p.push_dbutton_image(0, green)          # D1 again before anything ran
p.push_dbutton_image(3, blue)
p._app.pump(1.0)
check("only the latest picture per key is uploaded",
      sorted(c[2] for c in uploads) == ["0", "3"], [c[1:3] for c in uploads])
d1 = Image.open([c[3] for c in uploads if c[2] == "0"][0]).convert("RGB")
check("and it is the latest one", d1.getpixel((10, 10)) == (0, 255, 0),
      d1.getpixel((10, 10)))
check("the file is the key's size", d1.size == (72, 72), d1.size)
check("one pause of Monitor Mode for all of them", p.events == ["stop", "start"],
      p.events)

# A picture arriving while the upload runs joins the same pause.
uploads.clear(); p.events.clear()


def run_and_push(cmd, **kw):
    if cmd[2] == "1":
        p.push_dbutton_image(2, red)
    return fake_run(cmd, **kw)


emp.subprocess.run = run_and_push
p.push_dbutton_image(1, red)
p._app.pump(1.0)
emp.subprocess.run = fake_run
check("a picture arriving during an upload is taken in the same pause",
      [c[2] for c in uploads] == ["1", "2"] and p.events == ["stop", "start"],
      ([c[2] for c in uploads], p.events))

# Monitor Mode that was off stays off.
q = make_panel(monitor_running=False)
uploads.clear()
q.push_dbutton_image(0, red)
q._app.pump(1.0)
check("Monitor Mode that was off is not started", q.events == ["stop"], q.events)

# A failed upload is not taken for done.
uploads.clear()
fail_next.append(1)
r = make_panel()
r.push_dbutton_image(1, blue)
r._app.pump(1.0)
r.push_dbutton_image(1, blue)
r._app.pump(1.0)
check("a failed upload is tried again with the same picture", len(uploads) == 2,
      len(uploads))

check("a key outside D1-D4 is refused", r.push_dbutton_image(4, red) is False)


# ── The plugin API ───────────────────────────────────────────────────────────

class App:
    pass


ctx = plugin_api.PluginContext.__new__(plugin_api.PluginContext)
ctx._app = App()
check("with no Everest Max screen the API says no",
      ctx.push_keyboard_dbutton_image(0, red) is False)
ctx._app._everest_panel = make_panel()
ctx._app._dev_present = {"everest_max": False}
check("nor with the keyboard unplugged", ctx.push_keyboard_dbutton_image(0, red) is False)
ctx._app._dev_present = {"everest_max": True}
check("with it there, the picture is taken", ctx.push_keyboard_dbutton_image(0, red) is True)

shutil.rmtree(_TMP_HOME, ignore_errors=True)
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
