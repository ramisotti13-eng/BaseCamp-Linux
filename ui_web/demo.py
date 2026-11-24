"""Demo mode: the whole core against stand-in devices, in a throw-away
configuration directory, touching no hardware and nothing on the desktop.

For developing the web interface and for screenshots without devices. The
machine it runs on may well have real Mountain devices plugged in (the
development PC has an Everest Max, a Makalu 67 and a DisplayPad), so the
demo never reaches them:

  - presence is a stand-in: the DisplayPad, a Makalu 67, an Everest Max, an
    Everest 60 and a MacroPad are "plugged in", nothing else is;
  - every transport is a stand-in: a twelve-key DisplayPad in memory, a
    recorder in place of the Everest Max's controller command line (uploads
    that report progress, Monitor Mode as a fake process), a recorder for
    the Everest 60's controller, no controller process for the Makalu, a
    MacroPad in memory;
  - fence() replaces what lies below them as well (hidapi enumeration and
    opening, pyusb's find, the USB presence and access checks, the control
    socket's controller runs), so a code path the stand-ins miss finds no
    device instead of a real one;
  - key actions (keystrokes, text, commands, URLs, macros, plugin actions)
    are printed, not run; nothing goes on the network.

Order matters: shared.config fixes the configuration directory when it is
imported, so prepare() has to run before anything imports it:

    from ui_web import demo
    home = demo.prepare()        # HOME, APPDATA, XDG_RUNTIME_DIR -> a temp dir
    app, pad = demo.build()      # seeded configuration, stand-ins, build_core()
    start_core(app)
    ...
    app.stop(); demo.cleanup(home)
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

#: (vid, pid) the demo's devices answer to: DisplayPad, Makalu 67, Everest
#: Max, Everest 60 (ANSI), MacroPad.
PRESENT = {(0x3282, 0x0009), (0x3282, 0x0003), (0x3282, 0x0001),
           (0x3282, 0x0005), (0x3282, 0x0008)}

_prepared = None
#: The stand-ins of the last build(): the Everest 60's recorder and what the
#: MacroPad was told (for tests and for looking at what a screen sent).
everest60 = None
macropad_state = None


# ── The configuration directory ──────────────────────────────────────────────

def prepare(base=None):
    """Point the user's directories at a fresh temporary one. Returns it.

    Must run before shared.config is imported (it fixes CONFIG_DIR then);
    raises RuntimeError otherwise, since the demo would write into the
    user's real configuration."""
    global _prepared
    if _prepared is not None:
        return _prepared
    if "shared.config" in sys.modules:
        raise RuntimeError("demo.prepare() must run before shared.config is imported")
    home = tempfile.mkdtemp(prefix="basecamp-demo-", dir=base)
    run = os.path.join(home, "run")
    os.makedirs(run, mode=0o700)
    roaming = os.path.join(home, "AppData", "Roaming")
    os.makedirs(roaming)
    os.environ["HOME"] = home
    os.environ["USERPROFILE"] = home         # os.path.expanduser on Windows
    os.environ["APPDATA"] = roaming          # the configuration on Windows
    os.environ["LOCALAPPDATA"] = os.path.join(home, "AppData", "Local")
    os.environ["XDG_RUNTIME_DIR"] = run      # widget frames, the control socket
    os.environ.pop("SUDO_USER", None)
    os.environ.pop("WINEPREFIX", None)
    _prepared = home
    return home


def cleanup(home=None):
    shutil.rmtree(home or _prepared or "", ignore_errors=True)


# ── Stand-ins ────────────────────────────────────────────────────────────────

def _say(*parts):
    print("[demo]", *parts, flush=True)


class _NoReport:
    def read(self, _size, timeout=None):
        time.sleep(min(timeout or 0, 100) / 1000.0)
        return []

    def write(self, data):
        return len(data)

    def close(self):
        pass


class DemoPad:
    """The DisplayPad: twelve keys of memory, with protocol.py's names.
    press(key) makes a key go down and up as the pad reports it."""
    HID_AVAILABLE = True
    PYUSB_AVAILABLE = True
    #: seconds a key write takes (a real one takes about 40 ms)
    WRITE_S = 0.01

    def __init__(self):
        self.lock = threading.Lock()
        self.keys = [None] * 12
        self.brightness = None
        self.present = True
        self.writes = 0
        self._packets = []

    def find_command_path(self):
        return b"demo://displaypad/if3" if self.present else None

    def find_on_bus(self):
        return None

    def open_interfaces(self):
        if not self.present:
            raise RuntimeError("DisplayPad Interface 3 not found")
        return object(), _DemoHid(self)

    def open_command_interface(self):
        return _DemoHid(self)

    def init_device(self, _hid):
        pass

    def close_interfaces(self, _usb, _hid):
        pass

    def set_brightness(self, _hid, pct):
        self.brightness = pct

    def upload_button(self, _usb, _hid, key, bgr, key_events=None):
        time.sleep(self.WRITE_S)
        with self.lock:
            self.keys[key] = bytes(bgr)
            self.writes += 1

    def upload_panel(self, _usb, _hid, left, top, right, bottom, bgr, key_events=None, save=False):
        time.sleep(1.5 if save else self.WRITE_S * 12)      # a whole panel is a few keys' worth of data
        with self.lock:
            self.writes += 1

    def press(self, key):
        from core.devices.displaypad import protocol
        d = [0] * 64
        d[0] = 0x01
        bi, mask = protocol.KEY_MAP[key]
        d[bi] |= mask
        with self.lock:
            self._packets.append(d)
            self._packets.append([0x01] + [0] * 63)


