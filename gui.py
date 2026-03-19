#!/usr/bin/env python3
"""GUI for Mountain Everest Max display control."""
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image, ImageTk, ImageEnhance
import subprocess
import datetime
import threading
import re
import time
import sys
import os
import json
import math
import colorsys
import psutil
import pwd as _pwd

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    _BIN = os.path.dirname(sys.executable)
    _RES = sys._MEIPASS
    PYTHON = None
    SCRIPT = os.path.join(_BIN, "basecamp-controller")
    TRAY_HELPER = os.path.join(_BIN, "basecamp-tray")
else:
    _BIN = _HERE
    _RES = _HERE
    PYTHON = sys.executable
    SCRIPT = os.path.join(_HERE, "mountain-time-sync.py")
    TRAY_HELPER = os.path.join(_HERE, "tray_helper.py")

LANG_DIR = os.path.join(_RES, "lang")

STYLES = {"Analog": "analog", "Digital": "digital"}

def _cmd(*args):
    if _FROZEN:
        return [SCRIPT] + list(args)
    return [PYTHON, SCRIPT] + list(args)

_real_home = _pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir if os.environ.get("SUDO_USER") else os.path.expanduser("~")
CONFIG_DIR      = os.path.join(_real_home, ".config", "mountain-time-sync")
os.makedirs(CONFIG_DIR, exist_ok=True)
STYLE_FILE      = os.path.join(CONFIG_DIR, "style")
BUTTON_FILE     = os.path.join(CONFIG_DIR, "buttons.json")
OBS_FILE        = os.path.join(CONFIG_DIR, "obs.json")
OBS_BACKUP_FILE = os.path.join(CONFIG_DIR, "obs_backup.json")
MAIN_MODE_FILE  = os.path.join(CONFIG_DIR, "main_display_mode")
ZONE_FILE       = os.path.join(CONFIG_DIR, "zone_colors.json")
RGB_FILE        = os.path.join(CONFIG_DIR, "rgb_settings.json")
PER_KEY_FILE    = os.path.join(CONFIG_DIR, "per_key_colors.json")
RGB_PRESETS_FILE = os.path.join(CONFIG_DIR, "rgb_presets.json")

OBS_INTERNAL_ORDER = ["none", "scene", "record", "stream"]

BG     = "#0d0d14"
BG2    = "#16161f"
BG3    = "#1f1f2e"
FG     = "#e8e8ff"
FG2    = "#5a5a88"
BLUE   = "#0ea5e9"
YLW    = "#f5c400"
GRN    = "#22c55e"
RED    = "#ef4444"
BORDER = "#1e1e30"


