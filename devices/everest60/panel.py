"""Mountain Everest 60 panel for BaseCamp Linux."""
import os
import subprocess
import threading
import tkinter as tk
import customtkinter as ctk

from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, YLW, BORDER
from shared.config import (CONFIG_DIR, load_rgb_config, save_rgb_config,
                            _load_per_key_60, _save_per_key_60,
                            _load_presets_60, _save_presets_60)
from devices.everest60.controller import detect_model, PID_ANSI


def _hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"


class Everest60Panel(ctk.CTkFrame):
    """Panel for Mountain Everest 60 / Everest 60 ISO keyboard."""

    VID = 0x3282
    PID = PID_ANSI

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app       = app
        self._connected = False
        self._sections  = []
        self._i18n      = []   # (widget, attr, key)

        pid, model = detect_model()
        self._model_name = model or "Everest 60"
        if pid:
            self.PID = pid

        self._build_ui()

    # ── i18n ─────────────────────────────────────────────────────────────────

    def T(self, key, **kw):
        return self._app._lang.get(key, key).format(**kw) if kw else self._app._lang.get(key, key)

    def _reg(self, widget, key, attr="text"):
        self._i18n.append((widget, attr, key))
        widget.configure(**{attr: self.T(key)})
        return widget

    def apply_lang(self):
        for widget, attr, key in self._i18n:
            try:
                widget.configure(**{attr: self.T(key)})
            except Exception:
                pass

    # ── Command builder ───────────────────────────────────────────────────────

    def _cmd(self, *args):
        return self._app._cmd_for_device("everest60", *args)

    def _run_async(self, cmd, on_done=None):
        def _worker():
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                ok = r.returncode == 0 and r.stdout.strip() == "ok"
                if on_done:
                    self.after(0, lambda: on_done(ok, r.stdout.strip()))
            except Exception as e:
                if on_done:
                    self.after(0, lambda e=e: on_done(False, str(e)))
        threading.Thread(target=_worker, daemon=True).start()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Not-connected banner
        self._banner = ctk.CTkFrame(self, fg_color="#3b1515", corner_radius=6)
        self._banner_lbl = ctk.CTkLabel(
            self._banner,
            text=self.T("device_not_connected", model=self._model_name),
            font=("Helvetica", 11), text_color=RED)
        self._banner_lbl.pack(pady=8, padx=16)
        if not self._connected:
            self._banner.pack(fill="x", padx=12, pady=(8, 4))

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))

        self._build_rgb_section(scroll)
        self._build_custom_rgb_section(scroll)

        self._app.update_idletasks()
        for s in self._sections:
            s.measure()

    def _build_rgb_section(self, scroll):
        title = f"{self.T('rgb_title')} — {self._model_name}"
        s = _Section(scroll, self._app, "💡", title)
        self._sections.append(s)
        self._rgb_section = s
        self._build_rgb_content(s.content)

    def _build_rgb_content(self, parent):
        _RGB_EFFECTS = [
            # (display_name, cli_sub, has_speed, has_bri, has_c1, has_c2, has_dir)
            ("Static",            "static",           False, True,  True,  False, False),
            ("Breathing",         "breathing",        True,  True,  True,  True,  False),
            ("Breathing Rainbow", "breathing-rainbow",True,  True,  False, False, False),
            ("Wave",              "wave",             True,  True,  True,  True,  True),
            ("Wave Rainbow",      "wave-rainbow",     True,  True,  False, False, True),
            ("Tornado",           "tornado",          True,  True,  True,  False, True),
            ("Tornado Rainbow",   "tornado-rainbow",  True,  True,  False, False, True),
            ("Reactive",          "reactive",         True,  True,  True,  True,  False),
            ("Yeti",              "yeti",             True,  True,  True,  True,  False),
            ("Off",               "off",              False, False, False, False, False),
        ]
        self._rgb_effect_map = {
            name: (sub, hs, hb, hc1, hc2, hd)
            for name, sub, hs, hb, hc1, hc2, hd in _RGB_EFFECTS
        }
        _rgb_names = [e[0] for e in _RGB_EFFECTS]

        # Effect row
        mode_row = ctk.CTkFrame(parent, fg_color="transparent")
        mode_row.pack(fill="x", padx=10, pady=(10, 2))
        self._reg(ctk.CTkLabel(mode_row, text="", font=("Helvetica", 11),
                               text_color=FG2), "rgb_mode_label").pack(side="left", padx=(0, 6))
        self._rgb_mode_var = tk.StringVar(value=_rgb_names[0])
        ctk.CTkOptionMenu(
            mode_row, variable=self._rgb_mode_var, values=_rgb_names,
            command=lambda _: self._rgb_update_controls(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=180, height=32
        ).pack(side="left")

        # Speed / brightness sliders
        def _slider(par, key, init):
            row = ctk.CTkFrame(par, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            self._reg(ctk.CTkLabel(row, text="", text_color=FG2,
                                   font=("Helvetica", 11), width=120, anchor="w"), key).pack(side="left")
            val_lbl = ctk.CTkLabel(row, text=str(init), text_color=FG,
                                   font=("Helvetica", 11), width=30)
            val_lbl.pack(side="right")
            sl = ctk.CTkSlider(row, from_=0, to=100, number_of_steps=100,
                               fg_color=BG3, progress_color=BLUE,
                               button_color=BLUE, button_hover_color=BLUE,
                               width=180, height=16)
            sl.set(init)
            sl.pack(side="left", padx=(0, 4))
            sl.configure(command=lambda v, l=val_lbl: l.configure(text=str(int(v))))
            return sl, row

        self._rgb_speed_sl, self._rgb_speed_row = _slider(parent, "rgb_speed_label", 50)
        self._rgb_bri_sl,   self._rgb_bri_row   = _slider(parent, "rgb_brightness_label", 100)

        # Color pickers
        color_row = ctk.CTkFrame(parent, fg_color="transparent")
        color_row.pack(fill="x", padx=10, pady=2)
        self._rgb_color1 = (255, 0, 0)
        self._rgb_color2 = (0, 0, 255)
        self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2,
                               font=("Helvetica", 11)), "rgb_color1_label").pack(side="left", padx=(0, 4))
        self._rgb_c1_btn = ctk.CTkButton(
            color_row, text="", width=40, height=28,
            fg_color=_hex(*self._rgb_color1), hover_color=_hex(*self._rgb_color1),
            corner_radius=4, command=lambda: self._pick_color(1))
        self._rgb_c1_btn.pack(side="left", padx=(0, 12))
        self._rgb_c2_lbl = self._reg(ctk.CTkLabel(color_row, text="", text_color=FG2,
                                                   font=("Helvetica", 11)), "rgb_color2_label")
        self._rgb_c2_lbl.pack(side="left", padx=(0, 4))
        self._rgb_c2_btn = ctk.CTkButton(
            color_row, text="", width=40, height=28,
            fg_color=_hex(*self._rgb_color2), hover_color=_hex(*self._rgb_color2),
            corner_radius=4, command=lambda: self._pick_color(2))
        self._rgb_c2_btn.pack(side="left")

        # Direction picker
        dir_row = ctk.CTkFrame(parent, fg_color="transparent")
        dir_row.pack(fill="x", padx=10, pady=2)
        self._rgb_dir_row = dir_row
        self._reg(ctk.CTkLabel(dir_row, text="", text_color=FG2,
                               font=("Helvetica", 11)), "rgb_direction_label").pack(side="left", padx=(0, 6))
        self._dir_wave    = ["→ L→R", "↓ T→B", "← R→L", "↑ B→T"]
        self._dir_tornado = ["↻ CW", "↺ CCW"]
        self._rgb_dir_map = {"→ L→R": 0, "↓ T→B": 2, "← R→L": 4, "↑ B→T": 6,
                             "↻ CW": 0, "↺ CCW": 1}
        self._rgb_dir_var = tk.StringVar(value=self._dir_wave[0])
        self._rgb_dir_menu = ctk.CTkOptionMenu(
            dir_row, variable=self._rgb_dir_var, values=self._dir_wave,
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=("Helvetica", 11), width=120, height=28)
        self._rgb_dir_menu.pack(side="left")

        # Status + Apply
        self._rgb_status = ctk.CTkLabel(parent, text="", font=("Helvetica", 11),
                                        text_color=FG2, fg_color="transparent")
        self._rgb_status.pack(pady=(4, 0))
        self._reg(ctk.CTkButton(
            parent, text="", height=32, corner_radius=4,
            fg_color=BLUE, hover_color="#0884be", text_color=FG,
            font=("Helvetica", 11), command=self._apply_rgb), "rgb_apply"
        ).pack(fill="x", padx=10, pady=(4, 10))

        # Restore saved settings
        saved = load_rgb_config()
        if saved.get("effect") in self._rgb_effect_map:
            self._rgb_mode_var.set(saved["effect"])
        if "speed" in saved:
            self._rgb_speed_sl.set(saved["speed"])
        if "brightness" in saved:
            self._rgb_bri_sl.set(saved["brightness"])
        if "color1" in saved:
            self._rgb_color1 = tuple(saved["color1"])
            h = _hex(*self._rgb_color1)
            self._rgb_c1_btn.configure(fg_color=h, hover_color=h)
        if "color2" in saved:
            self._rgb_color2 = tuple(saved["color2"])
            h = _hex(*self._rgb_color2)
            self._rgb_c2_btn.configure(fg_color=h, hover_color=h)
        if "direction" in saved:
            self._rgb_dir_var.set(saved["direction"])

        self._rgb_update_controls()

    def _build_custom_rgb_section(self, scroll):
        s = _Section(scroll, self._app, "🎨", f"{self.T('zone_title')} — {self._model_name}")
        self._sections.append(s)

        self._custom_rgb_status = ctk.CTkLabel(
            s.content, text="", font=("Helvetica", 11), text_color=FG2, fg_color="transparent")
        self._custom_rgb_status.pack(pady=(8, 4))

        self._reg(ctk.CTkButton(
            s.content, text="",
            height=32, corner_radius=4, fg_color=BLUE, hover_color="#0884be",
            text_color=FG, font=("Helvetica", 11),
            command=self._open_custom_rgb
        ), "custom_rgb_open").pack(fill="x", padx=10, pady=(0, 10))

    def _open_custom_rgb(self):
        from shared.ui_helpers import CustomRGBWindow, _KB60_LAYOUT, _KB60_CANVAS_W, _KB60_CANVAS_H, _KB60_NUM_LEDS
        w = CustomRGBWindow(
            self._app,
            layout=_KB60_LAYOUT,
            canvas_w=_KB60_CANVAS_W,
            canvas_h=_KB60_CANVAS_H,
            num_leds=_KB60_NUM_LEDS,
            has_side_leds=False,
            has_numpad=False,
            has_persist=False,
            load_per_key=_load_per_key_60,
            save_per_key=_save_per_key_60,
            load_presets=_load_presets_60,
            save_presets=_save_presets_60,
            apply_cmd=lambda *a: self._app._cmd_for_device("everest60", *a),
        )
        w.lift()
        w.focus_force()

    def _rgb_update_controls(self):
        name = self._rgb_mode_var.get()
        _, hs, hb, hc1, hc2, hd = self._rgb_effect_map.get(name, ("", False, False, False, False, False))
        self._rgb_speed_sl.configure(state="normal" if hs else "disabled")
        self._rgb_bri_sl.configure(state="normal" if hb else "disabled")
        # Re-set slider values to fix visual position after enable/disable cycle
        if hs:
            self._rgb_speed_sl.set(self._rgb_speed_sl.get())
        if hb:
            self._rgb_bri_sl.set(self._rgb_bri_sl.get())
        self._rgb_c1_btn.configure(state="normal" if hc1 else "disabled")
        self._rgb_c2_btn.configure(state="normal" if hc2 else "disabled")
        self._rgb_c2_lbl.configure(state="normal" if hc2 else "disabled")
        # Direction values
        is_tornado = "tornado" in name.lower()
        dirs = self._dir_tornado if is_tornado else self._dir_wave
        self._rgb_dir_menu.configure(values=dirs, state="normal" if hd else "disabled")
        if self._rgb_dir_var.get() not in dirs:
            self._rgb_dir_var.set(dirs[0])

    def _pick_color(self, slot):
        from shared.ui_helpers import pick_color
        initial = self._rgb_color1 if slot == 1 else self._rgb_color2
        rgb = pick_color(self._app, initial_rgb=initial, title=self.T("color_picker_title"), show_brightness=False)
        if rgb is None:
            return
        h = _hex(*rgb)
        if slot == 1:
            self._rgb_color1 = rgb
            self._rgb_c1_btn.configure(fg_color=h, hover_color=h)
        else:
            self._rgb_color2 = rgb
            self._rgb_c2_btn.configure(fg_color=h, hover_color=h)

    def _apply_rgb(self):
        name = self._rgb_mode_var.get()
        sub, hs, hb, hc1, hc2, hd = self._rgb_effect_map.get(name, ("off", False, False, False, False, False))
        speed = int(self._rgb_speed_sl.get())
        bri   = int(self._rgb_bri_sl.get())
        r1, g1, b1 = self._rgb_color1
        r2, g2, b2 = self._rgb_color2
        direction  = self._rgb_dir_map.get(self._rgb_dir_var.get(), 0)

        if sub == "off":
            cmd = self._cmd("rgb", "off")
        elif sub == "static":
            cmd = self._cmd("rgb", "static", str(r1), str(g1), str(b1), str(bri))
        elif sub == "breathing":
            cmd = self._cmd("rgb", "breathing", str(r1), str(g1), str(b1),
                            str(r2), str(g2), str(b2), str(bri), str(speed))
        elif sub == "breathing-rainbow":
            cmd = self._cmd("rgb", "breathing-rainbow", str(bri), str(speed))
        elif sub == "wave":
            cmd = self._cmd("rgb", "wave", str(r1), str(g1), str(b1),
                            str(r2), str(g2), str(b2), str(bri), str(speed), str(direction))
        elif sub == "wave-rainbow":
            cmd = self._cmd("rgb", "wave-rainbow", str(bri), str(speed), str(direction))
        elif sub == "tornado":
            cmd = self._cmd("rgb", "tornado", str(r1), str(g1), str(b1),
                            str(bri), str(speed), str(direction))
        elif sub == "tornado-rainbow":
            cmd = self._cmd("rgb", "tornado-rainbow", str(bri), str(speed), str(direction))
        elif sub == "reactive":
            cmd = self._cmd("rgb", "reactive", str(r1), str(g1), str(b1),
                            str(r2), str(g2), str(b2), str(bri), str(speed))
        elif sub == "yeti":
            cmd = self._cmd("rgb", "yeti", str(r1), str(g1), str(b1),
                            str(r2), str(g2), str(b2), str(bri), str(speed))
        else:
            return

        save_rgb_config({
            "effect": name, "speed": speed, "brightness": bri,
            "color1": list(self._rgb_color1), "color2": list(self._rgb_color2),
            "direction": self._rgb_dir_var.get(),
        })

        self._rgb_status.configure(text=self.T("rgb_applying"), text_color=YLW)

        def _done(ok, msg):
            self._rgb_status.configure(
                text=self.T("rgb_applied") if ok else f"{self.T('rgb_error')}: {msg[:40]}",
                text_color=GRN if ok else RED)
            self.after(3000, lambda: self._rgb_status.configure(text=""))

        self._run_async(cmd, _done)

    # ── Connection state ──────────────────────────────────────────────────────

    def set_connected(self, connected: bool):
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            self._banner.pack_forget()
        else:
            self._banner.pack(fill="x", padx=12, pady=(8, 4), before=self.winfo_children()[1])

    # ── CPU proc stubs (GUI expects these) ────────────────────────────────────

    def _stop_cpu_proc(self):
        return False

    def _start_cpu_auto(self):
        pass


