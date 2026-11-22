#!/usr/bin/env python3
"""
Checks where widget frames go: into memory, not onto the disk (#105).

    python3 tools/test_frame_dir.py

A clock, a system monitor or a pipe text key redraws up to once a second, and
every redraw was a new file in ~/.config/mountain-time-sync, whether or not
anything was looking. The frames are only how a widget hands its picture to the
pad and the editor, so they belong in $XDG_RUNTIME_DIR, which is memory.

No hardware, no display: the configuration goes to a temporary directory, and
the variants of the environment run in a python of their own each.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-62s %s" % (name, detail), flush=True)
    if not ok:
        failures.append(name)


tmp = tempfile.mkdtemp(prefix="basecamp-frames-test-")
home = os.path.join(tmp, "home")
run = os.path.join(tmp, "run")
os.makedirs(home)
os.makedirs(run, mode=0o700)


def dirs(**env):
    """FRAME_DIR and CONFIG_DIR as a fresh import sees them under `env`."""
    e = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
    e["HOME"] = home
    e.update({k: v for k, v in env.items() if v is not None})
    r = subprocess.run(
        [sys.executable, "-c",
         "from shared.config import FRAME_DIR, CONFIG_DIR\n"
         "print('FRAME=' + FRAME_DIR); print('CONFIG=' + CONFIG_DIR)"],
        cwd=HERE, env=e, capture_output=True, text=True)
    if r.returncode != 0:
        return (None, r.stderr.strip().splitlines()[-1:])
    got = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    return got.get("FRAME"), got.get("CONFIG")


frame, config = dirs(XDG_RUNTIME_DIR=run)
check("with a runtime directory, frames go there",
      frame == os.path.join(run, "basecamp-linux", "frames"), frame)
check("which exists, and only its owner can enter it",
      frame and os.path.isdir(frame)
      and stat.S_IMODE(os.stat(frame).st_mode) == 0o700,
      frame and oct(stat.S_IMODE(os.stat(frame).st_mode)))
check("and is not the configuration directory", frame != config, (frame, config))

frame, config = dirs()
check("without one, the configuration directory as before", frame == config,
      (frame, config))
frame, config = dirs(XDG_RUNTIME_DIR=os.path.join(tmp, "not-there"))
check("nor when it names a directory that does not exist", frame == config,
      (frame, config))
ro = os.path.join(tmp, "readonly")
os.makedirs(ro, mode=0o500)
frame, config = dirs(XDG_RUNTIME_DIR=ro)
check("nor when it cannot be written to", frame == config or os.geteuid() == 0,
      (frame, config))

# In this process from here on: the runtime directory of the test.
os.environ["HOME"] = home
os.environ["XDG_RUNTIME_DIR"] = run
from shared.config import FRAME_DIR, CONFIG_DIR           # noqa: E402
from devices.displaypad import panel as dpp               # noqa: E402

check("the panel takes a frame in memory for one",
      dpp._widget_frame_of(os.path.join(FRAME_DIR, "dp_clock_p2_k5.png")) == (2, 5))
check("and still one in the config directory, from a widget not yet updated",
      dpp._widget_frame_of(os.path.join(CONFIG_DIR, "dp_mon_p0_k3.png")) == (0, 3))
check("but not a label the application drew, wherever it is",
      dpp._widget_frame_of(os.path.join(FRAME_DIR, "dp_label_p0_k1.png")) is None
      and dpp._widget_frame_of(os.path.join(CONFIG_DIR, "dp_label_p0_k1.png")) is None)
check("nor a picture of the user's that happens to have such a name",
      dpp._widget_frame_of(os.path.join(home, "Pictures", "dp_clock_p0_k1.png")) is None)

# The bundled Now Playing files its frame in memory.
import importlib.util                                     # noqa: E402
from PIL import Image                                     # noqa: E402

spec = importlib.util.spec_from_file_location(
    "np_plugin", os.path.join(HERE, "plugins", "now_playing", "__init__.py"))
np_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(np_mod)


class FakePad:
    _current_page = 0

    def __init__(self):
        self._images = {}
        self._page_images = {}


class Ctx:
    def __init__(self):
        self.pad = FakePad()
        self.pushed = []

    def get_displaypad(self):
        return self.pad

    def get_displaypad_current_page(self):
        return self.pad._current_page

    def push_displaypad_image(self, key, img):
        self.pushed.append(key)


np = np_mod.Plugin.__new__(np_mod.Plugin)
np.ctx = Ctx()
np._show_on_key(0, 4, Image.new("RGB", (102, 102), (9, 9, 9)))
filed = np.ctx.pad._images.get("4", "")
check("Now Playing files its cover in memory",
      os.path.dirname(filed) == FRAME_DIR and os.path.isfile(filed), filed)
check("and nothing of it in the config directory",
      not [n for n in os.listdir(CONFIG_DIR) if n.startswith("dp_now_playing")],
      [n for n in os.listdir(CONFIG_DIR) if n.startswith("dp_")])

os.chmod(ro, 0o700)
shutil.rmtree(tmp, ignore_errors=True)
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