def load_lang(code):
    path = os.path.join(LANG_DIR, f"{code}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        try:
            with open(os.path.join(LANG_DIR, "de.json"), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def available_langs():
    result = {}
    try:
        for fname in os.listdir(LANG_DIR):
            if fname.endswith(".json"):
                code = fname[:-5]
                try:
                    with open(os.path.join(LANG_DIR, fname), encoding="utf-8") as f:
                        data = json.load(f)
                    result[code] = data.get("name", code)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return result


_DESKTOP_EXEC_RE = re.compile(r"%[a-zA-Z]")
_desktop_apps_cache = None


def _run_as_sudouser(cmd):
    """Run cmd as SUDO_USER (when launched via sudo) or directly."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        uid = _pwd.getpwnam(sudo_user).pw_uid
        env = os.environ.copy()
        env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
        cmd = ["sudo", "-u", sudo_user, "-E"] + cmd
        return subprocess.run(cmd, capture_output=True, text=True, env=env)
    return subprocess.run(cmd, capture_output=True, text=True)


def native_open_image(title="Bild wählen"):
    import shutil
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    filter_img = "*.png *.jpg *.jpeg *.bmp *.webp *.gif"

    if shutil.which("kdialog") and ("KDE" in desktop or "PLASMA" in desktop):
        try:
            r = _run_as_sudouser(["kdialog", "--getopenfilename",
                                   os.path.expanduser("~"),
                                   f"{filter_img} | Bilder", "--title", title])
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    if shutil.which("zenity"):
        try:
            patterns = [f"--file-filter=Bilder | {filter_img}", "--file-filter=Alle | *"]
            r = _run_as_sudouser(["zenity", "--file-selection", f"--title={title}"] + patterns)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    return filedialog.askopenfilename(
        title=title,
        filetypes=[("PNG","*.png"),("JPEG","*.jpg *.jpeg"),
                   ("BMP","*.bmp"),("WebP","*.webp"),("GIF","*.gif"),("Alle","*.*")])


def native_open_folder(title="Ordner wählen"):
    import shutil
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()

    if shutil.which("kdialog") and ("KDE" in desktop or "PLASMA" in desktop):
        try:
            r = _run_as_sudouser(["kdialog", "--getexistingdirectory",
                                   os.path.expanduser("~"), "--title", title])
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    if shutil.which("zenity"):
        try:
            r = _run_as_sudouser(["zenity", "--file-selection", "--directory",
                                   f"--title={title}"])
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    return filedialog.askdirectory(title=title)


def parse_desktop_apps():
    """Scan .desktop files and return sorted list of (name, exec_cmd) for all visible apps.
    Result is cached for the lifetime of the process."""
    global _desktop_apps_cache
    if _desktop_apps_cache is not None:
        return _desktop_apps_cache

    search_dirs = [
        "/usr/share/applications",
        "/usr/local/share/applications",
        os.path.expanduser("~/.local/share/applications"),
    ]
    xdg_data = os.environ.get("XDG_DATA_DIRS", "")
    for d in xdg_data.split(":"):
        path = os.path.join(d, "applications")
        if path not in search_dirs and os.path.isdir(path):
            search_dirs.append(path)

    apps = {}
    for d in search_dirs:
        try:
            entries = os.listdir(d)
        except FileNotFoundError:
            continue
        for fname in entries:
            if not fname.endswith(".desktop"):
                continue
            path = os.path.join(d, fname)
            try:
                name = exec_cmd = None
                no_display = hidden = False
                with open(path, encoding="utf-8", errors="replace") as f:
                    in_entry = False
                    for line in f:
                        line = line.strip()
                        if line == "[Desktop Entry]":
                            in_entry = True
                        elif line.startswith("[") and line != "[Desktop Entry]":
                            in_entry = False
                        if not in_entry:
                            continue
                        if line.startswith("Name=") and name is None:
                            name = line[5:]
                        elif line.startswith("Exec=") and exec_cmd is None:
                            raw = line[5:]
                            exec_cmd = _DESKTOP_EXEC_RE.sub("", raw).strip()
                        elif line == "NoDisplay=true":
                            no_display = True
                        elif line == "Hidden=true":
                            hidden = True
                if name and exec_cmd and not no_display and not hidden:
                    apps[name] = exec_cmd
            except Exception:
                continue
    return sorted(apps.items(), key=lambda x: x[0].lower())


def save_style(style_arg):
    with open(STYLE_FILE, "w") as f:
        f.write(style_arg)


def load_style():
    try:
        with open(STYLE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "analog"


def load_buttons():
    default = [{"icon": 7, "action": "", "type": "shell"} for _ in range(4)]
    try:
        with open(BUTTON_FILE) as f:
            data = json.load(f)
        for i in range(4):
            if i < len(data):
                default[i].update(data[i])
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return default


def save_buttons(buttons):
    with open(BUTTON_FILE, "w") as f:
        json.dump(buttons, f, indent=2)


def load_obs_config():
    default = {"host": "localhost", "port": 4455, "password": "",
               "buttons": [{"type": "none", "scene": ""} for _ in range(4)]}
    try:
        with open(OBS_FILE) as f:
            data = json.load(f)
        for k in ("host", "port", "password"):
            if k in data:
                default[k] = data[k]
        for i in range(4):
            if i < len(data.get("buttons", [])):
                default["buttons"][i].update(data["buttons"][i])
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return default


def save_obs_config(cfg):
    with open(OBS_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


_AUTOSTART_FILE = os.path.join(
    os.path.expanduser("~") if not os.environ.get("SUDO_USER") else
    _pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir,
    ".config", "autostart", "basecamp-linux.desktop"
)


def _autostart_exec():
    if _FROZEN:
        return os.environ.get("APPIMAGE", sys.executable)
    return f"{sys.executable} {os.path.abspath(__file__)}"


def load_autostart_enabled():
    return os.path.exists(_AUTOSTART_FILE)


def save_autostart_enabled(val):
    if val:
        os.makedirs(os.path.dirname(_AUTOSTART_FILE), exist_ok=True)
        with open(_AUTOSTART_FILE, "w") as f:
            f.write(f"""[Desktop Entry]
Type=Application
Name=BaseCamp Linux
Comment=Mountain Everest Max display control
Exec={_autostart_exec()}
Icon=basecamp-linux
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
""")
    else:
        try:
            os.remove(_AUTOSTART_FILE)
        except FileNotFoundError:
            pass


def load_splash_enabled():
    try:
        return open(os.path.join(CONFIG_DIR, "splash")).read().strip() != "0"
    except FileNotFoundError:
        return True


def save_splash_enabled(val):
    with open(os.path.join(CONFIG_DIR, "splash"), "w") as f:
        f.write("1" if val else "0")


def load_zone_config(defaults):
    try:
        import json as _json
        data = _json.loads(open(ZONE_FILE).read())
        colors = dict(defaults)
        for k in colors:
            if k in data and len(data[k]) == 3:
                colors[k] = tuple(data[k])
        brightness = int(data.get("brightness", 100))
        return colors, brightness
    except Exception:
        return dict(defaults), 100


def save_zone_config(colors, brightness):
    import json as _json
    data = {k: list(v) for k, v in colors.items()}
    data["brightness"] = brightness
    with open(ZONE_FILE, "w") as f:
        f.write(_json.dumps(data, indent=2))


def load_rgb_config():
    try:
        import json as _json
        return _json.loads(open(RGB_FILE).read())
    except Exception:
        return {}


def save_rgb_config(data):
    import json as _json
    with open(RGB_FILE, "w") as f:
        f.write(_json.dumps(data, indent=2))


# ── Per-Key RGB ───────────────────────────────────────────────────────────────

_SIDE_ZONE_INDICES = [
    [13,14,15,7,6,5,4,3,2,1,0],           # main top   (11)
    [9,8,10,11],                            # main right  (4)
    [20,21,22,23,24,25,26,27,28,29,30,12], # main bottom(12)
    [16,17,18,19],                          # main left   (4)
    [31,44,43,42],                          # np top      (4)
    [41,40,39],                             # np right    (3)
    [35,36,37,38],                          # np bottom   (4)
    [32,33,34],                             # np left     (3)
]


def _load_per_key():
    import json as _j
    default_side = [(255, 255, 255)] * 45
    try:
        d = _j.loads(open(PER_KEY_FILE).read())
        leds = [tuple(c) for c in d.get("leds", [])]
        leds = (leds + [(20, 20, 20)] * 126)[:126]
        raw = d.get("side", [])
        if isinstance(raw, list) and len(raw) == 45:
            side = [tuple(c) for c in raw]
        elif isinstance(raw, dict):
            # backward compat: zone dict → expand to 45
            side = list(default_side)
            zone_map = {
                "Top":    _SIDE_ZONE_INDICES[0], "Right":  _SIDE_ZONE_INDICES[1],
                "Bottom": _SIDE_ZONE_INDICES[2], "Left":   _SIDE_ZONE_INDICES[3],
                "NP": _SIDE_ZONE_INDICES[4] + _SIDE_ZONE_INDICES[5] +
                      _SIDE_ZONE_INDICES[6] + _SIDE_ZONE_INDICES[7],
            }
            for z, idxs in zone_map.items():
                c = tuple(raw.get(z, (255, 255, 255)))
                for i in idxs:
                    side[i] = c
        else:
            side = list(default_side)
        bri = int(d.get("brightness", 100))
        return leds, side, bri
    except Exception:
        return [(20, 20, 20)] * 126, list(default_side), 100


def _save_per_key(leds, side, bri):
    import json as _j
    with open(PER_KEY_FILE, "w") as f:
        f.write(_j.dumps({"leds": [list(c) for c in leds],
                           "side": [list(c) for c in side],
                           "brightness": bri}, indent=2))


def _load_presets():
    import json as _j
    # Built-in presets shipped with the app
    defaults = {}
    _default_file = os.path.join(_RES, "default_presets.json")
    try:
        defaults = _j.loads(open(_default_file).read())
    except Exception:
        pass
    # User presets (override built-ins if same name)
    try:
        user = _j.loads(open(RGB_PRESETS_FILE).read())
        defaults.update(user)
    except Exception:
        pass
    return defaults


def _save_presets(presets):
    import json as _j
    with open(RGB_PRESETS_FILE, "w") as f:
        f.write(_j.dumps(presets, indent=2))


def _build_kb_layout():
    """Return list of (label, led_idx_or_None, x, y, w, h) for all keys."""
    SC = 0.82
    KH = int(30 * SC)       # key height  ≈ 24px
    RS = int(32 * SC)       # row stride  ≈ 26px
    IW = int(510 * SC)      # onediv inner width ≈ 418px
    FW = int(616 * SC)      # fn-row full inner width ≈ 505px
    OX = 14 + int(26 * SC)  # onediv x-start ≈ 35px
    OY = 14 + int(12 * SC)  # onediv y-start ≈ 23px
    # numpad params
    NP_X0 = 14 + int(642 * SC) + 32  # gap for side LEDs + visual separation
    NPS   = int(33 * SC)              # ≈ 27px
    NPG   = int(7 * SC)               # ≈ 5px
    # mini-div (nav + arrow cluster): 3 cols, anchored to numpad left edge
    # layout: col0=← only, col1=nav+↑+↓, col2=nav+→
    MS  = 25                        # mini key width
    MG  = 4                         # mini col gap
    npx = NP_X0 + int(5 * SC)      # first numpad key x
    MX  = OX + IW + 8              # col0 anchored 8px right of main keyboard
    NPTAL = KH + RS                   # tall key height ≈ 50px

    def sbet(specs, inner_w, y):
        total = sum(int(w * SC) for _, _, w in specs)
        gap   = (inner_w - total) / max(1, len(specs) - 1)
        res, x = [], OX
        for lbl, idx, cw in specs:
            pw = int(cw * SC)
            res.append((lbl, idx, int(x), y, pw, KH))
            x += pw + gap
        return res

    L = []
    # Row 0 — Fn (full width FW)
    L += sbet([
        ('ESC',0,30),('F1',9,30),('F2',18,30),('F3',27,30),
        ('F4',36,30),('F5',45,30),('F6',54,30),('F7',63,30),
        ('F8',72,30),('F9',81,30),('F10',90,30),('F11',99,30),
        ('F12',108,30),('PrtSc',117,30),('ScrLk',114,30),('Pause',123,30),
    ], FW, OY)

    # Row 1 — Number row  + nav: Ins Del
    y1 = OY + RS
    L += sbet([
        ('`',1,30),('1',10,30),('2',19,30),('3',28,30),('4',37,30),
        ('5',46,30),('6',55,30),('7',64,30),('8',73,30),('9',82,30),
        ('0',91,30),('-',100,30),('=',109,30),('⌫',87,68),
    ], IW, y1)
    L += [('Ins',96,MX+MS+MG,y1,MS,KH), ('Del',88,MX+2*(MS+MG),y1,MS,KH)]

    # Row 2 — QWERTY  + Home PgUp
    y2 = OY + 2 * RS
    L += sbet([
        ('Tab',2,50),('Q',11,30),('W',20,30),('E',29,30),('R',38,30),
        ('T',47,30),('Y',56,30),('U',65,30),('I',74,30),('O',83,30),
        ('P',92,30),('[',101,30),(']',110,30),('\\',119,50),
    ], IW, y2)
    L += [('Home',105,MX+MS+MG,y2,MS,KH), ('PgUp',115,MX+2*(MS+MG),y2,MS,KH)]

    # Row 3 — Home row  + End PgDn
    y3 = OY + 3 * RS
    L += sbet([
        ('Caps',3,60),('A',12,30),('S',21,30),('D',30,30),('F',39,30),
        ('G',48,30),('H',57,30),('J',66,30),('K',75,30),('L',84,30),
        (';',93,30),("'",102,30),('↵',120,73),
    ], IW, y3)
    L += [('End',97,MX+MS+MG,y3,MS,KH), ('PgDn',106,MX+2*(MS+MG),y3,MS,KH)]

    # Row 4 — Shift  + ↑
    y4 = OY + 4 * RS
    L += sbet([
        ('⇧',4,80),('Z',22,30),('X',31,30),('C',40,30),('V',49,30),
        ('B',58,30),('N',67,30),('M',76,30),(',',85,30),('.',94,30),
        ('/',103,30),('⇧',121,88),
    ], IW, y4)
    L.append(('↑', 124, MX + MS + MG, y4, MS, KH))  # col1, above ↓

    # Row 5 — Bottom  + ← ↓ →
    y5 = OY + 5 * RS
    L += sbet([
        ('Ctrl',5,42),('⊞',14,42),('Alt',23,42),(' ',41,210),
        ('Alt',68,42),('⊞',77,42),('FN',86,42),('≡',None,42),('Ctrl',95,42),
    ], IW, y5)
    L += [
        ('←',104,MX,y5,MS,KH),
        ('↓',113,MX+MS+MG,y5,MS,KH),
        ('→',122,MX+2*(MS+MG),y5,MS,KH),
    ]

    # Numpad
    npx = NP_X0 + int(5 * SC)
    npy = OY
    for i, (lbl, idx) in enumerate([('NumLk',6),('/',24),('*',16),('-',15)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy, NPS, KH))
    for i, (lbl, idx) in enumerate([('7',61),('8',69),('9',70)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+RS, NPS, KH))
    L.append(('+', 7, npx + 3*(NPS+NPG), npy+RS, NPS, NPTAL))
    for i, (lbl, idx) in enumerate([('4',51),('5',52),('6',60)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+2*RS, NPS, KH))
    for i, (lbl, idx) in enumerate([('1',34),('2',42),('3',43)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+3*RS, NPS, KH))
    L.append(('↵', 33, npx + 3*(NPS+NPG), npy+3*RS, NPS, NPTAL))
    L.append(('0', 78, npx, npy+4*RS, 2*NPS+NPG, KH))
    L.append(('.', 79, npx + 2*NPS+2*NPG, npy+4*RS, NPS, KH))

    return L


_KB_LAYOUT    = _build_kb_layout()
_KB_CANVAS_W  = 14 + int(642 * 0.82) + 32 + int(166 * 0.82) + 14  # gap=32 for side LEDs
_KB_CANVAS_H  = (14 + int(12 * 0.82)) + 5 * int(32 * 0.82) + int(30 * 0.82) + 18 + 24  # +24 for side LED rows
_SIDE_SZ      = 9   # side LED square pixel size
_SIDE_OFFSET  = 12  # vertical canvas margin for top/bottom side LEDs

_QUICK_COLORS = [
    ("#ff0000", (255,0,0)), ("#ff8800", (255,136,0)),
    ("#ffff00", (255,255,0)), ("#00ff00", (0,255,0)),
    ("#00ffff", (0,255,255)), ("#0088ff", (0,136,255)),
    ("#8800ff", (136,0,255)), ("#ff00ff", (255,0,255)),
    ("#ffffff", (255,255,255)), ("#000000", (0,0,0)),
]

# Side LED render positions (§11.2).
# Each tuple: (side_led_indices_in_order, x1, x2, y_or_x, orientation)
# Defined dynamically in _draw_keys; stored here for profile compat only.
# Individual LED index order (left→right or top→bottom per side):
#   Main top(11):    13,14,15,7,6,5,4,3,2,1,0
#   Main right(4):   9,8,10,11
#   Main bottom(12): 20,21,22,23,24,25,26,27,28,29,30,12
#   Main left(4):    16,17,18,19
#   NP top(4):       31,44,43,42
#   NP right(3):     41,40,39
#   NP bottom(4):    35,36,37,38
#   NP left(3):      32,33,34


class CustomRGBWindow(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.title("Custom RGB — Per Key")
        self.resizable(False, False)
        self._app = app
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._leds, self._side_leds, self._bri = _load_per_key()
        self._selected   = set()         # key idx 0-125; side idx 200-244
        self._fill_rgb   = (255, 0, 0)   # current paint color
        self._drag_rect  = None          # (x0,y0) start of drag
        self._item_led   = {}            # canvas item_id → idx
        self._led_item   = {}            # idx → canvas item_id
        self._undo_stack = []            # list of (leds, side_leds) snapshots

        self._build_ui()
        self.after(50, self._draw_keys)  # wait for canvas to be ready

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = 12
        self.configure(fg_color=BG)

        # ── keyboard canvas ───────────────────────────────────────────────
        self._cv = tk.Canvas(self, width=_KB_CANVAS_W, height=_KB_CANVAS_H,
                             bg="#111118", highlightthickness=0, bd=0)
        self._cv.pack(padx=PAD, pady=(PAD, 4))
        self._cv.bind("<Button-1>",         self._on_click)
        self._cv.bind("<B1-Motion>",        self._on_drag)
        self._cv.bind("<ButtonRelease-1>",  self._on_release)
        self._cv.bind("<Double-Button-1>",  self._on_dbl)
        self._cv.bind("<Button-3>",         self._on_rclick)
        self._cv.bind("<Alt-Button-1>",     self._on_eyedrop)
        self.bind("<Control-z>",            self._undo)
        self.bind("<Control-Z>",            self._undo)

        # ── color strip ───────────────────────────────────────────────────
        strip = ctk.CTkFrame(self, fg_color=BG2, corner_radius=6)
        strip.pack(fill="x", padx=PAD, pady=4)

        # fill swatch + pick
        self._fill_swatch = tk.Canvas(strip, width=28, height=28,
                                      bg=_rgb_hex(self._fill_rgb),
                                      highlightthickness=1,
                                      highlightbackground="#555")
        self._fill_swatch.pack(side="left", padx=(8, 2), pady=6)
        ctk.CTkButton(strip, text="Pick", width=50, height=28,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._pick_fill).pack(side="left", padx=(0, 8))

        # quick swatches
        for hex_c, rgb in _QUICK_COLORS:
            btn = tk.Canvas(strip, width=20, height=20, bg=hex_c,
                            highlightthickness=1, highlightbackground="#333",
                            cursor="hand2")
            btn.pack(side="left", padx=2, pady=8)
            btn.bind("<Button-1>", lambda e, c=rgb: self._set_fill(c))

        # spacer + sel label
        self._sel_lbl = ctk.CTkLabel(strip, text="0 keys selected",
                                     text_color=FG2, font=("Helvetica", 11))
        self._sel_lbl.pack(side="right", padx=10)

        # ── actions row ───────────────────────────────────────────────────
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.pack(fill="x", padx=PAD, pady=4)

        ctk.CTkButton(act, text="Fill Selected", width=110, height=30,
                      fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._fill_selected).pack(side="left", padx=(0,4))
        ctk.CTkButton(act, text="Select All", width=90, height=30,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._select_all).pack(side="left", padx=4)
        ctk.CTkButton(act, text="Deselect", width=80, height=30,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._deselect_all).pack(side="left", padx=4)
        ctk.CTkButton(act, text="All Black", width=80, height=30,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=lambda: self._fill_all((0,0,0))).pack(side="left", padx=4)
        ctk.CTkButton(act, text="All White", width=80, height=30,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=lambda: self._fill_all((255,255,255))).pack(side="left", padx=4)
        ctk.CTkButton(act, text="↩ Undo", width=70, height=30,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._undo).pack(side="right", padx=(4, 0))
        ctk.CTkLabel(act, text="Alt+click = Eyedropper", text_color=FG2,
                     font=("Helvetica", 10)).pack(side="right", padx=8)

        # ── presets ───────────────────────────────────────────────────────
        pre = ctk.CTkFrame(self, fg_color=BG2, corner_radius=6)
        pre.pack(fill="x", padx=PAD, pady=(0, 4))

        ctk.CTkLabel(pre, text="Presets:", text_color=FG2,
                     font=("Helvetica", 11)).pack(side="left", padx=(8, 4), pady=6)
        self._preset_var = tk.StringVar()
        self._preset_combo = ctk.CTkComboBox(
            pre, variable=self._preset_var, values=[], width=180, height=28,
            fg_color=BG3, border_color=BORDER, button_color=BLUE,
            dropdown_fg_color=BG2, text_color=FG, font=("Helvetica", 11))
        self._preset_combo.pack(side="left", padx=(0, 4), pady=6)
        ctk.CTkButton(pre, text="Load", width=60, height=28,
                      fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._preset_load).pack(side="left", padx=2)
        ctk.CTkButton(pre, text="Save as…", width=80, height=28,
                      fg_color="#166534", hover_color="#14532d", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._preset_save_as).pack(side="left", padx=2)
        ctk.CTkButton(pre, text="Delete", width=68, height=28,
                      fg_color="#7f1d1d", hover_color="#6b1a1a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._preset_delete).pack(side="left", padx=2)
        self._preset_status = ctk.CTkLabel(pre, text="", text_color=FG2,
                                           font=("Helvetica", 10))
        self._preset_status.pack(side="left", padx=8)
        self._preset_refresh()

        # ── brightness ────────────────────────────────────────────────────
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=PAD, pady=4)

        ctk.CTkLabel(bot, text="Brightness:", text_color=FG2,
                     font=("Helvetica", 11)).pack(side="left")
        self._bri_val = ctk.CTkLabel(bot, text=str(self._bri), text_color=FG,
                                     font=("Helvetica", 11), width=30)
        self._bri_val.pack(side="right")
        self._bri_sl = ctk.CTkSlider(bot, from_=10, to=100, number_of_steps=90,
                                     fg_color=BG3, progress_color=BLUE,
                                     button_color=BLUE, button_hover_color=BLUE,
                                     width=160, height=16)
        self._bri_sl.set(self._bri)
        self._bri_sl.configure(command=lambda v: self._bri_val.configure(text=str(int(v))))
        self._bri_sl.pack(side="right", padx=(0, 6))

        # ── apply / persist / save / load ────────────────────────────────
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=PAD, pady=(4, PAD))

        ctk.CTkButton(btns, text="Apply to Keyboard", width=140, height=32,
                      fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                      font=("Helvetica", 11, "bold"),
                      command=self._apply).pack(side="left", padx=(0, 4))
        ctk.CTkButton(btns, text="Persist to Slot", width=120, height=32,
                      fg_color="#166534", hover_color="#14532d", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._persist).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Save Profile", width=100, height=32,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._save_profile).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Load Profile", width=100, height=32,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._load_profile).pack(side="left", padx=4)
        self._status = ctk.CTkLabel(btns, text="", text_color=FG2,
                                    font=("Helvetica", 11))
        self._status.pack(side="left", padx=10)

    # ── canvas drawing ────────────────────────────────────────────────────

    def _draw_keys(self):
        self._cv.delete("all")
        self._item_led.clear()
        self._led_item.clear()

        SC  = 0.82
        YO  = _SIDE_OFFSET   # vertical shift so top side LEDs fit above bezel
        SZ  = _SIDE_SZ
        GAP = 2

        bx1  = 11;  by1 = 11 + YO
        bx2  = 14 + int(642 * SC) + 4
        by2  = _KB_CANVAS_H - YO - 6
        npx  = 14 + int(642 * SC) + 32  # same gap as NP_X0
        npbx1 = npx - 3
        npbx2 = npx + int(166 * SC) + 3

        self._cv.create_rectangle(bx1, by1, bx2, by2,
                                  fill="#1a1a22", outline="#333", width=1)
        self._cv.create_rectangle(npbx1, by1, npbx2, by2,
                                  fill="#1a1a22", outline="#333", width=1)

        # keyboard keys (y shifted by YO)
        for (lbl, idx, x, y, w, h) in _KB_LAYOUT:
            yo    = y + YO
            color = _rgb_hex(self._leds[idx]) if idx is not None and 0 <= idx <= 125 else "#252530"
            sel   = idx in self._selected
            item  = self._cv.create_rectangle(
                x, yo, x + w, yo + h,
                fill=color,
                outline="#00d4ff" if sel else "#111",
                width=2 if sel else 1,
            )
            font_size = 6 if w < 22 else 7
            self._cv.create_text(x + w // 2, yo + h // 2, text=lbl,
                                 fill="#cccccc", font=("Helvetica", font_size),
                                 anchor="center")
            if idx is not None:
                self._item_led[item] = idx
                self._led_item[idx]  = item

        # side LED squares
        def hstrip(indices, x1, x2, y):
            n = len(indices)
            for i, si in enumerate(indices):
                px = int(x1 + i * (x2 - x1 - SZ) / max(1, n - 1)) if n > 1 else (x1 + x2 - SZ) // 2
                color = _rgb_hex(self._side_leds[si])
                sel   = (200 + si) in self._selected
                item  = self._cv.create_rectangle(px, y, px + SZ, y + SZ,
                            fill=color,
                            outline="#00d4ff" if sel else "#555",
                            width=2 if sel else 1)
                self._item_led[item]     = 200 + si
                self._led_item[200 + si] = item

        def vstrip(indices, y1, y2, x):
            n = len(indices)
            for i, si in enumerate(indices):
                py = int(y1 + i * (y2 - y1 - SZ) / max(1, n - 1)) if n > 1 else (y1 + y2 - SZ) // 2
                color = _rgb_hex(self._side_leds[si])
                sel   = (200 + si) in self._selected
                item  = self._cv.create_rectangle(x, py, x + SZ, py + SZ,
                            fill=color,
                            outline="#00d4ff" if sel else "#555",
                            width=2 if sel else 1)
                self._item_led[item]     = 200 + si
                self._led_item[200 + si] = item

        # main keyboard ring
        hstrip([13,14,15,7,6,5,4,3,2,1,0],            bx1, bx2,   by1 - GAP - SZ)  # top  11
        vstrip([9,8,10,11],                             by1, by2,   bx2 + GAP)       # right 4
        hstrip([20,21,22,23,24,25,26,27,28,29,30,12],  bx1, bx2,   by2 + GAP)       # bottom 12
        vstrip([16,17,18,19],                           by1, by2,   bx1 - GAP - SZ)  # left 4
        # numpad ring — corners belong to left(31)/right(38), not top/bottom
        hstrip([44,43,42],      npbx1, npbx2, by1 - GAP - SZ)  # top    3
        vstrip([41,40,39,38],   by1,   by2,   npbx2 + GAP)      # right  4 (38=BR corner)
        hstrip([35,36,37],      npbx1, npbx2, by2 + GAP)        # bottom 3
        vstrip([31,32,33,34],   by1,   by2,   npbx1 - GAP - SZ) # left   4 (31=TL corner)

    def _refresh_key(self, idx):
        item = self._led_item.get(idx)
        if item is None:
            return
        sel = idx in self._selected
        if 200 <= idx <= 244:
            color   = _rgb_hex(self._side_leds[idx - 200])
            outline = "#00d4ff" if sel else "#555"
        else:
            color   = _rgb_hex(self._leds[idx]) if 0 <= idx <= 125 else "#252530"
            outline = "#00d4ff" if sel else "#111"
        self._cv.itemconfigure(item, fill=color, outline=outline, width=2 if sel else 1)

    # ── mouse events ──────────────────────────────────────────────────────

    def _key_at(self, ex, ey):
        """Return led_idx of topmost key under (ex, ey), or None."""
        items = self._cv.find_overlapping(ex, ey, ex, ey)
        for item in reversed(items):
            if item in self._item_led:
                return self._item_led[item]
        return None

    def _on_click(self, e):
        ctrl = (e.state & 0x0004) != 0
        idx  = self._key_at(e.x, e.y)
        self._drag_rect = (e.x, e.y)
        if idx is None:
            if not ctrl:
                self._deselect_all()
            return
        if ctrl:
            if idx in self._selected:
                self._selected.discard(idx)
            else:
                self._selected.add(idx)
            self._refresh_key(idx)
        else:
            old = set(self._selected)
            self._selected = {idx}
            for i in old:
                self._refresh_key(i)
            self._refresh_key(idx)
        self._update_sel_lbl()

    def _on_drag(self, e):
        if self._drag_rect is None:
            return
        x0, y0 = self._drag_rect
        self._cv.delete("drag_rect")
        self._cv.create_rectangle(x0, y0, e.x, e.y,
                                  outline="#4488ff", dash=(4, 2),
                                  fill="", width=1, tags="drag_rect")

    def _on_release(self, e):
        if self._drag_rect is None:
            return
        x0, y0 = self._drag_rect
        self._drag_rect = None
        self._cv.delete("drag_rect")
        dx, dy = abs(e.x - x0), abs(e.y - y0)
        if dx < 5 and dy < 5:
            return  # handled by _on_click
        # rectangle selection
        rx1, rx2 = min(x0, e.x), max(x0, e.x)
        ry1, ry2 = min(y0, e.y), max(y0, e.y)
        ctrl = (e.state & 0x0004) != 0
        if not ctrl:
            old = set(self._selected)
            self._selected.clear()
            for i in old:
                self._refresh_key(i)
        for item, idx in self._item_led.items():
            coords = self._cv.coords(item)
            if coords[0] < rx2 and coords[2] > rx1 and \
               coords[1] < ry2 and coords[3] > ry1:
                self._selected.add(idx)
        for idx in self._selected:
            self._refresh_key(idx)
        self._update_sel_lbl()

    def _on_dbl(self, e):
        idx = self._key_at(e.x, e.y)
        if idx is not None and idx not in self._selected:
            self._selected.add(idx)
            self._refresh_key(idx)
        if self._selected:
            self._pick_fill()

    def _on_rclick(self, e):
        idx = self._key_at(e.x, e.y)
        if idx is None:
            return
        if idx in self._selected:
            self._selected.discard(idx)
        else:
            self._selected.add(idx)
        self._refresh_key(idx)
        self._update_sel_lbl()

    # ── color actions ─────────────────────────────────────────────────────

    def _set_fill(self, rgb):
        self._fill_rgb = rgb
        self._fill_swatch.configure(bg=_rgb_hex(rgb))

    def _pick_fill(self):
        rgb = pick_color(self, initial_rgb=tuple(self._fill_rgb), title="Key Color")
        if rgb is None:
            return
        self._set_fill(rgb)
        if self._selected:
            self._fill_selected()

    def _fill_selected(self):
        self._push_undo()
        for idx in self._selected:
            if 0 <= idx <= 125:
                self._leds[idx] = self._fill_rgb
            elif 200 <= idx <= 244:
                self._side_leds[idx - 200] = self._fill_rgb
            self._refresh_key(idx)

    def _fill_all(self, rgb):
        self._push_undo()
        self._leds = [rgb] * 126
        self._side_leds = [rgb] * 45
        self._draw_keys()

    def _select_all(self):
        self._selected = {idx for _, idx, *_ in _KB_LAYOUT if idx is not None and 0 <= idx <= 125}
        self._selected.update(200 + i for i in range(45))
        self._draw_keys()
        self._update_sel_lbl()

    def _deselect_all(self):
        old = set(self._selected)
        self._selected.clear()
        for i in old:
            self._refresh_key(i)
        self._update_sel_lbl()


    def _update_sel_lbl(self):
        n = len(self._selected)
        self._sel_lbl.configure(text=f"{n} key{'s' if n != 1 else ''} selected")

    # ── undo ──────────────────────────────────────────────────────────────

    def _push_undo(self):
        self._undo_stack.append((list(self._leds), list(self._side_leds)))
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)

    def _undo(self, event=None):
        if not self._undo_stack:
            return
        self._leds, self._side_leds = self._undo_stack.pop()
        self._draw_keys()

    # ── eyedropper ────────────────────────────────────────────────────────

    def _on_eyedrop(self, e):
        """Alt+click: sample the clicked key/LED colour into the fill swatch."""
        idx = self._key_at(e.x, e.y)
        if idx is None:
            return
        if 200 <= idx <= 244:
            col = self._side_leds[idx - 200]
        elif 0 <= idx <= 125:
            col = self._leds[idx]
        else:
            return
        self._set_fill(col)

    # ── presets ───────────────────────────────────────────────────────────

    def _preset_refresh(self):
        names = sorted(_load_presets().keys())
        self._preset_combo.configure(values=names)
        if names and not self._preset_var.get():
            self._preset_combo.set(names[0])

    def _preset_load(self):
        name = self._preset_var.get().strip()
        presets = _load_presets()
        if name not in presets:
            self._preset_status.configure(text="Not found", text_color=RED)
            return
        self._push_undo()
        d = presets[name]
        leds = [tuple(c) for c in d.get("leds", [])]
        self._leds = (leds + [(20, 20, 20)] * 126)[:126]
        raw = d.get("side", [])
        if isinstance(raw, list) and len(raw) == 45:
            self._side_leds = [tuple(c) for c in raw]
        bri = int(d.get("brightness", 100))
        self._bri_sl.set(bri)
        self._bri_val.configure(text=str(bri))
        self._draw_keys()
        self._preset_status.configure(text=f'Loaded "{name}"', text_color=GRN)

    def _preset_save_as(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Save Preset")
        dlg.resizable(False, False)
        dlg.geometry("300x110")
        dlg.grab_set()
        dlg.configure(fg_color=BG)
        ctk.CTkLabel(dlg, text="Preset name:", text_color=FG,
                     font=("Helvetica", 12)).pack(pady=(14, 4))
        var = tk.StringVar(value=self._preset_var.get())
        entry = ctk.CTkEntry(dlg, textvariable=var, width=200, height=30,
                             fg_color=BG2, text_color=FG, border_color=BORDER,
                             font=("Helvetica", 12))
        entry.pack()
        entry.focus()
        def _save():
            name = var.get().strip()
            if not name:
                return
            presets = _load_presets()
            presets[name] = {"leds":  [list(c) for c in self._leds],
                              "side":  [list(c) for c in self._side_leds],
                              "brightness": self._current_bri()}
            _save_presets(presets)
            self._preset_refresh()
            self._preset_combo.set(name)
            self._preset_status.configure(text=f'Saved "{name}"', text_color=GRN)
            dlg.destroy()
        entry.bind("<Return>", lambda e: _save())
        ctk.CTkButton(dlg, text="Save", width=80, height=28,
                      fg_color=BLUE, text_color=FG, command=_save).pack(pady=8)

    def _preset_delete(self):
        name = self._preset_var.get().strip()
        presets = _load_presets()
        if name not in presets:
            self._preset_status.configure(text="Not found", text_color=RED)
            return
        del presets[name]
        _save_presets(presets)
        self._preset_refresh()
        remaining = sorted(presets.keys())
        self._preset_combo.set(remaining[0] if remaining else "")
        self._preset_status.configure(text=f'Deleted "{name}"', text_color=FG2)

    # ── apply / persist / save / load ────────────────────────────────────

    def _current_bri(self):
        return int(self._bri_sl.get())

    def _build_payload(self):
        import json as _j
        return _j.dumps({"leds":  [list(c) for c in self._leds],
                          "side":  [list(c) for c in self._side_leds],
                          "brightness": self._current_bri()})

    def _apply(self):
        self._status.configure(text="Sending…", text_color=YLW)
        self.update_idletasks()
        payload = self._build_payload()
        was_running = self._app._stop_cpu_proc()
        def run():
            r = subprocess.run(_cmd("per-key-rgb", payload), capture_output=True)
            ok = r.returncode == 0
            err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            _save_per_key(self._leds, self._side_leds, self._current_bri())
            def finish():
                self._status.configure(
                    text="Applied ✓" if ok else f"Error — {err}",
                    text_color=GRN if ok else RED)
                if was_running:
                    self._app._start_cpu_auto()
            self.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _persist(self):
        """Apply + persist to slot 6 (Custom slot on dial)."""
        self._status.configure(text="Persisting…", text_color=YLW)
        self.update_idletasks()
        payload = self._build_payload()
        was_running = self._app._stop_cpu_proc()
        def run():
            r = subprocess.run(_cmd("per-key-rgb", payload, "--persist"), capture_output=True)
            ok = r.returncode == 0
            if ok:
                _save_per_key(self._leds, self._side_leds, self._current_bri())
            err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            def finish():
                self._status.configure(
                    text="Persisted ✓" if ok else f"Error — {err}",
                    text_color=GRN if ok else RED)
                if was_running:
                    self._app._start_cpu_auto()
            self.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _save_profile(self):
        import tkinter.filedialog as _fd
        import json as _j
        path = _fd.asksaveasfilename(
            parent=self, defaultextension=".json",
            filetypes=[("JSON Profile", "*.json"), ("All", "*.*")],
            title="Save RGB Profile")
        if not path:
            return
        with open(path, "w") as f:
            f.write(_j.dumps({"leds":  [list(c) for c in self._leds],
                               "side":  [list(c) for c in self._side_leds],
                               "brightness": self._current_bri()}, indent=2))
        self._status.configure(text="Saved ✓", text_color=GRN)

    def _load_profile(self):
        import tkinter.filedialog as _fd
        import json as _j
        path = _fd.askopenfilename(
            parent=self,
            filetypes=[("JSON Profile", "*.json"), ("All", "*.*")],
            title="Load RGB Profile")
        if not path:
            return
        try:
            d = _j.loads(open(path).read())
            leds = [tuple(c) for c in d.get("leds", [])]
            self._leds = (leds + [(20,20,20)] * 126)[:126]
            raw = d.get("side", [])
            if isinstance(raw, list) and len(raw) == 45:
                self._side_leds = [tuple(c) for c in raw]
            else:
                self._side_leds = [(255, 255, 255)] * 45
            self._bri_sl.set(int(d.get("brightness", 100)))
            self._bri_val.configure(text=str(int(d.get("brightness", 100))))
            self._draw_keys()
            self._status.configure(text="Loaded ✓", text_color=GRN)
        except Exception as ex:
            self._status.configure(text=f"Load error: {ex}", text_color=RED)

    def _on_close(self):
        self.destroy()


def _rgb_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


# ── Color Picker Dialog ────────────────────────────────────────────────────────

_WHL = 220          # wheel diameter in pixels
_WHL_R = _WHL // 2  # wheel radius
_WHL_BG = (18, 18, 30)

def _make_wheel_full():
    """Build HSV colour wheel at V=1.0.  Called once per dialog open."""
    R = _WHL_R
    pixels = bytearray(_WHL * _WHL * 3)
    off = 0
    for y in range(_WHL):
        dy = y - R
        for x in range(_WHL):
            dx = x - R
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > R:
                pixels[off:off+3] = _WHL_BG
            else:
                h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
                s = dist / R
                r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
                pixels[off]   = int(r * 255)
                pixels[off+1] = int(g * 255)
                pixels[off+2] = int(b * 255)
            off += 3
    return Image.frombytes("RGB", (_WHL, _WHL), bytes(pixels))


class ColorPickerDialog(ctk.CTkToplevel):
    """Modern HSV colour wheel dialog.  result is (r,g,b) or None."""

    def __init__(self, parent, initial_rgb=(255, 255, 255), title="Pick Color"):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None

        r, g, b = [x / 255.0 for x in initial_rgb]
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        self._h = h
        self._s = s
        self._v = max(v, 0.02)   # keep at least a sliver of brightness so wheel shows

        self._initial_rgb = initial_rgb
        self._wheel_full = _make_wheel_full()
        self._wheel_photo = None  # ImageTk reference kept alive

        self._build_ui()
        self._refresh_wheel()
        self._update_marker()

        # Centre over parent
        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width() // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        dw = self.winfo_width()
        dh = self.winfo_height()
        self.geometry(f"+{pw - dw//2}+{ph - dh//2}")

        self.grab_set()
        self.wait_window()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = 16
        self.configure(fg_color=BG2)

        # ── Wheel canvas ──────────────────────────────────────────────────────
        self._canvas = tk.Canvas(self, width=_WHL, height=_WHL,
                                 bg=_rgb_hex(_WHL_BG), highlightthickness=0,
                                 cursor="crosshair")
        self._canvas.pack(padx=PAD, pady=(PAD, 6))
        self._wheel_item = self._canvas.create_image(0, 0, anchor="nw")
        self._canvas.bind("<Button-1>",  self._on_wheel_click)
        self._canvas.bind("<B1-Motion>", self._on_wheel_click)

        # ── Brightness slider ─────────────────────────────────────────────────
        bri_row = ctk.CTkFrame(self, fg_color="transparent")
        bri_row.pack(fill="x", padx=PAD, pady=2)
        ctk.CTkLabel(bri_row, text="☀", width=20, text_color=FG2).pack(side="left")
        self._bri_var = tk.DoubleVar(value=self._v * 100)
        ctk.CTkSlider(bri_row, from_=0, to=100, variable=self._bri_var,
                      command=self._on_bri_change,
                      button_color=BLUE, progress_color=BG3
                      ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        # ── Hex input ─────────────────────────────────────────────────────────
        hex_row = ctk.CTkFrame(self, fg_color="transparent")
        hex_row.pack(fill="x", padx=PAD, pady=4)
        ctk.CTkLabel(hex_row, text="#", width=14, text_color=FG2).pack(side="left")
        self._hex_var = tk.StringVar()
        hex_ent = ctk.CTkEntry(hex_row, textvariable=self._hex_var,
                               width=90, height=30, fg_color=BG3,
                               border_color=BORDER)
        hex_ent.pack(side="left", padx=(0, 8))
        hex_ent.bind("<Return>",   self._on_hex_commit)
        hex_ent.bind("<FocusOut>", self._on_hex_commit)

        # ── Before / After preview ────────────────────────────────────────────
        self._swatch_before = tk.Canvas(hex_row, width=30, height=30,
                                        highlightthickness=1,
                                        highlightbackground="#444")
        self._swatch_before.configure(bg=_rgb_hex(self._initial_rgb))
        self._swatch_before.pack(side="left", padx=(0, 2))
        self._swatch_after = tk.Canvas(hex_row, width=30, height=30,
                                       highlightthickness=1,
                                       highlightbackground="#666")
        self._swatch_after.pack(side="left")

        # ── OK / Cancel ───────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=PAD, pady=(4, PAD))
        ctk.CTkButton(btn_row, text="Cancel", width=90, height=32,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      command=self.destroy).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="OK", width=90, height=32,
                      fg_color=BLUE, hover_color="#0284c7",
                      command=self._ok).pack(side="right")

        self._sync_fields()

    # ── Drawing ────────────────────────────────────────────────────────────────

    def _refresh_wheel(self):
        """Apply current V to the precomputed V=1.0 wheel and update canvas."""
        img = ImageEnhance.Brightness(self._wheel_full).enhance(self._v)
        self._wheel_photo = ImageTk.PhotoImage(img)
        self._canvas.itemconfig(self._wheel_item, image=self._wheel_photo)

    def _update_marker(self):
        R = _WHL_R
        angle = self._h * 2 * math.pi
        dist  = self._s * R
        mx = R + dist * math.cos(angle)
        my = R + dist * math.sin(angle)
        MR = 7
        self._canvas.delete("marker")
        # Outer white ring
        self._canvas.create_oval(mx-MR, my-MR, mx+MR, my+MR,
                                  outline="#ffffff", width=2, tags="marker")
        # Inner black ring for contrast on light colours
        self._canvas.create_oval(mx-MR+2, my-MR+2, mx+MR-2, my+MR-2,
                                  outline="#000000", width=1, tags="marker")

    # ── Events ─────────────────────────────────────────────────────────────────

    def _on_wheel_click(self, e):
        R = _WHL_R
        dx, dy = e.x - R, e.y - R
        dist = math.sqrt(dx * dx + dy * dy)
        dist = min(dist, R)          # clamp to circle edge
        self._h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self._s = dist / R
        self._update_marker()
        self._sync_fields()

    def _on_bri_change(self, val):
        self._v = max(float(val) / 100.0, 0.001)
        self._refresh_wheel()
        self._update_marker()
        self._sync_fields()

    def _on_hex_commit(self, _=None):
        raw = self._hex_var.get().strip().lstrip("#")
        if len(raw) != 6:
            return
        try:
            r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        except ValueError:
            return
        h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
        self._h, self._s, self._v = h, s, max(v, 0.001)
        self._bri_var.set(self._v * 100)
        self._refresh_wheel()
        self._update_marker()
        self._update_swatches()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _current_rgb(self):
        r, g, b = colorsys.hsv_to_rgb(self._h, self._s, self._v)
        return (int(r * 255), int(g * 255), int(b * 255))

    def _sync_fields(self):
        rgb = self._current_rgb()
        self._hex_var.set("{:02x}{:02x}{:02x}".format(*rgb))
        self._update_swatches()

    def _update_swatches(self):
        self._swatch_after.configure(bg=_rgb_hex(self._current_rgb()))

    def _ok(self):
        self.result = self._current_rgb()
        self.destroy()


def pick_color(parent, initial_rgb=(255, 255, 255), title="Pick Color"):
    """Open ColorPickerDialog and return (r,g,b) or None."""
    dlg = ColorPickerDialog(parent, initial_rgb, title)
    return dlg.result


# ── Accordion ─────────────────────────────────────────────────────────────────

ANIM_STEPS = 8
ANIM_MS    = 12


class AccordionSection:
    def __init__(self, parent, app, icon, title_key):
        self._app      = app
        self._open     = False
        self._natural_h = 0
        self._anim_id  = None

        self._outer = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        self._outer.pack(fill="x", pady=2)

        # Header
        self._header = ctk.CTkFrame(self._outer, fg_color=BG2, corner_radius=6,
                                    cursor="hand2")
        self._header.pack(fill="x")

        # Yellow accent bar (3 px)
        accent = tk.Frame(self._header, bg=YLW, width=4)
        accent.pack(side="left", fill="y")

        ctk.CTkLabel(self._header, text=icon, font=("Helvetica", 14),
                     text_color=YLW, width=30).pack(side="left", padx=(8, 4))

        self._title_lbl = ctk.CTkLabel(self._header, text="",
                                        font=("Helvetica", 11, "bold"),
                                        text_color=FG, anchor="w")
        self._title_lbl.pack(side="left", fill="x", expand=True, padx=4, pady=12)
        app._reg(self._title_lbl, title_key)

        self._chevron = ctk.CTkLabel(self._header, text="▶",
                                      font=("Helvetica", 10), text_color=FG2, width=24)
        self._chevron.pack(side="right", padx=(0, 12))

        # Collapsible content
        self._content = ctk.CTkFrame(self._outer, fg_color=BG2, corner_radius=0, height=0)
        self._content.pack(fill="x", pady=(1, 0))
        self._content.pack_propagate(False)

        for w in (self._header, accent, self._chevron, self._title_lbl):
            w.bind("<Button-1>", self._toggle)

    @property
    def content(self):
        return self._content

    def measure(self):
        self._content.pack_propagate(True)
        self._app.update_idletasks()
        self._natural_h = self._content.winfo_reqheight()
        self._content.pack_propagate(False)
        self._content.configure(height=0)

    def open(self):
        if self._open:
            return
        self._open = True
        self._chevron.configure(text="▼")
        if self._natural_h > 0:
            self._animate(self._content.winfo_height(), self._natural_h)

    def close(self):
        if not self._open:
            return
        self._open = False
        self._chevron.configure(text="▶")
        self._animate(self._content.winfo_height(), 0)

    def _toggle(self, event=None):
        self.close() if self._open else self.open()

    def _animate(self, current, target):
        if self._anim_id:
            self._app.after_cancel(self._anim_id)
            self._anim_id = None
        if current == target:
            return
        step  = (target - current) / ANIM_STEPS
        new_h = current + step
        if abs(new_h - target) < 1:
            new_h = target
        else:
            new_h = int(new_h)
            if new_h == current:
                new_h = current + (1 if target > current else -1)
        self._content.configure(height=new_h)
        if new_h != target:
            self._anim_id = self._app.after(ANIM_MS,
                                             lambda: self._animate(new_h, target))


# ── App ───────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Mountain Everest Max")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.geometry("440x720")

        try:
            _icon = ImageTk.PhotoImage(Image.open(
                os.path.join(_RES, "resources", "app_icon_64.png")))
            self.iconphoto(True, _icon)
        except Exception:
            pass

        # i18n
        self._lang = {}
        self._i18n_widgets = []
        self._avail_langs = available_langs()

        def _read_cfg(name, default):
            try:
                return open(os.path.join(CONFIG_DIR, name)).read().strip()
            except FileNotFoundError:
                return default

        code = _read_cfg("language", "de")
        if code not in self._avail_langs:
            code = "de"
        self._lang      = load_lang(code)
        self._lang_code = code
        self._rebuild_obs_type_map()

        self._splash_var    = tk.BooleanVar(value=load_splash_enabled())
        self._autostart_var = tk.BooleanVar(value=load_autostart_enabled())

        saved = load_style()
        self._current_style = tk.StringVar(value=next(
            (k for k, v in STYLES.items() if v == saved), "Analog"))
        self._cpu_proc = None

        self._btn_action = [tk.StringVar(value="") for _ in range(4)]
        self._btn_type   = [tk.StringVar(value="shell") for _ in range(4)]
        buttons = load_buttons()
        for i, b in enumerate(buttons):
            self._btn_action[i].set(b.get("action", ""))
            self._btn_type[i].set(b.get("type", "shell"))

        obs_cfg = load_obs_config()
        self._obs_host     = tk.StringVar(value=obs_cfg["host"])
        self._obs_port     = tk.StringVar(value=str(obs_cfg["port"]))
        self._obs_password = tk.StringVar(value=obs_cfg["password"])

        self._obs_btn_type_internal = [
            obs_cfg["buttons"][i]["type"] for i in range(4)
        ]
        self._obs_btn_type = [
            tk.StringVar(value=self._obs_type_options.get(
                obs_cfg["buttons"][i]["type"],
                self._obs_type_options.get("none", "")))
            for i in range(4)
        ]
        self._obs_btn_scene = [
            tk.StringVar(value=obs_cfg["buttons"][i].get("scene", ""))
            for i in range(4)
        ]

        self._clock_format = tk.StringVar(value=_read_cfg("clock_format", "24H"))
        self._lang_var     = tk.StringVar()

        self._build_ui()

        lang_names = list(self._avail_langs.values())
        self._lang_combo.configure(values=lang_names)
        current_name = self._avail_langs.get(self._lang_code, "")
        self._lang_var.set(current_name)

        self._tick()
        self._update_cpu_bar()
        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._hide_window)
        self.bind("<Unmap>", lambda e: self._hide_window() if self.state() == "iconic" else None)
        self.after(500, self._start_cpu_auto_clean)

    # ── i18n ──────────────────────────────────────────────────────────────────

    def T(self, key, **kwargs):
        val = self._lang.get(key, key)
        if kwargs:
            try:
                val = val.format(**kwargs)
            except (KeyError, IndexError):
                pass
        return val

    def _reg(self, widget, key, attr="text"):
        self._i18n_widgets.append((widget, key, attr))
        return widget

    def _rebuild_obs_type_map(self):
        self._obs_type_options = {
            internal: self._lang.get(f"obs_{internal}", internal)
            for internal in OBS_INTERNAL_ORDER
        }
        self._obs_type_display_to_internal = {
            v: k for k, v in self._obs_type_options.items()
        }

    def _load_lang_code(self, code):
        self._lang      = load_lang(code)
        self._lang_code = code
        self._rebuild_obs_type_map()
        self._apply_lang()

    def _apply_lang(self):
        for widget, key, attr in self._i18n_widgets:
            try:
                widget.configure(**{attr: self.T(key)})
            except Exception:
                pass

        obs_type_labels = [self._obs_type_options[k] for k in OBS_INTERNAL_ORDER]
        for i in range(4):
            if hasattr(self, "_obs_type_combos"):
                self._obs_type_combos[i].configure(values=obs_type_labels)
                internal = self._obs_btn_type_internal[i]
                display  = self._obs_type_options.get(internal, obs_type_labels[0])
                self._obs_btn_type[i].set(display)

        if hasattr(self, "_btn_type_menus"):
            new_labels = self._numpad_type_labels_fn()
            for i, menu in enumerate(self._btn_type_menus):
                menu.configure(values=new_labels)
                cur = self._btn_type[i].get()
                try:
                    menu.set(new_labels[self._numpad_type_internal.index(cur)])
                except (ValueError, IndexError):
                    menu.set(new_labels[1])

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header bar ──
        hdr = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0, height=50)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(inner, text="MOUNTAIN", font=("Helvetica", 15, "bold"),
                     text_color=FG).pack(side="left")
        ctk.CTkLabel(inner, text=" EVEREST MAX", font=("Helvetica", 15, "bold"),
                     text_color=BLUE).pack(side="left")
        ctk.CTkButton(hdr, text="✕", width=32, height=32, corner_radius=6,
                      fg_color="transparent", hover_color=BG3, text_color=FG2,
                      font=("Helvetica", 14), command=self._quit).place(relx=1.0,
                      rely=0.5, anchor="e", x=-8)

        # ── Dashboard ──
        dash = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        dash.pack(fill="x", pady=(2, 0))

        self._clock_label = ctk.CTkLabel(dash, text="",
                                          font=("Courier", 30, "bold"), text_color=BLUE)
        self._clock_label.pack(pady=(12, 0))

        self._date_label = ctk.CTkLabel(dash, text="",
                                         font=("Helvetica", 10), text_color=FG2)
        self._date_label.pack(pady=(2, 4))

        # Format + Language row
        fmt_row = ctk.CTkFrame(dash, fg_color="transparent")
        fmt_row.pack(pady=4)

        ctk.CTkSegmentedButton(
            fmt_row, values=["24H", "12H"],
            variable=self._clock_format,
            command=lambda _: self._on_format_change(),
            font=("Helvetica", 10),
            fg_color=BG3, selected_color=BLUE, selected_hover_color=BLUE,
            unselected_color=BG3, unselected_hover_color=BG2,
            text_color=FG, width=90, height=28,
        ).pack(side="left", padx=(0, 10))

        self._reg(
            ctk.CTkLabel(fmt_row, text="", text_color=FG2, font=("Helvetica", 11)),
            "language_label"
        ).pack(side="left", padx=(0, 4))

        self._lang_combo = ctk.CTkComboBox(
            fmt_row, variable=self._lang_var, values=[],
            command=lambda val: self._on_lang_change(val),
            width=120, height=30, font=("Helvetica", 11),
            fg_color=BG3, button_color=BLUE, border_color=BORDER,
            text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
            dropdown_hover_color=BG3)
        self._lang_combo.pack(side="left")

        self._reg(
            ctk.CTkButton(
                fmt_row, text="", font=("Helvetica", 11, "bold"),
                fg_color=RED, hover_color="#b91c1c", text_color=BG,
                width=130, height=30,
                command=self._reset_dial_image),
            "dial_reset_btn"
        ).pack(side="left", padx=(10, 0))

        # Analog/Digital row
        style_row = ctk.CTkFrame(dash, fg_color="transparent")
        style_row.pack(pady=(2, 2))

        ctk.CTkSegmentedButton(
            style_row, values=list(STYLES.keys()),
            variable=self._current_style,
            command=lambda _: self._on_style_change(),
            font=("Helvetica", 10),
            fg_color=BG3, selected_color=BLUE, selected_hover_color=BLUE,
            unselected_color=BG3, unselected_hover_color=BG2,
            text_color=FG, width=160, height=32,
        ).pack()

        self._style_status = ctk.CTkLabel(dash, text="", font=("Helvetica", 11),
                                           text_color=GRN)
        self._style_status.pack(pady=(0, 4))

        # Splash + Autostart switches
        sw_row = ctk.CTkFrame(dash, fg_color="transparent")
        sw_row.pack(pady=(0, 12))

        self._reg(
            ctk.CTkSwitch(sw_row, text="", variable=self._splash_var,
                          command=lambda: save_splash_enabled(self._splash_var.get()),
                          onvalue=True, offvalue=False,
                          progress_color=BLUE, button_color=FG, button_hover_color=FG2,
                          fg_color=BG3, text_color=FG2, font=("Helvetica", 11)),
            "splash_toggle"
        ).pack(side="left", padx=(0, 16))

        self._reg(
            ctk.CTkSwitch(sw_row, text="", variable=self._autostart_var,
                          command=lambda: save_autostart_enabled(self._autostart_var.get()),
                          onvalue=True, offvalue=False,
                          progress_color=BLUE, button_color=FG, button_hover_color=FG2,
                          fg_color=BG3, text_color=FG2, font=("Helvetica", 11)),
            "autostart_toggle"
        ).pack(side="left")

        # ── Accordion scroll area ──
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))

        self._sections = []

        # ── Section 1: Monitor Mode ──
        s1 = AccordionSection(scroll, self, "⚡", "monitor_title")
        self._sections.append(s1)
        b1 = s1.content

        self._btn_cpu = ctk.CTkButton(
            b1, text=self.T("monitor_start"), command=self._toggle_cpu,
            fg_color=YLW, text_color="#0d0d14", hover_color="#d4a900",
            font=("Helvetica", 10, "bold"), height=34, corner_radius=6)
        self._btn_cpu.pack(pady=8)

        self._cpu_status = ctk.CTkLabel(b1, text="", font=("Helvetica", 11),
                                         text_color=FG2)
        self._cpu_status.pack(pady=(0, 12))

        # ── Section 2: Main Display ──
        s2 = AccordionSection(scroll, self, "🖥", "main_display_title")
        self._sections.append(s2)
        b2 = s2.content

        mode_row = ctk.CTkFrame(b2, fg_color="transparent")
        mode_row.pack(pady=(10, 4))

        try:
            _saved_mode = open(MAIN_MODE_FILE).read().strip()
        except FileNotFoundError:
            _saved_mode = "clock"
        self._main_mode = _saved_mode if _saved_mode in (
            "image","clock","cpu","gpu","hd","network","ram","apm") else "clock"
        self._main_just_uploaded = False
        self._after_dial_reset = False

        _MODE_KEYS = ["image","clock","cpu","gpu","hd","network","ram","apm"]
        _MODE_LANG  = ["main_mode_image","main_mode_clock","main_mode_cpu",
                       "main_mode_gpu","main_mode_hd","main_mode_network",
                       "main_mode_ram","main_mode_apm"]
        self._mode_labels   = [self.T(k) for k in _MODE_LANG]
        self._mode_key_map  = {lbl: key for key, lbl in zip(_MODE_KEYS, self._mode_labels)}

        ctk.CTkLabel(mode_row, text="", font=("Helvetica", 11),
                     text_color=FG2).pack(side="left", padx=(0, 6))
        self._reg(ctk.CTkLabel(mode_row, text="", font=("Helvetica", 11),
                               text_color=FG2), "main_mode_label").pack(side="left", padx=(0,6))
        self._main_mode_var = tk.StringVar(value=self._mode_labels[_MODE_KEYS.index(self._main_mode)])
        self._main_mode_menu = ctk.CTkOptionMenu(
            mode_row, variable=self._main_mode_var,
            values=self._mode_labels,
            command=lambda lbl: self._set_main_mode(self._mode_key_map[lbl]),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=160, height=32)
        self._main_mode_menu.pack(side="left")

        self._reg(
            ctk.CTkButton(b2, text="", command=self._upload_main_image,
                          fg_color=BLUE, text_color=FG, hover_color="#0884be",
                          font=("Helvetica", 10, "bold"), height=34, corner_radius=6),
            "main_display_upload"
        ).pack(pady=4, padx=12, fill="x")

        self._main_bar = ctk.CTkProgressBar(b2, mode="determinate",
                                             progress_color=BLUE, fg_color=BG3,
                                             height=6, corner_radius=0)
        self._main_bar.set(0)
        self._main_bar.pack(fill="x", padx=12, pady=(0, 2))

        self._main_status = ctk.CTkLabel(b2, text="", font=("Helvetica", 11),
                                          text_color=FG2)
        self._main_status.pack(pady=(0, 12))

        # ── Section 3: Numpad Keys ──
        s3 = AccordionSection(scroll, self, "⌨", "numpad_title")
        self._sections.append(s3)
        b3 = s3.content

        self._reg(
            ctk.CTkLabel(b3, text="", font=("Helvetica", 11), text_color=FG2),
            "numpad_subtitle"
        ).pack(pady=(8, 4))

        self._img_btns        = []
        self._upload_btns     = []
        self._upload_bars     = []
        self._btn_type_menus  = []
        self._folder_btns     = []

        _TYPE_INTERNAL = ["none", "shell", "url", "folder", "app"]

        def _type_labels():
            return [self.T("action_type_none"),   self.T("action_type_shell"),
                    self.T("action_type_url"),     self.T("action_type_folder"),
                    self.T("action_type_app")]

        _folder_pil = Image.open(os.path.join(_RES, "resources", "foldericon.png")).convert("RGBA")
        self._folder_img = ctk.CTkImage(light_image=_folder_pil, dark_image=_folder_pil, size=(24, 24))
        _folder_pil_dim = _folder_pil.copy()
        _folder_pil_dim.putalpha(_folder_pil_dim.getchannel("A").point(lambda v: v // 3))
        self._folder_img_dim = ctk.CTkImage(light_image=_folder_pil_dim, dark_image=_folder_pil_dim, size=(24, 24))
        _folder_img     = self._folder_img
        _folder_img_dim = self._folder_img_dim

        for i in range(4):
            card = ctk.CTkFrame(b3, fg_color=BG3, corner_radius=4)
            card.pack(fill="x", padx=12, pady=2)

            # Header row
            header_row = ctk.CTkFrame(card, fg_color="transparent")
            header_row.pack(fill="x", padx=8, pady=(6, 0))
            ctk.CTkLabel(header_row, text=f"D{i+1}", font=("Helvetica", 10, "bold"),
                         text_color=YLW).pack(side="left")

            # Action row
            action_row = ctk.CTkFrame(card, fg_color="transparent")
            action_row.pack(fill="x", padx=4, pady=(2, 2))

            self._reg(
                ctk.CTkLabel(action_row, text="", font=("Helvetica", 10),
                             text_color=FG2, width=50, anchor="w"),
                "action_label"
            ).pack(side="left", padx=(4, 2))

            idx = i
            cur_internal = self._btn_type[i].get()
            labels = _type_labels()
            cur_label = labels[_TYPE_INTERNAL.index(cur_internal)] if cur_internal in _TYPE_INTERNAL else labels[1]

            type_menu = ctk.CTkOptionMenu(
                action_row, values=labels,
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=("Helvetica", 11), width=88, height=30,
                dynamic_resizing=False,
                command=lambda val, ix=idx: self._on_btn_type_change(val, ix)
            )
            type_menu.set(cur_label)
            type_menu.pack(side="left", padx=(2, 2))
            self._btn_type_menus.append(type_menu)

            ctk.CTkEntry(action_row, textvariable=self._btn_action[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=("Helvetica", 11), height=30,
                         ).pack(side="left", padx=4, expand=True, fill="x")

            cur_type = self._btn_type[i].get()
            browse_active = cur_type in ("folder", "app")
            folder_btn = ctk.CTkButton(
                action_row, text="",
                image=_folder_img if browse_active else _folder_img_dim,
                width=30, height=30,
                command=lambda ix=idx: self._browse_action(ix),
                fg_color="transparent", hover_color=BG3, corner_radius=4,
                state="normal" if browse_active else "disabled",
            )
            folder_btn.pack(side="left", padx=(0, 2))
            self._folder_btns.append(folder_btn)

            ctk.CTkButton(action_row, text="✓", width=36, height=30,
                          command=lambda ix=idx: self._apply_btn(ix),
                          fg_color=GRN, text_color=FG, hover_color="#18a348",
                          font=("Helvetica", 10, "bold"), corner_radius=4,
                          ).pack(side="left", padx=(0, 4))

            # Image row
            image_row = ctk.CTkFrame(card, fg_color="transparent")
            image_row.pack(fill="x", padx=4, pady=(0, 4))

            self._reg(
                ctk.CTkLabel(image_row, text="", font=("Helvetica", 10),
                             text_color=FG2, width=50, anchor="w"),
                "image_label"
            ).pack(side="left", padx=(4, 2))

            upload_btn = self._reg(
                ctk.CTkButton(
                    image_row, text="",
                    fg_color=BLUE, text_color=FG, hover_color="#0884be",
                    font=("Helvetica", 11), height=30, corner_radius=4,
                    command=lambda ix=idx: self._upload_image(ix),
                ),
                "main_display_upload"
            )
            upload_btn.pack(side="left", padx=(2, 4), expand=True)
            self._upload_btns.append(upload_btn)
            self._img_btns.append(upload_btn)

            bar = ctk.CTkProgressBar(card, mode="determinate",
                                     progress_color=BLUE, fg_color=BG3,
                                     height=4, corner_radius=0)
            bar.set(0)
            bar.pack(fill="x", padx=4, pady=(0, 4))
            self._upload_bars.append(bar)

        self._numpad_type_internal = _TYPE_INTERNAL
        self._numpad_type_labels_fn = _type_labels

        reset_row = ctk.CTkFrame(b3, fg_color="transparent")
        reset_row.pack(fill="x", padx=8, pady=(4, 0))
        self._reg(
            ctk.CTkButton(reset_row, text="", height=28, corner_radius=4,
                          fg_color=RED, hover_color="#b91c1c", text_color=FG,
                          font=("Helvetica", 11),
                          command=self._reset_buttons_flash),
            "reset_buttons_btn"
        ).pack(fill="x")

        self._numpad_info = ctk.CTkLabel(b3, text="", font=("Helvetica", 11),
                                          text_color=GRN)
        self._numpad_info.pack(pady=(4, 10))

        # ── RGB ──────────────────────────────────────────────────────────────
        s5 = AccordionSection(scroll, self, "💡", "rgb_title")
        self._sections.append(s5)
        c = s5.content

        # Effect selector
        rgb_mode_row = ctk.CTkFrame(c, fg_color="transparent")
        rgb_mode_row.pack(fill="x", padx=10, pady=(10, 2))
        self._reg(
            ctk.CTkLabel(rgb_mode_row, text="", font=("Helvetica", 11), text_color=FG2),
            "rgb_mode_label"
        ).pack(side="left", padx=(0, 6))

        # (effect_id, has_speed, has_brightness, has_color1, has_color2, has_direction)
        _RGB_EFFECTS = [
            ("Static",             "static",           False, True,  True,  False, False),
            ("Breathing",          "breathing",        True,  True,  True,  False, False),
            ("Breathing Rainbow",  "breathing-rainbow",True,  True,  False, False, False),
            ("Breathing Dual",     "breathing-dual",   True,  True,  True,  True,  False),
            ("Wave",               "wave",             True,  True,  True,  False, True),
            ("Wave Rainbow",       "wave-rainbow",     True,  True,  False, False, True),
            ("Tornado",            "tornado",          True,  True,  True,  False, True),
            ("Tornado Rainbow",    "tornado-rainbow",  True,  True,  False, False, True),
            ("Reactive",           "reactive",         True,  True,  True,  True,  False),
            ("Yeti",               "yeti",             True,  True,  True,  True,  False),
            ("Matrix",             "matrix",           True,  True,  True,  True,  False),
            ("Off",                "off",              False, False, False, False, False),
        ]
        self._rgb_effect_map = {name: (eid, hs, hb, hc1, hc2, hd)
                                for name, eid, hs, hb, hc1, hc2, hd in _RGB_EFFECTS}
        _rgb_names = [e[0] for e in _RGB_EFFECTS]
        self._rgb_mode_var = tk.StringVar(value=_rgb_names[0])
        self._rgb_mode_menu = ctk.CTkOptionMenu(
            rgb_mode_row, variable=self._rgb_mode_var, values=_rgb_names,
            command=lambda _: self._rgb_update_controls(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=180, height=32)
        self._rgb_mode_menu.pack(side="left")

        # Speed + Brightness sliders
        def _labeled_slider(parent, label_key, from_=0, to=100, init=50):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            lbl = self._reg(ctk.CTkLabel(row, text="", text_color=FG2, font=("Helvetica", 11), width=120, anchor="w"), label_key)
            lbl.pack(side="left")
            val_lbl = ctk.CTkLabel(row, text=str(init), text_color=FG, font=("Helvetica", 11), width=30)
            val_lbl.pack(side="right")
            sl = ctk.CTkSlider(row, from_=from_, to=to, number_of_steps=to-from_,
                               fg_color=BG3, progress_color=BLUE, button_color=BLUE,
                               button_hover_color=BLUE, width=180, height=16)
            sl.set(init)
            sl.pack(side="left", padx=(0, 4))
            sl.configure(command=lambda v, l=val_lbl: l.configure(text=str(int(v))))
            return sl, row

        self._rgb_speed_sl, self._rgb_speed_row = _labeled_slider(c, "rgb_speed_label", init=50)
        self._rgb_bri_sl,   self._rgb_bri_row   = _labeled_slider(c, "rgb_brightness_label", init=100)

        # Color pickers
        color_row = ctk.CTkFrame(c, fg_color="transparent")
        color_row.pack(fill="x", padx=10, pady=2)
        self._rgb_color1 = (255, 0, 0)
        self._rgb_color2 = (0, 0, 255)

        self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2, font=("Helvetica", 11)), "rgb_color1_label").pack(side="left", padx=(0, 4))
        self._rgb_c1_btn = ctk.CTkButton(color_row, text="", width=40, height=28,
                                          fg_color="#ff0000", hover_color="#ff0000", corner_radius=4,
                                          command=lambda: self._pick_rgb_color(1))
        self._rgb_c1_btn.pack(side="left", padx=(0, 12))

        self._rgb_c2_lbl = self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2, font=("Helvetica", 11)), "rgb_color2_label")
        self._rgb_c2_lbl.pack(side="left", padx=(0, 4))
        self._rgb_c2_btn = ctk.CTkButton(color_row, text="", width=40, height=28,
                                          fg_color="#0000ff", hover_color="#0000ff", corner_radius=4,
                                          command=lambda: self._pick_rgb_color(2))
        self._rgb_c2_btn.pack(side="left")

        # Direction
        dir_row = ctk.CTkFrame(c, fg_color="transparent")
        dir_row.pack(fill="x", padx=10, pady=2)
        self._rgb_dir_row = dir_row
        self._reg(ctk.CTkLabel(dir_row, text="", text_color=FG2, font=("Helvetica", 11)), "rgb_direction_label").pack(side="left", padx=(0, 6))
        self._dir_wave    = ["→ L→R", "↓ T→B", "← R→L", "↑ B→T"]
        self._dir_tornado = ["↻ CW", "↺ CCW"]
        self._rgb_dir_val_map = {"→ L→R": 0, "↓ T→B": 2, "← R→L": 4, "↑ B→T": 6,
                                 "↻ CW": 9, "↺ CCW": 10}
        self._rgb_dir_var = tk.StringVar(value=self._dir_wave[0])
        self._rgb_dir_menu = ctk.CTkOptionMenu(
            dir_row, variable=self._rgb_dir_var, values=self._dir_wave,
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=120, height=28)
        self._rgb_dir_menu.pack(side="left")

        # Load saved RGB settings
        _rgb_saved = load_rgb_config()
        if _rgb_saved.get("effect") in self._rgb_effect_map:
            self._rgb_mode_var.set(_rgb_saved["effect"])
        if "speed" in _rgb_saved:
            self._rgb_speed_sl.set(_rgb_saved["speed"])
        if "brightness" in _rgb_saved:
            self._rgb_bri_sl.set(_rgb_saved["brightness"])
        if "color1" in _rgb_saved and len(_rgb_saved["color1"]) == 3:
            self._rgb_color1 = tuple(_rgb_saved["color1"])
            _c1h = "#{:02x}{:02x}{:02x}".format(*self._rgb_color1)
            self._rgb_c1_btn.configure(fg_color=_c1h, hover_color=_c1h)
        if "color2" in _rgb_saved and len(_rgb_saved["color2"]) == 3:
            self._rgb_color2 = tuple(_rgb_saved["color2"])
            _c2h = "#{:02x}{:02x}{:02x}".format(*self._rgb_color2)
            self._rgb_c2_btn.configure(fg_color=_c2h, hover_color=_c2h)
        if "direction" in _rgb_saved and _rgb_saved["direction"] in self._rgb_dir_val_map:
            self._rgb_dir_var.set(_rgb_saved["direction"])
        self._rgb_update_controls()

        # Apply button + status
        rgb_apply_row = ctk.CTkFrame(c, fg_color="transparent")
        self._rgb_apply_row = rgb_apply_row
        rgb_apply_row.pack(fill="x", padx=10, pady=(6, 10))
        self._reg(
            ctk.CTkButton(rgb_apply_row, text="", font=("Helvetica", 11),
                          fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                          width=120, height=32, command=self._apply_rgb),
            "rgb_apply"
        ).pack(side="left")
        self._rgb_status = ctk.CTkLabel(rgb_apply_row, text="", text_color=FG2, font=("Helvetica", 11))
        self._rgb_status.pack(side="left", padx=(10, 0))

        self._rgb_section = s5
        self._rgb_update_controls()

        # ── Section 6: Zones & Side Ring ─────────────────────────────────────
        s6 = AccordionSection(scroll, self, "🎨", "zone_title")
        self._sections.append(s6)
        c6 = s6.content

        self._rgb_win = None   # reference to open CustomRGBWindow (if any)

        # stub so old methods don't AttributeError if called
        self._zone_status = ctk.CTkLabel(c6, text="", text_color=FG2,
                                         font=("Helvetica", 11))

        open_row = ctk.CTkFrame(c6, fg_color="transparent")
        open_row.pack(pady=(16, 16))
        self._reg(
            ctk.CTkButton(open_row, text="", font=("Helvetica", 12, "bold"),
                          fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                          width=220, height=38, command=self._open_rgb_editor),
            "zone_open_editor"
        ).pack()

        # ── Section 5 (moved): OBS Integration ───────────────────────────────
        s4 = AccordionSection(scroll, self, "📡", "obs_title")
        self._sections.append(s4)
        b4 = s4.content

        conn = ctk.CTkFrame(b4, fg_color=BG3, corner_radius=4)
        conn.pack(fill="x", padx=12, pady=(10, 4))

        obs_top = ctk.CTkFrame(conn, fg_color="transparent")
        obs_top.pack(pady=(8, 2))
        ctk.CTkLabel(obs_top, text="Host:", text_color=FG2,
                     font=("Helvetica", 11)).pack(side="left")
        ctk.CTkEntry(obs_top, textvariable=self._obs_host, width=120, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=("Helvetica", 11)).pack(side="left", padx=(2, 8))
        ctk.CTkLabel(obs_top, text="Port:", text_color=FG2,
                     font=("Helvetica", 11)).pack(side="left")
        ctk.CTkEntry(obs_top, textvariable=self._obs_port, width=62, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=("Helvetica", 11)).pack(side="left", padx=2)

        obs_pw = ctk.CTkFrame(conn, fg_color="transparent")
        obs_pw.pack(pady=2)
        self._reg(
            ctk.CTkLabel(obs_pw, text="", text_color=FG2, font=("Helvetica", 11)),
            "obs_password"
        ).pack(side="left")
        ctk.CTkEntry(obs_pw, textvariable=self._obs_password, width=180, height=30,
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=("Helvetica", 11), show="*").pack(side="left", padx=4)

        obs_btn_row = ctk.CTkFrame(conn, fg_color="transparent")
        obs_btn_row.pack(pady=(6, 8))
        self._reg(
            ctk.CTkButton(obs_btn_row, text="", command=self._obs_connect,
                          fg_color=BLUE, text_color=FG, hover_color="#0884be",
                          font=("Helvetica", 11, "bold"), height=34, corner_radius=6),
            "obs_connect"
        ).pack(side="left")
        self._reg(
            ctk.CTkButton(obs_btn_row, text="", command=self._obs_disconnect,
                          fg_color=RED, text_color=BG, hover_color="#c03030",
                          font=("Helvetica", 11, "bold"), height=34, corner_radius=6),
            "obs_disconnect"
        ).pack(side="left", padx=(6, 0))

        self._obs_status = ctk.CTkLabel(conn, text="", font=("Helvetica", 11),
                                         text_color=FG2, wraplength=300)
        self._obs_status.pack(pady=(0, 8))

        obs_btns_frame = ctk.CTkFrame(b4, fg_color="transparent")
        obs_btns_frame.pack(padx=12, pady=(0, 4), fill="x")
        self._obs_scene_entries = []
        self._obs_type_combos   = []

        obs_type_labels = [self._obs_type_options[k] for k in OBS_INTERNAL_ORDER]

        for i in range(4):
            row = ctk.CTkFrame(obs_btns_frame, fg_color=BG3, corner_radius=4)
            row.pack(fill="x", pady=2)

            tk.Frame(row, bg=BLUE, width=3).pack(side="left", fill="y")
            ctk.CTkLabel(row, text=f"D{i+1}", font=("Helvetica", 10, "bold"),
                         text_color=BLUE, width=30).pack(side="left", padx=(6, 4), pady=6)

            idx = i
            type_combo = ctk.CTkComboBox(
                row, variable=self._obs_btn_type[i], values=obs_type_labels,
                width=112, height=30, font=("Helvetica", 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=idx: self._on_obs_type_change(ix, val))
            type_combo.pack(side="left", padx=4)
            self._obs_type_combos.append(type_combo)

            internal_val = self._obs_btn_type_internal[i]
            scene_state  = "normal" if internal_val == "scene" else "disabled"
            scene_combo  = ctk.CTkComboBox(
                row, variable=self._obs_btn_scene[i],
                values=[], width=155, height=30, state=scene_state,
                font=("Helvetica", 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3)
            scene_combo.pack(side="left", padx=4)
            self._obs_scene_entries.append(scene_combo)
            self._obs_btn_scene[i].trace_add("write", lambda *_, ix=idx: self._obs_auto_save(ix))

        self._obs_info = ctk.CTkLabel(b4, text="", font=("Helvetica", 11),
                                       text_color=GRN)
        self._obs_info.pack(pady=(4, 10))

        # Apply i18n, then measure sections
        self._apply_lang()
        self.update_idletasks()
        for s in self._sections:
            s.measure()
        self._sections[0].open()

        # Scroll-Geschwindigkeit deckeln — CTk scrollt intern 5 Einheiten,
        # egal über welchem Child-Widget. yview direkt wrappen fängt alles ab.
        _c = scroll._parent_canvas
        _orig_yview = _c.yview

        def _capped_yview(*args):
            if args and args[0] == "scroll":
                n    = max(-2, min(2, int(args[1])))
                what = args[2] if len(args) > 2 else "units"
                return _orig_yview("scroll", n, what)
            return _orig_yview(*args)

        _c.yview = _capped_yview

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _rgb_update_controls(self):
        name = self._rgb_mode_var.get()
        _, hs, hb, hc1, hc2, hd = self._rgb_effect_map.get(name, ("", False,False,False,False,False))
        state_speed = "normal" if hs else "disabled"
        state_bri   = "normal" if hb else "disabled"
        state_c1    = "normal" if hc1 else "disabled"
        state_c2    = "normal" if hc2 else "disabled"
        self._rgb_speed_sl.configure(state=state_speed)
        self._rgb_bri_sl.configure(state=state_bri)
        self._rgb_c1_btn.configure(state=state_c1)
        self._rgb_c2_btn.configure(state=state_c2)
        self._rgb_c2_lbl.configure(text_color=FG2 if hc2 else BG3)
        # direction row: show/hide + update options based on effect type
        was_visible = self._rgb_dir_row.winfo_ismapped()
        if hd:
            is_tornado = "tornado" in self._rgb_effect_map.get(name, ("",))[0]
            new_opts = self._dir_tornado if is_tornado else self._dir_wave
            cur = self._rgb_dir_var.get()
            if cur not in new_opts:
                self._rgb_dir_var.set(new_opts[0])
            self._rgb_dir_menu.configure(values=new_opts)
            if not was_visible:
                self._rgb_dir_row.pack(fill="x", padx=10, pady=2,
                                       before=self._rgb_apply_row)
        else:
            self._rgb_dir_row.pack_forget()
        # Remeasure section so accordion height adjusts
        if hasattr(self, "_rgb_section"):
            self.update_idletasks()
            s = self._rgb_section
            was_open = s._open
            s.measure()
            if was_open:
                s._content.configure(height=s._natural_h)

    def _pick_rgb_color(self, which):
        initial = self._rgb_color1 if which == 1 else self._rgb_color2
        rgb = pick_color(self, initial_rgb=initial, title="Farbe wählen")
        if rgb is None:
            return
        hex_color = _rgb_hex(rgb)
        if which == 1:
            self._rgb_color1 = rgb
            self._rgb_c1_btn.configure(fg_color=hex_color, hover_color=hex_color)
        else:
            self._rgb_color2 = rgb
            self._rgb_c2_btn.configure(fg_color=hex_color, hover_color=hex_color)

    def _apply_rgb(self):
        name = self._rgb_mode_var.get()
        eid, hs, hb, hc1, hc2, hd = self._rgb_effect_map[name]
        speed = int(self._rgb_speed_sl.get())
        bri   = int(self._rgb_bri_sl.get())
        r1,g1,b1 = self._rgb_color1
        r2,g2,b2 = self._rgb_color2
        c1_hex = f"{r1:02x}{g1:02x}{b1:02x}"
        c2_hex = f"{r2:02x}{g2:02x}{b2:02x}"
        direction = self._rgb_dir_val_map.get(self._rgb_dir_var.get(), 0)
        self._rgb_status.configure(text=self.T("rgb_applying"), text_color=YLW)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None
        def run():
            r = subprocess.run(
                _cmd("rgb", eid, str(speed), str(bri), c1_hex, c2_hex, str(direction)),
                capture_output=True)
            ok = r.returncode == 0
            err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            if ok:
                save_rgb_config({
                    "effect": name,
                    "speed": speed,
                    "brightness": bri,
                    "color1": list(self._rgb_color1),
                    "color2": list(self._rgb_color2),
                    "direction": self._rgb_dir_var.get(),
                })
            def finish():
                self._rgb_status.configure(
                    text=self.T("rgb_applied") if ok else f"{self.T('rgb_error')} — {err}",
                    text_color=GRN if ok else RED)
                if was_running:
                    self._start_cpu_auto()
            self.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _pick_zone_color(self, zone_key):
        initial = self._zone_colors.get(zone_key, (0, 0, 0))
        rgb = pick_color(self, initial_rgb=initial, title="Farbe wählen")
        if rgb is None:
            return
        self._zone_colors[zone_key] = rgb
        hex_color = _rgb_hex(rgb)
        self._zone_btns[zone_key].configure(fg_color=hex_color, hover_color=hex_color)

    def _reset_zones(self):
        self._zone_colors = dict(self._zone_defaults)
        for k, rgb in self._zone_colors.items():
            hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
            if k in self._zone_btns:
                self._zone_btns[k].configure(fg_color=hex_color, hover_color=hex_color)
        self._zone_status.configure(text="", text_color=FG2)

    def _apply_zones(self):
        self._zone_status.configure(text=self.T("zone_applying"), text_color=YLW)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None
        brightness = int(self._zone_bri_sl.get())
        tokens = []
        for k, rgb in self._zone_colors.items():
            tokens.append(f"{k}:{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")
        tokens.append(f"brightness:{brightness}")
        def run():
            result = subprocess.run(_cmd("custom-rgb", *tokens), capture_output=True)
            ok = result.returncode == 0
            err = (result.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            if ok:
                save_zone_config(self._zone_colors, brightness)
            def finish():
                self._zone_status.configure(
                    text=self.T("zone_applied") if ok else f"{self.T('zone_error')} — {err}",
                    text_color=GRN if ok else RED)
                if was_running:
                    self._start_cpu_auto()
            self.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _reset_dial_image(self):
        def run():
            subprocess.run(_cmd("reset-dial"), capture_output=True)
            self.after(0, lambda: setattr(self, "_after_dial_reset", True))
        threading.Thread(target=run, daemon=True).start()

    def _on_format_change(self):
        with open(os.path.join(CONFIG_DIR, "clock_format"), "w") as f:
            f.write(self._clock_format.get())

    def _on_lang_change(self, val=None):
        selected_name = val if val is not None else self._lang_var.get()
        code = None
        for c, name in self._avail_langs.items():
            if name == selected_name:
                code = c
                break
        if code is None:
            return
        with open(os.path.join(CONFIG_DIR, "language"), "w") as f:
            f.write(code)
        self._load_lang_code(code)

    def _tick(self):
        now = datetime.datetime.now()
        if self._clock_format.get() == "12H":
            time_str = now.strftime("%I:%M:%S %p")
        else:
            time_str = now.strftime("%H:%M:%S")
        self._clock_label.configure(text=time_str)

        days   = self._lang.get("days",
            ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])
        months = self._lang.get("months",
            ["January","February","March","April","May","June",
             "July","August","September","October","November","December"])
        date_str = f"{days[now.weekday()]}, {now.day:02d}. {months[now.month-1]} {now.year}"
        self._date_label.configure(text=date_str)
        self.after(1000, self._tick)

    def _update_cpu_bar(self):
        if self._cpu_proc and self._cpu_proc.poll() is not None:
            # Subprocess died unexpectedly — reset button state
            self._cpu_proc = None
            self._btn_cpu.configure(text=self.T("monitor_start"),
                                    fg_color=YLW, text_color="#0d0d14")
            self._cpu_status.configure(text=self.T("monitor_stopped"), text_color=RED)
        self.after(2000, self._update_cpu_bar)

    def _on_style_change(self):
        label = self._current_style.get()
        save_style(STYLES[label])
        self._style_status.configure(
            text=self.T("style_sending", style=label), text_color=BLUE)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        def cb(ok):
            self._style_status.configure(
                text=self.T("style_active", style=label) if ok else self.T("style_error"),
                text_color=GRN if ok else RED)
            if was_running:
                self._start_cpu_auto()
        self._run_sync(callback=cb)

    def _run_sync(self, callback=None):
        style_arg = STYLES[self._current_style.get()]
        def task():
            result = subprocess.run(_cmd(style_arg), capture_output=True)
            ok = result.returncode == 0
            if callback:
                self.after(0, lambda: callback(ok))
        threading.Thread(target=task, daemon=True).start()

    def _open_rgb_editor(self):
        """Open the per-key RGB editor window (singleton)."""
        if self._rgb_win is not None and self._rgb_win.winfo_exists():
            self._rgb_win.focus()
            return
        self._rgb_win = CustomRGBWindow(self)

    def _stop_cpu_proc(self):
        """Terminate CPU monitor if running. Returns True if it was running."""
        if self._cpu_proc and self._cpu_proc.poll() is None:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None
            return True
        return False

    def _start_cpu_auto_clean(self):
        """First start: kill orphans from previous sessions in background, then start."""
        def run():
            pkill = "basecamp-controller.*cpu" if _FROZEN else r"mountain-time-sync\.py.*cpu"
            subprocess.run(["pkill", "-f", pkill], capture_output=True)
            time.sleep(0.4)
            self.after(0, self._start_cpu_auto)
        threading.Thread(target=run, daemon=True).start()

    def _start_cpu_auto(self):
        if not (self._cpu_proc and self._cpu_proc.poll() is None):
            self._toggle_cpu()

    def _toggle_cpu(self):
        if self._cpu_proc and self._cpu_proc.poll() is None:
            self._cpu_proc.terminate()
            self._cpu_proc = None
            self._btn_cpu.configure(text=self.T("monitor_start"),
                                    fg_color=YLW, text_color="#0d0d14")
            self._cpu_status.configure(text=self.T("monitor_stopped"), text_color=RED)
        else:
            style_arg = STYLES[self._current_style.get()]
            try:
                _stderr_log = open(os.path.join(CONFIG_DIR, "controller_error.log"), "w")
                self._cpu_proc = subprocess.Popen(
                    _cmd("cpu", style_arg),
                    stdout=subprocess.DEVNULL, stderr=_stderr_log)
                self._btn_cpu.configure(text=self.T("monitor_stop"),
                                        fg_color=RED, text_color=BG,
                                        font=("Helvetica", 11, "bold"))
                self._cpu_status.configure(text=self.T("monitor_running"), text_color=GRN)
            except Exception as e:
                self._cpu_status.configure(text=f"{self.T('error')}: {e}", text_color=RED)

    def _obs_connect(self):
        self._obs_status.configure(text=self.T("obs_connecting"), text_color=BLUE)
        cfg = self._obs_build_cfg()
        save_obs_config(cfg)
        # Update backup if we have configured buttons (not coming from disconnected state)
        if any(b["type"] != "none" for b in cfg["buttons"]):
            with open(OBS_BACKUP_FILE, "w") as f:
                json.dump(cfg, f, indent=2)

        def connect():
            import socket
            import obsws_python as obs
            old_timeout = socket.getdefaulttimeout()
            try:
                socket.setdefaulttimeout(4)
                cl   = obs.ReqClient(host=cfg["host"], port=cfg["port"],
                                     password=cfg.get("password", ""), timeout=4)
                resp = cl.get_scene_list()
                scenes = [s["sceneName"] for s in resp.scenes]
                cl.disconnect()
                self.after(0, lambda: self._obs_connected(scenes))
            except ConnectionRefusedError:
                self.after(0, lambda: self._obs_status.configure(
                    text=self.T("obs_unreachable"), text_color=RED))
            except Exception as e:
                msg = str(e) or type(e).__name__
                self.after(0, lambda: self._obs_status.configure(
                    text=self.T("obs_error", msg=msg), text_color=RED))
            finally:
                socket.setdefaulttimeout(old_timeout)
        threading.Thread(target=connect, daemon=True).start()

    def _obs_disconnect(self):
        cfg = self._obs_build_cfg()
        with open(OBS_BACKUP_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        none_display = self._obs_type_options.get("none", "")
        for i in range(4):
            self._obs_btn_type_internal[i] = "none"
            self._obs_btn_type[i].set(none_display)
            self._obs_btn_scene[i].set("")
            self._obs_scene_entries[i].configure(state="disabled", values=[])
        save_obs_config(self._obs_build_cfg())
        self._obs_status.configure(text=self.T("obs_disconnected"), text_color=FG2)

    def _obs_connected(self, scenes):
        try:
            with open(OBS_BACKUP_FILE) as f:
                backup = json.load(f)
            for i, btn in enumerate(backup.get("buttons", [])):
                internal_val = btn.get("type", "none")
                self._obs_btn_type_internal[i] = internal_val
                display = self._obs_type_options.get(
                    internal_val, self._obs_type_options.get("none", ""))
                self._obs_btn_type[i].set(display)
                self._obs_btn_scene[i].set(btn.get("scene", ""))
            save_obs_config(self._obs_build_cfg())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        self._obs_status.configure(
            text=self.T("obs_connected", n=len(scenes)), text_color=GRN)
        for i, combo in enumerate(self._obs_scene_entries):
            combo.configure(values=scenes)
            if self._obs_btn_type_internal[i] == "scene":
                combo.configure(state="normal")

    def _obs_build_cfg(self):
        return {
            "host":     self._obs_host.get().strip(),
            "port":     int(self._obs_port.get().strip() or "4455"),
            "password": self._obs_password.get(),
            "buttons": [
                {"type":  self._obs_btn_type_internal[i],
                 "scene": self._obs_btn_scene[i].get().strip()}
                for i in range(4)
            ]
        }

    def _on_obs_type_change(self, idx, display_val=None):
        if display_val is None:
            display_val = self._obs_btn_type[idx].get()
        internal_val = self._obs_type_display_to_internal.get(display_val, "none")
        self._obs_btn_type_internal[idx] = internal_val
        combo = self._obs_scene_entries[idx]
        combo.configure(state="normal" if internal_val == "scene" else "disabled")
        self._obs_auto_save(idx)

    def _obs_auto_save(self, idx):
        save_obs_config(self._obs_build_cfg())
        self._obs_info.configure(text=self.T("obs_saved", d=idx+1), text_color=GRN)

    def _on_btn_type_change(self, label, idx):
        labels = self._numpad_type_labels_fn()
        try:
            internal = self._numpad_type_internal[labels.index(label)]
        except (ValueError, IndexError):
            internal = "shell"
        self._btn_type[idx].set(internal)
        if hasattr(self, "_folder_btns") and idx < len(self._folder_btns):
            btn = self._folder_btns[idx]
            if internal in ("folder", "app"):
                btn.configure(state="normal", image=self._folder_img)
            else:
                btn.configure(state="disabled", image=self._folder_img_dim)

    def _browse_action(self, idx):
        btype = self._btn_type[idx].get()
        if btype == "folder":
            path = native_open_folder()
            if path:
                self._btn_action[idx].set(path)
        elif btype == "app":
            self._show_app_picker(idx)

    def _show_app_picker(self, idx):
        apps = parse_desktop_apps()
        if not apps:
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title(self.T("app_picker_title"))
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.geometry("360x480")
        dlg.update_idletasks()
        dlg.grab_set()

        search_var = tk.StringVar()
        search_entry = ctk.CTkEntry(
            dlg, textvariable=search_var, placeholder_text=self.T("app_picker_search"),
            fg_color=BG2, text_color=FG, border_color=BORDER,
            font=("Helvetica", 12), height=34,
        )
        search_entry.pack(fill="x", padx=12, pady=(12, 6))
        search_entry.focus()

        list_frame = ctk.CTkScrollableFrame(dlg, fg_color=BG2, corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        result = [None]
        _btn_refs = []

        def _select(name, exec_cmd):
            result[0] = exec_cmd
            self._btn_action[idx].set(exec_cmd)
            dlg.destroy()

        def _rebuild(filter_text=""):
            for b in _btn_refs:
                b.destroy()
            _btn_refs.clear()
            ft = filter_text.lower()
            for name, exec_cmd in apps:
                if ft and ft not in name.lower():
                    continue
                b = ctk.CTkButton(
                    list_frame, text=name, anchor="w",
                    fg_color="transparent", text_color=FG,
                    hover_color=BG3, font=("Helvetica", 11),
                    height=30, corner_radius=4,
                    command=lambda n=name, e=exec_cmd: _select(n, e),
                )
                b.pack(fill="x", pady=1)
                _btn_refs.append(b)

        _rebuild()
        search_var.trace_add("write", lambda *_: _rebuild(search_var.get()))

    def _apply_btn(self, idx):
        buttons = load_buttons()
        buttons[idx]["action"] = self._btn_action[idx].get().strip()
        buttons[idx]["type"]   = self._btn_type[idx].get()
        save_buttons(buttons)
        self._numpad_info.configure(text=self.T("action_saved", d=idx+1), text_color=GRN)

    def _reset_buttons_flash(self):
        was_running = self._stop_cpu_proc()

        self._numpad_info.configure(text=self.T("reset_buttons_running"), text_color=FG2)

        def _run():
            time.sleep(0.5)
            r = subprocess.run(_cmd("reset-buttons"), capture_output=True)
            if r.returncode == 0:
                self.after(0, lambda: self._numpad_info.configure(
                    text=self.T("reset_buttons_done"), text_color=GRN))
            else:
                self.after(0, lambda: self._numpad_info.configure(
                    text=self.T("reset_buttons_error"), text_color=RED))
            if was_running:
                self.after(0, self._start_cpu_auto)

        threading.Thread(target=_run, daemon=True).start()

    def _pick_gif_frame(self, path, n_frames):
        dlg = ctk.CTkToplevel(self)
        dlg.title(self.T("gif_frame_title", n=n_frames))
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        result    = [0]
        cancelled = [False]

        preview_label = ctk.CTkLabel(dlg, text="", width=144, height=144,
                                      fg_color=BG3)
        preview_label.pack(pady=(12, 2), padx=16)

        info_label = ctk.CTkLabel(dlg, text="", fg_color="transparent",
                                   text_color=FG2, font=("Helvetica", 11))
        info_label.pack()

        gif_img = Image.open(path)
        _photo  = [None]

        def _update_preview(frame_val):
            try:
                frame_idx = int(float(frame_val))
                gif_img.seek(frame_idx)
                frame = gif_img.copy().resize((144, 144), Image.LANCZOS).convert("RGB")
                ctk_img  = ctk.CTkImage(light_image=frame, dark_image=frame,
                                         size=(144, 144))
                _photo[0] = ctk_img
                preview_label.configure(image=ctk_img)
                info_label.configure(text=self.T("gif_frame_info",
                                                  frame=frame_idx + 1, total=n_frames))
            except Exception:
                pass

        slider = ctk.CTkSlider(dlg, from_=0, to=n_frames - 1,
                                number_of_steps=n_frames - 1,
                                command=_update_preview,
                                width=200, progress_color=BLUE, button_color=FG,
                                fg_color=BG3)
        slider.set(0)
        slider.pack(pady=(6, 2), padx=16)
        _update_preview(0)

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(pady=(6, 12))

        def _ok():
            result[0] = int(slider.get())
            dlg.destroy()

        def _cancel():
            cancelled[0] = True
            dlg.destroy()

        ctk.CTkButton(btn_row, text="OK", command=_ok,
                      fg_color=BLUE, text_color=FG, hover_color="#0884be",
                      font=("Helvetica", 11, "bold"), height=30, width=70,
                      corner_radius=6).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text=self.T("gif_frame_cancel"), command=_cancel,
                      fg_color=BG3, text_color=FG, hover_color=BG2,
                      font=("Helvetica", 11), height=30, width=70,
                      corner_radius=6).pack(side="left")

        dlg.wait_window()
        return None if cancelled[0] else result[0]

    def _upload_image(self, idx):
        path = native_open_image(title=f"Bild für D{idx+1} wählen")
        if not path:
            return

        gif_frame = 0
        if path.lower().endswith(".gif"):
            try:
                n = Image.open(path).n_frames
                if n > 1:
                    chosen = self._pick_gif_frame(path, n)
                    if chosen is None:
                        return
                    gif_frame = chosen
            except Exception:
                pass

        self._numpad_info.configure(text=self.T("image_uploading", d=idx+1),
                                    text_color=BLUE)

        bar = self._upload_bars[idx]
        bar.set(0)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        def do_upload():
            time.sleep(2.5 if was_running else 0.5)
            cmd = _cmd("upload", str(idx), path)
            if gif_frame:
                cmd = _cmd("upload", str(idx), path, "--frame", str(gif_frame))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            for line in proc.stdout:
                if line.startswith("PROGRESS:"):
                    try:
                        pct = int(line.strip()[9:])
                        self.after(0, lambda v=pct: bar.set(v / 100.0))
                    except ValueError:
                        pass
            proc.wait()
            ok = proc.returncode == 0
            if was_running:
                self.after(0, self._start_cpu_auto)
            err_hint = (proc.stderr.read().strip().splitlines() or [""])[-1]

            def finish():
                bar.set(0)
                self._numpad_info.configure(
                    text=(self.T("image_uploaded", d=idx+1) if ok
                          else f"D{idx+1}: Fehler — {err_hint}" if err_hint
                          else self.T("image_error", d=idx+1)),
                    text_color=GRN if ok else RED)
            self.after(0, finish)

        threading.Thread(target=do_upload, daemon=True).start()

    def _set_main_mode(self, mode):
        """Switch main display to any supported mode and persist the choice."""
        self._main_mode = mode
        with open(MAIN_MODE_FILE, "w") as f:
            f.write(mode)
        self._main_status.configure(text="", text_color=FG2)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        just_uploaded = self._main_just_uploaded
        self._main_just_uploaded = False
        needs_monitor = (mode != "image")

        def run():
            delay = 2.0 if just_uploaded else 0.8 if was_running else 0.5
            if just_uploaded:
                self.after(0, lambda: self._main_status.configure(
                    text=self.T("waiting_for_keyboard"), text_color=YLW))
            time.sleep(delay)
            pkill = "basecamp-controller.*cpu" if _FROZEN else r"mountain-time-sync\.py.*cpu"
            subprocess.run(["pkill", "-f", pkill], capture_output=True)
            time.sleep(0.3)
            r = subprocess.run(_cmd("main-mode", mode), capture_output=True)
            if r.returncode != 0:
                err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
                self.after(0, lambda: self._main_status.configure(
                    text=f"{mode}: {err or 'error'}", text_color=RED))
                return
            if needs_monitor:
                time.sleep(0.3)
                self.after(0, self._start_cpu_auto)
        threading.Thread(target=run, daemon=True).start()

    def _upload_main_image(self):
        path = native_open_image(title=self.T("main_display_upload"))
        if not path:
            return

        gif_frame = 0
        if path.lower().endswith(".gif"):
            try:
                n = Image.open(path).n_frames
                if n > 1:
                    chosen = self._pick_gif_frame(path, n)
                    if chosen is None:
                        return
                    gif_frame = chosen
            except Exception:
                pass

        self._main_status.configure(text=self.T("main_display_uploading"),
                                    text_color=BLUE)
        self._main_bar.set(0)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        need_mode_switch = (self._main_mode != "image")
        after_reset = self._after_dial_reset
        self._after_dial_reset = False

        def do_upload():
            time.sleep(2.5 if was_running else 0.5)
            if need_mode_switch and not after_reset:
                self._main_mode = "image"
                subprocess.run(_cmd("main-mode", "image"), capture_output=True)
                time.sleep(0.3)
            extras = ["--frame", str(gif_frame)] if gif_frame else []
            if after_reset:
                extras.append("--activate-custom")
            cmd = _cmd("upload-main", path, *extras)
            ok = False
            err_hint = ""
            for attempt in range(3):
                if attempt > 0:
                    self.after(0, lambda a=attempt: self._main_status.configure(
                        text=f"Retry {a}/2…", text_color=YLW))
                    time.sleep(2.0)
                    self.after(0, lambda: self._main_bar.set(0))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)
                for line in proc.stdout:
                    if line.startswith("PROGRESS:"):
                        try:
                            pct = int(line.strip()[9:])
                            self.after(0, lambda v=pct: self._main_bar.set(v / 100.0))
                        except ValueError:
                            pass
                    else:
                        print(line, end="", flush=True)
                proc.wait()
                ok = proc.returncode == 0
                err_hint = (proc.stderr.read().strip().splitlines() or [""])[-1]
                if ok:
                    break

            def finish():
                self._main_bar.set(0)
                self._main_status.configure(
                    text=(self.T("main_display_uploaded") if ok
                          else f"{self.T('main_display_error')} — {err_hint}" if err_hint
                          else self.T("main_display_error")),
                    text_color=GRN if ok else RED)
                if ok:
                    self._main_mode = "image"
                    self._main_mode_var.set(self._mode_labels[0])  # "Bild" / "Image"
                    self._main_just_uploaded = True
            self.after(0, finish)

        threading.Thread(target=do_upload, daemon=True).start()

    def _setup_tray(self):
        import signal as _signal
        _signal.signal(_signal.SIGUSR1, lambda *_: self.after(0, self._show_window))
        _signal.signal(_signal.SIGUSR2, lambda *_: self.after(0, self._quit))

        lang_file = os.path.join(LANG_DIR, f"{self._lang_code}.json")
        env = os.environ.copy()
        if os.environ.get("SUDO_USER"):
            user = os.environ["SUDO_USER"]
            uid  = _pwd.getpwnam(user).pw_uid
            env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
            env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
            if _FROZEN:
                cmd = ["sudo", "-u", user, "-E", TRAY_HELPER,
                       str(os.getpid()), lang_file]
            else:
                cmd = ["sudo", "-u", user, "-E", sys.executable, TRAY_HELPER,
                       str(os.getpid()), lang_file]
        else:
            if _FROZEN:
                cmd = [TRAY_HELPER, str(os.getpid()), lang_file]
            else:
                cmd = [sys.executable, TRAY_HELPER, str(os.getpid()), lang_file]
        self._tray_proc = subprocess.Popen(cmd, env=env)

    def _hide_window(self):
        self.withdraw()

    def _show_window(self):
        self.deiconify()
        self.lift()

    def _quit(self):
        self.destroy()

    def destroy(self):
        if self._cpu_proc and self._cpu_proc.poll() is None:
            self._cpu_proc.terminate()
        if hasattr(self, "_tray_proc") and self._tray_proc.poll() is None:
            self._tray_proc.terminate()
        super().destroy()


def show_splash():
    splash = tk.Tk()
    splash.overrideredirect(True)
    img = Image.open(os.path.join(_RES, "resources", "logo.png")).convert("RGBA")
    img = img.resize((768, 512), Image.LANCZOS)
    bg  = Image.new("RGBA", img.size, BG)
    bg.paste(img, mask=img.split()[3])
    photo = ImageTk.PhotoImage(bg.convert("RGB"))
    w, h  = img.size
    sw    = splash.winfo_screenwidth()
    sh    = splash.winfo_screenheight()
    splash.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    splash.configure(bg=BG)
    tk.Label(splash, image=photo, bd=0, bg=BG).pack()
    splash.after(3500, splash.destroy)
    splash.mainloop()


def _install_desktop_entry():
    """Install .desktop file and icon to ~/.local/share/ for app menu integration."""
    import shutil
    app_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    appimage_path = os.environ.get("APPIMAGE", os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))

    # Icon
    icon_src = os.path.join(app_dir, "_internal", "resources", "app_icon_256.png")
    if not os.path.exists(icon_src):
        icon_src = os.path.join(app_dir, "resources", "app_icon_256.png")
    icon_dst = os.path.join(_real_home, ".local", "share", "icons", "hicolor", "256x256", "apps", "basecamp-linux.png")
    os.makedirs(os.path.dirname(icon_dst), exist_ok=True)
    shutil.copy2(icon_src, icon_dst)

    # .desktop file
    desktop_dir = os.path.join(_real_home, ".local", "share", "applications")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_path = os.path.join(desktop_dir, "basecamp-linux.desktop")
    with open(desktop_path, "w") as f:
        f.write(f"""[Desktop Entry]
Name=BaseCamp Linux
Comment=Unofficial Linux companion app for the Mountain Everest Max keyboard
Exec={appimage_path}
Icon=basecamp-linux
Type=Application
Categories=Utility;
""")
    os.chmod(desktop_path, 0o755)
    print(f"Installed: {desktop_path}")
    print(f"Installed: {icon_dst}")
    print("Done. BaseCamp Linux should now appear in your app menu.")


if __name__ == "__main__":
    if "--install" in sys.argv:
        _install_desktop_entry()
        sys.exit(0)
    psutil.cpu_percent()
    if load_splash_enabled():
        show_splash()
    app = App()
    app.mainloop()
