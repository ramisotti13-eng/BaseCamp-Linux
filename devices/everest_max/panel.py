"""Everest Max device panel for BaseCamp Linux hub."""
import os
import sys
import json
import time
import threading
import subprocess
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageTk

from shared.config import (
    CONFIG_DIR, MAIN_MODE_FILE,
    load_config as load_style, save_config as save_style,
    load_buttons, save_buttons,
    load_autostart_enabled, save_autostart_enabled,
    load_splash_enabled, save_splash_enabled,
    load_rgb_settings as load_rgb_config, save_rgb_settings as save_rgb_config,
    load_zone_config, save_zone_config,
    _load_icon_last, _save_icon_last,
    _save_to_library, _save_to_main_library,
    _compute_lib_hash, _compute_main_lib_hash,
)
from shared.ui_helpers import (
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER,
    AccordionSection, LibraryPickerDialog, MultiUploadDialog, CustomRGBWindow,
    pick_color, pick_library_image, pick_main_library_image,
    native_open_image, native_open_folder, parse_desktop_apps,
    _rgb_hex,
)

# STYLES dict for this module
STYLES = {"Analog": "analog", "Digital": "digital"}

# ── EverestMaxPanel ────────────────────────────────────────────────────────────


class EverestMaxPanel(ctk.CTkFrame):
    """All Everest Max specific UI, packaged as a CTkFrame."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app = app

        # ── Local state ──────────────────────────────────────────────────────
        self._cpu_proc        = None
        self._sections        = []
        self._custom_rgb_win  = None
        self._multi_upload_win = None
        self._rgb_win         = None

        # Button action/type vars
        self._btn_action = [tk.StringVar(value="") for _ in range(4)]
        self._btn_type   = [tk.StringVar(value="shell") for _ in range(4)]
        buttons = load_buttons()
        for i, b in enumerate(buttons):
            self._btn_action[i].set(b.get("action", ""))
            self._btn_type[i].set(b.get("type", "shell"))

        # Clock format + style
        def _read_cfg(name, default):
            try:
                return open(os.path.join(CONFIG_DIR, name)).read().strip()
            except FileNotFoundError:
                return default

        self._clock_format = tk.StringVar(value=_read_cfg("clock_format", "24H"))
        self._current_style = tk.StringVar(value=next(
            (k for k, v in STYLES.items() if v == load_style()), "Analog"))

        # Splash / autostart
        self._splash_var    = tk.BooleanVar(value=load_splash_enabled())
        self._autostart_var = tk.BooleanVar(value=load_autostart_enabled())

        # Main display mode
        try:
            _saved_mode = open(MAIN_MODE_FILE).read().strip()
        except FileNotFoundError:
            _saved_mode = "clock"
        self._main_mode = _saved_mode if _saved_mode in (
            "image", "clock", "volume", "cpu", "gpu", "hd", "network", "ram", "apm"
        ) else "clock"
        self._main_just_uploaded = False
        self._after_dial_reset   = False

        # Build UI
        self._build_ui()

    # ── Translation / i18n delegation ─────────────────────────────────────────

    def T(self, key, **kwargs):
        return self._app.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    # ── subprocess command builder ────────────────────────────────────────────

    def _cmd(self, *args):
        return self._app._cmd(*args)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
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

        self._lang_var = self._app._lang_var
        self._lang_combo = ctk.CTkComboBox(
            fmt_row, variable=self._lang_var, values=[],
            command=lambda val: self._app._on_lang_change(val),
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

        self._build_monitor_section(scroll)
        self._build_main_display_section(scroll)
        self._build_numpad_section(scroll)
        self._build_rgb_section(scroll)
        self._build_zone_section(scroll)

        # Apply i18n, then measure sections
        self._app._apply_lang()
        self._app.update_idletasks()
        for s in self._sections:
            s.measure()
        self._sections[0].open()

        # Cap scroll speed
        _c = scroll._parent_canvas
        _orig_yview = _c.yview

        def _capped_yview(*args):
            if args and args[0] == "scroll":
                n    = max(-2, min(2, int(args[1])))
                what = args[2] if len(args) > 2 else "units"
                return _orig_yview("scroll", n, what)
            return _orig_yview(*args)

        _c.yview = _capped_yview

        # Start clock tick
        self._tick()
        self._update_cpu_bar()

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_monitor_section(self, scroll):
        s1 = AccordionSection(scroll, self._app, "⚡", "monitor_title")
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

    def _build_main_display_section(self, scroll):
        s2 = AccordionSection(scroll, self._app, "🖥", "main_display_title")
        self._sections.append(s2)
        b2 = s2.content

        mode_row = ctk.CTkFrame(b2, fg_color="transparent")
        mode_row.pack(pady=(10, 4))

        _MODE_KEYS = ["image", "clock", "volume", "cpu", "gpu", "hd", "network", "ram", "apm"]
        _MODE_LANG = ["main_mode_image", "main_mode_clock", "main_mode_volume", "main_mode_cpu",
                      "main_mode_gpu", "main_mode_hd", "main_mode_network",
                      "main_mode_ram", "main_mode_apm"]
        self._mode_labels  = [self.T(k) for k in _MODE_LANG]
        self._mode_key_map = {lbl: key for key, lbl in zip(_MODE_KEYS, self._mode_labels)}

        ctk.CTkLabel(mode_row, text="", font=("Helvetica", 11),
                     text_color=FG2).pack(side="left", padx=(0, 6))
        self._reg(ctk.CTkLabel(mode_row, text="", font=("Helvetica", 11),
                               text_color=FG2), "main_mode_label").pack(side="left", padx=(0, 6))
        self._main_mode_var = tk.StringVar(
            value=self._mode_labels[_MODE_KEYS.index(self._main_mode)])
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

    def _build_numpad_section(self, scroll):
        s3 = AccordionSection(scroll, self._app, "⌨", "numpad_title")
        self._sections.append(s3)
        b3 = s3.content

        self._reg(
            ctk.CTkLabel(b3, text="", font=("Helvetica", 11), text_color=FG2),
            "numpad_subtitle"
        ).pack(pady=(8, 4))

        multi_row = ctk.CTkFrame(b3, fg_color="transparent")
        multi_row.pack(fill="x", padx=8, pady=(0, 8))
        self._reg(
            ctk.CTkButton(
                multi_row, text="",
                height=34, corner_radius=6,
                fg_color="#7c3aed", hover_color="#6d28d9", text_color=FG,
                font=("Helvetica", 11, "bold"),
                command=self._open_multi_upload,
            ),
            "multi_upload_btn"
        ).pack(fill="x")

        self._btn_type_menus = []
        self._folder_btns    = []
        self._action_entries = []
        self._obs_combos     = []

        _TYPE_INTERNAL = ["none", "shell", "url", "folder", "app", "obs"]

        def _type_labels():
            return [self.T("action_type_none"),   self.T("action_type_shell"),
                    self.T("action_type_url"),     self.T("action_type_folder"),
                    self.T("action_type_app"),     "OBS"]

        _HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _FROZEN = getattr(sys, "frozen", False)
        _RES = getattr(sys, "_MEIPASS", _HERE) if _FROZEN else _HERE

        _folder_pil = Image.open(os.path.join(_RES, "resources", "foldericon.png")).convert("RGBA")
        self._folder_img = ctk.CTkImage(light_image=_folder_pil, dark_image=_folder_pil, size=(24, 24))
        _folder_pil_dim  = _folder_pil.copy()
        _folder_pil_dim.putalpha(_folder_pil_dim.getchannel("A").point(lambda v: v // 3))
        self._folder_img_dim = ctk.CTkImage(light_image=_folder_pil_dim, dark_image=_folder_pil_dim, size=(24, 24))
        _folder_img     = self._folder_img
        _folder_img_dim = self._folder_img_dim

        for i in range(4):
            card = ctk.CTkFrame(b3, fg_color=BG3, corner_radius=4)
            card.pack(fill="x", padx=12, pady=2)

            header_row = ctk.CTkFrame(card, fg_color="transparent")
            header_row.pack(fill="x", padx=8, pady=(6, 0))
            ctk.CTkLabel(header_row, text=f"D{i+1}", font=("Helvetica", 10, "bold"),
                         text_color=YLW).pack(side="left")

            action_row = ctk.CTkFrame(card, fg_color="transparent")
            action_row.pack(fill="x", padx=4, pady=(2, 6))

            self._reg(
                ctk.CTkLabel(action_row, text="", font=("Helvetica", 10),
                             text_color=FG2, width=50, anchor="w"),
                "action_label"
            ).pack(side="left", padx=(4, 2))

            idx = i
            cur_internal = self._btn_type[i].get()
            labels       = _type_labels()
            cur_label    = labels[_TYPE_INTERNAL.index(cur_internal)] if cur_internal in _TYPE_INTERNAL else labels[1]

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

            entry = ctk.CTkEntry(action_row, textvariable=self._btn_action[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=("Helvetica", 11), height=30)
            entry.pack(side="left", padx=4, expand=True, fill="x")
            self._action_entries.append(entry)

            obs_combo = ctk.CTkComboBox(
                action_row, values=[], width=140, height=30,
                font=("Helvetica", 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=idx: self._on_obs_select(val, ix))
            self._obs_combos.append(obs_combo)

            cur_type     = self._btn_type[i].get()
            if cur_type == "obs":
                entry.pack_forget()
                obs_panel = self._app._obs_panel
                scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
                obs_combo.configure(values=scenes + ["— Record", "— Stream"])
                cur_action = self._btn_action[i].get()
                if cur_action.startswith("scene:"):
                    obs_combo.set(cur_action[6:])
                elif cur_action in ("record", "stream"):
                    obs_combo.set(f"— {cur_action.capitalize()}")
                obs_combo.pack(side="left", padx=4, expand=True, fill="x")
            browse_active = cur_type in ("folder", "app")
            folder_btn   = ctk.CTkButton(
                action_row, text="",
                image=_folder_img if browse_active else _folder_img_dim,
                width=30, height=30,
                command=lambda ix=idx: self._browse_action(ix),
                fg_color="transparent", hover_color=BG3, corner_radius=4,
                state="normal" if browse_active else "disabled",
            )
            folder_btn.pack(side="left", padx=(0, 4))
            self._folder_btns.append(folder_btn)

            entry.bind("<Return>", lambda e, ix=idx: self._apply_btn(ix))
            entry.bind("<FocusOut>", lambda e, ix=idx: self._apply_btn(ix))

        self._numpad_type_internal   = _TYPE_INTERNAL
        self._numpad_type_labels_fn  = _type_labels

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

    def _build_rgb_section(self, scroll):
        s5 = AccordionSection(scroll, self._app, "💡", "rgb_title")
        self._sections.append(s5)
        c = s5.content

        rgb_mode_row = ctk.CTkFrame(c, fg_color="transparent")
        rgb_mode_row.pack(fill="x", padx=10, pady=(10, 2))
        self._reg(
            ctk.CTkLabel(rgb_mode_row, text="", font=("Helvetica", 11), text_color=FG2),
            "rgb_mode_label"
        ).pack(side="left", padx=(0, 6))

        _RGB_EFFECTS = [
            ("Static",             "static",            False, True,  True,  False, False),
            ("Breathing",          "breathing",         True,  True,  True,  False, False),
            ("Breathing Rainbow",  "breathing-rainbow", True,  True,  False, False, False),
            ("Breathing Dual",     "breathing-dual",    True,  True,  True,  True,  False),
            ("Wave",               "wave",              True,  True,  True,  False, True),
            ("Wave Rainbow",       "wave-rainbow",      True,  True,  False, False, True),
            ("Tornado",            "tornado",           True,  True,  True,  False, True),
            ("Tornado Rainbow",    "tornado-rainbow",   True,  True,  False, False, True),
            ("Reactive",           "reactive",          True,  True,  True,  True,  False),
            ("Yeti",               "yeti",              True,  True,  True,  True,  False),
            ("Matrix",             "matrix",            True,  True,  True,  True,  False),
            ("Off",                "off",               False, False, False, False, False),
        ]
        self._rgb_effect_map = {name: (eid, hs, hb, hc1, hc2, hd)
                                for name, eid, hs, hb, hc1, hc2, hd in _RGB_EFFECTS}
        _rgb_names = [e[0] for e in _RGB_EFFECTS]
        self._rgb_mode_var  = tk.StringVar(value=_rgb_names[0])
        self._rgb_mode_menu = ctk.CTkOptionMenu(
            rgb_mode_row, variable=self._rgb_mode_var, values=_rgb_names,
            command=lambda _: self._rgb_update_controls(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=180, height=32)
        self._rgb_mode_menu.pack(side="left")

        def _labeled_slider(parent, label_key, from_=0, to=100, init=50):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            lbl = self._reg(ctk.CTkLabel(row, text="", text_color=FG2,
                                         font=("Helvetica", 11), width=120, anchor="w"), label_key)
            lbl.pack(side="left")
            val_lbl = ctk.CTkLabel(row, text=str(init), text_color=FG,
                                   font=("Helvetica", 11), width=30)
            val_lbl.pack(side="right")
            sl = ctk.CTkSlider(row, from_=from_, to=to, number_of_steps=to - from_,
                               fg_color=BG3, progress_color=BLUE, button_color=BLUE,
                               button_hover_color=BLUE, width=180, height=16)
            sl.set(init)
            sl.pack(side="left", padx=(0, 4))
            sl.configure(command=lambda v, l=val_lbl: l.configure(text=str(int(v))))
            return sl, row

        self._rgb_speed_sl, self._rgb_speed_row = _labeled_slider(c, "rgb_speed_label", init=50)
        self._rgb_bri_sl,   self._rgb_bri_row   = _labeled_slider(c, "rgb_brightness_label", init=100)

        color_row = ctk.CTkFrame(c, fg_color="transparent")
        color_row.pack(fill="x", padx=10, pady=2)
        self._rgb_color1 = (255, 0, 0)
        self._rgb_color2 = (0, 0, 255)

        self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2, font=("Helvetica", 11)),
                  "rgb_color1_label").pack(side="left", padx=(0, 4))
        self._rgb_c1_btn = ctk.CTkButton(color_row, text="", width=40, height=28,
                                          fg_color="#ff0000", hover_color="#ff0000", corner_radius=4,
                                          command=lambda: self._pick_rgb_color(1))
        self._rgb_c1_btn.pack(side="left", padx=(0, 12))

        self._rgb_c2_lbl = self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2,
                                                   font=("Helvetica", 11)), "rgb_color2_label")
        self._rgb_c2_lbl.pack(side="left", padx=(0, 4))
        self._rgb_c2_btn = ctk.CTkButton(color_row, text="", width=40, height=28,
                                          fg_color="#0000ff", hover_color="#0000ff", corner_radius=4,
                                          command=lambda: self._pick_rgb_color(2))
        self._rgb_c2_btn.pack(side="left")

        dir_row = ctk.CTkFrame(c, fg_color="transparent")
        dir_row.pack(fill="x", padx=10, pady=2)
        self._rgb_dir_row = dir_row
        self._reg(ctk.CTkLabel(dir_row, text="", text_color=FG2, font=("Helvetica", 11)),
                  "rgb_direction_label").pack(side="left", padx=(0, 6))
        self._dir_wave    = ["→ L→R", "↓ T→B", "← R→L", "↑ B→T"]
        self._dir_tornado = ["↻ CW", "↺ CCW"]
        self._rgb_dir_val_map = {"→ L→R": 0, "↓ T→B": 2, "← R→L": 4, "↑ B→T": 6,
                                 "↻ CW": 9, "↺ CCW": 10}
        self._rgb_dir_var  = tk.StringVar(value=self._dir_wave[0])
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

        rgb_apply_row = ctk.CTkFrame(c, fg_color="transparent")
        self._rgb_apply_row = rgb_apply_row
        rgb_apply_row.pack(fill="x", padx=10, pady=(6, 10))
        self._reg(
            ctk.CTkButton(rgb_apply_row, text="", font=("Helvetica", 11),
                          fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                          width=120, height=32, command=self._apply_rgb),
            "rgb_apply"
        ).pack(side="left")
        self._rgb_status = ctk.CTkLabel(rgb_apply_row, text="", text_color=FG2,
                                         font=("Helvetica", 11))
        self._rgb_status.pack(side="left", padx=(10, 0))

        self._rgb_section = s5
        self._rgb_update_controls()

    def _build_zone_section(self, scroll):
        s6 = AccordionSection(scroll, self._app, "🎨", "zone_title")
        self._sections.append(s6)
        c6 = s6.content

        self._rgb_win      = None
        self._zone_status  = ctk.CTkLabel(c6, text="", text_color=FG2,
                                          font=("Helvetica", 11))

        open_row = ctk.CTkFrame(c6, fg_color="transparent")
        open_row.pack(pady=(16, 16))
        self._reg(
            ctk.CTkButton(open_row, text="", font=("Helvetica", 12, "bold"),
                          fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                          width=220, height=38, command=self._open_rgb_editor),
            "zone_open_editor"
        ).pack()


    # ── Logic methods ─────────────────────────────────────────────────────────

    def _tick(self):
        import datetime
        import threading
        import resource
        now = datetime.datetime.now()
        if self._clock_format.get() == "12H":
            time_str = now.strftime("%I:%M:%S %p")
        else:
            time_str = now.strftime("%H:%M:%S")
        self._clock_label.configure(text=time_str)

        days   = self._app._lang.get("days",
            ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])
        months = self._app._lang.get("months",
            ["January","February","March","April","May","June",
             "July","August","September","October","November","December"])
        date_str = f"{days[now.weekday()]}, {now.day:02d}. {months[now.month-1]} {now.year}"
        self._date_label.configure(text=date_str)

        self._app.after(1000, self._tick)

    def _update_cpu_bar(self):
        if self._cpu_proc and self._cpu_proc.poll() is not None:
            self._cpu_proc = None
            self._btn_cpu.configure(text=self.T("monitor_start"),
                                    fg_color=YLW, text_color="#0d0d14")
            self._cpu_status.configure(text=self.T("monitor_stopped"), text_color=RED)
        self._app.after(5000, self._update_cpu_bar)

    def _on_format_change(self):
        with open(os.path.join(CONFIG_DIR, "clock_format"), "w") as f:
            f.write(self._clock_format.get())

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
            result = subprocess.run(self._cmd(style_arg), capture_output=True)
            ok = result.returncode == 0
            if callback:
                self._app.after(0, lambda: callback(ok))
        threading.Thread(target=task, daemon=True).start()

    def _stop_cpu_proc(self):
        """Terminate CPU monitor if running. Returns True if it was running."""
        if self._cpu_proc and self._cpu_proc.poll() is None:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None
            return True
        return False

    def _start_cpu_auto(self):
        if not (self._cpu_proc and self._cpu_proc.poll() is None):
            self._toggle_cpu()

    def _start_cpu_auto_clean(self):
        _FROZEN = getattr(sys, "frozen", False)
        def run():
            pkill = "basecamp-controller.*cpu" if _FROZEN else r"mountain-time-sync\.py.*cpu"
            subprocess.run(["pkill", "-f", pkill], capture_output=True)
            time.sleep(0.4)
            self._app.after(0, self._start_cpu_auto)
        threading.Thread(target=run, daemon=True).start()

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
                    self._cmd("cpu", style_arg),
                    stdout=subprocess.DEVNULL, stderr=_stderr_log)
                self._btn_cpu.configure(text=self.T("monitor_stop"),
                                        fg_color=RED, text_color=BG,
                                        font=("Helvetica", 11, "bold"))
                self._cpu_status.configure(text=self.T("monitor_running"), text_color=GRN)
            except Exception as e:
                self._cpu_status.configure(text=f"{self.T('error')}: {e}", text_color=RED)

    def _reset_dial_image(self):
        def run():
            subprocess.run(self._cmd("reset-dial"), capture_output=True)
            self._app.after(0, lambda: setattr(self, "_after_dial_reset", True))
        threading.Thread(target=run, daemon=True).start()

    def _rgb_update_controls(self):
        name = self._rgb_mode_var.get()
        _, hs, hb, hc1, hc2, hd = self._rgb_effect_map.get(
            name, ("", False, False, False, False, False))
        state_speed = "normal" if hs else "disabled"
        state_bri   = "normal" if hb else "disabled"
        state_c1    = "normal" if hc1 else "disabled"
        state_c2    = "normal" if hc2 else "disabled"
        self._rgb_speed_sl.configure(state=state_speed)
        self._rgb_bri_sl.configure(state=state_bri)
        self._rgb_c1_btn.configure(state=state_c1)
        self._rgb_c2_btn.configure(state=state_c2)
        self._rgb_c2_lbl.configure(text_color=FG2 if hc2 else BG3)
        was_visible = self._rgb_dir_row.winfo_ismapped()
        if hd:
            is_tornado = "tornado" in self._rgb_effect_map.get(name, ("",))[0]
            new_opts   = self._dir_tornado if is_tornado else self._dir_wave
            cur        = self._rgb_dir_var.get()
            if cur not in new_opts:
                self._rgb_dir_var.set(new_opts[0])
            self._rgb_dir_menu.configure(values=new_opts)
            if not was_visible:
                self._rgb_dir_row.pack(fill="x", padx=10, pady=2,
                                       before=self._rgb_apply_row)
        else:
            self._rgb_dir_row.pack_forget()
        if hasattr(self, "_rgb_section"):
            self._app.update_idletasks()
            s = self._rgb_section
            was_open = s._open
            s.measure()
            if was_open:
                s._content.configure(height=s._natural_h)

    def _pick_rgb_color(self, which):
        initial = self._rgb_color1 if which == 1 else self._rgb_color2
        rgb = pick_color(self._app, initial_rgb=initial, title="Farbe wählen", show_brightness=False)
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
        r1, g1, b1 = self._rgb_color1
        r2, g2, b2 = self._rgb_color2
        c1_hex    = f"{r1:02x}{g1:02x}{b1:02x}"
        c2_hex    = f"{r2:02x}{g2:02x}{b2:02x}"
        direction = self._rgb_dir_val_map.get(self._rgb_dir_var.get(), 0)
        self._rgb_status.configure(text=self.T("rgb_applying"), text_color=YLW)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        def run():
            r = subprocess.run(
                self._cmd("rgb", eid, str(speed), str(bri), c1_hex, c2_hex, str(direction)),
                capture_output=True)
            ok  = r.returncode == 0
            err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            if ok:
                save_rgb_config({
                    "effect": name, "speed": speed, "brightness": bri,
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
            self._app.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _pick_zone_color(self, zone_key):
        initial = self._zone_colors.get(zone_key, (0, 0, 0))
        rgb = pick_color(self._app, initial_rgb=initial, title="Farbe wählen", show_brightness=False)
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
            result = subprocess.run(self._cmd("custom-rgb", *tokens), capture_output=True)
            ok  = result.returncode == 0
            err = (result.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            if ok:
                save_zone_config(self._zone_colors, brightness)
            def finish():
                self._zone_status.configure(
                    text=self.T("zone_applied") if ok else f"{self.T('zone_error')} — {err}",
                    text_color=GRN if ok else RED)
                if was_running:
                    self._start_cpu_auto()
            self._app.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _open_rgb_editor(self):
        if self._rgb_win is not None and self._rgb_win.winfo_exists():
            self._rgb_win.focus()
            return
        self._rgb_win = CustomRGBWindow(self._app)

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
        # Show/hide OBS combo vs entry+browse
        if hasattr(self, "_obs_combos") and idx < len(self._obs_combos):
            self._obs_combos[idx].pack_forget()
            self._action_entries[idx].pack_forget()
            self._folder_btns[idx].pack_forget()
            if internal == "obs":
                obs_panel = self._app._obs_panel
                scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
                self._obs_combos[idx].configure(values=scenes + ["— Record", "— Stream"])
                if scenes:
                    self._obs_combos[idx].set(scenes[0])
                    self._btn_action[idx].set(f"scene:{scenes[0]}")
                self._obs_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
            else:
                self._action_entries[idx].pack(side="left", padx=4, expand=True, fill="x")
                self._folder_btns[idx].pack(side="left", padx=(0, 4))
            self._apply_btn(idx)

    def _on_obs_select(self, val, idx):
        if val == "— Record":
            self._btn_action[idx].set("record")
        elif val == "— Stream":
            self._btn_action[idx].set("stream")
        else:
            self._btn_action[idx].set(f"scene:{val}")
        self._apply_btn(idx)

    def _browse_action(self, idx):
        btype = self._btn_type[idx].get()
        if btype == "folder":
            path = native_open_folder()
            if path:
                self._btn_action[idx].set(path)
                self._apply_btn(idx)
        elif btype == "app":
            self._show_app_picker(idx)  # auto-saves via _select

    def _show_app_picker(self, idx):
        apps = parse_desktop_apps()
        if not apps:
            return

        dlg = ctk.CTkToplevel(self._app)
        dlg.title(self.T("app_picker_title"))
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.geometry("360x480")
        dlg.update_idletasks()
        dlg.grab_set()

        search_var   = tk.StringVar()
        search_entry = ctk.CTkEntry(
            dlg, textvariable=search_var, placeholder_text=self.T("app_picker_search"),
            fg_color=BG2, text_color=FG, border_color=BORDER,
            font=("Helvetica", 12), height=34,
        )
        search_entry.pack(fill="x", padx=12, pady=(12, 6))
        search_entry.focus()

        list_frame = ctk.CTkScrollableFrame(dlg, fg_color=BG2, corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        result   = [None]
        _btn_refs = []

        def _select(name, exec_cmd):
            result[0] = exec_cmd
            self._btn_action[idx].set(exec_cmd)
            dlg.destroy()
            self._apply_btn(idx)

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

    def _open_multi_upload(self):
        if hasattr(self, "_multi_upload_win") and self._multi_upload_win is not None and self._multi_upload_win.winfo_exists():
            self._multi_upload_win.focus()
            return
        self._multi_upload_win = MultiUploadDialog(self._app)

    def _reset_buttons_flash(self):
        was_running = self._stop_cpu_proc()
        self._numpad_info.configure(text=self.T("reset_buttons_running"), text_color=FG2)

        def _run():
            time.sleep(0.5)
            r = subprocess.run(self._cmd("reset-buttons"), capture_output=True)
            if r.returncode == 0:
                self._app.after(0, lambda: self._numpad_info.configure(
                    text=self.T("reset_buttons_done"), text_color=GRN))
            else:
                self._app.after(0, lambda: self._numpad_info.configure(
                    text=self.T("reset_buttons_error"), text_color=RED))
            if was_running:
                self._app.after(0, self._start_cpu_auto)

        threading.Thread(target=_run, daemon=True).start()

    def _upload_image(self, idx):
        result = pick_library_image(self._app, self._app)
        if result is None:
            return
        path, gif_frame, thumb_fname = result

        stored   = _load_icon_last().get(str(idx))
        resolved = thumb_fname or _compute_lib_hash(path, gif_frame)
        if resolved and resolved == stored:
            self._numpad_info.configure(
                text=self.T("image_unchanged", d=idx+1), text_color=FG2)
            return

        self._numpad_info.configure(text=self.T("image_uploading", d=idx+1),
                                    text_color=BLUE)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        def do_upload():
            time.sleep(2.5 if was_running else 0.5)
            cmd = self._cmd("upload", str(idx), path)
            if gif_frame:
                cmd = self._cmd("upload", str(idx), path, "--frame", str(gif_frame))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            proc.stdout.read()
            proc.wait()
            ok = proc.returncode == 0
            if ok:
                new_fname = thumb_fname or _save_to_library(path, gif_frame)
                if new_fname:
                    _save_icon_last(idx, new_fname)
            if was_running:
                self._app.after(0, self._start_cpu_auto)
            err_hint = (proc.stderr.read().strip().splitlines() or [""])[-1]

            def finish():
                self._numpad_info.configure(
                    text=(self.T("image_uploaded", d=idx+1) if ok
                          else f"D{idx+1}: Fehler — {err_hint}" if err_hint
                          else self.T("image_error", d=idx+1)),
                    text_color=GRN if ok else RED)
            self._app.after(0, finish)

        threading.Thread(target=do_upload, daemon=True).start()

    def _set_main_mode(self, mode):
        self._main_mode = mode
        with open(MAIN_MODE_FILE, "w") as f:
            f.write(mode)
        self._main_status.configure(text="", text_color=FG2)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        just_uploaded  = self._main_just_uploaded
        self._main_just_uploaded = False
        needs_monitor  = (mode != "image")
        _FROZEN        = getattr(sys, "frozen", False)

        def run():
            delay = 2.0 if just_uploaded else 0.8 if was_running else 0.5
            if just_uploaded:
                self._app.after(0, lambda: self._main_status.configure(
                    text=self.T("waiting_for_keyboard"), text_color=YLW))
            time.sleep(delay)
            pkill = "basecamp-controller.*cpu" if _FROZEN else r"mountain-time-sync\.py.*cpu"
            subprocess.run(["pkill", "-f", pkill], capture_output=True)
            time.sleep(0.3)
            r = subprocess.run(self._cmd("main-mode", mode), capture_output=True)
            if r.returncode != 0:
                err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
                self._app.after(0, lambda: self._main_status.configure(
                    text=f"{mode}: {err or 'error'}", text_color=RED))
                return
            if needs_monitor:
                time.sleep(0.3)
                self._app.after(0, self._start_cpu_auto)
        threading.Thread(target=run, daemon=True).start()

    def _upload_main_image(self):
        result = pick_main_library_image(self._app, self._app)
        if result is None:
            return
        path, gif_frame, thumb_fname = result

        stored   = _load_icon_last().get("main")
        resolved = thumb_fname or _compute_main_lib_hash(path, gif_frame)
        if resolved and resolved == stored:
            self._main_status.configure(
                text=self.T("main_display_unchanged"), text_color=FG2)
            return

        self._main_status.configure(text=self.T("main_display_uploading"), text_color=BLUE)
        self._main_bar.set(0)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            self._cpu_proc.wait()
            self._cpu_proc = None

        need_mode_switch = (self._main_mode != "image")
        after_reset      = self._after_dial_reset
        self._after_dial_reset = False

        def do_upload():
            time.sleep(2.5 if was_running else 0.5)
            if need_mode_switch and not after_reset:
                self._main_mode = "image"
                subprocess.run(self._cmd("main-mode", "image"), capture_output=True)
                time.sleep(0.3)
            extras = ["--frame", str(gif_frame)] if gif_frame else []
            if after_reset:
                extras.append("--activate-custom")
            cmd    = self._cmd("upload-main", path, *extras)
            ok     = False
            err_hint = ""
            for attempt in range(3):
                if attempt > 0:
                    self._app.after(0, lambda a=attempt: self._main_status.configure(
                        text=f"Retry {a}/2…", text_color=YLW))
                    time.sleep(2.0)
                    self._app.after(0, lambda: self._main_bar.set(0))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)
                for line in proc.stdout:
                    if line.startswith("PROGRESS:"):
                        try:
                            pct = int(line.strip()[9:])
                            self._app.after(0, lambda v=pct: self._main_bar.set(v / 100.0))
                        except ValueError:
                            pass
                    else:
                        print(line, end="", flush=True)
                proc.wait()
                ok       = proc.returncode == 0
                err_hint = (proc.stderr.read().strip().splitlines() or [""])[-1]
                if ok:
                    break

            if ok:
                new_fname = thumb_fname or _save_to_main_library(path, gif_frame)
                if new_fname:
                    _save_icon_last("main", new_fname)

            def finish():
                self._main_bar.set(0)
                self._main_status.configure(
                    text=(self.T("main_display_uploaded") if ok
                          else f"{self.T('main_display_error')} — {err_hint}" if err_hint
                          else self.T("main_display_error")),
                    text_color=GRN if ok else RED)
                if ok:
                    self._main_mode = "image"
                    self._main_mode_var.set(self._mode_labels[0])
                    self._main_just_uploaded = True
            self._app.after(0, finish)

        threading.Thread(target=do_upload, daemon=True).start()

    # ── Public interface for App ───────────────────────────────────────────────

    def apply_lang(self):
        """Called by App when language changes to refresh button type menus."""
        if hasattr(self, "_btn_type_menus"):
            new_labels = self._numpad_type_labels_fn()
            for i, menu in enumerate(self._btn_type_menus):
                menu.configure(values=new_labels)
                cur = self._btn_type[i].get()
                try:
                    menu.set(new_labels[self._numpad_type_internal.index(cur)])
                except (ValueError, IndexError):
                    menu.set(new_labels[1])

    def set_connected(self, connected: bool):
        """Show/hide a 'not connected' banner (future use)."""
        pass