# ── Accordion section ─────────────────────────────────────────────────────────

class _Section:
    def __init__(self, parent, app, icon, title):
        self._app       = app
        self._open      = False
        self._natural_h = 0

        self._outer = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        self._outer.pack(fill="x", pady=2)

        self._header = ctk.CTkFrame(self._outer, fg_color=BG2, corner_radius=6, cursor="hand2")
        self._header.pack(fill="x")

        tk.Frame(self._header, bg=YLW, width=4).pack(side="left", fill="y")
        ctk.CTkLabel(self._header, text=icon, font=("Helvetica", 14),
                     text_color=YLW, width=30).pack(side="left", padx=(8, 4))
        self._title_lbl = ctk.CTkLabel(self._header, text=title,
                                       font=("Helvetica", 11, "bold"), text_color=FG, anchor="w")
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

    def measure(self):
        self._content.pack_propagate(True)
        self._app.update_idletasks()
        self._natural_h = self._content.winfo_reqheight()
        self._content.pack_propagate(False)
        self._content.configure(height=self._natural_h if self._open else 0)

    def open(self):
        if self._open:
            return
        self._open = True
        self._chevron.configure(text="▼")
        if self._natural_h > 0:
            self._content.configure(height=self._natural_h)

    def _toggle(self, _=None):
        self.close() if self._open else self.open()

    def close(self):
        if not self._open:
            return
        self._open = False
        self._chevron.configure(text="▶")
        self._content.configure(height=0)
