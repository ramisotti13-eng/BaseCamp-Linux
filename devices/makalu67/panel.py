"""Makalu 67 device panel for BaseCamp Linux hub."""
import subprocess
import threading
import tkinter as tk
import customtkinter as ctk

from shared.ui_helpers import (
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER,
    AccordionSection, pick_color, _rgb_hex,
)
from shared.config import (
    _load_makalu_dpi, _save_makalu_dpi, DPI_DEFAULTS,
    _load_makalu_remap, _save_makalu_remap,
)

# ── Mouse LED canvas layout (confirmed by USB capture) ─────────────────────────
# Physical:  LED[0]=top-left … LED[3]=bot-left | LED[4]=bot-right … LED[7]=top-right

_MOUSE_CANVAS_W = 300
_MOUSE_CANVAS_H = 340
_MOUSE_BODY     = (90, 25, 210, 315)   # mouse body rect on canvas

#                  x1   y1   x2   y2
_MOUSE_LED_RECTS = [
    ( 10,  55,  75, 110),   # LED 0 — top-left
    ( 10, 125,  75, 180),   # LED 1
    ( 10, 195,  75, 250),   # LED 2
    ( 10, 265,  75, 320),   # LED 3 — bottom-left
    (225, 265, 290, 320),   # LED 4 — bottom-right
    (225, 195, 290, 250),   # LED 5
    (225, 125, 290, 180),   # LED 6
    (225,  55, 290, 110),   # LED 7 — top-right
]

_QUICK_COLORS = [
    ("#ff0000", (255,   0,   0)), ("#ff8800", (255, 136,   0)),
    ("#ffff00", (255, 255,   0)), ("#00ff00", (  0, 255,   0)),
    ("#00ffff", (  0, 255, 255)), ("#0088ff", (  0, 136, 255)),
    ("#8800ff", (136,   0, 255)), ("#ff00ff", (255,   0, 255)),
    ("#ffffff", (255, 255, 255)), ("#000000", (  0,   0,   0)),
]

# (name, code, has_speed, has_color1, has_color2, has_direction)
_RGB_EFFECTS = [
    ("Static",       1, False, True,  False, False),
    ("Breathing",    5, True,  True,  True,  False),
    ("RGB Breathing",6, True,  False, False, False),
    ("Rainbow",      2, True,  False, False, True),
    ("Responsive",   7, False, True,  False, False),
    ("Yeti",         8, True,  True,  True,  False),
    ("Off",          0, False, False, False, False),
]
_EFFECT_MAP = {name: (code, hs, hc1, hc2, hd) for name, code, hs, hc1, hc2, hd in _RGB_EFFECTS}
_EFFECT_NAMES = [e[0] for e in _RGB_EFFECTS]


