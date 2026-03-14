#!/usr/bin/env python3
"""GUI for Mountain Everest Max display control."""
import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageTk
import subprocess
import datetime
import threading
import sys
import os
import json
import psutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    _BIN = os.path.dirname(sys.executable)
    _RES = sys._MEIPASS        # bundled resources (lang/, logo.png, etc.)
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
    """Build subprocess command, works both frozen and normal."""
    if _FROZEN:
        return [SCRIPT] + list(args)
    return [PYTHON, SCRIPT] + list(args)

import pwd as _pwd
_real_home = _pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir if os.environ.get("SUDO_USER") else os.path.expanduser("~")
CONFIG_DIR  = os.path.join(_real_home, ".config", "mountain-time-sync")
STYLE_FILE  = os.path.join(CONFIG_DIR, "style")
BUTTON_FILE = os.path.join(CONFIG_DIR, "buttons.json")
OBS_FILE        = os.path.join(CONFIG_DIR, "obs.json")
OBS_BACKUP_FILE = os.path.join(CONFIG_DIR, "obs_backup.json")

OBS_INTERNAL_ORDER = ["none", "scene", "record", "stream"]

# Colors (Mountain — Black / Blue / Yellow)
BG   = "#0d0d14"   # near-black background
BG2  = "#16161f"   # card surface
BG3  = "#1f1f2e"   # input / inner surface
FG   = "#e8e8ff"   # primary text
FG2  = "#5a5a88"   # muted text
BLUE = "#0ea5e9"   # Mountain electric blue
YLW  = "#f5c400"   # Mountain yellow
GRN  = "#22c55e"   # success green
RED  = "#ef4444"   # error red
BORDER = "#1e1e30" # subtle border


