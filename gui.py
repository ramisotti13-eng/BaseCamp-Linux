#!/usr/bin/env python3
"""BaseCamp Linux — multi-device hub GUI."""
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image, ImageTk
import subprocess
import datetime
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

_HERE   = os.path.dirname(os.path.abspath(__file__))
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

# ── Shared modules ─────────────────────────────────────────────────────────────

from shared.config import (
    _real_home, CONFIG_DIR,
    STYLE_FILE, BUTTON_FILE, OBS_FILE, OBS_BACKUP_FILE, MAIN_MODE_FILE,
    AUTOSTART_FILE, SPLASH_FILE, ZONE_FILE, RGB_FILE, PRESET_FILE,
    ICON_LAST_FILE, ICON_LIBRARY_DIR, MAIN_LIBRARY_DIR,
    RGB_PRESETS_FILE,
    load_config, save_config,
    load_style, save_style,
    load_buttons, save_buttons,
    load_obs_config, save_obs_config,
    load_autostart_enabled, save_autostart_enabled,
    load_splash_enabled, save_splash_enabled,
    load_zone_config, save_zone_config, load_zone_colors, save_zone_colors,
    load_rgb_settings, save_rgb_settings,
    load_rgb_config, save_rgb_config,
    _load_per_key, _save_per_key,
    _load_presets, _save_presets,
    _load_icon_last, _save_icon_last,
    _save_to_library, _save_to_main_library,
    _compute_lib_hash, _compute_main_lib_hash,
    _list_library, _list_main_library,
    OBS_INTERNAL_ORDER,
)
from shared.image_utils import image_to_rgb565
from shared.ui_helpers import (
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER,
    FONT, FONT_BOLD, FONT_SM, FONT_LG,
    ANIM_STEPS, ANIM_MS,
    _rgb_hex, _run_as_sudouser,
    native_open_image, native_open_folder, parse_desktop_apps,
    ColorPickerDialog, pick_color,
    LibraryPickerDialog, pick_library_image, pick_main_library_image,
    MultiUploadDialog,
    CustomRGBWindow,
    AccordionSection,
    _KB_LAYOUT, _KB_CANVAS_W, _KB_CANVAS_H, _SIDE_SZ, _SIDE_OFFSET,
    _QUICK_COLORS, _SIDE_ZONE_INDICES,
    _KB60_LAYOUT, _KB60_CANVAS_W, _KB60_CANVAS_H, _KB60_NUM_LEDS,
)
from devices.everest_max.panel import EverestMaxPanel
from devices.everest60.panel import Everest60Panel
from devices.makalu67.panel import Makalu67Panel
from devices.displaypad.panel import DisplayPadPanel
from devices.obs.panel import OBSPanel
from devices.macros.panel import MacroPanel

# ── Keep backward-compatible module-level names used by existing code ──────────

# These were previously defined at module level in gui.py; keep them so that
# any code that imports gui directly still works.
_AUTOSTART_FILE = AUTOSTART_FILE


def _cmd(*args):
    """Build subprocess command for Everest Max controller."""
    if _FROZEN:
        return [SCRIPT] + list(args)
    return [PYTHON, SCRIPT] + list(args)


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


# USB presence detection helpers (non-blocking, best-effort)

def _check_usb_presence(vid, pid):
    """Return True if a USB device with given VID:PID is present.
    Reads /sys/bus/usb/devices/ directly — no subprocess, no forking.
    """
    try:
        target_vid = f"{vid:04x}"
        target_pid = f"{pid:04x}"
        for entry in os.listdir("/sys/bus/usb/devices/"):
            base = f"/sys/bus/usb/devices/{entry}"
            try:
                with open(f"{base}/idVendor") as f:
                    if f.read().strip() != target_vid:
                        continue
                with open(f"{base}/idProduct") as f:
                    if f.read().strip() == target_pid:
                        return True
            except OSError:
                continue
        return False
    except OSError:
        return False