class _DemoHid(_NoReport):
    def __init__(self, pad):
        self._pad = pad

    def read(self, size, timeout=None):
        with self._pad.lock:
            if self._pad._packets:
                return self._pad._packets.pop(0)
        return super().read(size, timeout)


class _DemoProcess:
    """A Monitor Mode controller that is not there: it "runs" until it is
    told to stop (terminate), as a real one does, and says so in the
    recorder it was started by."""
    _pids = iter(range(41000, 99999))

    def __init__(self, record=None, label="monitor"):
        self.pid = next(self._pids)
        self.returncode = None
        self._record = record if record is not None else []
        self._label = label

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            self._record.append(("stop", self._label))
        self.returncode = 0

    kill = terminate


#: Seconds a stand-in upload takes. The real flash write of a D-key picture
#: takes 45-60 s; the demo shows the same progress in a fraction of it.
#: BASECAMP_DEMO_FLASH_S overrides it (set 50 to see the real wait).
DEMO_FLASH_S = float(os.environ.get("BASECAMP_DEMO_FLASH_S", "9"))
DEMO_MAIN_S = DEMO_FLASH_S / 3


class DemoEverestMaxBackend:
    """The Everest Max service's backend, without a controller process.

    A recorder for the controller command line: every command the service
    runs is kept in `calls` (("run", args...), ("stream", args...),
    ("spawn", style), ("stop", "monitor")) and printed. An upload reports
    PROGRESS:n lines the way the controller prints them, over DEMO_FLASH_S
    seconds; a picture whose file name contains "fail" is refused the way a
    busy keyboard refuses ("Failed to claim interface"). Monitor Mode is a
    fake process that runs until it is stopped."""

    #: The media dock's settings as Mountain's SDK read them on a real
    #: keyboard.
    DOCK_SETTINGS = "01ff15f3cc23111e001e00011213141501003200000000000000000000"

    def __init__(self):
        self.calls = []
        self.monitor = None
        self.dock = bytes.fromhex(self.DOCK_SETTINGS)
        self.board = {"game_mode": 4, "leds": 0}      # the Settings page's game mode and LEDs

    def run(self, *args, timeout=None, env=None):
        from core.devices.everest_max.backend import Result
        self.calls.append(("run",) + tuple(map(str, args)))
        _say("everest_max controller", " ".join(map(str, args))[:160])
        time.sleep(0.15)
        if args and args[0] == "dock-settings":
            # The controller's read-modify-write, on the demo's own bytes.
            from core.devices.everest_max import protocol as P
            before = self.dock
            if len(args) > 2 and args[1] == "--set":
                self.dock = P.apply_dock_settings(self.dock, **json.loads(args[2]))
            return Result(0, json.dumps({"before": before.hex(), "raw": self.dock.hex()}) + "\n")
        if args and args[0] == "board-settings":
            from core.devices.everest_max import protocol as P
            if len(args) > 2 and args[1] == "--set":
                change = json.loads(args[2])
                if change.get("game_mode"):
                    self.board["game_mode"] = P.apply_game_mode(self.board["game_mode"], change["game_mode"])
                if change.get("leds") is not None:
                    self.board["leds"] = int(bool(change["leds"]))
            return Result(0, json.dumps(self.board) + "\n")
        if args and args[0] == "versions":
            return Result(0, json.dumps({"keyboard": 0x57, "bootloader": 0x05,
                                         "dock": 0x24, "numpad": 0x20}) + "\n")
        return Result(0, "ok\n")

    def stream(self, *args, on_progress=None, echo=False):
        from core.devices.everest_max.backend import Result
        self.calls.append(("stream",) + tuple(map(str, args)))
        _say("everest_max controller", " ".join(map(str, args)))
        path = next((str(a) for a in args[1:] if "/" in str(a) or "\\" in str(a)), "")
        if "fail" in os.path.basename(path.replace("\\", "/")).lower():
            time.sleep(0.4)
            return Result(1, error="Failed to claim interface 3")
        total = DEMO_MAIN_S if args and args[0] == "upload-main" else DEMO_FLASH_S
        steps = 25
        for i in range(steps + 1):
            line = "PROGRESS:%d" % round(i * 100 / steps)       # what the controller prints
            if on_progress:
                on_progress(int(line[len("PROGRESS:"):]))
            time.sleep(total / steps)
        return Result(0)

    def spawn_monitor(self, style, log_path):
        _say("everest_max Monitor Mode", style)
        self.calls.append(("spawn", style))
        self.monitor = _DemoProcess(self.calls, "monitor")
        return self.monitor

    def kill_strays(self):
        pass        # never a pkill: a real Monitor Mode may be running on this machine


class DemoMeters:
    """What Monitor Mode sends, moving gently (the host's real numbers would
    say nothing about a demo)."""

    def __init__(self):
        self._t0 = time.monotonic()

    def sample(self):
        import math
        t = time.monotonic() - self._t0
        return {"cpu": 22 + 18 * math.sin(t / 3), "ram": 48 + 4 * math.sin(t / 11),
                "disk": 61.0, "net_mbs": max(0.0, 1.4 + 1.2 * math.sin(t / 2)),
                "volume": 35 + 20 * math.sin(t / 9)}


class DemoKeyTrigger:
    """The Everest Max's software trigger (keytrigger.py) without /dev/input:
    it says it listens and hears nothing."""

    state = "off"

    def start(self, on_key):
        self.state = "listening"
        _say("everest_max key trigger", "listening for F13-F24")

    def stop(self):
        self.state = "off"


def demo_runner(cmd, **_kw):
    """subprocess.run for a ControllerBackend: no process, "ok"."""
    import types
    _say("controller", " ".join(map(str, cmd[-3:])))
    return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")