class Makalu67Panel(ctk.CTkFrame):
    """Panel for Makalu 67 mouse."""

    VID = 0x3282
    PID = 0x0003

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app            = app
        self._connected      = False
        self._sections       = []
        self._custom_rgb_win = None

        self._build_ui()

    # ── Translation delegation ────────────────────────────────────────────────

    def T(self, key, **kwargs):
        return self._app.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    # ── Subprocess helper ──────────────────────────────────────────────────────

    def _cmd(self, *args):
        """Return command list for Makalu controller subprocess."""
        return self._app._cmd_for_device("makalu67", *args)

    def _run_async(self, cmd, on_done=None):
        """Run command in background thread, call on_done(ok, stdout) on main thread."""
        def _worker():
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                ok = r.returncode == 0 and r.stdout.strip() == "ok"
                if on_done:
                    self.after(0, lambda: on_done(ok, r.stdout.strip()))
            except Exception as e:
                if on_done:
                    self.after(0, lambda e=e: on_done(False, str(e)))
        threading.Thread(target=_worker, daemon=True).start()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._section_titles = []  # list of (section, lang_key)

        # Not-connected banner
        self._banner = ctk.CTkFrame(self, fg_color="#3b1515", corner_radius=6)
        self._banner_lbl = ctk.CTkLabel(
            self._banner,
            text=self.T("makalu_not_connected"),
            font=("Helvetica", 11), text_color=RED,
        )
        self._reg(self._banner_lbl, "makalu_not_connected")
        self._banner_lbl.pack(pady=8, padx=16)
        if not self._connected:
            self._banner.pack(fill="x", padx=12, pady=(8, 4))

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))

        self._build_rgb_section(scroll)
        self._build_custom_section(scroll)
        self._build_dpi_section(scroll)
        self._build_remap_section(scroll)
        self._build_settings_section(scroll)

        self._app.update_idletasks()
        for s in self._sections:
            s.measure()

    def _build_rgb_section(self, scroll):
        s = _PlaceholderSection(scroll, self._app, "💡", self.T("makalu_rgb_title"))
        self._sections.append(s)
        self._section_titles.append((s, "makalu_rgb_title"))
        self._rgb_section = s
        self._build_rgb_content(s.content)

    def _build_custom_section(self, scroll):
        s = _PlaceholderSection(scroll, self._app, "🎨", self.T("makalu_custom_title"))
        self._sections.append(s)
        self._section_titles.append((s, "makalu_custom_title"))
        self._custom_section = s
        self._build_custom_content(s.content)

    def _build_rgb_content(self, parent):
        # ── Effect dropdown ──────────────────────────────────────────────────
        mode_row = ctk.CTkFrame(parent, fg_color="transparent")
        mode_row.pack(fill="x", padx=10, pady=(10, 2))
        _lbl = ctk.CTkLabel(mode_row, text=self.T("makalu_rgb_effect"),
                            font=("Helvetica", 11), text_color=FG2)
        _lbl.pack(side="left", padx=(0, 6))
        self._reg(_lbl, "makalu_rgb_effect")
        self._rgb_mode_var = tk.StringVar(value=_EFFECT_NAMES[0])
        ctk.CTkOptionMenu(
            mode_row, variable=self._rgb_mode_var, values=_EFFECT_NAMES,
            command=lambda _: self._rgb_update_controls(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=180, height=32,
        ).pack(side="left")

        # ── Speed buttons ─────────────────────────────────────────────────────
        self._rgb_speed_row = ctk.CTkFrame(parent, fg_color="transparent")
        self._rgb_speed_row.pack(fill="x", padx=10, pady=2)
        _lbl = ctk.CTkLabel(self._rgb_speed_row, text=self.T("makalu_rgb_speed"),
                            font=("Helvetica", 11), text_color=FG2, width=120, anchor="w")
        _lbl.pack(side="left")
        self._reg(_lbl, "makalu_rgb_speed")
        self._rgb_speed_var = tk.StringVar(value="Medium")
        self._rgb_speed_seg = ctk.CTkSegmentedButton(
            self._rgb_speed_row, values=["Slow", "Medium", "Fast"],
            variable=self._rgb_speed_var,
            command=lambda _: self._apply_rgb(),
            fg_color=BG3, selected_color=BLUE, selected_hover_color="#0284c7",
            unselected_color=BG3, unselected_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), height=28)
        self._rgb_speed_seg.pack(side="left")

        # ── Direction buttons ─────────────────────────────────────────────────
        self._rgb_dir_row = ctk.CTkFrame(parent, fg_color="transparent")
        self._rgb_dir_row.pack(fill="x", padx=10, pady=2)
        _lbl = ctk.CTkLabel(self._rgb_dir_row, text=self.T("makalu_rgb_direction"),
                            font=("Helvetica", 11), text_color=FG2, width=120, anchor="w")
        _lbl.pack(side="left")
        self._reg(_lbl, "makalu_rgb_direction")
        self._rgb_dir_var = tk.StringVar(value="→")
        self._rgb_dir_seg = ctk.CTkSegmentedButton(
            self._rgb_dir_row, values=["←", "→"],
            variable=self._rgb_dir_var,
            command=lambda _: self._apply_rgb(),
            fg_color=BG3, selected_color=BLUE, selected_hover_color="#0284c7",
            unselected_color=BG3, unselected_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), height=28)
        self._rgb_dir_seg.pack(side="left")

        # ── Brightness dropdown ──────────────────────────────────────────────
        self._rgb_bri_row = ctk.CTkFrame(parent, fg_color="transparent")
        self._rgb_bri_row.pack(fill="x", padx=10, pady=2)
        _lbl = ctk.CTkLabel(self._rgb_bri_row, text=self.T("makalu_rgb_brightness"),
                            font=("Helvetica", 11), text_color=FG2, width=120, anchor="w")
        _lbl.pack(side="left")
        self._reg(_lbl, "makalu_rgb_brightness")
        self._rgb_bri_var = tk.StringVar(value="100%")
        ctk.CTkOptionMenu(
            self._rgb_bri_row, variable=self._rgb_bri_var,
            values=["0%", "25%", "50%", "75%", "100%"],
            command=lambda _: self._apply_rgb(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=120, height=28,
        ).pack(side="left")

        # ── Color buttons ────────────────────────────────────────────────────
        color_row = ctk.CTkFrame(parent, fg_color="transparent")
        color_row.pack(fill="x", padx=10, pady=2)
        self._rgb_color1 = (0, 118, 204)
        self._rgb_color2 = (255, 0, 0)

        _lbl = ctk.CTkLabel(color_row, text=self.T("makalu_rgb_color1"),
                            font=("Helvetica", 11), text_color=FG2)
        _lbl.pack(side="left", padx=(0, 4))
        self._reg(_lbl, "makalu_rgb_color1")
        self._rgb_c1_btn = ctk.CTkButton(
            color_row, text="", width=40, height=28, corner_radius=4,
            fg_color=_rgb_hex(self._rgb_color1),
            hover_color=_rgb_hex(self._rgb_color1),
            command=lambda: self._pick_rgb_color(1))
        self._rgb_c1_btn.pack(side="left", padx=(0, 12))

        self._rgb_c2_lbl = ctk.CTkLabel(color_row, text=self.T("makalu_rgb_color2"),
                                         font=("Helvetica", 11), text_color=FG2)
        self._reg(self._rgb_c2_lbl, "makalu_rgb_color2")
        self._rgb_c2_btn = ctk.CTkButton(
            color_row, text="", width=40, height=28, corner_radius=4,
            fg_color=_rgb_hex(self._rgb_color2),
            hover_color=_rgb_hex(self._rgb_color2),
            command=lambda: self._pick_rgb_color(2))

        # ── Color presets ────────────────────────────────────────────────────
        _PRESETS = [
            (255,   0,   0), (204,   0,  67), (235,  64,  52), (220,  41, 188),
            (179,  53, 127), ( 71,   0, 204), (  0,  60, 204), (  0, 118, 204),
            (  0, 204, 181), ( 41, 255, 204), ( 91, 222,  98), (152, 235,  53),
        ]
        self._rgb_preset_row = ctk.CTkFrame(parent, fg_color="transparent")
        preset_row = self._rgb_preset_row
        preset_row.pack(fill="x", padx=10, pady=(2, 4))
        _lbl = ctk.CTkLabel(preset_row, text=self.T("makalu_rgb_presets"),
                            font=("Helvetica", 11), text_color=FG2, width=120, anchor="w")
        _lbl.pack(side="left")
        self._reg(_lbl, "makalu_rgb_presets")
        swatch_frame = ctk.CTkFrame(preset_row, fg_color="transparent")
        swatch_frame.pack(side="left")
        for rgb in _PRESETS:
            hex_col = _rgb_hex(rgb)
            btn = ctk.CTkButton(
                swatch_frame, text="", width=22, height=22, corner_radius=3,
                fg_color=hex_col, hover_color=hex_col,
                command=lambda c=rgb: self._apply_preset(c))
            btn.pack(side="left", padx=1)

        # ── Apply row ────────────────────────────────────────────────────────
        self._rgb_apply_row = ctk.CTkFrame(parent, fg_color="transparent")
        self._rgb_apply_row.pack(fill="x", padx=10, pady=(6, 10))
        _btn = ctk.CTkButton(
            self._rgb_apply_row, text=self.T("makalu_apply"),
            font=("Helvetica", 11), fg_color=BLUE, hover_color="#0284c7",
            text_color=FG, width=120, height=32, command=self._apply_rgb,
        )
        _btn.pack(side="left")
        self._reg(_btn, "makalu_apply")
        self._rgb_status = ctk.CTkLabel(self._rgb_apply_row, text="",
                                         text_color=FG2, font=("Helvetica", 11))
        self._rgb_status.pack(side="left", padx=(10, 0))

        self._rgb_update_controls()

    # ── Custom RGB ────────────────────────────────────────────────────────────

    def _build_custom_content(self, parent):
        _btn = ctk.CTkButton(
            parent, text=self.T("makalu_custom_open"),
            font=("Helvetica", 12, "bold"), fg_color=BLUE, hover_color="#0284c7",
            text_color=FG, width=200, height=36,
            command=self._open_custom_rgb,
        )
        _btn.pack(pady=20)
        self._reg(_btn, "makalu_custom_open")

    # ── DPI ───────────────────────────────────────────────────────────────────

    def _build_dpi_section(self, scroll):
        s = _PlaceholderSection(scroll, self._app, "🎯", self.T("makalu_dpi_title"),
                                on_open=self._dpi_start_poll,
                                on_close=self._dpi_stop_poll)
        self._sections.append(s)
        self._section_titles.append((s, "makalu_dpi_title"))
        self._dpi_section = s
        self._dpi_poll_id = None
        self._build_dpi_content(s.content)

    def _dpi_start_poll(self):
        """Start DPI polling — called when DPI section is opened."""
        if self._dpi_poll_id is not None:
            return  # already running
        self._dpi_load_from_device()
        self._dpi_poll()

    def _dpi_stop_poll(self):
        """Stop DPI polling — called when DPI section is closed."""
        if self._dpi_poll_id is not None:
            self.after_cancel(self._dpi_poll_id)
            self._dpi_poll_id = None

    def _build_dpi_content(self, parent):
        self._dpi_values = _load_makalu_dpi()
        self._dpi_active = 0  # currently selected level index

        # Level buttons
        prof_row = ctk.CTkFrame(parent, fg_color="transparent")
        prof_row.pack(fill="x", padx=10, pady=(12, 6))
        self._dpi_prof_btns = []
        for i in range(5):
            btn = ctk.CTkButton(
                prof_row, text=f"L{i+1}\n—",
                font=("Helvetica", 10), width=54, height=46, corner_radius=6,
                fg_color=BLUE if i == 0 else BG3,
                hover_color="#0284c7",
                text_color=FG,
                command=lambda idx=i: self._dpi_select_profile(idx),
            )
            btn.pack(side="left", padx=3)
            self._dpi_prof_btns.append(btn)

        # Slider row
        slider_row = ctk.CTkFrame(parent, fg_color="transparent")
        slider_row.pack(fill="x", padx=10, pady=(4, 4))
        self._dpi_slider_var = tk.DoubleVar(value=self._dpi_values[0])
        ctk.CTkSlider(
            slider_row, from_=50, to=19000, number_of_steps=379,
            variable=self._dpi_slider_var,
            command=self._on_dpi_slider,
            width=180, height=16,
            button_color=BLUE, button_hover_color="#0284c7",
            progress_color=BLUE, fg_color=BG2,
        ).pack(side="left")
        self._dpi_entry_var = tk.StringVar(value=str(self._dpi_values[0]))
        self._dpi_entry = ctk.CTkEntry(
            slider_row, textvariable=self._dpi_entry_var,
            width=72, height=26, font=("Helvetica", 11, "bold"),
            fg_color=BG2, text_color=FG, border_color=BG3,
            justify="center",
        )
        self._dpi_entry.pack(side="left", padx=(8, 0))
        self._dpi_entry.bind("<Return>", self._on_dpi_entry)
        self._dpi_entry.bind("<FocusOut>", self._on_dpi_entry)

        # Apply row
        apply_row = ctk.CTkFrame(parent, fg_color="transparent")
        apply_row.pack(fill="x", padx=10, pady=(4, 12))
        _btn = ctk.CTkButton(
            apply_row, text=self.T("makalu_apply"),
            font=("Helvetica", 11), fg_color=BLUE, hover_color="#0284c7",
            text_color=FG, width=80, height=28, corner_radius=5,
            command=self._apply_dpi,
        )
        _btn.pack(side="left")
        self._reg(_btn, "makalu_apply")
        _btn = ctk.CTkButton(
            apply_row, text=self.T("makalu_reset_btn"),
            font=("Helvetica", 11), fg_color=BG3, hover_color=BG2,
            text_color=FG2, width=60, height=28, corner_radius=5,
            command=self._reset_dpi,
        )
        _btn.pack(side="left", padx=(6, 0))
        self._reg(_btn, "makalu_reset_btn")
        self._dpi_status = ctk.CTkLabel(apply_row, text="",
                                         font=("Helvetica", 10), text_color=FG2)
        self._dpi_status.pack(side="left", padx=(8, 0))

        self._dpi_update_btn_labels()
        self._dpi_load_from_device()

    def _dpi_update_btn_labels(self):
        for i, btn in enumerate(self._dpi_prof_btns):
            dpi = self._dpi_values[i]
            label = f"L{i+1}\n{dpi}"
            btn.configure(
                text=label,
                fg_color=BLUE if i == self._dpi_active else BG3,
            )

    def _dpi_select_profile(self, idx):
        self._dpi_active = idx
        dpi = self._dpi_values[idx]
        self._dpi_slider_var.set(dpi)
        self._dpi_entry_var.set(str(dpi))
        self._dpi_update_btn_labels()

    def _on_dpi_slider(self, val):
        dpi = round(float(val) / 50) * 50
        dpi = max(50, min(19000, dpi))
        self._dpi_values[self._dpi_active] = dpi
        self._dpi_entry_var.set(str(dpi))
        self._dpi_update_btn_labels()

    def _on_dpi_entry(self, _event=None):
        try:
            dpi = int(self._dpi_entry_var.get())
        except ValueError:
            dpi = self._dpi_values[self._dpi_active]
        dpi = round(max(50, min(19000, dpi)) / 50) * 50
        self._dpi_values[self._dpi_active] = dpi
        self._dpi_entry_var.set(str(dpi))
        self._dpi_slider_var.set(dpi)
        self._dpi_update_btn_labels()

    def _fetch_dpi(self, on_result):
        """Run 'dpi get' in a thread; call on_result(values, active) on success."""
        def _worker():
            try:
                cmd = self._cmd("dpi", "get")
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip():
                    parts = r.stdout.strip().split()
                    if len(parts) == 6:
                        values = [int(p) for p in parts[:5]]
                        active = int(parts[5])
                        self.after(0, lambda v=values, a=active: on_result(v, a))
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def _dpi_load_from_device(self):
        self._fetch_dpi(self._dpi_apply_loaded)

    def _dpi_apply_loaded(self, values, active=None):
        self._dpi_values = values
        if active is not None:
            self._dpi_active = active
        dpi = values[self._dpi_active]
        self._dpi_slider_var.set(dpi)
        self._dpi_entry_var.set(str(dpi))
        self._dpi_update_btn_labels()
        self.after(50, self._dpi_section.measure)

    def _reset_dpi(self):
        self._dpi_apply_loaded(list(DPI_DEFAULTS))

    def _dpi_poll(self):
        self._dpi_poll_id = None  # consumed — will be reset by after() below
        def _on_result(_, active):
            if active != self._dpi_active:
                self._dpi_select_profile(active)
        self._fetch_dpi(_on_result)
        self._dpi_poll_id = self.after(1500, self._dpi_poll)

    def _apply_dpi(self):
        self._on_dpi_entry()  # flush any typed value before reading _dpi_values
        values = list(self._dpi_values)
        self._dpi_status.configure(text="...", text_color=FG2)
        cmd = self._cmd("dpi", *[str(v) for v in values], str(self._dpi_active + 1))
        def _on_done(ok, _):
            if ok:
                _save_makalu_dpi(values)
            self._dpi_status.configure(
                text=self.T("makalu_dpi_applied") if ok else self.T("makalu_dpi_failed"),
                text_color=GRN if ok else RED,
            )
        self._run_async(cmd, on_done=_on_done)

    # ── Button Remap ──────────────────────────────────────────────────────────

    # Categories with their functions
    _REMAP_CATEGORIES = {
        "Mouse":  ["left", "right", "middle", "back", "forward", "disabled"],
        "DPI":    ["dpi+", "dpi-", "disabled"],
        "Scroll": ["scroll_up", "scroll_down", "disabled"],
        "Sniper": ["sniper"],
    }

    _REMAP_BTN_NAMES = {
        "1": "Left Btn",
        "2": "Right Btn",
        "3": "Middle Btn",
        "4": "Back",
        "5": "Forward",
        "6": "DPI+",
    }

    # ── Remap translation helpers ─────────────────────────────────────────────

    _FN_LANG_KEYS = {
        "left":        "makalu_remap_fn_left",
        "right":       "makalu_remap_fn_right",
        "middle":      "makalu_remap_fn_middle",
        "back":        "makalu_remap_fn_back",
        "forward":     "makalu_remap_fn_forward",
        "dpi+":        "makalu_remap_fn_dpi_plus",
        "dpi-":        "makalu_remap_fn_dpi_minus",
        "scroll_up":   "makalu_remap_fn_scroll_up",
        "scroll_down": "makalu_remap_fn_scroll_down",
        "disabled":    "makalu_remap_fn_disabled",
        "sniper":      "makalu_remap_fn_sniper",
    }
    _BTN_LANG_KEYS = {
        "1": "makalu_remap_btn_left",
        "2": "makalu_remap_btn_right",
        "3": "makalu_remap_btn_middle",
        "4": "makalu_remap_btn_back",
        "5": "makalu_remap_btn_forward",
        "6": "makalu_remap_btn_dpi",
    }
    _CAT_LANG_KEYS = {
        "Mouse":  "makalu_remap_cat_mouse",
        "DPI":    "makalu_remap_cat_dpi",
        "Scroll": "makalu_remap_cat_scroll",
        "Sniper": "makalu_remap_cat_sniper",
    }

    def _t_fn(self, key):
        return self.T(self._FN_LANG_KEYS.get(key, key))

    def _t_btn(self, key):
        return self.T(self._BTN_LANG_KEYS.get(key, key))

    def _t_cat(self, cat):
        return self.T(self._CAT_LANG_KEYS.get(cat, cat))

    def _build_remap_section(self, scroll):
        s = _PlaceholderSection(scroll, self._app, "🖱", self.T("makalu_remap_title"))
        self._sections.append(s)
        self._section_titles.append((s, "makalu_remap_title"))
        self._remap_section = s
        self._build_remap_content(s.content)

    def _build_remap_content(self, parent):
        self._remap_assignments = _load_makalu_remap()
        self._remap_active = "1"  # selected physical button
        self._remap_cat_key = "Mouse"  # internal category key (always English)
        self._remap_current_fn_keys = list(self._REMAP_CATEGORIES["Mouse"])

        # ── Physical button grid ─────────────────────────────────────────────
        grid_card = ctk.CTkFrame(parent, fg_color=BG3, corner_radius=8)
        grid_card.pack(fill="x", padx=10, pady=(12, 6))
        btn_grid = ctk.CTkFrame(grid_card, fg_color="transparent")
        btn_grid.pack(padx=8, pady=8)
        self._remap_btns = {}
        for i, key in enumerate(self._REMAP_BTN_NAMES.keys()):
            col = i % 3
            row = i // 3
            assignment_val = self._remap_assignments.get(key, "left")
            btn = ctk.CTkButton(
                btn_grid,
                text=self._remap_btn_text(key, assignment_val),
                font=("Helvetica", 10), width=96, height=46, corner_radius=6,
                fg_color=BLUE if key == self._remap_active else BG2,
                hover_color="#0284c7", text_color=FG,
                command=lambda k=key: self._remap_select_btn(k),
            )
            btn.grid(row=row, column=col, padx=4, pady=3)
            self._remap_btns[key] = btn

        # ── Assignment card ──────────────────────────────────────────────────
        assign_card = ctk.CTkFrame(parent, fg_color=BG3, corner_radius=8)
        assign_card.pack(fill="x", padx=10, pady=(0, 6))

        # Category row
        cat_row = ctk.CTkFrame(assign_card, fg_color="transparent")
        cat_row.pack(fill="x", padx=12, pady=(10, 4))
        _lbl = ctk.CTkLabel(cat_row, text=self.T("makalu_remap_category"),
                            font=("Helvetica", 11), text_color=FG2, width=72, anchor="w")
        _lbl.pack(side="left")
        self._reg(_lbl, "makalu_remap_category")
        cat_keys = list(self._REMAP_CATEGORIES.keys())
        cat_labels = [self._t_cat(k) for k in cat_keys]
        self._remap_cat_var = tk.StringVar(value=cat_labels[0])
        self._remap_cat_dd = ctk.CTkOptionMenu(
            cat_row, variable=self._remap_cat_var,
            values=cat_labels,
            command=self._on_remap_cat,
            width=180, height=30, font=("Helvetica", 11),
            fg_color=BG2, button_color=BG2, button_hover_color=BG,
            text_color=FG, dropdown_fg_color=BG2,
        )
        self._remap_cat_dd.pack(side="left", padx=(6, 0))

        # Function row
        fn_row = ctk.CTkFrame(assign_card, fg_color="transparent")
        fn_row.pack(fill="x", padx=12, pady=(0, 4))
        _lbl = ctk.CTkLabel(fn_row, text=self.T("makalu_remap_function"),
                            font=("Helvetica", 11), text_color=FG2, width=72, anchor="w")
        _lbl.pack(side="left")
        self._reg(_lbl, "makalu_remap_function")
        init_fn_labels = [self._t_fn(k) for k in self._remap_current_fn_keys]
        self._remap_fn_var = tk.StringVar(value=init_fn_labels[0])
        self._remap_fn_dd = ctk.CTkOptionMenu(
            fn_row, variable=self._remap_fn_var,
            values=init_fn_labels,
            width=180, height=30, font=("Helvetica", 11),
            fg_color=BG2, button_color=BG2, button_hover_color=BG,
            text_color=FG, dropdown_fg_color=BG2,
        )
        self._remap_fn_dd.pack(side="left", padx=(6, 0))

        # Sniper DPI row (shown only when Sniper category selected)
        self._sniper_row = ctk.CTkFrame(assign_card, fg_color="transparent")
        self._sniper_row.pack(fill="x", padx=12, pady=(0, 4))
        _lbl = ctk.CTkLabel(self._sniper_row, text=self.T("makalu_remap_sniper_dpi"),
                            font=("Helvetica", 11), text_color=FG2, width=72, anchor="w")
        _lbl.pack(side="left")
        self._reg(_lbl, "makalu_remap_sniper_dpi")
        self._sniper_dpi = 400
        self._sniper_slider_var = tk.DoubleVar(value=400)
        ctk.CTkSlider(
            self._sniper_row, from_=50, to=19000, number_of_steps=379,
            variable=self._sniper_slider_var,
            command=self._on_sniper_slider,
            width=110, height=16,
            button_color=BLUE, button_hover_color="#0284c7",
            progress_color=BLUE, fg_color=BG2,
        ).pack(side="left", padx=(6, 0))
        self._sniper_entry_var = tk.StringVar(value="400")
        self._sniper_entry = ctk.CTkEntry(
            self._sniper_row, textvariable=self._sniper_entry_var,
            width=66, height=28, font=("Helvetica", 11, "bold"),
            fg_color=BG2, text_color=FG, border_color=BG3, justify="center",
        )
        self._sniper_entry.pack(side="left", padx=(8, 0))
        self._sniper_entry.bind("<Return>", self._on_sniper_entry)
        self._sniper_entry.bind("<FocusOut>", self._on_sniper_entry)
        self._sniper_row.pack_forget()  # hidden by default

        # Apply row
        self._remap_apply_row = ctk.CTkFrame(assign_card, fg_color="transparent")
        apply_row = self._remap_apply_row
        apply_row.pack(fill="x", padx=12, pady=(6, 10))
        _btn = ctk.CTkButton(
            apply_row, text=self.T("makalu_apply"),
            font=("Helvetica", 11), fg_color=BLUE, hover_color="#0284c7",
            text_color=FG, width=80, height=28, corner_radius=5,
            command=self._apply_remap,
        )
        _btn.pack(side="left")
        self._reg(_btn, "makalu_apply")
        _btn = ctk.CTkButton(
            apply_row, text=self.T("makalu_reset_btn"),
            font=("Helvetica", 11), fg_color=BG2, hover_color=BG,
            text_color=FG2, width=60, height=28, corner_radius=5,
            command=self._reset_remap,
        )
        _btn.pack(side="left", padx=(6, 0))
        self._reg(_btn, "makalu_reset_btn")
        self._remap_status = ctk.CTkLabel(apply_row, text="",
                                           font=("Helvetica", 10), text_color=FG2)
        self._remap_status.pack(side="left", padx=(10, 0))

        # Set dropdowns to current assignment of button 1
        self._remap_sync_dropdowns("1")

    def _remap_select_btn(self, key):
        self._remap_active = key
        for k, btn in self._remap_btns.items():
            btn.configure(fg_color=BLUE if k == key else BG3)
        self._remap_sync_dropdowns(key)

    def _remap_sync_dropdowns(self, key):
        """Set category+function dropdowns to match the current assignment of key."""
        raw = self._remap_assignments.get(key, "left")
        # Sniper stored as "sniper:<dpi>"
        if raw.startswith("sniper:"):
            try:
                dpi = int(raw.split(":")[1])
            except (IndexError, ValueError):
                dpi = 400
            self._sniper_dpi = dpi
            self._sniper_slider_var.set(dpi)
            self._sniper_entry_var.set(str(dpi))
            fn_key = "sniper"
        else:
            fn_key = raw
        for cat_key, fns in self._REMAP_CATEGORIES.items():
            if fn_key in fns:
                self._remap_cat_key = cat_key
                self._remap_cat_var.set(self._t_cat(cat_key))
                self._remap_current_fn_keys = list(fns)
                labels = [self._t_fn(k) for k in fns]
                self._remap_fn_dd.configure(values=labels)
                self._remap_fn_var.set(self._t_fn(fn_key))
                self._update_sniper_row_visibility(cat_key)
                return
        # Fallback
        self._remap_cat_key = "Mouse"
        self._remap_cat_var.set(self._t_cat("Mouse"))
        self._on_remap_cat(self._t_cat("Mouse"))

    def _remap_btn_text(self, key, assignment_val):
        """Build the button grid label for a physical button."""
        if assignment_val.startswith("sniper:"):
            dpi = assignment_val.split(":")[1]
            return f"{self._t_btn(key)}\n{self.T('makalu_remap_fn_sniper')} {dpi}"
        return f"{self._t_btn(key)}\n{self._t_fn(assignment_val)}"

    def _update_sniper_row_visibility(self, cat_key=None):
        if cat_key is None:
            cat_key = self._remap_cat_key
        if cat_key == "Sniper":
            self._sniper_row.pack(fill="x", padx=10, pady=(2, 0),
                                  before=self._remap_apply_row)
        else:
            self._sniper_row.pack_forget()
        self.after(50, self._remap_section.measure)

    def _on_sniper_slider(self, val):
        dpi = round(float(val) / 50) * 50
        dpi = max(50, min(19000, dpi))
        self._sniper_dpi = dpi
        self._sniper_entry_var.set(str(dpi))

    def _on_sniper_entry(self, _event=None):
        try:
            dpi = int(self._sniper_entry_var.get())
        except ValueError:
            dpi = self._sniper_dpi
        dpi = round(max(50, min(19000, dpi)) / 50) * 50
        self._sniper_dpi = dpi
        self._sniper_entry_var.set(str(dpi))
        self._sniper_slider_var.set(dpi)

    def _on_remap_cat(self, cat_label):
        # Reverse-map translated label → internal key
        cat_key = next(
            (k for k in self._REMAP_CATEGORIES if self._t_cat(k) == cat_label),
            self._remap_cat_key,
        )
        self._remap_cat_key = cat_key
        fns = self._REMAP_CATEGORIES.get(cat_key, [])
        self._remap_current_fn_keys = list(fns)
        labels = [self._t_fn(k) for k in fns]
        self._remap_fn_dd.configure(values=labels)
        self._remap_fn_var.set(labels[0] if labels else "")
        self._update_sniper_row_visibility(cat_key)

    def _apply_remap(self):
        key = self._remap_active
        fn_label = self._remap_fn_var.get()
        try:
            idx = list(self._remap_fn_dd.cget("values")).index(fn_label)
            fn_key = self._remap_current_fn_keys[idx]
        except (ValueError, IndexError):
            return
        old_raw = self._remap_assignments.get(key, key)
        self._remap_status.configure(text="...", text_color=FG2)

        if fn_key == "sniper":
            self._on_sniper_entry()
            dpi = self._sniper_dpi
            assignment_val = f"sniper:{dpi}"
            cmd = self._cmd("sniper", key, str(dpi))
        else:
            assignment_val = fn_key
            cmd = self._cmd("remap", key, fn_key)

        def _on_done(ok, _):
            if not ok:
                self._remap_status.configure(text=self.T("makalu_failed"), text_color=RED)
                return
            self._remap_assignments[key] = assignment_val
            _save_makalu_remap(self._remap_assignments)
            self._remap_btns[key].configure(
                text=self._remap_btn_text(key, assignment_val)
            )
            if key == "1" and fn_key != "left":
                self._remap_confirm_dialog(key, fn_key, old_raw)
            else:
                self._remap_status.configure(text=self.T("makalu_remap_applied"), text_color=GRN)
        self._run_async(cmd, on_done=_on_done)

    def _remap_confirm_dialog(self, key, fn_key, old_fn_key, seconds=10):
        """Countdown dialog after remapping left button — auto-reverts if not confirmed."""
        dlg = ctk.CTkToplevel(self)
        dlg.title(self.T("makalu_remap_keep_title"))
        dlg.geometry("320x150")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG)
        dlg.attributes("-topmost", True)

        self._remap_countdown = seconds
        lbl = ctk.CTkLabel(
            dlg,
            text=self.T("makalu_remap_keep_text").format(n=seconds),
            font=("Helvetica", 12), text_color=FG, wraplength=280,
        )
        lbl.pack(pady=(20, 12))

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack()

        confirmed = [False]

        def _confirm():
            confirmed[0] = True
            dlg.destroy()
            self._remap_status.configure(text=self.T("makalu_remap_applied"), text_color=GRN)

        def _revert():
            dlg.destroy()
            self._remap_status.configure(text="...", text_color=FG2)
            revert_cmd = self._cmd("remap", key, old_fn_key)
            def _on_revert(ok, _):
                if ok:
                    self._remap_assignments[key] = old_fn_key
                    _save_makalu_remap(self._remap_assignments)
                    self._remap_btns[key].configure(
                        text=self._remap_btn_text(key, old_fn_key)
                    )
                    self._remap_sync_dropdowns(key)
                self._remap_status.configure(
                    text=self.T("makalu_remap_reverted") if ok else self.T("makalu_failed"),
                    text_color=FG2 if ok else RED,
                )
            self._run_async(revert_cmd, on_done=_on_revert)

        ctk.CTkButton(dlg, text=self.T("makalu_remap_keep_btn"),
                      fg_color=BLUE, hover_color="#0284c7",
                      text_color=FG, width=100, height=30,
                      command=_confirm).pack(side="left", padx=8, in_=btn_row)
        ctk.CTkButton(dlg, text=self.T("makalu_remap_revert_btn"),
                      fg_color=BG3, hover_color=BG2,
                      text_color=FG2, width=110, height=30,
                      command=_revert).pack(side="left", padx=8, in_=btn_row)

        def _tick():
            if confirmed[0] or not dlg.winfo_exists():
                return
            self._remap_countdown -= 1
            if self._remap_countdown <= 0:
                _revert()
                return
            lbl.configure(text=self.T("makalu_remap_keep_text").format(n=self._remap_countdown))
            dlg.after(1000, _tick)

        dlg.after(1000, _tick)

    def _reset_remap(self):
        from shared.config import REMAP_DEFAULTS
        for key, fn_key in REMAP_DEFAULTS.items():
            self._remap_assignments[key] = fn_key
            self._remap_btns[key].configure(
                text=self._remap_btn_text(key, fn_key)
            )
        self._remap_sync_dropdowns(self._remap_active)
        _save_makalu_remap(self._remap_assignments)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _build_settings_section(self, scroll):
        s = _PlaceholderSection(scroll, self._app, "⚙", self.T("makalu_settings_title"))
        self._sections.append(s)
        self._section_titles.append((s, "makalu_settings_title"))
        self._settings_section = s
        self._build_settings_content(s.content)

    def _settings_card(self, parent, label, pady=(2, 2)):
        """Returns (card_frame, right_frame, label_widget)."""
        card = ctk.CTkFrame(parent, fg_color=BG3, corner_radius=6)
        card.pack(fill="x", padx=10, pady=pady)
        lbl = ctk.CTkLabel(card, text=label, font=("Helvetica", 11, "bold"),
                           text_color=FG2, width=130, anchor="w")
        lbl.pack(side="left", padx=(10, 6), pady=10)
        right = ctk.CTkFrame(card, fg_color="transparent")
        right.pack(side="left", fill="x", expand=True, padx=(0, 8))
        return card, right, lbl

    def _status_lbl(self, parent):
        lbl = ctk.CTkLabel(parent, text="", font=("Helvetica", 10),
                           text_color=FG2, width=60, anchor="w")
        lbl.pack(side="left", padx=(6, 4))
        return lbl

    def _apply_btn(self, parent, cmd):
        btn = ctk.CTkButton(
            parent, text=self.T("makalu_apply"), font=("Helvetica", 11),
            fg_color=BLUE, hover_color="#0284c7", text_color=FG,
            width=70, height=28, corner_radius=5, command=cmd,
        )
        btn.pack(side="left", padx=(8, 0))
        self._reg(btn, "makalu_apply")
        return btn

    def _build_settings_content(self, parent):
        # ── Section header: Mouse Hardware ────────────────────────────────────
        _hdr = ctk.CTkLabel(parent, text="  " + self.T("makalu_setting_header"),
                            font=("Helvetica", 9, "bold"), text_color=FG2)
        _hdr.pack(anchor="w", padx=12, pady=(10, 2))
        self._settings_header_lbl = _hdr

        # Polling Rate
        _, r, _lbl = self._settings_card(parent, self.T("makalu_setting_poll"))
        self._reg(_lbl, "makalu_setting_poll")
        self._poll_var = tk.StringVar(value="1000 Hz")
        ctk.CTkSegmentedButton(
            r, values=["125 Hz", "250 Hz", "500 Hz", "1000 Hz"],
            variable=self._poll_var,
            fg_color=BG2, selected_color=BLUE, selected_hover_color="#0284c7",
            unselected_color=BG2, unselected_hover_color=BG3,
            text_color=FG, font=("Helvetica", 11), height=28,
        ).pack(side="left")
        self._apply_btn(r, self._apply_polling_rate)
        self._poll_status = self._status_lbl(r)

        # Button Response
        _, r, _lbl = self._settings_card(parent, self.T("makalu_setting_debounce"))
        self._reg(_lbl, "makalu_setting_debounce")
        self._debounce_ms = 2
        ctk.CTkSlider(
            r, from_=0, to=5, number_of_steps=5,
            variable=tk.DoubleVar(value=0),
            command=self._on_debounce_slider,
            width=140, height=16,
            button_color=BLUE, button_hover_color="#0284c7",
            progress_color=BLUE, fg_color=BG2,
        ).pack(side="left", padx=(0, 6))
        self._debounce_val_lbl = ctk.CTkLabel(r, text="2 ms", width=38,
                                               font=("Helvetica", 11), text_color=FG)
        self._debounce_val_lbl.pack(side="left")
        self._apply_btn(r, self._apply_debounce)
        self._debounce_status = self._status_lbl(r)

        # Angle Snapping
        _, r, _lbl = self._settings_card(parent, self.T("makalu_setting_angle"))
        self._reg(_lbl, "makalu_setting_angle")
        self._angle_snap_var = tk.StringVar(value="Off")
        ctk.CTkSegmentedButton(
            r, values=["Off", "On"],
            variable=self._angle_snap_var,
            command=lambda _: self._apply_angle_snapping(),
            fg_color=BG2, selected_color=BLUE, selected_hover_color="#0284c7",
            unselected_color=BG2, unselected_hover_color=BG3,
            text_color=FG, font=("Helvetica", 11), height=28,
        ).pack(side="left")
        self._angle_snap_status = self._status_lbl(r)

        # Lift-off Distance
        _, r, _lbl = self._settings_card(parent, self.T("makalu_setting_liftoff"))
        self._reg(_lbl, "makalu_setting_liftoff")
        self._liftoff_var = tk.StringVar(value="Low")
        ctk.CTkSegmentedButton(
            r, values=["Low", "High"],
            variable=self._liftoff_var,
            command=lambda _: self._apply_liftoff(),
            fg_color=BG2, selected_color=BLUE, selected_hover_color="#0284c7",
            unselected_color=BG2, unselected_hover_color=BG3,
            text_color=FG, font=("Helvetica", 11), height=28,
        ).pack(side="left")
        self._liftoff_status = self._status_lbl(r)


    def _on_debounce_slider(self, val):
        self._debounce_ms = [2, 4, 6, 8, 10, 12][round(float(val))]
        self._debounce_val_lbl.configure(text=f"{self._debounce_ms} ms")

    def _apply_debounce(self):
        ms = self._debounce_ms
        self._debounce_status.configure(text="...", text_color=FG2)
        cmd = self._cmd("debounce", str(ms))
        self._run_async(cmd, on_done=lambda ok, _: self._debounce_status.configure(
            text=self.T("makalu_applied") if ok else self.T("makalu_failed"),
            text_color=GRN if ok else RED,
        ))

    def _apply_liftoff(self):
        high = self._liftoff_var.get() == "High"
        self._liftoff_status.configure(text="...", text_color=FG2)
        cmd = self._cmd("lift-off", "high" if high else "low")
        self._run_async(cmd, on_done=lambda ok, _: self._liftoff_status.configure(
            text=self.T("makalu_applied") if ok else self.T("makalu_failed"),
            text_color=GRN if ok else RED,
        ))

    def _apply_angle_snapping(self):
        enabled = self._angle_snap_var.get() == "On"
        self._angle_snap_status.configure(text="...", text_color=FG2)
        cmd = self._cmd("angle-snapping", "on" if enabled else "off")
        self._run_async(cmd, on_done=lambda ok, _: self._angle_snap_status.configure(
            text=self.T("makalu_applied") if ok else self.T("makalu_failed"),
            text_color=GRN if ok else RED,
        ))

    def _apply_polling_rate(self):
        hz = int(self._poll_var.get().split()[0])
        self._poll_status.configure(text="...", text_color=FG2)
        cmd = self._cmd("polling-rate", str(hz))
        self._run_async(cmd, on_done=lambda ok, _: self._poll_status.configure(
            text=self.T("makalu_applied") if ok else self.T("makalu_failed"),
            text_color=GRN if ok else RED,
        ))


    def _open_custom_rgb(self):
        if self._custom_rgb_win is not None and self._custom_rgb_win.winfo_exists():
            self._custom_rgb_win.focus()
            return
        self._custom_rgb_win = MakaluCustomRGBWindow(self._app, self)

    def _rgb_update_controls(self):
        name = self._rgb_mode_var.get()
        _, hs, hc1, hc2, hd = _EFFECT_MAP.get(name, (0, False, False, False, False))
        self._rgb_speed_seg.configure(state="normal" if hs else "disabled")
        self._rgb_c1_btn.configure(state="normal" if hc1 else "disabled")
        if hd:
            self._rgb_dir_row.pack(fill="x", padx=10, pady=2,
                                   after=self._rgb_speed_row)
        else:
            self._rgb_dir_row.pack_forget()
        if hc1:
            self._rgb_preset_row.pack(fill="x", padx=10, pady=(2, 4))
        else:
            self._rgb_preset_row.pack_forget()
        if hc2:
            self._rgb_c2_lbl.pack(side="left", padx=(0, 4))
            self._rgb_c2_btn.pack(side="left")
        else:
            self._rgb_c2_lbl.pack_forget()
            self._rgb_c2_btn.pack_forget()
        if hasattr(self, "_rgb_section"):
            self._app.update_idletasks()
            s = self._rgb_section
            was_open = s._open
            s.measure()
            if was_open:
                s._content.configure(height=s._natural_h)

    def _pick_rgb_color(self, which):
        initial = self._rgb_color1 if which == 1 else self._rgb_color2
        rgb = pick_color(self._app, initial_rgb=initial, title="Pick Color",
                         show_brightness=False)
        if rgb is None:
            return
        h = _rgb_hex(rgb)
        if which == 1:
            self._rgb_color1 = rgb
            self._rgb_c1_btn.configure(fg_color=h, hover_color=h)
        else:
            self._rgb_color2 = rgb
            self._rgb_c2_btn.configure(fg_color=h, hover_color=h)
        self._apply_rgb()

    def _apply_preset(self, rgb):
        h = _rgb_hex(rgb)
        self._rgb_color1 = rgb
        self._rgb_c1_btn.configure(fg_color=h, hover_color=h)
        self._apply_rgb()

    def _apply_rgb(self):
        if getattr(self, "_applying", False):
            return
        self._applying = True

        name = self._rgb_mode_var.get()
        code, hs, hc1, hc2, hd = _EFFECT_MAP.get(name, (0, False, False, False, False))
        self._rgb_status.configure(text="Applying…", text_color=YLW)

        r1, g1, b1 = self._rgb_color1
        r2, g2, b2 = self._rgb_color2
        bri = {"0%": 0, "25%": 25, "50%": 50, "75%": 75, "100%": 100}.get(self._rgb_bri_var.get(), 100)
        spd = {"Slow": 0, "Medium": 1, "Fast": 2}.get(self._rgb_speed_var.get(), 1) if hs else 0
        dir_ = 1 if self._rgb_dir_var.get() == "→" else 0

        if hc2:
            cmd = self._cmd("rgb", "code2", str(code),
                            str(r1), str(g1), str(b1),
                            str(r2), str(g2), str(b2), str(bri), str(spd), str(dir_))
        else:
            cmd = self._cmd("rgb", "code", str(code),
                            str(r1), str(g1), str(b1), str(bri), str(spd), str(dir_))

        def _done(ok, msg):
            self._applying = False
            if ok:
                self._rgb_status.configure(text=self.T("makalu_applied") + " ✓", text_color=GRN)
                self.after(3000, lambda: self._rgb_status.configure(text=""))
            else:
                self._rgb_status.configure(
                    text=f"{self.T('makalu_failed')}: {msg[:50]}" if msg else self.T("makalu_failed"),
                    text_color=RED)

        self._run_async(cmd, _done)

    # ── Public interface ──────────────────────────────────────────────────────

    def set_connected(self, connected: bool):
        """Show or hide the 'not connected' banner."""
        self._connected = connected
        if connected:
            self._banner.pack_forget()
        else:
            self._banner.pack(fill="x", padx=12, pady=(8, 4),
                              before=self.winfo_children()[1])

    def apply_lang(self):
        """Called by App when language changes."""
        # Section titles
        for section, key in self._section_titles:
            section.set_title(self.T(key))

        # Settings MOUSE header (has leading spaces, not a plain _reg)
        self._settings_header_lbl.configure(text="  " + self.T("makalu_setting_header"))

        # Remap button grid
        for key, btn in self._remap_btns.items():
            assignment_val = self._remap_assignments.get(key, "left")
            btn.configure(text=self._remap_btn_text(key, assignment_val))

        # Remap category dropdown
        cat_keys = list(self._REMAP_CATEGORIES.keys())
        cat_labels = [self._t_cat(k) for k in cat_keys]
        self._remap_cat_dd.configure(values=cat_labels)
        self._remap_cat_var.set(self._t_cat(self._remap_cat_key))

        # Remap function dropdown
        fns = self._REMAP_CATEGORIES.get(self._remap_cat_key, [])
        self._remap_current_fn_keys = list(fns)
        fn_labels = [self._t_fn(k) for k in fns]
        self._remap_fn_dd.configure(values=fn_labels)
        cur_fn_key = self._remap_assignments.get(self._remap_active, "left")
        if cur_fn_key in fns:
            self._remap_fn_var.set(self._t_fn(cur_fn_key))
        elif fn_labels:
            self._remap_fn_var.set(fn_labels[0])

    def _stop_cpu_proc(self):
        """No-op stub — Makalu panel has no background process."""
        return False

    def _start_cpu_auto(self):
        """No-op stub."""
        pass


