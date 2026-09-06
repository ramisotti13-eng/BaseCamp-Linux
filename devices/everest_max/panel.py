"""Everest Max device panel for BaseCamp Linux hub."""
import os
import sys
import json
import time
import threading
import subprocess
import tkinter as tk
import customtkinter as ctk
import shared.ui as UI
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
    macro_names,
)
from shared.volume import system_volume, start_watch
from shared.ui_helpers import (CardColumns,
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER,
    AccordionSection, LibraryPickerDialog, MultiUploadDialog, CustomRGBWindow,
    pick_color, pick_library_image, pick_main_library_image,
    native_open_image, native_open_folder, parse_desktop_apps,
    _rgb_hex,
)

# STYLES dict for this module
STYLES = {"Analog": "analog", "Digital": "digital"}

# ── EverestMaxPanel ────────────────────────────────────────────────────────────


def _wait_for_controller(proc, timeout=8):
    """Wait for a stopped controller, but never for ever.

    Every stop here runs on the interface thread, and the controller now
    releases the keyboard on its way out instead of dying where it stands.
    That release is quick, but it talks to a device that may be mid-reset,
    so waiting without a bound would freeze the window on it. The controller
    ends itself after five seconds if its own cleanup does not come back;
    this is the backstop for that backstop.
    """
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("[Everest] controller did not stop in %ds, killing it" % timeout,
              file=sys.stderr, flush=True)
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


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
                with open(os.path.join(CONFIG_DIR, name)) as f:
                    return f.read().strip()
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
            with open(MAIN_MODE_FILE) as f:
                _saved_mode = f.read().strip()
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

    def _row(self, parent, label_key):
        """A card row: label on the left, control packed to the right.

        The old panel centred every block, which reads as a poster rather than
        as a set of settings. Everything in a card lines up on one left edge
        and its control on one right edge.
        """
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)
        self._reg(ctk.CTkLabel(row, text=self.T(label_key), font=(UI.FONT_FAMILY, 11),
                               text_color=FG2, anchor="w"), label_key).pack(side="left")
        return row

    # ── subprocess command builder ────────────────────────────────────────────

    def _cmd(self, *args):
        return self._app._cmd(*args)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Cards ──
        # The clock used to be the largest thing on the screen, above the
        # language picker and the autostart switches, although it is a setting
        # of the keyboard display and not a state of the app. It is a card
        # among the others now.
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        cards = ctk.CTkFrame(scroll, fg_color="transparent")
        cards.pack(fill="both", expand=True, padx=12, pady=8)

        # Ask for the column before building each card, so every card is
        # packed under the one above it instead of hanging in a grid row.
        cols = CardColumns(cards)
        for build in (self._build_clock_section, self._build_monitor_section,
                      self._build_main_display_section,
                      self._build_numpad_section, self._build_rgb_section,
                      self._build_zone_section):
            build(cols.next())
            cols.place(self._sections[-1])

        self._app._apply_lang()
        self._app.update_idletasks()
        self._finish_ui(scroll)

    def _build_clock_section(self, parent):
        clock_card = AccordionSection(parent, self._app, "", "clock_title",
                                      card=True, auto_pack=False,
                                      hint=self._current_style.get())
        self._sections.append(clock_card)
        self._clock_card = clock_card
        dash = clock_card.content

        # Dial preview on the left, the two settings as label/value rows on the
        # right. The design puts the thing being configured next to what
        # configures it instead of stacking centred blocks.
        face = ctk.CTkFrame(dash, fg_color="transparent")
        face.pack(side="left", padx=(0, 14))
        self._clock_label = ctk.CTkLabel(face, text="",
                                         font=("Courier", 24, "bold"), text_color=BLUE)
        self._clock_label.pack()
        self._date_label = ctk.CTkLabel(face, text="",
                                        font=(UI.FONT_FAMILY, 10), text_color=FG2)
        self._date_label.pack(pady=(2, 0))

        rows = ctk.CTkFrame(dash, fg_color="transparent")
        rows.pack(side="left", fill="x", expand=True)

        fmt_row = self._row(rows, "clock_format_label")
        ctk.CTkSegmentedButton(
            fmt_row, values=["24H", "12H"],
            variable=self._clock_format,
            command=lambda _: self._on_format_change(),
            font=(UI.FONT_FAMILY, 10),
            fg_color=BG3, selected_color=BLUE, selected_hover_color=BLUE,
            unselected_color=BG3, unselected_hover_color=BG2,
            text_color=FG, width=94, height=UI.CTRL_H_SM,
        ).pack(side="right")

        style_row = self._row(rows, "clock_style_label")
        ctk.CTkSegmentedButton(
            style_row, values=list(STYLES.keys()),
            variable=self._current_style,
            command=lambda _: self._on_style_change(),
            font=(UI.FONT_FAMILY, 10),
            fg_color=BG3, selected_color=BLUE, selected_hover_color=BLUE,
            unselected_color=BG3, unselected_hover_color=BG2,
            text_color=FG, width=150, height=UI.CTRL_H_SM,
        ).pack(side="right")

        reset_row = self._row(rows, "dial_reset_label")
        self._reg(
            UI.DangerButton(reset_row, "", self._reset_dial_image, width=150,
                            height=UI.CTRL_H_SM),
            "dial_reset_btn"
        ).pack(side="right")

        # The language picker moved to the settings screen with autostart and
        # the splash: it belongs to the app, not to a keyboard. The combo box
        # itself stays alive so _apply_lang keeps working.
        self._lang_var = self._app._lang_var
        self._lang_combo = ctk.CTkComboBox(rows, variable=self._lang_var, values=[])

        self._style_status = ctk.CTkLabel(rows, text="", font=(UI.FONT_FAMILY, 10),
                                          text_color=GRN, anchor="w")
        self._style_status.pack(fill="x", pady=(6, 0))

        # Splash and autostart moved to the settings screen: they are
        # application settings, and sitting here they were out of reach for
        # anyone whose keyboard is not plugged in. The variables stay so the
        # rest of this panel keeps working unchanged.

    def _finish_ui(self, scroll):
        from shared.ui_helpers import cap_scroll_speed
        cap_scroll_speed(scroll)

        # Start clock tick
        self._tick()
        self._update_cpu_bar()

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_monitor_section(self, parent):
        s1 = AccordionSection(parent, self._app, "", "monitor_title", card=True,
                              auto_pack=False)
        self._sections.append(s1)
        self._monitor_card = s1
        b1 = s1.content

        # The card showed a button and a line of text about a mode whose whole
        # point is live numbers. It shows the numbers, from the same psutil the
        # controller uses, so you can see what the keyboard is being sent.
        self._meters = {}
        for key, label_key in (("cpu", "meter_cpu"), ("ram", "meter_ram"),
                               ("disk", "meter_disk"), ("net", "meter_net"),
                               ("vol", "meter_volume")):
            row = ctk.CTkFrame(b1, fg_color="transparent")
            row.pack(fill="x", pady=2)
            self._reg(ctk.CTkLabel(row, text=self.T(label_key), width=52,
                                   font=(UI.FONT_FAMILY, 10), text_color=FG2,
                                   anchor="w"), label_key).pack(side="left")
            val = ctk.CTkLabel(row, text="0", width=44, font=(UI.FONT_FAMILY, 10),
                               text_color=FG, anchor="e")
            val.pack(side="right")
            bar = ctk.CTkProgressBar(row, height=5, corner_radius=3,
                                     progress_color=BLUE, fg_color=BG3)
            bar.set(0)
            bar.pack(side="left", fill="x", expand=True, padx=8)
            self._meters[key] = (bar, val)

        self._btn_cpu = UI.PrimaryButton(b1, self.T("monitor_start"),
                                         self._toggle_cpu)
        self._btn_cpu.pack(fill="x", pady=(10, 4))

        self._cpu_status = ctk.CTkLabel(b1, text="", font=(UI.FONT_FAMILY, 10),
                                        text_color=FG2, anchor="w")
        self._cpu_status.pack(fill="x")
        self._meter_after = None
        self._net_last = None

    def _tick_meters(self):
        """Refresh the meters. Runs only while this screen is visible:
        the shell calls refresh() when it is shown and on_hide() when it is
        left, so nothing polls in the background for a screen nobody sees."""
        try:
            import psutil, time
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            io = psutil.net_io_counters()
            now = time.monotonic()
            mbs = 0.0
            if self._net_last:
                dt = max(0.001, now - self._net_last[0])
                mbs = ((io.bytes_sent + io.bytes_recv) - self._net_last[1]) / dt / 1e6
            self._net_last = (now, io.bytes_sent + io.bytes_recv)
            # Volume is the one meter that is not psutil: it is what the
            # loop pushes to the wheel display, so show the same number. The
            # watcher started in refresh() makes this a variable read; without
            # it this would fork a mixer command on the Tk thread every tick.
            vol = system_volume()
            for key, value, shown in (("cpu", cpu, f"{cpu:.0f}%"),
                                      ("ram", ram, f"{ram:.0f}%"),
                                      ("disk", disk, f"{disk:.0f}%"),
                                      ("net", min(mbs / 10 * 100, 100), f"{mbs:.1f}"),
                                      ("vol", vol or 0,
                                       "--" if vol is None else f"{vol:.0f}%")):
                bar, lbl = self._meters[key]
                bar.set(max(0.0, min(1.0, value / 100)))
                lbl.configure(text=shown)
        except Exception:
            pass
        self._meter_after = self._app.after(2000, self._tick_meters)

    def refresh(self):
        self._sync_card_hints()
        start_watch()          # follow the mixer while this screen is on show
        if getattr(self, "_meter_after", None) is None:
            self._tick_meters()

    def on_hide(self):
        if getattr(self, "_meter_after", None) is not None:
            try:
                self._app.after_cancel(self._meter_after)
            except Exception:
                pass
            self._meter_after = None

    def _build_main_display_section(self, parent):
        s2 = AccordionSection(parent, self._app, "", "main_display_title",
                              card=True, auto_pack=False)
        self._sections.append(s2)
        self._main_card = s2
        b2 = s2.content

        _MODE_KEYS = ["image", "clock", "volume", "cpu", "gpu", "hd", "network", "ram", "apm"]
        _MODE_LANG = ["main_mode_image", "main_mode_clock", "main_mode_volume", "main_mode_cpu",
                      "main_mode_gpu", "main_mode_hd", "main_mode_network",
                      "main_mode_ram", "main_mode_apm"]
        self._mode_labels  = [self.T(k) for k in _MODE_LANG]
        self._mode_key_map = {lbl: key for key, lbl in zip(_MODE_KEYS, self._mode_labels)}

        mode_row = self._row(b2, "main_mode_label")
        self._main_mode_var = tk.StringVar(
            value=self._mode_labels[_MODE_KEYS.index(self._main_mode)])
        self._main_mode_menu = ctk.CTkOptionMenu(
            mode_row, variable=self._main_mode_var,
            values=self._mode_labels,
            command=lambda lbl: self._set_main_mode(self._mode_key_map[lbl]),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=170,
            height=UI.CTRL_H_SM)
        self._main_mode_menu.pack(side="right")

        img_row = self._row(b2, "main_display_image_label")
        self._reg(
            UI.GhostButton(img_row, "", self._upload_main_image, width=170,
                           height=UI.CTRL_H_SM),
            "main_display_upload"
        ).pack(side="right")

        self._main_bar = ctk.CTkProgressBar(b2, mode="determinate",
                                             progress_color=BLUE, fg_color=BG3,
                                             height=6, corner_radius=0)
        self._main_bar.set(0)
        self._main_bar.pack(fill="x", padx=12, pady=(0, 2))

        self._main_status = ctk.CTkLabel(b2, text="", font=(UI.FONT_FAMILY, 11),
                                          text_color=FG2)
        self._main_status.pack(pady=(0, 12))

    def _build_numpad_section(self, parent):
        s3 = AccordionSection(parent, self._app, "", "numpad_title", card=True,
                              auto_pack=False, hint="D1 - D4")
        self._sections.append(s3)
        b3 = s3.content

        self._reg(
            ctk.CTkLabel(b3, text="", font=(UI.FONT_FAMILY, 10), text_color=FG2,
                         anchor="w"),
            "numpad_subtitle"
        ).pack(fill="x", pady=(0, 6))

        multi_row = ctk.CTkFrame(b3, fg_color="transparent")
        multi_row.pack(fill="x", pady=(0, 8))
        self._reg(
            UI.GhostButton(multi_row, "", self._open_multi_upload),
            "multi_upload_btn"
        ).pack(fill="x")

        self._btn_type_menus = []
        self._folder_btns    = []
        self._action_entries = []
        self._obs_combos     = []
        self._macro_combos   = []

        _TYPE_INTERNAL = ["none", "shell", "url", "folder", "app", "obs", "macro",
                          "keypress", "text", "page", "set_key"]

        def _type_internal_dynamic():
            base = list(_TYPE_INTERNAL)
            pm = getattr(self._app, "_plugin_manager", None)
            if pm:
                base.extend(pm.get_action_type_ids())
            return base

        def _type_labels():
            labels = [self.T("action_type_none"),     self.T("action_type_shell"),
                    self.T("action_type_url"),       self.T("action_type_folder"),
                    self.T("action_type_app"),       "OBS",
                    self.T("action_type_macro"),
                    self.T("action_type_keypress"),  self.T("action_type_text"),
                    self.T("action_type_page"),      self.T("action_type_set_key")]
            pm = getattr(self._app, "_plugin_manager", None)
            if pm:
                for _tid, lbl in pm.get_action_type_labels():
                    labels.append(lbl)
            return labels

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
            ctk.CTkLabel(header_row, text=f"D{i+1}", font=(UI.FONT_FAMILY, 10, "bold"),
                         text_color=YLW).pack(side="left")

            action_row = ctk.CTkFrame(card, fg_color="transparent")
            action_row.pack(fill="x", padx=4, pady=(2, 6))

            self._reg(
                ctk.CTkLabel(action_row, text="", font=(UI.FONT_FAMILY, 10),
                             text_color=FG2, width=50, anchor="w"),
                "action_label"
            ).pack(side="left", padx=(4, 2))

            idx = i
            cur_internal = self._btn_type[i].get()
            labels       = _type_labels()
            all_types    = _type_internal_dynamic()
            cur_label    = labels[all_types.index(cur_internal)] if cur_internal in all_types else labels[1]

            type_menu = ctk.CTkOptionMenu(
                action_row, values=labels,
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=(UI.FONT_FAMILY, 11), width=88, height=30,
                dynamic_resizing=False,
                command=lambda val, ix=idx: self._on_btn_type_change(val, ix)
            )
            type_menu.set(cur_label)
            type_menu.pack(side="left", padx=(2, 2))
            self._btn_type_menus.append(type_menu)

            entry = ctk.CTkEntry(action_row, textvariable=self._btn_action[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=(UI.FONT_FAMILY, 11), height=30)
            entry.pack(side="left", padx=4, expand=True, fill="x")
            self._action_entries.append(entry)

            obs_combo = ctk.CTkComboBox(
                action_row, values=[], width=140, height=30,
                font=(UI.FONT_FAMILY, 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=idx: self._on_obs_select(val, ix))
            self._obs_combos.append(obs_combo)

            macro_combo = ctk.CTkComboBox(
                action_row, values=[], width=140, height=30,
                font=(UI.FONT_FAMILY, 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=idx: self._on_macro_select(val, ix))
            self._macro_combos.append(macro_combo)

            cur_type     = self._btn_type[i].get()
            if cur_type == "obs":
                entry.pack_forget()
                obs_panel = self._app._obs_panel
                scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
                obs_combo.configure(values=scenes + ["OBS: Record", "OBS: Stream"])
                cur_action = self._btn_action[i].get()
                if cur_action.startswith("scene:"):
                    obs_combo.set(cur_action[6:])
                elif cur_action in ("record", "stream"):
                    obs_combo.set(f"OBS: {cur_action.capitalize()}")
                obs_combo.pack(side="left", padx=4, expand=True, fill="x")
            elif cur_type == "macro":
                entry.pack_forget()
                self._populate_macro_combo(macro_combo, self._btn_action[i].get(), btn_idx=i)
                macro_combo.pack(side="left", padx=4, expand=True, fill="x")
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

        self._numpad_type_internal_fn = _type_internal_dynamic
        self._numpad_type_labels_fn  = _type_labels

        reset_row = ctk.CTkFrame(b3, fg_color="transparent")
        reset_row.pack(fill="x", padx=8, pady=(4, 0))
        self._reg(
            UI.DangerButton(reset_row, "", self._reset_buttons_flash,
                            height=UI.CTRL_H_SM),
            "reset_buttons_btn"
        ).pack(fill="x")

        # Clarify scope: this only clears app-set actions, not firmware-level key
        # remaps configured in Windows BaseCamp (issue #11).
        self._reg(
            ctk.CTkLabel(b3, text="", font=(UI.FONT_FAMILY, 10), text_color=FG2,
                         wraplength=360, justify="left"),
            "reset_buttons_note"
        ).pack(fill="x", padx=8, pady=(4, 0))

        self._numpad_info = ctk.CTkLabel(b3, text="", font=(UI.FONT_FAMILY, 11),
                                          text_color=GRN)
        self._numpad_info.pack(pady=(4, 10))

    def _build_rgb_section(self, parent):
        s5 = AccordionSection(parent, self._app, "", "rgb_title", card=True,
                              auto_pack=False, hint=self._rgb_mode_var.get()
                              if hasattr(self, "_rgb_mode_var") else None)
        self._sections.append(s5)
        c = s5.content

        rgb_mode_row = ctk.CTkFrame(c, fg_color="transparent")
        rgb_mode_row.pack(fill="x", padx=10, pady=(10, 2))
        self._reg(
            ctk.CTkLabel(rgb_mode_row, text="", font=(UI.FONT_FAMILY, 11), text_color=FG2),
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
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=180, height=32)
        self._rgb_mode_menu.pack(side="left")

        def _labeled_slider(parent, label_key, from_=0, to=100, init=50):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            lbl = self._reg(ctk.CTkLabel(row, text="", text_color=FG2,
                                         font=(UI.FONT_FAMILY, 11), width=120, anchor="w"), label_key)
            lbl.pack(side="left")
            val_lbl = ctk.CTkLabel(row, text=str(init), text_color=FG,
                                   font=(UI.FONT_FAMILY, 11), width=30)
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

        self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2, font=(UI.FONT_FAMILY, 11)),
                  "rgb_color1_label").pack(side="left", padx=(0, 4))
        self._rgb_c1_btn = ctk.CTkButton(color_row, text="", width=40, height=28,
                                          fg_color="#ff0000", hover_color="#ff0000", corner_radius=4,
                                          command=lambda: self._pick_rgb_color(1))
        self._rgb_c1_btn.pack(side="left", padx=(0, 12))

        self._rgb_c2_lbl = self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2,
                                                   font=(UI.FONT_FAMILY, 11)), "rgb_color2_label")
        self._rgb_c2_lbl.pack(side="left", padx=(0, 4))
        self._rgb_c2_btn = ctk.CTkButton(color_row, text="", width=40, height=28,
                                          fg_color="#0000ff", hover_color="#0000ff", corner_radius=4,
                                          command=lambda: self._pick_rgb_color(2))
        self._rgb_c2_btn.pack(side="left")

        dir_row = ctk.CTkFrame(c, fg_color="transparent")
        dir_row.pack(fill="x", padx=10, pady=2)
        self._rgb_dir_row = dir_row
        self._reg(ctk.CTkLabel(dir_row, text="", text_color=FG2, font=(UI.FONT_FAMILY, 11)),
                  "rgb_direction_label").pack(side="left", padx=(0, 6))
        self._dir_wave    = ["→ L→R", "↓ T→B", "← R→L", "↑ B→T"]
        self._dir_tornado = ["↻ CW", "↺ CCW"]
        self._rgb_dir_val_map = {"→ L→R": 0, "↓ T→B": 2, "← R→L": 4, "↑ B→T": 6,
                                 "↻ CW": 9, "↺ CCW": 10}
        self._rgb_dir_var  = tk.StringVar(value=self._dir_wave[0])
        self._rgb_dir_menu = ctk.CTkOptionMenu(
            dir_row, variable=self._rgb_dir_var, values=self._dir_wave,
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=120, height=28)
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

        rgb_apply_row = ctk.CTkFrame(c, fg_color="transparent")
        self._rgb_apply_row = rgb_apply_row
        rgb_apply_row.pack(fill="x", padx=10, pady=(6, 10))
        self._reg(
            UI.PrimaryButton(rgb_apply_row, "", self._apply_rgb, width=140),
            "rgb_apply"
        ).pack(side="left")
        self._rgb_status = ctk.CTkLabel(rgb_apply_row, text="", text_color=FG2,
                                         font=(UI.FONT_FAMILY, 11))
        self._rgb_status.pack(side="left", padx=(10, 0))

        self._rgb_update_controls()

        self._rgb_section = s5
        self._rgb_update_controls()

    def _build_zone_section(self, parent):
        s6 = AccordionSection(parent, self._app, "", "zone_title", card=True,
                              auto_pack=False)
        self._sections.append(s6)
        c6 = s6.content

        self._rgb_win      = None
        self._zone_status  = ctk.CTkLabel(c6, text="", text_color=FG2,
                                          font=(UI.FONT_FAMILY, 11))

        open_row = ctk.CTkFrame(c6, fg_color="transparent")
        open_row.pack(pady=(16, 16))
        self._reg(
            UI.GhostButton(open_row, "", self._open_rgb_editor, width=240),
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
                                    fg_color=UI.ACCENT, text_color=UI.tokens.ACCENT_TEXT)
            self._cpu_status.configure(text=self.T("monitor_stopped"), text_color=RED)
        self._app.after(5000, self._update_cpu_bar)

    def _on_format_change(self):
        with open(os.path.join(CONFIG_DIR, "clock_format"), "w") as f:
            f.write(self._clock_format.get())

    def _sync_card_hints(self):
        """Card headers show the current value, so the hint has to follow it."""
        try:
            self._clock_card.set_hint(self._current_style.get())
        except Exception:
            pass
        try:
            self._main_card.set_hint(self._main_mode_var.get())
        except Exception:
            pass

    def _on_style_change(self):
        self._sync_card_hints()
        label = self._current_style.get()
        save_style(STYLES[label])
        self._style_status.configure(
            text=self.T("style_sending", style=label), text_color=BLUE)
        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            _wait_for_controller(self._cpu_proc)
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
            _wait_for_controller(self._cpu_proc)
            self._cpu_proc = None
            return True
        return False

    def _start_cpu_auto(self):
        if not (self._cpu_proc and self._cpu_proc.poll() is None):
            self._toggle_cpu()

    def _start_cpu_auto_clean(self):
        _FROZEN = getattr(sys, "frozen", False)
        def run():
            pkill = "basecamp-controller.*cpu" if _FROZEN else r"emax_controller\.py.*cpu"
            subprocess.run(["pkill", "-f", pkill], capture_output=True)
            time.sleep(0.4)
            self._app.after(0, self._start_cpu_auto)
        threading.Thread(target=run, daemon=True).start()

    def _toggle_cpu(self):
        if self._cpu_proc and self._cpu_proc.poll() is None:
            self._cpu_proc.terminate()
            self._cpu_proc = None
            self._btn_cpu.configure(text=self.T("monitor_start"),
                                    fg_color=UI.ACCENT, text_color=UI.tokens.ACCENT_TEXT)
            self._cpu_status.configure(text=self.T("monitor_stopped"), text_color=RED)
        else:
            style_arg = STYLES[self._current_style.get()]
            _stderr_log = None
            try:
                _stderr_log = open(os.path.join(CONFIG_DIR, "controller_error.log"), "w")
                self._cpu_proc = subprocess.Popen(
                    self._cmd("cpu", style_arg),
                    stdout=subprocess.DEVNULL, stderr=_stderr_log)
                # Stopping a monitor is not destructive; red here only
                # competed with the actual delete buttons elsewhere. The state
                # is in the line below the button.
                self._btn_cpu.configure(text=self.T("monitor_stop"),
                                        fg_color=UI.ACCENT,
                                        text_color=UI.tokens.ACCENT_TEXT)
                self._cpu_status.configure(text=self.T("monitor_running"), text_color=GRN)
            except Exception as e:
                self._cpu_status.configure(text=f"{self.T('error')}: {e}", text_color=RED)
            finally:
                if _stderr_log is not None:
                    _stderr_log.close()

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
        rgb = pick_color(self._app, initial_rgb=initial, title=self.T("ui_pick_color"), show_brightness=False)
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
            _wait_for_controller(self._cpu_proc)
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
                    text=self.T("rgb_applied") if ok else f"{self.T('rgb_error')}: {err}",
                    text_color=GRN if ok else RED)
                if was_running:
                    self._start_cpu_auto()
            self._app.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _pick_zone_color(self, zone_key):
        initial = self._zone_colors.get(zone_key, (0, 0, 0))
        rgb = pick_color(self._app, initial_rgb=initial, title=self.T("ui_pick_color"), show_brightness=False)
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
            _wait_for_controller(self._cpu_proc)
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
                    text=self.T("zone_applied") if ok else f"{self.T('zone_error')}: {err}",
                    text_color=GRN if ok else RED)
                if was_running:
                    self._start_cpu_auto()
            self._app.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _open_rgb_editor(self):
        """Open the per-key editor as a screen, not as a window on top of the
        window it belongs to."""
        self._app.open_screen(
            "custom_rgb_max",
            lambda parent: CustomRGBWindow(
                self._app, parent=parent,
                on_close=lambda: self._app.close_screen("custom_rgb_max",
                                                        "everest_max")),
            title=self.T("custom_rgb_title"))

    def _on_btn_type_change(self, label, idx):
        labels = self._numpad_type_labels_fn()
        type_internal = self._numpad_type_internal_fn()
        try:
            internal = type_internal[labels.index(label)]
        except (ValueError, IndexError):
            internal = "shell"
        self._btn_type[idx].set(internal)
        if hasattr(self, "_folder_btns") and idx < len(self._folder_btns):
            btn = self._folder_btns[idx]
            if internal in ("folder", "app"):
                btn.configure(state="normal", image=self._folder_img)
            else:
                btn.configure(state="disabled", image=self._folder_img_dim)
        # Show/hide OBS combo / macro combo vs entry+browse
        if hasattr(self, "_obs_combos") and idx < len(self._obs_combos):
            self._obs_combos[idx].pack_forget()
            self._macro_combos[idx].pack_forget()
            self._action_entries[idx].pack_forget()
            self._folder_btns[idx].pack_forget()
            if internal == "obs":
                obs_panel = self._app._obs_panel
                scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
                self._obs_combos[idx].configure(values=scenes + ["OBS: Record", "OBS: Stream"])
                if scenes:
                    self._obs_combos[idx].set(scenes[0])
                    self._btn_action[idx].set(f"scene:{scenes[0]}")
                self._obs_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
            elif internal == "macro":
                self._populate_macro_combo(self._macro_combos[idx], btn_idx=idx)
                self._macro_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
            else:
                self._action_entries[idx].pack(side="left", padx=4, expand=True, fill="x")
                self._folder_btns[idx].pack(side="left", padx=(0, 4))
            self._apply_btn(idx)

    def _on_obs_select(self, val, idx):
        if val == "OBS: Record":
            self._btn_action[idx].set("record")
        elif val == "OBS: Stream":
            self._btn_action[idx].set("stream")
        else:
            self._btn_action[idx].set(f"scene:{val}")
        self._apply_btn(idx)

    def _populate_macro_combo(self, combo, current_uuid="", btn_idx=None):
        names = self._macro_names()
        self._macro_uuid_list = list(names.keys())
        display = list(names.values())
        none_available = self.T("macro_none_available")
        combo.configure(values=display if display else [none_available])
        if current_uuid and current_uuid in names:
            combo.set(names[current_uuid])
        elif self._macro_uuid_list:
            combo.set(display[0])
            # Auto-set the first macro UUID so saving works immediately
            if btn_idx is not None:
                self._btn_action[btn_idx].set(self._macro_uuid_list[0])
        else:
            # Say so, rather than leaving the widget's own placeholder on screen.
            combo.set(none_available)

    def _macro_names(self):
        """{uuid: name}, from the Macros screen while it exists and from the
        saved macros before it is first opened (see config.macro_names)."""
        macro_panel = getattr(self._app, "_macro_panel", None)
        if macro_panel is not None:
            return macro_panel.get_macro_names()
        return macro_names()

    def _on_macro_select(self, val, idx):
        # Use the parallel UUID list to resolve by position (handles duplicate names)
        names = self._macro_names()
        display = list(names.values())
        uuids = list(names.keys())
        try:
            pos = display.index(val)
            self._btn_action[idx].set(uuids[pos])
        except (ValueError, IndexError):
            pass
        self._apply_btn(idx)

    def _browse_action(self, idx):
        btype = self._btn_type[idx].get()
        if btype == "folder":
            path = native_open_folder(title=self.T("ui_pick_folder"))
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
            font=(UI.FONT_FAMILY, 12), height=34,
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
                    hover_color=BG3, font=(UI.FONT_FAMILY, 11),
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
            _wait_for_controller(self._cpu_proc)
            self._cpu_proc = None

        def do_upload():
            time.sleep(2.5 if was_running else 0.5)
            cmd = self._cmd("upload", str(idx), path)
            if gif_frame:
                cmd = self._cmd("upload", str(idx), path, "--frame", str(gif_frame))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            stdout_data, stderr_data = proc.communicate()
            ok = proc.returncode == 0
            if ok:
                new_fname = thumb_fname or _save_to_library(path, gif_frame)
                if new_fname:
                    _save_icon_last(idx, new_fname)
            if was_running:
                self._app.after(0, self._start_cpu_auto)
            err_hint = (stderr_data.strip().splitlines() or [""])[-1]

            def finish():
                self._numpad_info.configure(
                    text=(self.T("image_uploaded", d=idx+1) if ok
                          else f"D{idx+1}: Fehler — {err_hint}" if err_hint
                          else self.T("image_error", d=idx+1)),
                    text_color=GRN if ok else RED)
            self._app.after(0, finish)

        threading.Thread(target=do_upload, daemon=True).start()

    def _set_main_mode(self, mode):
        self._sync_card_hints()
        self._main_mode = mode
        with open(MAIN_MODE_FILE, "w") as f:
            f.write(mode)
        self._main_status.configure(text="", text_color=FG2)

        was_running = self._cpu_proc and self._cpu_proc.poll() is None
        if was_running:
            self._cpu_proc.terminate()
            _wait_for_controller(self._cpu_proc)
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
            pkill = "basecamp-controller.*cpu" if _FROZEN else r"emax_controller\.py.*cpu"
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
            _wait_for_controller(self._cpu_proc)
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
                # Merge stderr into stdout so we drain a single pipe — avoids
                # the classic deadlock where a full stderr buffer blocks the
                # subprocess while we're stuck in proc.wait().
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True)
                last_non_progress = ""
                for line in proc.stdout:
                    if line.startswith("PROGRESS:"):
                        try:
                            pct = int(line.strip()[9:])
                            self._app.after(0, lambda v=pct: self._main_bar.set(v / 100.0))
                        except ValueError:
                            pass
                    else:
                        stripped = line.rstrip()
                        if stripped:
                            last_non_progress = stripped
                        print(line, end="", flush=True)
                proc.wait()
                ok       = proc.returncode == 0
                err_hint = last_non_progress
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
        # The monitor button and its status line are written when monitoring
        # starts and stops, so on a language change they keep whichever words
        # were current at the time. Write them again from the state itself.
        if hasattr(self, "_btn_cpu"):
            running = getattr(self, "_cpu_proc", None) is not None \
                and self._cpu_proc.poll() is None
            self._btn_cpu.configure(
                text=self.T("monitor_stop") if running else self.T("monitor_start"))
            self._cpu_status.configure(
                text=self.T("monitor_running") if running else self.T("monitor_stopped"))
        if hasattr(self, "_btn_type_menus"):
            new_labels = self._numpad_type_labels_fn()
            type_internal = self._numpad_type_internal_fn()
            for i, menu in enumerate(self._btn_type_menus):
                menu.configure(values=new_labels)
                cur = self._btn_type[i].get()
                try:
                    menu.set(new_labels[type_internal.index(cur)])
                except (ValueError, IndexError):
                    menu.set(new_labels[1])

    def set_connected(self, connected: bool):
        """Show/hide a 'not connected' banner (future use)."""
        pass