class DemoMakalu:
    """The Makalu controller (devices/makalu67/controller.py) as a runner for
    a ControllerBackend: no process, no hardware. It answers each verb as the
    controller prints it: "ok" for a setting the mouse took, `dpi get` with
    the five levels and the active one (0-based), `status` with "connected",
    and an "error: ..." line with exit code 1 for a missing or wrong argument.
    The DPI levels are kept, so what was set is what is read back."""

    FUNCTIONS = ("left", "right", "middle", "back", "forward", "dpi+", "dpi-",
                 "scroll_up", "scroll_down", "disabled")

    def __init__(self):
        self.levels = [400, 800, 1600, 3200, 6400]
        self.active = 1
        self.lock = threading.Lock()

    def _error(self, msg):
        import types
        return types.SimpleNamespace(returncode=1, stdout="", stderr="error: %s\n" % msg)

    def _ok(self, text="ok"):
        import types
        return types.SimpleNamespace(returncode=0, stdout=text + "\n", stderr="")

    def __call__(self, cmd, **_kw):
        cmd = [str(c) for c in cmd]
        # [python, script, verb ...] or [binary, verb ...]
        i = next((n for n, c in enumerate(cmd[:2])
                  if c.endswith("controller.py") or c.endswith("makalu-controller")
                  or c.endswith("makalu-controller.exe")), 0)
        args = cmd[i + 1:]
        _say("makalu", " ".join(args))
        if not args:
            return self._error("no command")
        verb, rest = args[0], args[1:]
        try:
            if verb == "status":
                return self._ok("connected: demo")
            if verb == "rgb":
                return self._rgb(rest)
            if verb == "lift-off":
                return self._choice(rest, ("low", "high"), "lift-off requires <low|high>")
            if verb == "angle-snapping":
                return self._choice(rest, ("on", "off"), "angle-snapping requires <on|off>")
            if verb == "debounce":
                return self._choice(rest, ("2", "4", "6", "8", "10", "12"),
                                    "debounce requires <ms>: 2, 4, 6, 8, 10, 12")
            if verb == "polling-rate":
                return self._choice(rest, ("125", "250", "500", "1000"),
                                    "polling-rate requires <hz>: 125, 250, 500, 1000")
            if verb == "dpi":
                return self._dpi(rest)
            if verb == "remap":
                if len(rest) < 2:
                    return self._error("remap requires <button 1-6> <function>")
                if int(rest[0]) < 1 or rest[1] not in self.FUNCTIONS:
                    return self._error("unknown button or function")
                return self._ok()
            if verb == "sniper":
                if len(rest) < 2:
                    return self._error("sniper requires <button 1-6> <dpi 50-19000>")
                int(rest[0]), int(rest[1])
                return self._ok()
        except ValueError as e:
            return self._error(str(e))
        return self._error("unknown command '%s'" % verb)

    def _choice(self, rest, allowed, usage):
        if not rest:
            return self._error(usage)
        return self._ok() if rest[0].lower() in allowed else self._error(usage)

    def _rgb(self, rest):
        if not rest:
            return self._error("rgb: subcommand required (off/static/breathing/rainbow/live)")
        sub, vals = rest[0], rest[1:]
        need = {"static": 3, "custom": 24, "code": 1, "code2": 7}.get(sub)
        if sub not in ("off", "breathing", "rainbow", "static", "custom", "code", "code2"):
            return self._error("rgb: unknown subcommand '%s'" % sub)
        if need is not None and len(vals) < need:
            return self._error("rgb %s: too few values" % sub)
        for v in vals:
            int(v)
        return self._ok()

    def _dpi(self, rest):
        with self.lock:
            if not rest:
                return self._error("dpi requires 'get' or 5 DPI values (P1 P2 P3 P4 P5)")
            if rest[0] == "get":
                return self._ok(" ".join(str(v) for v in self.levels) + " %d" % self.active)
            if len(rest) < 5:
                return self._error("dpi requires exactly 5 values: dpi <p1> <p2> <p3> <p4> <p5> [active]")
            levels = [min(19000, max(50, round(int(v) / 50) * 50)) for v in rest[:5]]
            active = int(rest[5]) if len(rest) > 5 else 1
            self.levels, self.active = levels, max(0, min(4, active - 1))
            return self._ok()


def no_macropad():
    raise OSError("demo mode: there is no MacroPad")
class DemoEverest60:
    """The Everest 60 controller (devices/everest60/controller.py) as a runner
    for a ControllerBackend: no process, no hardware. It answers "ok" like the
    real one, "error: ..." with exit code 1 for arguments the controller would
    refuse, and records every call in `calls` (a list of argument lists) and
    the last per-key map in `per_key`, so a test or a person can see what the
    screen sent."""

    SUBS = {"off": 0, "static": 4, "breathing": 9, "wave": 10, "tornado": 6,
            "reactive": 8, "matrix": 8, "yeti": 8,
            "breathing-rainbow": 2, "wave-rainbow": 3, "tornado-rainbow": 3}

    def __init__(self):
        self.calls = []
        self.per_key = None
        self.lock = threading.Lock()

    def _result(self, code, out="", err=""):
        import types
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)

    def __call__(self, cmd, **_kw):
        import json
        cmd = [str(c) for c in cmd]
        i = next((n for n, c in enumerate(cmd[:2])
                  if c.endswith("controller.py") or c.endswith("everest60-controller")
                  or c.endswith("everest60-controller.exe")), 0)
        args = cmd[i + 1:]
        with self.lock:
            self.calls.append(args)
        if not args:
            return self._result(1, err="error: no command\n")
        verb, rest = args[0], args[1:]
        if verb == "status":
            return self._result(0, "connected: demo\n")
        if verb == "rgb":
            sub = rest[0] if rest else ""
            need = self.SUBS.get(sub)
            if need is None:
                return self._result(1, err="error: rgb: unknown subcommand '%s'\n" % sub)
            if len(rest) - 1 < need:
                return self._result(1, err="error: rgb %s: too few values\n" % sub)
            _say("everest60 rgb", " ".join(rest))
            return self._result(0, "ok\n")
        if verb == "per-key-rgb":
            try:
                data = json.loads(rest[0])
                assert len(data["leds"]) == 64, "64 key colours needed"
                assert len(data.get("side") or []) in (0, 44), "44 side colours or none"
            except Exception as e:
                return self._result(1, err="error: per-key-rgb: %s\n" % e)
            with self.lock:
                self.per_key = data
            _say("everest60 per-key-rgb: 64 keys%s, brightness %s" % (
                " + side ring" if data.get("side") else "", data.get("brightness")))
            return self._result(0, "ok\n")
        return self._result(1, err="error: unknown command '%s'\n" % verb)