# ── Makalu 67 Custom RGB Window ───────────────────────────────────────────────

class MakaluCustomRGBWindow(ctk.CTkToplevel):
    """Per-LED color editor for the Makalu 67 (8 LEDs, 4 left + 4 right)."""

    def __init__(self, app, panel):
        super().__init__(app)
        self.title("Custom RGB — Makalu 67")
        self.resizable(False, False)
        self._app   = app
        self._panel = panel
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        from shared.config import (
            _load_makalu_leds, _save_makalu_leds,
            _load_makalu_presets, _save_makalu_presets,
        )
        self._load_leds    = _load_makalu_leds
        self._save_leds    = _save_makalu_leds
        self._load_presets = _load_makalu_presets
        self._save_presets = _save_makalu_presets

        leds, bri, last_preset = _load_makalu_leds()
        self._leds        = list(leds)
        self._bri         = bri
        self._last_preset = last_preset
        self._selected   = set()
        self._fill_rgb   = (255, 0, 0)
        self._drag_rect  = None
        self._led_items  = {}    # led_idx → canvas rect id
        self._undo_stack = []

        self._build_ui()
        self.after(50, self._draw_leds)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = 12
        self.configure(fg_color=BG)

        # Canvas
        self._cv = tk.Canvas(self, width=_MOUSE_CANVAS_W, height=_MOUSE_CANVAS_H,
                             bg="#111118", highlightthickness=0, bd=0)
        self._cv.pack(padx=PAD, pady=(PAD, 4))
        self._cv.bind("<Button-1>",        self._on_click)
        self._cv.bind("<B1-Motion>",       self._on_drag)
        self._cv.bind("<ButtonRelease-1>", self._on_release)
        self._cv.bind("<Button-3>",        self._on_rclick)
        self._cv.bind("<Alt-Button-1>",    self._on_eyedrop)
        self.bind("<Control-z>", self._undo)
        self.bind("<Control-Z>", self._undo)

        # Color strip
        strip = ctk.CTkFrame(self, fg_color=BG2, corner_radius=6)
        strip.pack(fill="x", padx=PAD, pady=4)
        self._fill_swatch = tk.Canvas(strip, width=28, height=28,
                                      bg=_rgb_hex(self._fill_rgb),
                                      highlightthickness=1, highlightbackground="#555")
        self._fill_swatch.pack(side="left", padx=(8, 2), pady=6)
        ctk.CTkButton(strip, text="Pick", width=50, height=28,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._pick_fill).pack(side="left", padx=(0, 8))
        for hex_c, rgb in _QUICK_COLORS:
            btn = tk.Canvas(strip, width=20, height=20, bg=hex_c,
                            highlightthickness=1, highlightbackground="#333",
                            cursor="hand2")
            btn.pack(side="left", padx=2, pady=8)
            btn.bind("<Button-1>", lambda e, c=rgb: self._set_fill(c))
        self._sel_lbl = ctk.CTkLabel(strip, text="0 LEDs selected",
                                     text_color=FG2, font=("Helvetica", 11))
        self._sel_lbl.pack(side="right", padx=10)

        # Action buttons
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.pack(fill="x", padx=PAD, pady=4)
        ctk.CTkButton(act, text="Fill Selected", width=110, height=30,
                      fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._fill_selected).pack(side="left", padx=(0, 4))
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
                      command=lambda: self._fill_all((0, 0, 0))).pack(side="left", padx=4)
        ctk.CTkButton(act, text="All White", width=80, height=30,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=lambda: self._fill_all((255, 255, 255))).pack(side="left", padx=4)
        ctk.CTkButton(act, text="↩ Undo", width=70, height=30,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      font=("Helvetica", 11),
                      command=self._undo).pack(side="right", padx=(4, 0))
        ctk.CTkLabel(act, text="Alt+click = Eyedropper", text_color=FG2,
                     font=("Helvetica", 10)).pack(side="right", padx=8)

        # Presets
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

        # Brightness + Apply
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=PAD, pady=(4, PAD))
        ctk.CTkLabel(bot, text="Brightness:", text_color=FG2,
                     font=("Helvetica", 11)).pack(side="left")
        _bri_levels = [0, 25, 50, 75, 100]
        bri_safe = min(_bri_levels, key=lambda x: abs(x - self._bri))
        self._bri_var = tk.StringVar(value=f"{bri_safe}%")
        ctk.CTkOptionMenu(
            bot, variable=self._bri_var,
            values=["0%", "25%", "50%", "75%", "100%"],
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=100, height=28,
        ).pack(side="left", padx=(4, 16))
        ctk.CTkButton(bot, text="Apply to Mouse", width=130, height=32,
                      fg_color=BLUE, hover_color="#0284c7", text_color=FG,
                      font=("Helvetica", 11, "bold"),
                      command=self._apply).pack(side="left", padx=(0, 4))
        self._status = ctk.CTkLabel(bot, text="", text_color=FG2,
                                    font=("Helvetica", 11))
        self._status.pack(side="left", padx=10)

    # ── Canvas ────────────────────────────────────────────────────────────────

    def _draw_leds(self):
        self._cv.delete("all")
        self._led_items.clear()

        # Mouse body
        x1, y1, x2, y2 = _MOUSE_BODY
        self._cv.create_rectangle(x1, y1, x2, y2,
                                  fill="#1a1a22", outline="#444", width=2)
        self._cv.create_text((x1 + x2) // 2, (y1 + y2) // 2,
                             text="🖱️", font=("Helvetica", 48), fill="#2a2a36")

        # Side labels
        self._cv.create_text(42, 36, text="Left",  fill="#555", font=("Helvetica", 9))
        self._cv.create_text(258, 36, text="Right", fill="#555", font=("Helvetica", 9))

        # LED squares
        labels = ["L0", "L1", "L2", "L3", "R4", "R5", "R6", "R7"]
        for i, (lx1, ly1, lx2, ly2) in enumerate(_MOUSE_LED_RECTS):
            color = _rgb_hex(self._leds[i])
            sel   = i in self._selected
            item  = self._cv.create_rectangle(
                lx1, ly1, lx2, ly2,
                fill=color,
                outline="#00d4ff" if sel else "#444",
                width=2 if sel else 1)
            self._led_items[i] = item
            self._cv.create_text(
                (lx1 + lx2) // 2, (ly1 + ly2) // 2,
                text=labels[i], fill="#cccccc", font=("Helvetica", 9))

    def _refresh_led(self, idx):
        item = self._led_items.get(idx)
        if item is None:
            return
        sel = idx in self._selected
        self._cv.itemconfigure(item,
                               fill=_rgb_hex(self._leds[idx]),
                               outline="#00d4ff" if sel else "#444",
                               width=2 if sel else 1)

    def _led_at(self, ex, ey):
        for i, (lx1, ly1, lx2, ly2) in enumerate(_MOUSE_LED_RECTS):
            if lx1 <= ex <= lx2 and ly1 <= ey <= ly2:
                return i
        return None

    # ── Mouse events ──────────────────────────────────────────────────────────

    def _on_click(self, e):
        ctrl = (e.state & 0x0004) != 0
        idx  = self._led_at(e.x, e.y)
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
            self._refresh_led(idx)
        else:
            old = set(self._selected)
            self._selected = {idx}
            for i in old:
                self._refresh_led(i)
            self._refresh_led(idx)
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
        if abs(e.x - x0) < 5 and abs(e.y - y0) < 5:
            return
        rx1, rx2 = min(x0, e.x), max(x0, e.x)
        ry1, ry2 = min(y0, e.y), max(y0, e.y)
        ctrl = (e.state & 0x0004) != 0
        if not ctrl:
            old = set(self._selected)
            self._selected.clear()
            for i in old:
                self._refresh_led(i)
        for i, (lx1, ly1, lx2, ly2) in enumerate(_MOUSE_LED_RECTS):
            if lx1 < rx2 and lx2 > rx1 and ly1 < ry2 and ly2 > ry1:
                self._selected.add(i)
        for i in self._selected:
            self._refresh_led(i)
        self._update_sel_lbl()

    def _on_rclick(self, e):
        idx = self._led_at(e.x, e.y)
        if idx is None:
            return
        if idx in self._selected:
            self._selected.discard(idx)
        else:
            self._selected.add(idx)
        self._refresh_led(idx)
        self._update_sel_lbl()

    def _on_eyedrop(self, e):
        idx = self._led_at(e.x, e.y)
        if idx is not None:
            self._set_fill(self._leds[idx])

    # ── Color / selection helpers ─────────────────────────────────────────────

    def _set_fill(self, rgb):
        self._fill_rgb = rgb
        self._fill_swatch.configure(bg=_rgb_hex(rgb))

    def _pick_fill(self):
        rgb = pick_color(self, initial_rgb=tuple(self._fill_rgb), title="LED Color",
                         show_brightness=False)
        if rgb is None:
            return
        self._set_fill(rgb)
        if self._selected:
            self._fill_selected()

    def _fill_selected(self):
        if not self._selected:
            return
        self._push_undo()
        for idx in self._selected:
            self._leds[idx] = self._fill_rgb
            self._refresh_led(idx)

    def _fill_all(self, rgb):
        self._push_undo()
        self._leds = [rgb] * 8
        self._draw_leds()

    def _select_all(self):
        self._selected = set(range(8))
        self._draw_leds()
        self._update_sel_lbl()

    def _deselect_all(self):
        old = set(self._selected)
        self._selected.clear()
        for i in old:
            self._refresh_led(i)
        self._update_sel_lbl()

    def _update_sel_lbl(self):
        n = len(self._selected)
        self._sel_lbl.configure(text=f"{n} LED{'s' if n != 1 else ''} selected")

    def _push_undo(self):
        self._undo_stack.append(list(self._leds))
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)

    def _undo(self, event=None):
        if not self._undo_stack:
            return
        self._leds = self._undo_stack.pop()
        self._draw_leds()

    # ── Presets ───────────────────────────────────────────────────────────────

    def _preset_refresh(self):
        names = sorted(self._load_presets().keys())
        self._preset_combo.configure(values=names)
        current = self._preset_var.get()
        if not current:
            restore = getattr(self, "_last_preset", "")
            if restore in names:
                self._preset_combo.set(restore)
            elif names:
                self._preset_combo.set(names[0])

    def _preset_load(self):
        name = self._preset_var.get().strip()
        presets = self._load_presets()
        if name not in presets:
            self._preset_status.configure(text="Not found", text_color=RED)
            return
        self._push_undo()
        d    = presets[name]
        leds = [tuple(c) for c in d.get("leds", [])]
        self._leds = (leds + [(20, 20, 20)] * 8)[:8]
        bri = int(d.get("brightness", 100))
        self._bri_var.set(f"{bri}%")
        self._draw_leds()
        self._save_leds(self._leds, int(self._bri_var.get().replace("%", "")), name)
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
            bri = int(self._bri_var.get().replace("%", ""))
            presets = self._load_presets()
            presets[name] = {"leds": [list(c) for c in self._leds], "brightness": bri}
            self._save_presets(presets)
            self._preset_refresh()
            self._preset_combo.set(name)
            self._save_leds(self._leds, bri, name)
            self._preset_status.configure(text=f'Saved "{name}"', text_color=GRN)
            dlg.destroy()
        entry.bind("<Return>", lambda e: _save())
        ctk.CTkButton(dlg, text="Save", width=80, height=28,
                      fg_color=BLUE, text_color=FG, command=_save).pack(pady=8)

    def _preset_delete(self):
        name = self._preset_var.get().strip()
        presets = self._load_presets()
        if name not in presets:
            self._preset_status.configure(text="Not found", text_color=RED)
            return
        del presets[name]
        self._save_presets(presets)
        self._preset_refresh()
        remaining = sorted(presets.keys())
        self._preset_combo.set(remaining[0] if remaining else "")
        self._preset_status.configure(text=f'Deleted "{name}"', text_color=FG2)

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _apply(self):
        self._status.configure(text="Sending…", text_color=YLW)
        self.update_idletasks()
        bri = int(self._bri_var.get().replace("%", ""))
        flat = []
        for r, g, b in self._leds:
            flat.extend([str(r), str(g), str(b)])
        cmd = self._panel._cmd("rgb", "custom", *flat, str(bri))
        self._save_leds(self._leds, bri, self._preset_var.get())

        def run():
            try:
                r  = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                ok = r.returncode == 0 and r.stdout.strip() == "ok"
                msg = r.stdout.strip()
            except Exception as ex:
                ok, msg = False, str(ex)
            def finish():
                self._status.configure(
                    text="Applied ✓" if ok else f"Failed: {msg[:40]}",
                    text_color=GRN if ok else RED)
                self.after(3000, lambda: self._status.configure(text=""))
            self.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _on_close(self):
        self.destroy()