def load_lang(code):
    path = os.path.join(LANG_DIR, f"{code}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # fall back to de.json
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


def native_open_image(title="Bild wählen"):
    """Open native file picker. Returns path string or empty string."""
    import shutil
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    filter_img = "*.png *.jpg *.jpeg *.bmp *.webp *.gif"

    if shutil.which("kdialog") and ("KDE" in desktop or "PLASMA" in desktop):
        try:
            r = subprocess.run(
                ["kdialog", "--getopenfilename",
                 os.path.expanduser("~"),
                 f"{filter_img} | Bilder",
                 "--title", title],
                capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    if shutil.which("zenity"):
        try:
            patterns = [f"--file-filter=Bilder | {filter_img}",
                        "--file-filter=Alle | *"]
            r = subprocess.run(
                ["zenity", "--file-selection", f"--title={title}"] + patterns,
                capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    # fallback: tkinter
    from tkinter import filedialog
    return filedialog.askopenfilename(
        title=title,
        filetypes=[("PNG","*.png"),("JPEG","*.jpg *.jpeg"),
                   ("BMP","*.bmp"),("WebP","*.webp"),("GIF","*.gif"),("Alle","*.*")])


def save_style(style_arg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(STYLE_FILE, "w") as f:
        f.write(style_arg)


def load_style():
    try:
        with open(STYLE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "analog"


def load_buttons():
    default = [{"icon": 7, "action": ""} for _ in range(4)]
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
    os.makedirs(CONFIG_DIR, exist_ok=True)
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
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(OBS_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mountain Everest Max")
        self.resizable(False, False)
        self.configure(bg=BG)

        # Dark ttk style for Comboboxes
        _style = ttk.Style(self)
        _style.theme_use("default")
        _style.configure("TCombobox",
                         fieldbackground=BG3, background=BG2,
                         foreground=FG, selectforeground=FG,
                         selectbackground=BLUE,
                         arrowcolor=BLUE, bordercolor=BORDER,
                         lightcolor=BG2, darkcolor=BG2)
        _style.map("TCombobox",
                   fieldbackground=[("readonly", BG3)],
                   foreground=[("readonly", FG)])
        _style.configure("TProgressbar",
                         troughcolor=BG3, background=BLUE,
                         bordercolor=BG3, lightcolor=BLUE, darkcolor=BLUE,
                         thickness=4)
        try:
            _icon = ImageTk.PhotoImage(Image.open(
                os.path.join(os.path.dirname(__file__), "logo.png")).resize((64, 64), Image.LANCZOS))
            self.iconphoto(True, _icon)
        except Exception:
            pass

        # i18n state
        self._lang = {}
        self._i18n_widgets = []

        # 1. Load available langs
        self._avail_langs = available_langs()

        # 2. Determine saved lang code
        def _read_cfg(name, default):
            try:
                return open(os.path.join(CONFIG_DIR, name)).read().strip()
            except FileNotFoundError:
                return default

        code = _read_cfg("language", "de")
        if code not in self._avail_langs:
            code = "de"

        # 3. Load lang
        self._lang = load_lang(code)

        # 4. Set lang code
        self._lang_code = code

        # 5. Build OBS type map from lang
        self._rebuild_obs_type_map()

        saved = load_style()
        self._current_style = tk.StringVar(value=next(
            (k for k, v in STYLES.items() if v == saved), "Analog"))
        self._cpu_proc = None

        # 6. Create all StringVars
        self._btn_action = [tk.StringVar(value="") for _ in range(4)]

        buttons = load_buttons()
        for i, b in enumerate(buttons):
            self._btn_action[i].set(b.get("action", ""))

        obs_cfg = load_obs_config()
        self._obs_host     = tk.StringVar(value=obs_cfg["host"])
        self._obs_port     = tk.StringVar(value=str(obs_cfg["port"]))
        self._obs_password = tk.StringVar(value=obs_cfg["password"])

        # Internal keys list and display StringVars for OBS button types
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
        self._lang_var = tk.StringVar()

        # 7. Build UI
        self._build_ui()

        # 8. Populate lang combo with names, select current
        lang_names = list(self._avail_langs.values())
        self._lang_combo["values"] = lang_names
        current_name = self._avail_langs.get(self._lang_code, "")
        self._lang_var.set(current_name)

        # 9. Start tick, cpu bar, tray, etc.
        self._tick()
        self._update_cpu_bar()
        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._hide_window)
        self.bind("<Unmap>", lambda e: self._hide_window() if self.state() == "iconic" else None)
        self.after(500, self._start_cpu_auto)

    # ── i18n ─────────────────────────────────────────────────────────────────

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
        """Build OBS display<->internal maps from current lang."""
        self._obs_type_options = {
            internal: self._lang.get(f"obs_{internal}", internal)
            for internal in OBS_INTERNAL_ORDER
        }
        # display -> internal
        self._obs_type_display_to_internal = {
            v: k for k, v in self._obs_type_options.items()
        }

    def _load_lang_code(self, code):
        self._lang = load_lang(code)
        self._lang_code = code
        self._rebuild_obs_type_map()
        self._apply_lang()

    def _apply_lang(self):
        for widget, key, attr in self._i18n_widgets:
            try:
                widget.config(**{attr: self.T(key)})
            except Exception:
                pass

        # Update OBS type comboboxes: options and current display values
        obs_type_labels = [self._obs_type_options[k] for k in OBS_INTERNAL_ORDER]
        for i in range(4):
            if hasattr(self, "_obs_type_combos"):
                self._obs_type_combos[i]["values"] = obs_type_labels
                internal = self._obs_btn_type_internal[i]
                display = self._obs_type_options.get(internal, obs_type_labels[0])
                self._obs_btn_type[i].set(display)

        # Update lang label via _reg
        # (already registered, applied above)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _section(self, key):
        """Styled section header with left accent bar."""
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="x", padx=16, pady=(14, 4))
        tk.Frame(outer, bg=YLW, width=3).pack(side="left", fill="y")
        self._reg(
            tk.Label(outer, text="", font=("Helvetica", 10, "bold"),
                     bg=BG, fg=FG, padx=8),
            key
        ).pack(side="left")

    def _divider(self):
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=4)

    def _card(self, parent=None, pady=2, padx=12):
        f = tk.Frame(parent or self, bg=BG2, padx=padx, pady=pady)
        f.pack(fill="x", padx=16, pady=2)
        return f

    def _build_ui(self):
        # ── Header ──
        hdr = tk.Frame(self, bg=BG2)
        hdr.pack(fill="x")
        inner = tk.Frame(hdr, bg=BG2)
        inner.pack(pady=14)
        tk.Label(inner, text="MOUNTAIN", font=("Helvetica", 15, "bold"),
                 bg=BG2, fg=FG).pack(side="left")
        tk.Label(inner, text=" EVEREST MAX", font=("Helvetica", 15, "bold"),
                 bg=BG2, fg=BLUE).pack(side="left")
        tk.Label(self, text="Display Control", font=("Helvetica", 9),
                 bg=BG, fg=FG2).pack(pady=(4, 0))

        # ── Clock ──
        clock_card = tk.Frame(self, bg=BG2)
        clock_card.pack(fill="x", padx=16, pady=(12, 4))
        self._clock_label = tk.Label(clock_card, text="", font=("Courier", 30, "bold"),
                                     bg=BG2, fg=YLW)
        self._clock_label.pack(pady=(10, 0))
        self._date_label = tk.Label(clock_card, text="", font=("Helvetica", 10),
                                    bg=BG2, fg=FG2)
        self._date_label.pack(pady=(2, 6))

        # Format + Language row
        fmt_row = tk.Frame(clock_card, bg=BG2)
        fmt_row.pack(pady=(0, 10))

        for text, val in [("24H", "24H"), ("12H", "12H")]:
            tk.Radiobutton(fmt_row, text=text, variable=self._clock_format,
                           value=val, command=self._on_format_change,
                           bg=BG3, fg=FG, selectcolor=BLUE,
                           activebackground=BG3, activeforeground=FG,
                           font=("Helvetica", 9), relief="flat",
                           indicatoron=0, padx=10, pady=4,
                           cursor="hand2").pack(side="left", padx=3)

        tk.Label(fmt_row, text=" ", bg=BG2).pack(side="left")

        self._lang_label = self._reg(
            tk.Label(fmt_row, text="", bg=BG2, fg=FG2, font=("Helvetica", 9)),
            "language_label")
        self._lang_label.pack(side="left", padx=(6, 2))

        style_combo = ttk.Combobox(fmt_row, textvariable=self._lang_var, width=9,
                                   state="readonly", font=("Helvetica", 9))
        style_combo.pack(side="left")
        style_combo.bind("<<ComboboxSelected>>", self._on_lang_change)
        self._lang_combo = style_combo

        self._divider()

        # ── Display Style ──
        self._section("display_style_title")
        style_frame = tk.Frame(self, bg=BG)
        style_frame.pack(pady=(0, 4))
        for label in STYLES:
            tk.Radiobutton(
                style_frame, text=label, variable=self._current_style,
                value=label, command=self._on_style_change,
                bg=BG2, fg=FG, selectcolor=BLUE,
                activebackground=BG2, activeforeground=YLW,
                font=("Helvetica", 10), indicatoron=0,
                relief="flat", bd=0, padx=18, pady=7, cursor="hand2",
                highlightthickness=0,
            ).pack(side="left", padx=4)

        self._style_status = tk.Label(self, text="", font=("Helvetica", 9),
                                      bg=BG, fg=GRN)
        self._style_status.pack()

        self._divider()

        # ── Monitor Mode ──
        self._section("monitor_title")

        bar_card = tk.Frame(self, bg=BG2)
        bar_card.pack(fill="x", padx=16, pady=(0, 4))
        bar_outer = tk.Frame(bar_card, bg=BG3, height=8)
        bar_outer.pack(fill="x", padx=12, pady=(8, 0))
        bar_outer.pack_propagate(False)
        self._cpu_bar = tk.Frame(bar_outer, bg=BLUE, width=0, height=8)
        self._cpu_bar.place(x=0, y=0, relheight=1)
        self._cpu_label = tk.Label(bar_card, text="CPU: --%", font=("Helvetica", 9),
                                   bg=BG2, fg=BLUE)
        self._cpu_label.pack(pady=(4, 0))

        self._btn_cpu = tk.Button(bar_card, text=self.T("monitor_start"),
                                  command=self._toggle_cpu,
                                  bg=YLW, fg="#0d0d14",
                                  font=("Helvetica", 10, "bold"),
                                  relief="flat", padx=18, pady=7, cursor="hand2")
        self._btn_cpu.pack(pady=8)
        self._cpu_status = tk.Label(bar_card, text="", font=("Helvetica", 9),
                                    bg=BG2, fg=FG2)
        self._cpu_status.pack(pady=(0, 8))

        self._divider()

        # ── Numpad D1–D4 ──
        self._section("numpad_title")
        self._reg(
            tk.Label(self, text="", font=("Helvetica", 9), bg=BG, fg=FG2),
            "numpad_subtitle"
        ).pack(pady=(0, 6))

        numpad_frame = tk.Frame(self, bg=BG)
        numpad_frame.pack(padx=16, fill="x")

        self._img_btns = []
        self._action_labels = []
        self._upload_bars = []

        for i in range(4):
            card = tk.Frame(numpad_frame, bg=BG2)
            card.pack(fill="x", pady=2)

            row = tk.Frame(card, bg=BG2)
            row.pack(fill="x")

            # D-label with yellow accent
            tk.Frame(row, bg=YLW, width=3).pack(side="left", fill="y")
            tk.Label(row, text=f"D{i+1}", font=("Helvetica", 10, "bold"),
                     bg=BG2, fg=YLW, width=3).pack(side="left", padx=(6, 2), pady=8)

            action_lbl = self._reg(
                tk.Label(row, text="", font=("Helvetica", 9), bg=BG2, fg=FG2),
                "action_label"
            )
            action_lbl.pack(side="left", padx=(4, 2))

            tk.Entry(row, textvariable=self._btn_action[i],
                     bg=BG3, fg=FG, insertbackground=FG,
                     relief="flat", width=20,
                     font=("Helvetica", 9)).pack(side="left", padx=4)

            idx = i
            tk.Button(row, text="✓",
                      command=lambda ix=idx: self._apply_btn(ix),
                      bg=BG2, fg=GRN, font=("Helvetica", 10),
                      relief="flat", padx=6, cursor="hand2").pack(side="left", padx=2)

            img_btn = self._reg(
                tk.Button(row, text="",
                          command=lambda ix=idx: self._upload_image(ix),
                          bg=BG2, fg=BLUE, font=("Helvetica", 9),
                          relief="flat", padx=6, cursor="hand2"),
                "image_btn"
            )
            img_btn.pack(side="left", padx=(2, 8))
            self._img_btns.append(img_btn)

            # Thin progress bar — hidden until upload starts
            bar = ttk.Progressbar(card, mode="indeterminate", length=300)
            self._upload_bars.append(bar)

        self._numpad_info = tk.Label(self, text="", font=("Helvetica", 9),
                                     bg=BG, fg=GRN)
        self._numpad_info.pack(pady=(6, 2))

        self._divider()

        # ── OBS Integration ──
        self._section("obs_title")

        obs_conn_card = tk.Frame(self, bg=BG2)
        obs_conn_card.pack(fill="x", padx=16, pady=(0, 4))

        obs_top = tk.Frame(obs_conn_card, bg=BG2)
        obs_top.pack(pady=(8, 2))
        tk.Label(obs_top, text="Host:", bg=BG2, fg=FG2, font=("Helvetica", 9)).pack(side="left")
        tk.Entry(obs_top, textvariable=self._obs_host, bg=BG3, fg=FG, insertbackground=FG,
                 relief="flat", width=14, font=("Helvetica", 9)).pack(side="left", padx=(2, 10))
        tk.Label(obs_top, text="Port:", bg=BG2, fg=FG2, font=("Helvetica", 9)).pack(side="left")
        tk.Entry(obs_top, textvariable=self._obs_port, bg=BG3, fg=FG, insertbackground=FG,
                 relief="flat", width=5, font=("Helvetica", 9)).pack(side="left", padx=2)

        obs_pw = tk.Frame(obs_conn_card, bg=BG2)
        obs_pw.pack(pady=2)
        self._reg(
            tk.Label(obs_pw, text="", bg=BG2, fg=FG2, font=("Helvetica", 9)),
            "obs_password"
        ).pack(side="left")
        tk.Entry(obs_pw, textvariable=self._obs_password, bg=BG3, fg=FG, insertbackground=FG,
                 relief="flat", width=20, font=("Helvetica", 9), show="*").pack(side="left", padx=4)

        obs_btn_row = tk.Frame(obs_conn_card, bg=BG2)
        obs_btn_row.pack(pady=(6, 8))
        self._reg(
            tk.Button(obs_btn_row, text="",
                      command=self._obs_connect,
                      bg=BLUE, fg=BG, font=("Helvetica", 9, "bold"),
                      relief="flat", padx=12, pady=5, cursor="hand2"),
            "obs_connect"
        ).pack(side="left")
        self._reg(
            tk.Button(obs_btn_row, text="",
                      command=self._obs_disconnect,
                      bg=BG3, fg=FG2, font=("Helvetica", 9),
                      relief="flat", padx=12, pady=5, cursor="hand2"),
            "obs_disconnect"
        ).pack(side="left", padx=(6, 0))
        self._obs_status = tk.Label(obs_conn_card, text="", font=("Helvetica", 9),
                                    bg=BG2, fg=FG2, wraplength=300)
        self._obs_status.pack(pady=(0, 8))

        obs_btns_frame = tk.Frame(self, bg=BG)
        obs_btns_frame.pack(padx=16, pady=(0, 4), fill="x")
        self._obs_scene_entries = []
        self._obs_type_combos = []

        obs_type_labels = [self._obs_type_options[k] for k in OBS_INTERNAL_ORDER]

        for i in range(4):
            row = tk.Frame(obs_btns_frame, bg=BG2)
            row.pack(fill="x", pady=2)

            lbl_frame = tk.Frame(row, bg=BLUE, width=3)
            lbl_frame.pack(side="left", fill="y")
            tk.Label(row, text=f"D{i+1}", font=("Helvetica", 10, "bold"),
                     bg=BG2, fg=BLUE, width=3).pack(side="left", padx=(6, 4), pady=6)
            idx = i

            type_combo = ttk.Combobox(row, textvariable=self._obs_btn_type[i],
                                       values=obs_type_labels, width=10,
                                       state="readonly", font=("Helvetica", 9))
            type_combo.pack(side="left", padx=4)
            type_combo.bind("<<ComboboxSelected>>",
                            lambda e, ix=idx: self._on_obs_type_change(ix))
            self._obs_type_combos.append(type_combo)

            internal_val = self._obs_btn_type_internal[i]
            scene_state = "readonly" if internal_val == "scene" else "disabled"
            scene_combo = ttk.Combobox(row, textvariable=self._obs_btn_scene[i],
                                       width=18, font=("Helvetica", 9),
                                       state=scene_state)
            scene_combo.pack(side="left", padx=4)
            self._obs_scene_entries.append(scene_combo)

            tk.Button(row, text="✓",
                      command=lambda ix=idx: self._obs_save_btn(ix),
                      bg=BG2, fg=GRN, font=("Helvetica", 10),
                      relief="flat", padx=8, cursor="hand2").pack(side="left", padx=4)

        self._obs_info = tk.Label(self, text="", font=("Helvetica", 9), bg=BG, fg=GRN)
        self._obs_info.pack(pady=(4, 8))

        # Apply initial lang to all registered widgets
        self._apply_lang()

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _on_format_change(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "clock_format"), "w") as f:
            f.write(self._clock_format.get())

    def _on_lang_change(self, event=None):
        selected_name = self._lang_var.get()
        # find code by matching name
        code = None
        for c, name in self._avail_langs.items():
            if name == selected_name:
                code = c
                break
        if code is None:
            return
        # save to config
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "language"), "w") as f:
            f.write(code)
        self._load_lang_code(code)

    def _tick(self):
        now = datetime.datetime.now()

        if self._clock_format.get() == "12H":
            time_str = now.strftime("%I:%M:%S %p")
        else:
            time_str = now.strftime("%H:%M:%S")
        self._clock_label.config(text=time_str)

        days = self._lang.get("days",
            ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])
        months = self._lang.get("months",
            ["January","February","March","April","May","June",
             "July","August","September","October","November","December"])
        date_str = f"{days[now.weekday()]}, {now.day:02d}. {months[now.month-1]} {now.year}"
        self._date_label.config(text=date_str)

        self.after(1000, self._tick)

    def _update_cpu_bar(self):
        cpu = psutil.cpu_percent(interval=None)
        self._cpu_label.config(text=f"CPU: {cpu:.0f}%")
        self._cpu_bar.place(x=0, y=0, width=int(220 * cpu / 100), relheight=1)
        self.after(1000, self._update_cpu_bar)

    def _on_style_change(self):
        label = self._current_style.get()
        save_style(STYLES[label])
        self._style_status.config(text=self.T("style_sending", style=label), fg=BLUE)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None
        def cb(ok):
            self._style_status.config(
                text=self.T("style_active", style=label) if ok else self.T("style_error"),
                fg=GRN if ok else RED)
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

    def _start_cpu_auto(self):
        """CPU-Modus beim Start automatisch aktivieren."""
        if not (self._cpu_proc and self._cpu_proc.poll() is None):
            self._toggle_cpu()

    def _toggle_cpu(self):
        if self._cpu_proc and self._cpu_proc.poll() is None:
            self._cpu_proc.terminate()
            self._cpu_proc = None
            self._btn_cpu.config(text=self.T("monitor_start"), bg=YLW, fg="#0d0d14")
            self._cpu_status.config(text=self.T("monitor_stopped"), fg=RED)
        else:
            style_arg = STYLES[self._current_style.get()]
            self._cpu_proc = subprocess.Popen(
                _cmd("cpu", style_arg),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._btn_cpu.config(text=self.T("monitor_stop"), bg=RED, fg=FG)
            self._cpu_status.config(text=self.T("monitor_running"), fg=GRN)

    def _obs_connect(self):
        self._obs_status.config(text=self.T("obs_connecting"), fg=BLUE)
        cfg = self._obs_build_cfg()
        save_obs_config(cfg)
        def connect():
            import socket, obsws_python as obs
            old_timeout = socket.getdefaulttimeout()
            try:
                socket.setdefaulttimeout(4)
                cl = obs.ReqClient(host=cfg["host"], port=cfg["port"],
                                   password=cfg.get("password", ""), timeout=4)
                resp = cl.get_scene_list()
                scenes = [s["sceneName"] for s in resp.scenes]
                cl.disconnect()
                self.after(0, lambda: self._obs_connected(scenes))
            except ConnectionRefusedError:
                self.after(0, lambda: self._obs_status.config(
                    text=self.T("obs_unreachable"), fg=RED))
            except Exception as e:
                msg = str(e) or type(e).__name__
                self.after(0, lambda: self._obs_status.config(
                    text=self.T("obs_error", msg=msg), fg=RED))
            finally:
                socket.setdefaulttimeout(old_timeout)
        threading.Thread(target=connect, daemon=True).start()

    def _obs_disconnect(self):
        # Backup current settings, then set all buttons to none
        cfg = self._obs_build_cfg()
        with open(OBS_BACKUP_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        none_display = self._obs_type_options.get("none", "")
        for i in range(4):
            self._obs_btn_type_internal[i] = "none"
            self._obs_btn_type[i].set(none_display)
            self._obs_btn_scene[i].set("")
            self._obs_scene_entries[i].config(state="disabled", values=[])
        save_obs_config(self._obs_build_cfg())
        self._obs_status.config(text=self.T("obs_disconnected"), fg=FG2)

    def _obs_connected(self, scenes):
        # Restore backup if available
        try:
            with open(OBS_BACKUP_FILE) as f:
                backup = json.load(f)
            for i, btn in enumerate(backup.get("buttons", [])):
                internal_val = btn.get("type", "none")
                self._obs_btn_type_internal[i] = internal_val
                display = self._obs_type_options.get(internal_val,
                    self._obs_type_options.get("none", ""))
                self._obs_btn_type[i].set(display)
                self._obs_btn_scene[i].set(btn.get("scene", ""))
            save_obs_config(self._obs_build_cfg())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        self._obs_status.config(text=self.T("obs_connected", n=len(scenes)), fg=GRN)
        for i, combo in enumerate(self._obs_scene_entries):
            combo.config(values=scenes)
            if self._obs_btn_type_internal[i] == "scene":
                combo.config(state="readonly")

    def _obs_build_cfg(self):
        return {
            "host": self._obs_host.get().strip(),
            "port": int(self._obs_port.get().strip() or "4455"),
            "password": self._obs_password.get(),
            "buttons": [
                {"type": self._obs_btn_type_internal[i],
                 "scene": self._obs_btn_scene[i].get().strip()}
                for i in range(4)
            ]
        }

    def _on_obs_type_change(self, idx):
        display_val = self._obs_btn_type[idx].get()
        internal_val = self._obs_type_display_to_internal.get(display_val, "none")
        self._obs_btn_type_internal[idx] = internal_val
        combo = self._obs_scene_entries[idx]
        if internal_val == "scene":
            combo.config(state="readonly" if combo["values"] else "normal")
        else:
            combo.config(state="disabled")

    def _obs_save_btn(self, idx):
        save_obs_config(self._obs_build_cfg())
        self._obs_info.config(text=self.T("obs_saved", d=idx+1), fg=GRN)

    def _apply_btn(self, idx):
        """Save action for a button."""
        buttons = load_buttons()
        buttons[idx]["action"] = self._btn_action[idx].get().strip()
        save_buttons(buttons)
        self._numpad_info.config(text=self.T("action_saved", d=idx+1), fg=GRN)

    def _upload_image(self, idx):
        """Select image file and upload to D1-D4."""
        path = native_open_image(title=f"Bild für D{idx+1} wählen")
        if not path:
            return
        self._numpad_info.config(text=self.T("image_uploading", d=idx+1), fg=BLUE)

        bar = self._upload_bars[idx]
        bar.pack(fill="x", padx=3, pady=(0, 3))
        bar.start(12)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        def do_upload():
            result = subprocess.run(
                _cmd("upload", str(idx), path),
                capture_output=True)
            ok = result.returncode == 0
            if was_running:
                self.after(0, self._start_cpu_auto)
            def finish():
                bar.stop()
                bar.pack_forget()
                self._numpad_info.config(
                    text=self.T("image_uploaded", d=idx+1) if ok else self.T("image_error", d=idx+1),
                    fg=GRN if ok else RED)
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
    img = Image.open(os.path.join(os.path.dirname(__file__), "logo.png")).convert("RGBA")
    img = img.resize((768, 512), Image.LANCZOS)
    bg = Image.new("RGBA", img.size, BG)
    bg.paste(img, mask=img.split()[3])
    photo = ImageTk.PhotoImage(bg.convert("RGB"))
    w, h = img.size
    sw = splash.winfo_screenwidth()
    sh = splash.winfo_screenheight()
    splash.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    splash.configure(bg=BG)
    tk.Label(splash, image=photo, bd=0, bg=BG).pack()
    splash.after(3500, splash.destroy)
    splash.mainloop()

if __name__ == "__main__":
    psutil.cpu_percent()
    show_splash()
    app = App()
    app.mainloop()