class DemoMacroPadState:
    """What the MacroPad stand-in was told, shared by every opening of it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.effects = []          # (effect, kwargs)
        self.colors = None
        self.saved = 0
        self.packets = []          # key reports waiting to be read
        self.opened = 0


class DemoMacroPad:
    """A MacroPad in memory, for MacroPadService.use_opener(): what
    controller.MacroPad offers the worker (init, close, dev.read, the key
    event queue, the effect, colour and save calls), nothing on a bus.
    press_macropad(state, key) makes a key go down and up as the pad reports it."""

    def __init__(self, state):
        self.state = state
        self.dev = _DemoMacroPadHid(state)
        with state.lock:
            state.opened += 1

    def init(self):
        return b"\xff\xaa"

    def close(self):
        pass

    def drain_key_events(self):
        return []

    def set_effect_auto(self, effect, **kwargs):
        _say("macropad effect", effect, kwargs)
        with self.state.lock:
            self.state.effects.append((effect, kwargs))

    def set_key_colors(self, colors, brightness=100):
        _say("macropad key colours (%d), brightness %s" % (len(colors), brightness))
        with self.state.lock:
            self.state.colors = [tuple(c) for c in colors]

    def save(self, slot=0):
        _say("macropad saved to the pad")
        with self.state.lock:
            self.state.saved += 1


class _DemoMacroPadHid(_NoReport):
    def __init__(self, state):
        self._state = state

    def read(self, size, timeout=None):
        with self._state.lock:
            if self._state.packets:
                return self._state.packets.pop(0)
        return super().read(size, timeout)


def press_macropad(state, key):
    """A report that says key `key` (0-11) went down, then one with all up."""
    from devices.macropad import controller as mp
    down = [0] * mp.PAYLOAD_LEN
    down[0] = 0x01
    byte, mask = mp.KEY_MAP[key]
    down[byte] |= mask
    up = [0x01] + [0] * (mp.PAYLOAD_LEN - 1)
    with state.lock:
        state.packets += [down, up]


def demo_presence(vid, pid):
    return (vid, pid) in PRESENT


class NoNetwork:
    """The update check's HTTP and the plugin service's net."""

    def _refuse(self, *a, **k):
        raise OSError("demo mode: no network")
    get_json = get_text = open = fetch_index = download_repo = _refuse


#: The demo's plugin repository: what the "index" lists and "downloads". Nothing
#: leaves the machine; a download writes a small plugin into a temporary folder.
DEMO_REPO = "https://github.com/demo/basecamp-demo-plugins"
_PLUGIN_BODY = "class Plugin:\n    def __init__(self, ctx):\n        pass\n"
DEMO_PLUGINS = [
    # id, name, installed version, published version, types, description, extra manifest fields
    ("demo_clock", "Demo Clock", "1.0", "1.2", ["action"],
     "Shows the time on a key and speaks it on press.", {}),
    ("demo_monitor", "Demo System Monitor", "2.1", "2.1", ["panel", "service"],
     "CPU and memory on a panel. The panel is drawn by the classic interface.", {}),
    ("demo_needs", "Demo Needs A Package", "1.0", "1.0", ["service"],
     "Wants a Python package that is not installed here.",
     {"requires": ["demo-missing-package"]}),
    ("demo_broken", "Demo Broken Plugin", "0.3", "0.3", ["action"],
     "Fails while it loads, to show the red dot and the message.", {"_broken": True}),
    ("demo_off", "Demo Switched Off", "1.0", "1.0", ["action"],
     "Installed, but disabled.", {}),
    ("demo_weather", "Demo Weather", None, "1.0", ["action", "service"],
     "Published, not installed yet.", {}),
    ("demo_notes", "Demo Notes", None, "0.9", ["panel"],
     "Published, not installed yet. Has a panel.", {}),
]


def _plugin_manifest(pid, name, version, types, description, extra):
    m = {"id": pid, "name": name, "version": version, "type": types,
         "description": description, "author": "BaseCamp demo", "entry": "__init__"}
    m.update({k: v for k, v in extra.items() if not k.startswith("_")})
    return m


def seed_plugins(plugins_dir):
    """The installed demo plugins, written where the plugin manager looks."""
    for pid, name, have, _pub, types, desc, extra in DEMO_PLUGINS:
        if have is None:
            continue
        d = os.path.join(plugins_dir, pid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
            json.dump(_plugin_manifest(pid, name, have, types, desc, extra), f)
        body = ("raise RuntimeError('demo: this plugin does not load')\n"
                if extra.get("_broken") else _PLUGIN_BODY)
        with open(os.path.join(d, "__init__.py"), "w", encoding="utf-8") as f:
            f.write(body)
    from shared.config import PLUGINS_DISABLED_FILE
    with open(PLUGINS_DISABLED_FILE, "w", encoding="utf-8") as f:
        json.dump(["demo_off"], f)


class DemoPluginNet:
    """The plugin service's net: an index of the demo plugins, and downloads
    that unpack a demo plugin from memory into a temporary folder."""

    def fetch_index(self, timeout=8):
        return [{"id": pid, "name": name, "version": pub, "type": types,
                 "description": desc, "author": "BaseCamp demo",
                 "url": "%s/tree/main/%s" % (DEMO_REPO, pid)}
                for pid, name, _have, pub, types, desc, _extra in DEMO_PLUGINS]

    def download_repo(self, url):
        tmp = tempfile.mkdtemp(prefix="basecamp-demo-dl-")
        root = os.path.join(tmp, "basecamp-demo-plugins-main")
        for pid, name, _have, pub, types, desc, extra in DEMO_PLUGINS:
            d = os.path.join(root, pid)
            os.makedirs(d)
            with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
                json.dump(_plugin_manifest(pid, name, pub, types, desc, extra), f)
            with open(os.path.join(d, "__init__.py"), "w", encoding="utf-8") as f:
                f.write(_PLUGIN_BODY)
        _repo, _branch, sub = _split_plugin_url(url)
        return tmp, root, sub


def _split_plugin_url(url):
    from core.plugins import repo_branch_path
    return repo_branch_path(url)


class DemoHttp:
    """The update check's HTTP: a release list from nowhere. By default a newer
    version than ours is "published"; BASECAMP_DEMO_UPDATE=none makes it up to
    date. Nothing is ever downloaded: see _fake_download()."""

    def __init__(self, version):
        self.version = version

    def get_json(self, url, timeout=5):
        if os.environ.get("BASECAMP_DEMO_UPDATE", "").lower() == "none":
            return []
        base = self.version.split("-")[0]
        parts = [int(p) if p.isdigit() else 0 for p in base.split(".")]
        while len(parts) < 3:
            parts.append(0)
        parts[1] += 1
        newer = ".".join(str(p) for p in parts)
        return [{"tag_name": "v" + newer, "prerelease": False, "draft": False,
                 "assets": [{"name": "BaseCamp-%s.AppImage" % newer,
                             "browser_download_url": "demo://release/%s.AppImage" % newer}]}]

    def get_text(self, url, max_bytes=256, timeout=10):
        raise OSError("demo mode: no network")

    def open(self, url, timeout=15):
        raise OSError("demo mode: no network")


def _fake_download(app, svc):
    """update.download_and_install for the demo: progress events from a timer,
    no download and nothing replaced."""
    def run():
        def say(event, **p):
            app.scheduler.call_soon(lambda: svc.publish(event, **p))
        for pct in range(0, 101, 10):
            say("progress", pct=pct)
            time.sleep(0.15)
        say("installing")
        time.sleep(0.4)
        say("done", kind="appimage")

    def download_and_install(info=None, **_kw):
        _say("update: simulated download (nothing is fetched)")
        threading.Thread(target=run, daemon=True, name="demo-update").start()
    return download_and_install


class DemoObs:
    """OBS's WebSocket client: three scenes, actions printed."""
    SCENES = ("Camera", "Be right back", "Desktop")

    def __init__(self, *_a, **_k):
        pass

    def get_scene_list(self):
        import types
        return types.SimpleNamespace(scenes=[{"sceneName": s} for s in self.SCENES])

    def set_current_program_scene(self, name):
        _say("OBS scene", name)

    def toggle_record(self):
        _say("OBS record")

    def toggle_stream(self):
        _say("OBS stream")

    def disconnect(self):
        pass