# ── App ────────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    # VID/PID constants for supported devices
    EVEREST_MAX_VID     = 0x3282
    EVEREST_MAX_PID     = 0x0001
    EVEREST60_VID       = 0x3282
    EVEREST60_PID_ANSI  = 0x0005
    EVEREST60_PID_ISO   = 0x0006
    MAKALU67_VID        = 0x3282
    MAKALU67_PID        = 0x0003
    DISPLAYPAD_VID      = 0x3282
    DISPLAYPAD_PID      = 0x0009

    def __init__(self):
        super().__init__()
        self.title("BaseCamp Linux")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.geometry("480x760")

        try:
            _icon = ImageTk.PhotoImage(Image.open(
                os.path.join(_RES, "resources", "app_icon_64.png")))
            self.iconphoto(True, _icon)
        except Exception:
            pass

        # i18n
        self._lang          = {}
        self._i18n_widgets  = []
        self._avail_langs   = available_langs()

        def _read_cfg(name, default):
            try:
                with open(os.path.join(CONFIG_DIR, name)) as f:
                    return f.read().strip()
            except FileNotFoundError:
                return default

        code = _read_cfg("language", "de")
        if code not in self._avail_langs:
            code = "de"
        self._lang      = load_lang(code)
        self._lang_code = code
        self._rebuild_obs_type_map()

        self._lang_var = tk.StringVar()

        self._active_device = None   # "everest_max" | "everest60" | "makalu67" | "displaypad"
        self._panels        = {}     # populated in _build_ui
        self._kb_panel_id   = "everest_max"   # which keyboard panel is active
        self._dev_present   = {"everest_max": False, "everest60": False,
                               "makalu67": False, "displaypad": False, "obs": False}

        self._build_ui()

        # Populate language combo (now that EverestMaxPanel has created it)
        lang_names   = list(self._avail_langs.values())
        current_name = self._avail_langs.get(self._lang_code, "")
        self._lang_var.set(current_name)
        if hasattr(self, "_everest_panel"):
            self._everest_panel._lang_combo.configure(values=lang_names)

        self._restore_debounce_id = None
        self._was_withdrawn = False
        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._hide_window)
        self.bind("<Unmap>", lambda e: self._hide_window() if self.state() == "iconic" else None)
        # Recover from display sleep — force refresh only after withdraw/deiconify
        self.bind("<Map>", self._on_window_restore)
        self.after(500, self._start_cpu_auto_clean)
        # Run first device check immediately so the correct panel is shown
        self._check_devices()

    # ── subprocess command builder ────────────────────────────────────────────

    def _cmd(self, *args):
        """Build subprocess command for Everest Max controller (default device)."""
        return _cmd(*args)

    def _cmd_for_device(self, device_id, *args):
        """Build subprocess command for a specific device controller."""
        if device_id == "makalu67":
            script = os.path.join(_HERE, "devices", "makalu67", "controller.py")
            if _FROZEN:
                return [os.path.join(_BIN, "makalu-controller")] + list(args)
            return [PYTHON, script] + list(args)
        if device_id == "everest60":
            script = os.path.join(_HERE, "devices", "everest60", "controller.py")
            if _FROZEN:
                return [os.path.join(_BIN, "everest60-controller")] + list(args)
            return [PYTHON, script] + list(args)
        return _cmd(*args)

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
        # Delegate to active panel for panel-specific i18n (OBS combos, type menus)
        for panel in self._panels.values():
            if hasattr(panel, "apply_lang"):
                panel.apply_lang()

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
                frame    = gif_img.copy().resize((144, 144), Image.LANCZOS).convert("RGB")
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
        ctk.CTkLabel(inner, text=" BASECAMP", font=("Helvetica", 15, "bold"),
                     text_color=BLUE).pack(side="left")
        ctk.CTkButton(hdr, text="✕", width=32, height=32, corner_radius=6,
                      fg_color="transparent", hover_color=BG3, text_color=FG2,
                      font=("Helvetica", 14), command=self._quit).place(relx=1.0,
                      rely=0.5, anchor="e", x=-8)

        # ── Device switcher bar (2 rows) ──
        switcher = ctk.CTkFrame(self, fg_color=BG3, corner_radius=0)
        switcher.pack(fill="x")

        row1 = ctk.CTkFrame(switcher, fg_color="transparent")
        row1.pack(pady=(4, 0))

        self._sw_keyboard_btn = ctk.CTkButton(
            row1, text="Keyboard", font=("Helvetica", 11, "bold"),
            fg_color=BLUE, hover_color="#0884be", text_color=FG,
            height=28, corner_radius=4,
            command=lambda: self._switch_device(self._kb_panel_id))
        self._sw_keyboard_btn.pack(side="left", padx=4)

        self._sw_mouse_btn = ctk.CTkButton(
            row1, text="Mouse", font=("Helvetica", 11, "bold"),
            fg_color=BG2, hover_color="#222232", text_color=FG2,
            height=28, corner_radius=4,
            command=lambda: self._switch_device("makalu67"))
        self._sw_mouse_btn.pack(side="left", padx=4)

        self._sw_displaypad_btn = ctk.CTkButton(
            row1, text="DisplayPad", font=("Helvetica", 11, "bold"),
            fg_color=BG2, hover_color="#222232", text_color=FG2,
            height=28, corner_radius=4,
            command=lambda: self._switch_device("displaypad"))
        self._sw_displaypad_btn.pack(side="left", padx=4)

        row2 = ctk.CTkFrame(switcher, fg_color="transparent")
        row2.pack(pady=(2, 4))

        self._sw_obs_btn = ctk.CTkButton(
            row2, text="OBS Studio", font=("Helvetica", 11, "bold"),
            fg_color=BG2, hover_color="#222232", text_color=FG2,
            height=28, corner_radius=4, width=110,
            command=lambda: self._switch_device("obs"))
        self._sw_obs_btn.pack(side="left", padx=4)

        self._sw_macros_btn = ctk.CTkButton(
            row2, text="Macros", font=("Helvetica", 11, "bold"),
            fg_color=BG2, hover_color="#222232", text_color=FG2,
            height=28, corner_radius=4, width=110,
            command=lambda: self._switch_device("macros"))
        self._sw_macros_btn.pack(side="left", padx=4)

        # ── Panel area ──
        self._panel_area = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._panel_area.pack(fill="both", expand=True)

        # Instantiate panels (OBS first — other panels reference it)
        self._obs_panel         = OBSPanel(self._panel_area, self)
        self._macro_panel       = MacroPanel(self._panel_area, self)
        self._everest_panel     = EverestMaxPanel(self._panel_area, self)
        self._everest60_panel   = Everest60Panel(self._panel_area, self)
        self._makalu_panel      = Makalu67Panel(self._panel_area, self)
        self._displaypad_panel  = DisplayPadPanel(self._panel_area, self)

        self._panels = {
            "everest_max": self._everest_panel,
            "everest60":   self._everest60_panel,
            "makalu67":    self._makalu_panel,
            "displaypad":  self._displaypad_panel,
            "obs":         self._obs_panel,
            "macros":      self._macro_panel,
        }

        # Show keyboard panel by default
        self._switch_device("everest_max")

    # ── Device switching ──────────────────────────────────────────────────────

    def _switch_device(self, device_id):
        if self._active_device == device_id:
            return
        # Hide all panels
        for panel in self._panels.values():
            panel.pack_forget()
        # Show selected panel
        self._panels[device_id].pack(fill="both", expand=True)
        self._active_device = device_id

        # Update switcher button styles
        self._refresh_switcher_colors()

    # ── Controller delegation ─────────────────────────────────────────────────

    def _stop_cpu_proc(self):
        """Stop CPU monitor on active panel. Returns True if was running."""
        panel = self._panels.get(self._active_device)
        if panel and hasattr(panel, "_stop_cpu_proc"):
            return panel._stop_cpu_proc()
        return False

    def _start_cpu_auto(self):
        """Start CPU monitor on active panel."""
        panel = self._panels.get(self._active_device)
        if panel and hasattr(panel, "_start_cpu_auto"):
            panel._start_cpu_auto()

    def _start_cpu_auto_clean(self):
        """Delegate to Everest panel (only keyboard has CPU monitor)."""
        if hasattr(self, "_everest_panel"):
            self._everest_panel._start_cpu_auto_clean()

    # ── USB presence check ────────────────────────────────────────────────────

    def _check_devices(self):
        """Periodic USB presence check (runs in main thread — /sys reads are <1ms)."""
        kb_max_present = _check_usb_presence(self.EVEREST_MAX_VID, self.EVEREST_MAX_PID)
        kb_60_present  = (_check_usb_presence(self.EVEREST60_VID, self.EVEREST60_PID_ANSI)
                          or _check_usb_presence(self.EVEREST60_VID, self.EVEREST60_PID_ISO))
        mouse_present  = (_check_usb_presence(self.MAKALU67_VID, self.MAKALU67_PID)
                          or _check_usb_presence(self.MAKALU67_VID, 0x0002))
        dp_present     = _check_usb_presence(self.DISPLAYPAD_VID, self.DISPLAYPAD_PID)
        self._update_device_status(kb_max_present, kb_60_present, mouse_present, dp_present)
        self.after(5000, self._check_devices)

    def _update_device_status(self, kb_max_present, kb_60_present=False,
                               mouse_present=False, dp_present=False):
        """Update switcher button appearance based on device presence."""
        obs_connected = hasattr(self, "_obs_panel") and self._obs_panel.is_connected()
        self._dev_present["everest_max"] = kb_max_present
        self._dev_present["everest60"]   = kb_60_present
        self._dev_present["makalu67"]    = mouse_present
        self._dev_present["displaypad"]  = dp_present
        self._dev_present["obs"]         = obs_connected
        # Determine active keyboard panel (Everest 60 takes priority if connected)
        old_kb_id = self._kb_panel_id
        if kb_60_present:
            self._kb_panel_id = "everest60"
        elif kb_max_present:
            self._kb_panel_id = "everest_max"
        # Auto-switch if viewing a keyboard panel that changed
        if (self._active_device in ("everest_max", "everest60")
                and self._kb_panel_id != old_kb_id):
            self._active_device = None  # force re-switch
            self._switch_device(self._kb_panel_id)
        # Update button labels
        mouse_label = getattr(self._makalu_panel, "_model_name", "Mouse") if hasattr(self, "_makalu_panel") else "Mouse"
        if kb_60_present and hasattr(self, "_everest60_panel"):
            kb_label = getattr(self._everest60_panel, "_model_name", "Everest 60")
        elif kb_max_present:
            kb_label = "Everest Max"
        else:
            kb_label = "Keyboard"
        self._sw_keyboard_btn.configure(text=kb_label)
        self._sw_mouse_btn.configure(text=mouse_label)
        self._sw_displaypad_btn.configure(text="DisplayPad")
        self._refresh_switcher_colors()
        # Notify panels
        if hasattr(self, "_makalu_panel"):
            self._makalu_panel.set_connected(mouse_present)
        if hasattr(self, "_everest60_panel"):
            self._everest60_panel.set_connected(kb_60_present)

    def _refresh_switcher_colors(self):
        """Apply fg_color/text_color to each switcher button: blue=active, green=present, gray=absent."""
        # Keyboard button covers both Everest Max and Everest 60
        kb_active  = self._active_device in ("everest_max", "everest60")
        kb_present = (self._dev_present.get("everest_max", False)
                      or self._dev_present.get("everest60", False))
        if kb_active:
            self._sw_keyboard_btn.configure(fg_color=BLUE, text_color=FG)
        elif kb_present:
            self._sw_keyboard_btn.configure(fg_color=GRN, text_color=FG)
        else:
            self._sw_keyboard_btn.configure(fg_color=BG2, text_color=FG2)

        for dev_id, btn in [
            ("makalu67",   self._sw_mouse_btn),
            ("displaypad", self._sw_displaypad_btn),
            ("obs",        self._sw_obs_btn),
            ("macros",     self._sw_macros_btn),
        ]:
            active  = self._active_device == dev_id
            present = self._dev_present.get(dev_id, False)
            if active:
                btn.configure(fg_color=BLUE, text_color=FG)
            elif present:
                btn.configure(fg_color=GRN, text_color=FG)
            else:
                btn.configure(fg_color=BG2, text_color=FG2)

    # ── Tray / lifecycle ──────────────────────────────────────────────────────

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

    def _on_window_restore(self, event=None):
        """Force UI refresh after withdraw/deiconify (tray restore or display sleep)."""
        if not self._was_withdrawn:
            return
        self._was_withdrawn = False
        if self._restore_debounce_id is not None:
            self.after_cancel(self._restore_debounce_id)
        self._restore_debounce_id = self.after(200, self._do_window_restore)

    def _do_window_restore(self):
        """Actual restore logic, called once after debounce settles."""
        self._restore_debounce_id = None
        try:
            geo = self.geometry()
            self.geometry(geo)
            self.update_idletasks()
            self._refresh_switcher_colors()
            if self._active_device and self._active_device in self._panels:
                panel = self._panels[self._active_device]
                panel.pack_forget()
                panel.pack(fill="both", expand=True)
            self.lift()
        except Exception:
            pass

    def _hide_window(self):
        self._was_withdrawn = True
        self.withdraw()

    def _show_window(self):
        self.deiconify()
        self.lift()

    def _quit(self):
        self.destroy()

    def destroy(self):
        # Signal all background HID threads to stop
        if hasattr(self, "_displaypad_panel"):
            p = self._displaypad_panel
            if hasattr(p, "_monitor_stop"):
                p._monitor_stop.set()
            if hasattr(p, "_key_stop"):
                p._key_stop.set()
            if hasattr(p, "_anim_stop"):
                p._anim_stop.set()
        # Stop Everest panel CPU proc if running
        if hasattr(self, "_everest_panel"):
            if self._everest_panel._cpu_proc and \
               self._everest_panel._cpu_proc.poll() is None:
                self._everest_panel._cpu_proc.terminate()
        if hasattr(self, "_tray_proc") and self._tray_proc.poll() is None:
            self._tray_proc.terminate()
        # Give HID threads time to close their devices before tearing down
        import time
        time.sleep(0.4)
        super().destroy()


