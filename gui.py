#!/usr/bin/env python3
"""GUI for Mountain Everest Max display control."""
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image, ImageTk
import subprocess
import datetime
import threading
import time
import sys
import os
import json
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


def native_open_image(title="Bild wählen"):
    import shutil
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    filter_img = "*.png *.jpg *.jpeg *.bmp *.webp *.gif"
    sudo_user = os.environ.get("SUDO_USER")

    def _run_as_user(cmd):
        if sudo_user:
            uid = _pwd.getpwnam(sudo_user).pw_uid
            env = os.environ.copy()
            env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
            env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
            cmd = ["sudo", "-u", sudo_user, "-E"] + cmd
            return subprocess.run(cmd, capture_output=True, text=True, env=env)
        return subprocess.run(cmd, capture_output=True, text=True)

    if shutil.which("kdialog") and ("KDE" in desktop or "PLASMA" in desktop):
        try:
            r = _run_as_user(["kdialog", "--getopenfilename",
                               os.path.expanduser("~"),
                               f"{filter_img} | Bilder", "--title", title])
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    if shutil.which("zenity"):
        try:
            patterns = [f"--file-filter=Bilder | {filter_img}", "--file-filter=Alle | *"]
            r = _run_as_user(["zenity", "--file-selection", f"--title={title}"] + patterns)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass

    from tkinter import filedialog
    return filedialog.askopenfilename(
        title=title,
        filetypes=[("PNG","*.png"),("JPEG","*.jpg *.jpeg"),
                   ("BMP","*.bmp"),("WebP","*.webp"),("GIF","*.gif"),("Alle","*.*")])


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
        buttons = load_buttons()
        for i, b in enumerate(buttons):
            self._btn_action[i].set(b.get("action", ""))

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
            _saved_mode = "image"
        self._main_mode_clock = (_saved_mode == "clock")

        self._btn_main_image = ctk.CTkButton(
            mode_row, text="", command=self._set_main_mode_image,
            fg_color=BG3 if self._main_mode_clock else YLW,
            text_color=FG2 if self._main_mode_clock else "#0d0d14",
            hover_color="#d4a900", font=("Helvetica", 10, "bold"), height=32, width=90, corner_radius=6)
        self._btn_main_image.pack(side="left", padx=3)
        self._reg(self._btn_main_image, "main_mode_image")

        self._btn_main_clock = ctk.CTkButton(
            mode_row, text="", command=self._set_main_mode_clock,
            fg_color=YLW if self._main_mode_clock else BG3,
            text_color="#0d0d14" if self._main_mode_clock else FG2,
            hover_color=BG2, font=("Helvetica", 10, "bold"), height=32, width=90, corner_radius=6)
        self._btn_main_clock.pack(side="left", padx=3)
        self._reg(self._btn_main_clock, "main_mode_clock")

        self._reg(
            ctk.CTkButton(b2, text="", command=self._upload_main_image,
                          fg_color=BLUE, text_color=BG, hover_color="#0884be",
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

        self._img_btns    = []
        self._upload_bars = []

        for i in range(4):
            card = ctk.CTkFrame(b3, fg_color=BG3, corner_radius=4)
            card.pack(fill="x", padx=12, pady=2)

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=(6, 2))

            tk.Frame(row, bg=YLW, width=3).pack(side="left", fill="y")

            ctk.CTkLabel(row, text=f"D{i+1}", font=("Helvetica", 10, "bold"),
                         text_color=YLW, width=30).pack(side="left", padx=(6, 2))

            self._reg(
                ctk.CTkLabel(row, text="", font=("Helvetica", 11), text_color=FG2),
                "action_label"
            ).pack(side="left", padx=(2, 2))

            ctk.CTkEntry(row, textvariable=self._btn_action[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=("Helvetica", 11), width=165, height=30,
                         ).pack(side="left", padx=4)

            idx = i
            ctk.CTkButton(row, text="✓", width=36, height=30,
                          command=lambda ix=idx: self._apply_btn(ix),
                          fg_color=GRN, text_color=BG, hover_color="#18a348",
                          font=("Helvetica", 10, "bold"), corner_radius=4,
                          ).pack(side="left", padx=2)

            img_btn = self._reg(
                ctk.CTkButton(row, text="", width=72, height=30,
                              command=lambda ix=idx: self._upload_image(ix),
                              fg_color=BLUE, text_color=BG, hover_color="#0884be",
                              font=("Helvetica", 11, "bold"), corner_radius=4,
                              border_width=0),
                "image_btn"
            )
            img_btn.pack(side="left", padx=(2, 6))
            self._img_btns.append(img_btn)

            bar = ctk.CTkProgressBar(card, mode="determinate",
                                     progress_color=BLUE, fg_color=BG3,
                                     height=4, corner_radius=0)
            bar.set(0)
            bar.pack(fill="x", padx=4, pady=(0, 4))
            self._upload_bars.append(bar)

        self._numpad_info = ctk.CTkLabel(b3, text="", font=("Helvetica", 11),
                                          text_color=GRN)
        self._numpad_info.pack(pady=(4, 10))

        # ── Section 4: OBS Integration ──
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
                          fg_color=BLUE, text_color=BG, hover_color="#0884be",
                          font=("Helvetica", 11, "bold"), height=34, corner_radius=6),
            "obs_connect"
        ).pack(side="left")
        self._reg(
            ctk.CTkButton(obs_btn_row, text="", command=self._obs_disconnect,
                          fg_color=BG2, text_color=FG2, hover_color=BG3,
                          font=("Helvetica", 11), height=34, corner_radius=6),
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

            ctk.CTkButton(row, text="✓", width=36, height=30,
                          command=lambda ix=idx: self._obs_save_btn(ix),
                          fg_color=GRN, text_color=BG, hover_color="#18a348",
                          font=("Helvetica", 10, "bold"), corner_radius=4,
                          ).pack(side="left", padx=4)

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
                                        fg_color=RED, text_color=FG)
                self._cpu_status.configure(text=self.T("monitor_running"), text_color=GRN)
            except Exception as e:
                self._cpu_status.configure(text=f"Fehler: {e}", text_color=RED)

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

    def _obs_save_btn(self, idx):
        save_obs_config(self._obs_build_cfg())
        self._obs_info.configure(text=self.T("obs_saved", d=idx+1), text_color=GRN)

    def _apply_btn(self, idx):
        buttons = load_buttons()
        buttons[idx]["action"] = self._btn_action[idx].get().strip()
        save_buttons(buttons)
        self._numpad_info.configure(text=self.T("action_saved", d=idx+1), text_color=GRN)

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
                      fg_color=BLUE, text_color=BG, hover_color="#0884be",
                      font=("Helvetica", 11, "bold"), height=30, width=70,
                      corner_radius=6).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text=self.T("gif_frame_cancel"), command=_cancel,
                      fg_color=BG3, text_color=FG2, hover_color=BG2,
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

    def _set_main_mode_image(self):
        self._main_mode_clock = False
        with open(MAIN_MODE_FILE, "w") as f:
            f.write("image")
        self._btn_main_image.configure(fg_color=YLW, text_color="#0d0d14")
        self._btn_main_clock.configure(fg_color=BG3, text_color=FG2)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None
        self._main_status.configure(text="", text_color=FG2)

        def run():
            subprocess.run(_cmd("main-mode", "image"), capture_output=True)
        threading.Thread(target=run, daemon=True).start()

    def _set_main_mode_clock(self):
        self._main_mode_clock = True
        with open(MAIN_MODE_FILE, "w") as f:
            f.write("clock")
        self._btn_main_clock.configure(fg_color=YLW, text_color="#0d0d14")
        self._btn_main_image.configure(fg_color=BG3, text_color=FG2)
        self._main_status.configure(text="", text_color=FG2)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        def run():
            time.sleep(0.8 if was_running else 0.5)
            # Kill any orphaned controller processes from previous sessions
            pkill = "basecamp-controller.*cpu" if _FROZEN else r"mountain-time-sync\.py.*cpu"
            subprocess.run(["pkill", "-f", pkill], capture_output=True)
            time.sleep(0.5)
            subprocess.run(_cmd("main-mode", "clock"), capture_output=True)
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

        need_mode_switch = self._main_mode_clock

        def do_upload():
            time.sleep(2.5 if was_running else 0.5)
            if need_mode_switch:
                self.after(0, lambda: (
                    self._btn_main_image.configure(fg_color=YLW, text_color="#0d0d14"),
                    self._btn_main_clock.configure(fg_color=BG3, text_color=FG2),
                ))
                self._main_mode_clock = False
                subprocess.run(_cmd("main-mode", "image"), capture_output=True)
                time.sleep(0.3)
            cmd = _cmd("upload-main", path)
            if gif_frame:
                cmd = _cmd("upload-main", path, "--frame", str(gif_frame))
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
                    # Modus-Buttons auf "Bild" setzen — Controller bleibt gestoppt
                    # damit die Uhr das Bild nicht überschreibt
                    self._btn_main_image.configure(fg_color=YLW, text_color="#0d0d14")
                    self._btn_main_clock.configure(fg_color=BG3, text_color=FG2)
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