def fence():
    """Make every way to a device below the stand-ins find nothing, and
    every way to the desktop print what it would have done."""
    from shared import hid_compat
    from core.platform import usb as platform_usb, input as platform_input
    from core.platform import launcher
    import core.control
    import core.macros.engine as engine

    def no_device(*_a, **_k):
        raise OSError("demo mode: no hardware")
    hid_compat.enumerate = lambda *a, **k: []
    hid_compat.open_path = no_device
    if os.environ.get("BASECAMP_DEMO_MAKALU", "").lower() == "max":
        # The Makalu screen with a Makalu Max: only the model lookup sees it; opening still fails.
        hid_compat.HID_AVAILABLE = True
        hid_compat.enumerate = lambda vid=None, pid=None, *a, **k: (
            [{"interface_number": 1, "path": b"demo"}] if (vid, pid) == (0x3282, 0x0002) else [])
    # The Everest 60 asks the HID bus which model it is (ANSI or ISO); the demo
    # answers by itself. BASECAMP_DEMO_E60=iso shows the ISO board.
    import core.devices.everest60.device as e60_device
    iso = os.environ.get("BASECAMP_DEMO_E60", "").lower() == "iso"
    e60_device.detect = lambda: (0x0006, "Everest 60 ISO") if iso else (0x0005, "Everest 60")
    try:
        import usb.core
        usb.core.find = lambda *a, **k: None
    except ImportError:
        pass
    platform_usb.usb_present = lambda vid, pid: False
    platform_usb.device_nodes = lambda vid, pid: []
    platform_usb.access_denied = lambda vid, pid, nodes=None: []
    # The Everest Max's key trigger reads /dev/input: it finds nothing here.
    from core.devices.everest_max import keytrigger
    keytrigger.find_nodes = lambda root=None: []

    def run_controller(cmdline, timeout=None):
        _say("controller", " ".join(map(str, cmdline[-3:])))
        return {"ok": True, "code": 0, "stdout": "ok", "stderr": ""}
    core.control.run_controller = run_controller

    platform_input.simulate_keypress = lambda key: _say("keypress", key)
    platform_input.simulate_text = lambda text: _say("type text", repr(text))
    platform_input.run_tool = lambda tool, *a: _say("input tool", tool, *a)
    platform_input.run_tool_output = lambda tool, *a: ""
    if not os.environ.get("BASECAMP_DEMO_NO_TOOLS"):     # set it to see the "no tool" hint
        platform_input.tools_status = lambda: ("xdotool", "x11")
    launcher.run_shell = lambda command: _say("run", command)
    launcher.open_target = lambda target: _say("open", target)
    def run_actions(actions, stop_event=None):
        _say("macro with %d step(s)" % len(actions or []))
        if stop_event is not None:      # a toggle macro repeats until stopped
            stop_event.wait(1.0)
    engine.run_actions = run_actions