# ── Splash screen ─────────────────────────────────────────────────────────────

def show_splash():
    splash = tk.Tk()
    splash.overrideredirect(True)
    img   = Image.open(os.path.join(_RES, "resources", "logo.png")).convert("RGBA")
    img   = img.resize((768, 512), Image.LANCZOS)
    bg    = Image.new("RGBA", img.size, BG)
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
    app_dir       = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    appimage_path = os.environ.get("APPIMAGE", os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))

    icon_src = os.path.join(app_dir, "_internal", "resources", "app_icon_256.png")
    if not os.path.exists(icon_src):
        icon_src = os.path.join(app_dir, "resources", "app_icon_256.png")
    icon_dst = os.path.join(_real_home, ".local", "share", "icons", "hicolor",
                             "256x256", "apps", "basecamp-linux.png")
    os.makedirs(os.path.dirname(icon_dst), exist_ok=True)
    shutil.copy2(icon_src, icon_dst)

    desktop_dir  = os.path.join(_real_home, ".local", "share", "applications")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_path = os.path.join(desktop_dir, "basecamp-linux.desktop")
    with open(desktop_path, "w") as f:
        f.write(f"""[Desktop Entry]
Name=BaseCamp Linux
Comment=Unofficial Linux companion app for the Mountain Everest Max keyboard
Exec="{appimage_path}"
Icon=basecamp-linux
Type=Application
Categories=Utility;
""")
    os.chmod(desktop_path, 0o755)
    print(f"Installed: {desktop_path}")
    print(f"Installed: {icon_dst}")

    # Update autostart .desktop if it exists
    if os.path.exists(AUTOSTART_FILE):
        with open(AUTOSTART_FILE, "w") as f:
            f.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=BaseCamp Linux\n"
                "Comment=Mountain Everest Max display control\n"
                f'Exec="{appimage_path}" --minimized\n'
                "Icon=basecamp-linux\n"
                "Hidden=false\n"
                "NoDisplay=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
        print(f"Updated:   {AUTOSTART_FILE}")

    # Refresh desktop cache so the launcher picks up the new .desktop immediately
    try:
        subprocess.run(["update-desktop-database", desktop_dir],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass

    print("Done. BaseCamp Linux should now appear in your app menu.")


if __name__ == "__main__":
    if "--install" in sys.argv:
        _install_desktop_entry()
        sys.exit(0)
    psutil.cpu_percent()
    _start_minimized = "--minimized" in sys.argv
    if not _start_minimized and load_splash_enabled():
        show_splash()
    app = App()
    if _start_minimized:
        app._was_withdrawn = True
        app.withdraw()
    app.mainloop()