# ── Helper: accordion section with plain string title ─────────────────────────

class _PlaceholderSection:
    """Accordion section with a plain string title (not a lang key)."""

    def __init__(self, parent, app, icon, title, on_open=None, on_close=None):
        self._app       = app
        self._open      = False
        self._natural_h = 0
        self._on_open   = on_open
        self._on_close  = on_close

        self._outer = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        self._outer.pack(fill="x", pady=2)

        self._header = ctk.CTkFrame(self._outer, fg_color=BG2, corner_radius=6,
                                    cursor="hand2")
        self._header.pack(fill="x")

        accent = tk.Frame(self._header, bg=YLW, width=4)
        accent.pack(side="left", fill="y")

        ctk.CTkLabel(self._header, text=icon, font=("Helvetica", 14),
                     text_color=YLW, width=30).pack(side="left", padx=(8, 4))

        self._title_lbl = ctk.CTkLabel(self._header, text=title,
                     font=("Helvetica", 11, "bold"),
                     text_color=FG, anchor="w")
        self._title_lbl.pack(side="left", fill="x", expand=True, padx=4, pady=12)

        self._chevron = ctk.CTkLabel(self._header, text="▶",
                                      font=("Helvetica", 10), text_color=FG2, width=24)
        self._chevron.pack(side="right", padx=(0, 12))

        self._content = ctk.CTkFrame(self._outer, fg_color=BG2, corner_radius=0, height=0)
        self._content.pack(fill="x", pady=(1, 0))
        self._content.pack_propagate(False)

        def _bind_all(w):
            w.bind("<Button-1>", self._toggle)
            for child in w.winfo_children():
                _bind_all(child)
        _bind_all(self._header)

    @property
    def content(self):
        return self._content

    def set_title(self, text):
        self._title_lbl.configure(text=text)

    def measure(self):
        self._content.pack_propagate(True)
        self._app.update_idletasks()
        self._natural_h = self._content.winfo_reqheight()
        self._content.pack_propagate(False)
        if not self._open:
            self._content.configure(height=0)
        else:
            self._content.configure(height=self._natural_h)

    def open(self):
        if self._open:
            return
        self._open = True
        self._chevron.configure(text="▼")
        if self._natural_h > 0:
            self._content.configure(height=self._natural_h)
        if self._on_open:
            self._on_open()

    def close(self):
        if not self._open:
            return
        self._open = False
        self._chevron.configure(text="▶")
        self._content.configure(height=0)
        if self._on_close:
            self._on_close()

    def _toggle(self, event=None):
        self.close() if self._open else self.open()