# ── The demo's configuration ─────────────────────────────────────────────────

#: Key pictures drawn for the demo: name -> (background, text colour, label).
_BLUE, _ORANGE, _NIGHT, _WHITE = (0, 56, 226), (253, 157, 2), (20, 31, 58), (255, 255, 255)
PICTURES = {
    "term": (_NIGHT, _ORANGE, ">_"), "web": (_BLUE, _WHITE, "WEB"),
    "files": (_NIGHT, _WHITE, "DIR"), "mute": (_ORANGE, _NIGHT, "MUTE"),
    "cpu": (_NIGHT, _ORANGE, "CPU"), "build": (_BLUE, _WHITE, "F5"),
    "git": (_NIGHT, _ORANGE, "GIT"), "save": (_BLUE, _WHITE, "SAVE"),
    "find": (_NIGHT, _WHITE, "FIND"), "rec": ((200, 20, 40), _WHITE, "REC"),
    "live": (_ORANGE, _NIGHT, "LIVE"), "cam": (_BLUE, _WHITE, "CAM"),
    "brb": (_NIGHT, _ORANGE, "BRB"), "chat": (_BLUE, _WHITE, "CHAT"),
}


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                   # Pillow < 10.1
        return ImageFont.load_default()


def draw_picture(path, background, colour, label, size=102):
    """A key picture: the label on a rounded tile."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, size - 5, size - 5), radius=14, fill=background)
    font = _font(30 if len(label) <= 3 else 24)
    box = d.textbbox((0, 0), label, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), label, fill=colour, font=font)
    img.save(path)
    return path


def draw_gif(path, size=102):
    """A small animation for a key: a dot going round."""
    from PIL import Image, ImageDraw
    import math
    frames = []
    for i in range(12):
        img = Image.new("RGB", (size, size), _NIGHT)
        d = ImageDraw.Draw(img)
        a = i * math.tau / 12
        cx, cy = size / 2 + 28 * math.cos(a), size / 2 + 28 * math.sin(a)
        d.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=_ORANGE)
        frames.append(img)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=90, loop=0)
    return path


def _act(type_, action="", **extra):
    out = {"type": type_, "action": action}
    out.update(extra)
    return out


def seed():
    """Three DisplayPad pages with keys, pictures, hints and a note, and the
    other files a first start would find. Returns {name: picture path}."""
    import shared.config as cfg
    pics_dir = os.path.join(cfg.CONFIG_DIR, "demo-pictures")
    os.makedirs(pics_dir, exist_ok=True)
    pics = {name: draw_picture(os.path.join(pics_dir, name + ".png"), *spec)
            for name, spec in PICTURES.items()}
    pics["spinner"] = draw_gif(os.path.join(pics_dir, "spinner.gif"))
    none = _act("none")

    main = [
        _act("page", "Editor", target="Editor"),
        _act("page", "Streaming", target="Streaming"),
        _act("shell", "x-terminal-emulator"),
        _act("url", "https://github.com/ramisotti13-eng/BaseCamp-Linux"),
        _act("folder", "~"),
        _act("keypress", "XF86AudioMute",
             double=_act("keypress", "XF86AudioPlay")),
        _act("text", "Hello from BaseCamp Linux"),
    ] + [dict(none) for _ in range(5)]
    main_pics = {"2": pics["term"], "3": pics["web"], "4": pics["files"],
                 "5": pics["mute"], "6": pics["chat"]}
    editor = [
        _act("page", "Main", target="Main"),
        _act("keypress", "F5"),
        _act("keypress", "ctrl+s"),
        _act("keypress", "ctrl+shift+f"),
        _act("shell", "git status"),
        _act("keypress", "ctrl+grave"),
    ] + [dict(none) for _ in range(6)]
    editor_pics = {"1": pics["build"], "2": pics["save"], "3": pics["find"],
                   "4": pics["git"], "5": pics["term"]}
    streaming = [
        _act("page", "Main", target="Main"),
        _act("obs", "record"),
        _act("obs", "stream"),
        _act("obs", "scene:Camera"),
        _act("obs", "scene:Be right back"),
        _act("keypress", "XF86AudioMicMute"),
        dict(none),
    ] + [dict(none) for _ in range(5)]
    streaming_pics = {"1": pics["rec"], "2": pics["live"], "3": pics["cam"],
                      "4": pics["brb"], "5": pics["mute"], "6": pics["spinner"]}

    cfg._save_displaypad_actions(main)
    cfg._save_displaypad_buttons(main_pics)
    cfg._save_displaypad_pages({
        "1": {"v": 2, "buttons": editor_pics, "actions": editor, "fullscreen": None},
        "2": {"v": 2, "buttons": streaming_pics, "actions": streaming, "fullscreen": None},
    })
    cfg._save_displaypad_page_names({0: "Main", 1: "Editor", 2: "Streaming"})
    cfg._save_displaypad_page_hints(0, note="", keys={
        "0": "Editor keys", "1": "Streaming keys", "2": "Terminal",
        "5": "Mute (double: play)"}, show=True)
    cfg._save_displaypad_page_hints(1, note="Keys for coding", keys={
        "1": "Build", "2": "Save", "3": "Find in files", "4": "Git status"}, show=True)
    cfg._save_displaypad_page_hints(2, note="OBS: record, stream, scenes", keys={
        "1": "Record", "2": "Go live", "3": "Camera scene", "4": "Be right back"},
        show=True)
    seed_everest_max(pics_dir)
    seed_everest60()
    seed_macropad()
    return pics


def draw_main_picture(path, size=(240, 204)):
    """A picture for the Everest Max's main display: night blue with a beak-orange wedge."""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", size, _NIGHT)
    d = ImageDraw.Draw(img)
    d.polygon([(0, h * 0.78), (w, h * 0.42), (w, h * 0.58), (0, h)], fill=_ORANGE)
    d.polygon([(0, h * 0.86), (w, h * 0.58), (w, h * 0.64), (0, h * 0.94)], fill=_BLUE)
    font = _font(30)
    d.text((18, 26), "BaseCamp", fill=_WHITE, font=font)
    d.text((18, 62), "Linux", fill=_ORANGE, font=font)
    img.save(path)
    return path


def seed_everest_max(pics_dir):
    """What an Everest Max that has been set up shows: four D-keys with
    actions and pictures (in the picture library, as an upload leaves them),
    a picture on the main display, an effect, per-key colours and the clock."""
    import shared.config as cfg
    cfg.save_buttons([
        {"icon": 7, "type": "shell", "action": "x-terminal-emulator"},
        {"icon": 7, "type": "url", "action": "https://github.com/ramisotti13-eng/BaseCamp-Linux"},
        {"icon": 7, "type": "keypress", "action": "XF86AudioMute"},
        {"icon": 7, "type": "none", "action": ""},
    ])
    for i, name in enumerate(("term", "web", "mute")):
        lib = cfg._save_to_library(os.path.join(pics_dir, name + ".png"))
        cfg._save_icon_last(i, lib)
    main_png = draw_main_picture(os.path.join(pics_dir, "main_display.png"))
    cfg._save_icon_last("main", cfg._save_to_main_library(main_png))
    cfg.save_rgb_settings({"effect": "Wave", "speed": 40, "brightness": 80,
                           "color1": [0, 56, 226], "color2": [253, 157, 2],
                           "direction": "→ L→R"})
    # per-key colours: a blue-to-orange sweep over the keys, the ring in the logo blue
    from core.devices.everest_max import layout
    leds = [(20, 20, 20)] * layout.NUM_LEDS
    xs = [k[2] for k in layout.LAYOUT]
    lo, hi = min(xs), max(xs)
    for lbl, idx, x, y, w, h in layout.LAYOUT:
        if idx is None:
            continue
        f = (x - lo) / max(1, hi - lo)
        leds[idx] = (int(0 + 253 * f), int(56 + 101 * f), int(226 - 224 * f))
    cfg._save_per_key(leds, [(0, 56, 226)] * layout.NUM_SIDE_LEDS, 90)
    # Key binding: Pause plays and pauses, Caps Lock is off, Scroll Lock opens a web page
    # (through F13), the dial turned right is the next profile.
    from core.devices.everest_max import keybindings as KB
    keymap = KB.empty()
    keymap["keys"] = {126: {"function": "media", "value": "play_pause"}, 30: {"function": "disable"},
                      125: {"function": "url", "value": "https://github.com/ramisotti13-eng/BaseCamp-Linux",
                            "fkey": 230},
                      187: {"function": "profile", "value": "next"}}
    KB.save(os.path.join(cfg.CONFIG_DIR, KB.FILE_NAME), keymap)
    with open(os.path.join(cfg.CONFIG_DIR, "clock_format"), "w") as f:
        f.write("24H")
    cfg.save_config("digital")
    with open(cfg.MAIN_MODE_FILE, "w") as f:
        f.write("cpu")


def seed_everest60():
    """A saved lighting (Wave) and a per-key map (a colour gradient, the
    modifiers white), as a keyboard somebody already configured."""
    import colorsys
    import shared.config as cfg
    from core.devices.everest60 import protocol as P
    cfg.save_rgb_settings(P.to_saved(P.normalize(
        effect="wave", color_mode="dual", speed=50, brightness=75,
        color1=[0, 56, 226], color2=[253, 157, 2], direction="l2r")))
    leds = []
    for i in range(P.NUM_KEYS):
        r, g, b = colorsys.hsv_to_rgb(0.58 + 0.12 * (i % 14) / 14.0, 0.85, 0.9)
        leds.append((int(r * 255), int(g * 255), int(b * 255)))
    for i in (41, 52, 55, 56, 57, 59, 60):
        leds[i] = (255, 255, 255)
    side = []
    for i in range(P.NUM_SIDE_LEDS):
        r, g, b = colorsys.hsv_to_rgb(i / float(P.NUM_SIDE_LEDS), 0.9, 1.0)
        side.append((int(r * 255), int(g * 255), int(b * 255)))
    cfg._save_per_key_60(leds, side, 75)


def seed_macropad():
    """Seven bound keys of the MacroPad and a Wave lighting with per-key
    colours for the Custom effect."""
    import shared.config as cfg
    none = _act("none")
    actions = [
        _act("shell", "x-terminal-emulator"),
        _act("url", "https://github.com/ramisotti13-eng/BaseCamp-Linux"),
        _act("keypress", "ctrl+shift+m"), _act("text", "Hello from BaseCamp Linux"),
        _act("folder", "~"), _act("obs", "record"), _act("macro", ""),
    ] + [dict(none) for _ in range(5)]
    cfg._save_macropad_actions(actions)
    palette = [(0, 56, 226), (253, 157, 2), (124, 58, 237), (22, 163, 74), (225, 29, 72),
               (14, 165, 233)]
    cfg._save_macropad_rgb({
        "effect": 4, "brightness": 70, "speed": 60, "direction": 0,
        "color1": [0, 56, 226], "color2": [253, 157, 2],
        "colors": [list(palette[i % len(palette)]) for i in range(12)],
    })


def picker(home=None):
    """A stand-in for the native file dialog, for a browser (devserver --demo).

    "save" answers a new file in the demo's exports folder, "folder" a small
    plugin folder written there, anything else the newest file in the exports
    folder (what a save wrote before, so export then import works), or None.
    """
    root = os.path.join(home or _prepared, "exports")

    def pick(title, kind, name=""):
        os.makedirs(root, exist_ok=True)
        if kind == "save":
            return os.path.join(root, name or "export.json")
        if kind == "folder":
            d = os.path.join(root, "demo_from_folder")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
                json.dump(_plugin_manifest("demo_from_folder", "Demo From Folder", "1.0",
                                           ["action"], "Installed from a picked folder.", {}), f)
            with open(os.path.join(d, "__init__.py"), "w", encoding="utf-8") as f:
                f.write(_PLUGIN_BODY)
            return d
        files = [os.path.join(root, n) for n in os.listdir(root)
                 if os.path.isfile(os.path.join(root, n))]
        return max(files, key=os.path.getmtime) if files else None
    return pick


def _seed_macros(app):
    """A few macros: a key sequence, a text, a toggle, a mouse one."""
    import shared.config as cfg
    macros = app.service("macros")
    step = lambda type_, value="", delay=0: {"type": type_, "value": value, "delay": delay}
    macros.create(name="Greeting", actions=[
        step("text", "Hello from BaseCamp Linux", 50), step("key_tap", "enter", 0)])
    intro = macros.create(name="Stream intro", repeat_mode="once", actions=[
        step("key_down", "ctrl"), step("key_tap", "f5", 20), step("key_up", "ctrl"),
        step("delay", "500"), step("mouse_move", "640, 360", 100),
        step("mouse_click", "left", 100), step("mouse_scroll", "down 3", 0)])
    # Keys only: the key binding screen writes this one into the Everest Max itself.
    macros.create(name="Copy", actions=[step("key_down", "ctrl"), step("key_tap", "c", 20),
                                        step("key_up", "ctrl")])
    macros.create(name="Autoclicker", repeat_mode="toggle",
                  actions=[step("mouse_click", "left", 80)])
    macros.create(name="Backup ping", repeat_mode="repeat", repeat_count=3,
                  actions=[step("shell", "echo ping", 200), step("url", "https://example.org", 0)])
    # the stream intro sits on a DisplayPad key, so "Assigned to" has something to say
    acts = cfg._load_displaypad_actions(0)
    if acts and len(acts) > 7:
        acts[7] = _act("macro", intro)
        cfg._save_displaypad_actions(acts)


# ── The core ─────────────────────────────────────────────────────────────────

def build(version=None, plugins=True):
    """The demo's core, not started: (app, pad). prepare() must have run."""
    if _prepared is None:
        raise RuntimeError("demo.prepare() has not run")
    fence()
    seed()
    from core.bootstrap import build_core
    from core.devices.controller_backend import ControllerBackend
    from ui_web import app_version

    global everest60, macropad_state
    from shared.config import CONFIG_DIR
    seed_plugins(os.path.join(CONFIG_DIR, "plugins"))
    version = version or app_version() + "-demo"
    pad = DemoPad()
    everest60, macropad_state = DemoEverest60(), DemoMacroPadState()
    app = build_core(
        presence=demo_presence,
        transports={"displaypad": pad, "everest_max": DemoEverestMaxBackend(),
                    "macropad": lambda: DemoMacroPad(macropad_state)},
        version=version,
        net=DemoPluginNet(), http=DemoHttp(version), obs_client=DemoObs, plugins=plugins,
        control_path=os.path.join(os.environ["XDG_RUNTIME_DIR"], "basecamp-demo.sock"))
    app.service("everest_max")._meters = DemoMeters()
    app.service("everest_max").key_trigger_factory = DemoKeyTrigger
    runners = {"everest60": everest60, "makalu": DemoMakalu()}
    for name, dev_id in (("everest60", "everest60"), ("makalu", "makalu67")):
        svc = app.service(name)
        svc.backend = ControllerBackend(app, dev_id, timeout=svc.TIMEOUT_S,
                                        runner=runners[name])

    # A plugin's action prints instead of running.
    plugin_host = app.service("plugins")
    actions = app.service("macros").actions

    def plugin_lookup(type_id):
        if plugin_host.manager.get_action_handler(type_id) is None:
            return None
        return lambda value: _say("plugin action", type_id, repr(value))
    actions.set_plugin_lookup(plugin_lookup)

    # Updates: reported, never downloaded or installed.
    upd = app.service("update")
    upd.install_type = lambda: "appimage"
    upd.can_install = lambda: True
    upd.download_and_install = _fake_download(app, upd)
    upd.restart = lambda *a, **k: _say("restart: not done in the demo")

    _seed_macros(app)
    macros = app.service("macros")
    greeting = next((uid for uid, name in macros.names().items() if name == "Greeting"), None)
    # OBS "connected" (a stand-in): its scenes are what the Streaming keys use.
    app.service("obs").connect(wait=True)
    app.service("macropad").set_action(6, "macro", greeting)     # M7 runs the macro
    return app, pad
