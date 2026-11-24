"""Mountain Everest 60 panel for BaseCamp Linux.

A client of Everest60Service (core/devices/everest60/service.py): the
service owns the lighting settings, the replay when the board appears and
the controller calls; this screen fills its widgets from the service, sends
what the person chose, and shows the results the service publishes.
"""
import tkinter as tk
import customtkinter as ctk

import shared.ui as UI
from shared.ui.core_bridge import subscribe_tk
from shared.ui_helpers import (CardColumns, BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, YLW, BORDER,
                               cap_scroll_speed)
from shared.config import (_load_per_key_60, _save_per_key_60,
                           _load_presets_60, _save_presets_60)
from core.devices.everest60 import protocol as P
from core.devices.everest60.protocol import PID_ANSI


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

        self._svc = app.core.service("everest60")
        # The application builds the controller's command line; the service
        # runs it (one call at a time, off this thread).
        self._svc.use_command(lambda *a: app._cmd_for_device("everest60", *a))
        found = self._svc.detect()
        self._model_name = found["model"]
        if found["pid"]:
            self.PID = found["pid"]
        # The device registry tells the service whether the board is here,
        # also before this screen existed; the banner follows the service.
        self._connected = bool(self._svc.present)

        self._build_ui()
        subscribe_tk(app.core, "everest60.*", self, self._on_core_event)

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
        # Widgets whose text takes a format argument or drives a dropdown aren't
        # part of the simple (widget, attr, key) table — refresh them by hand.
        if hasattr(self, "_rgb_build_lbl"):
            try:
                self._rgb_build_lbl.configure(
                    text=self.T("rgb_build_label", ver=self._rgb_build_ver))
            except Exception:
                pass
        if hasattr(self, "_rgb_cmode_menu"):
            try:
                cur = self._cmode_from_label.get(self._rgb_cmode_var.get(), "dual")
                self._cmode_labels = {
                    "single":  self.T("rgb_cmode_single"),
                    "dual":    self.T("rgb_cmode_dual"),
                    "rainbow": self.T("rgb_cmode_rainbow"),
                }
                self._cmode_from_label = {v: k for k, v in self._cmode_labels.items()}
                self._rgb_cmode_var.set(self._cmode_labels.get(cur, self._cmode_labels["dual"]))
                self._rgb_update_controls()
            except Exception:
                pass

    # ── Command builder ───────────────────────────────────────────────────────

    def _cmd(self, *args):
        return self._app._cmd_for_device("everest60", *args)

    # ── Results from the service ──────────────────────────────────────────────

    def _on_core_event(self, topic, payload):
        if topic == "everest60.presence":
            self._show_connected(self._svc.present)
            return
        if topic != "everest60.rgb_applied":
            return
        op, ok, msg = payload.get("op"), payload.get("ok"), payload.get("detail") or ""
        if op == "lighting":
            self._rgb_status.configure(
                text=self.T("rgb_applied") if ok else f"{self.T('rgb_error')}: {msg[:40]}",
                text_color=GRN if ok else RED)
            self.after(3000, lambda: self._rgb_status.configure(text=""))
        elif op == "side_ring":
            self._side_leds_status.configure(
                text=self.T("side_leds_applied") if ok else msg,
                text_color=GRN if ok else RED)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Not-connected banner
        self._banner = ctk.CTkFrame(self, fg_color="#3b1515", corner_radius=6)
        self._banner_lbl = ctk.CTkLabel(
            self._banner,
            text=self.T("device_not_connected", model=self._model_name),
            font=(UI.FONT_FAMILY, 11), text_color=RED)
        self._banner_lbl.pack(pady=8, padx=16)
        if not self._connected:
            self._banner.pack(fill="x", padx=12, pady=(8, 4))

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        cap_scroll_speed(scroll)

        cards = ctk.CTkFrame(scroll, fg_color="transparent")
        cards.pack(fill="both", expand=True, padx=12, pady=8)


        cols = CardColumns(cards)
        self._build_rgb_section(cols.next())
        cols.place(self._sections[-1])
        self._build_side_leds_section(cols.next())
        cols.place(self._sections[-1])
        # Custom RGB is now the "Custom" entry in the effect dropdown (#34) — no
        # separate section.

        self._app.update_idletasks()
        for s in self._sections:
            s.measure()

    def _build_rgb_section(self, parent):
        title = f"{self.T('rgb_title')}, {self._model_name}"
        s = _Section(parent, self._app, "", title, card=True, auto_pack=False)
        self._sections.append(s)
        self._rgb_section = s
        self._build_rgb_content(s.content)

    def _build_rgb_content(self, parent):
        # Effect capability table (issue #32), from the core: each effect
        # declares the colour modes it supports and a single Color-mode
        # dropdown offers only those, as the original Base Camp does.
        #   name -> (cli_sub, colour_modes, has_speed, has_bri, dir_kind)
        self._rgb_effect_map = {
            e.name: (e.sub, e.color_modes, e.has_speed, e.has_brightness, e.direction)
            for e in P.RGB_EFFECTS
        }
        _rgb_names = [e.name for e in P.RGB_EFFECTS]
        # Color-mode display labels ↔ internal keys.
        self._cmode_labels = {
            "single":  self.T("rgb_cmode_single"),
            "dual":    self.T("rgb_cmode_dual"),
            "rainbow": self.T("rgb_cmode_rainbow"),
        }
        self._cmode_from_label = {v: k for k, v in self._cmode_labels.items()}

        # Effect row
        mode_row = ctk.CTkFrame(parent, fg_color="transparent")
        mode_row.pack(fill="x", padx=10, pady=(10, 2))
        self._reg(ctk.CTkLabel(mode_row, text="", font=(UI.FONT_FAMILY, 11),
                               text_color=FG2), "rgb_mode_label").pack(side="left", padx=(0, 6))
        self._rgb_mode_var = tk.StringVar(value=_rgb_names[0])
        ctk.CTkOptionMenu(
            mode_row, variable=self._rgb_mode_var, values=_rgb_names,
            command=lambda _: self._on_rgb_mode_change(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=180, height=32
        ).pack(side="left")

        # Color-mode row (Single / Dual / Rainbow) — only shown when the effect
        # offers a choice (issue #32).
        self._rgb_cmode_row = ctk.CTkFrame(parent, fg_color="transparent")
        self._reg(ctk.CTkLabel(self._rgb_cmode_row, text="", font=(UI.FONT_FAMILY, 11),
                               text_color=FG2), "rgb_colormode_label").pack(side="left", padx=(0, 6))
        self._rgb_cmode_var = tk.StringVar(value=self._cmode_labels["dual"])
        self._rgb_cmode_menu = ctk.CTkOptionMenu(
            self._rgb_cmode_row, variable=self._rgb_cmode_var,
            values=list(self._cmode_labels.values()),
            command=lambda _: self._on_rgb_cmode_change(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=140, height=32)
        self._rgb_cmode_menu.pack(side="left")

        # Speed / brightness sliders
        def _slider(par, key, init):
            row = ctk.CTkFrame(par, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            self._reg(ctk.CTkLabel(row, text="", text_color=FG2,
                                   font=(UI.FONT_FAMILY, 11), width=120, anchor="w"), key).pack(side="left")
            val_lbl = ctk.CTkLabel(row, text=str(init), text_color=FG,
                                   font=(UI.FONT_FAMILY, 11), width=30)
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
        self._rgb_color_row = ctk.CTkFrame(parent, fg_color="transparent")
        self._rgb_color_row.pack(fill="x", padx=10, pady=2)
        self._rgb_color1 = (255, 0, 0)
        self._rgb_color2 = (0, 0, 255)
        self._rgb_c1_lbl = self._reg(ctk.CTkLabel(
            self._rgb_color_row, text="", text_color=FG2,
            font=(UI.FONT_FAMILY, 11)), "rgb_color1_label")
        self._rgb_c1_lbl.pack(side="left", padx=(0, 4))
        self._rgb_c1_btn = ctk.CTkButton(
            self._rgb_color_row, text="", width=40, height=28,
            fg_color=_hex(*self._rgb_color1), hover_color=_hex(*self._rgb_color1),
            corner_radius=4, command=lambda: self._pick_color(1))
        self._rgb_c1_btn.pack(side="left", padx=(0, 12))
        self._rgb_c2_lbl = self._reg(ctk.CTkLabel(
            self._rgb_color_row, text="", text_color=FG2,
            font=(UI.FONT_FAMILY, 11)), "rgb_color2_label")
        self._rgb_c2_lbl.pack(side="left", padx=(0, 4))
        self._rgb_c2_btn = ctk.CTkButton(
            self._rgb_color_row, text="", width=40, height=28,
            fg_color=_hex(*self._rgb_color2), hover_color=_hex(*self._rgb_color2),
            corner_radius=4, command=lambda: self._pick_color(2))
        self._rgb_c2_btn.pack(side="left")

        # Direction picker
        dir_row = ctk.CTkFrame(parent, fg_color="transparent")
        dir_row.pack(fill="x", padx=10, pady=2)
        self._rgb_dir_row = dir_row
        self._reg(ctk.CTkLabel(dir_row, text="", text_color=FG2,
                               font=(UI.FONT_FAMILY, 11)), "rgb_direction_label").pack(side="left", padx=(0, 6))
        self._dir_wave    = [d[1] for d in P.DIRECTIONS["wave"]]
        self._dir_tornado = [d[1] for d in P.DIRECTIONS["tornado"]]
        self._rgb_dir_map = {d[1]: d[2] for kind in P.DIRECTIONS.values() for d in kind}
        self._rgb_dir_var = tk.StringVar(value=self._dir_wave[0])
        self._rgb_dir_menu = ctk.CTkOptionMenu(
            dir_row, variable=self._rgb_dir_var, values=self._dir_wave,
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=120, height=28)
        self._rgb_dir_menu.pack(side="left")

        # Status + Apply
        self._rgb_status = ctk.CTkLabel(parent, text="", font=(UI.FONT_FAMILY, 11),
                                        text_color=FG2, fg_color="transparent")
        self._rgb_status.pack(pady=(4, 0))
        self._rgb_apply_btn = self._reg(ctk.CTkButton(
            parent, text="", height=32, corner_radius=4,
            fg_color=BLUE, hover_color="#0884be", text_color=FG,
            font=(UI.FONT_FAMILY, 11), command=self._apply_rgb), "rgb_apply")
        self._rgb_apply_btn.pack(fill="x", padx=10, pady=(4, 10))
        # Editor launcher, shown only when the "Custom" effect is selected (#34).
        self._rgb_custom_btn = self._reg(ctk.CTkButton(
            parent, text="", height=32, corner_radius=4,
            fg_color=BLUE, hover_color="#0884be", text_color=FG,
            font=(UI.FONT_FAMILY, 11), command=self._open_custom_rgb), "custom_rgb_open")

        # Version line at the very bottom of the form (FransM's request in #32:
        # a build marker so screenshots/reports can be tied to a version).
        try:
            from gui import APP_VERSION as _ver
        except Exception:
            _ver = "?"
        self._rgb_build_lbl = ctk.CTkLabel(
            parent, text=self.T("rgb_build_label", ver=_ver),
            font=(UI.FONT_FAMILY, 9), text_color=FG2, fg_color="transparent")
        self._rgb_build_ver = _ver  # refreshed in apply_lang (takes a format arg)

        # Restore the saved settings (the service reads them; anything not
        # saved comes back as the defaults these widgets start with).
        saved = self._svc.lighting()
        self._rgb_mode_var.set(P.effect(saved["effect"]).name)
        self._rgb_speed_sl.set(saved["speed"])
        self._rgb_bri_sl.set(saved["brightness"])
        self._rgb_color1 = tuple(saved["color1"])
        h = _hex(*self._rgb_color1)
        self._rgb_c1_btn.configure(fg_color=h, hover_color=h)
        self._rgb_color2 = tuple(saved["color2"])
        h = _hex(*self._rgb_color2)
        self._rgb_c2_btn.configure(fg_color=h, hover_color=h)
        self._rgb_dir_var.set(P.direction(saved["direction"])[1])
        self._rgb_cmode_var.set(self._cmode_labels[saved["color_mode"]])

        self._rgb_update_controls()

    def _build_side_leds_section(self, parent):
        """Side perimeter ring (44 LEDs) — single colour for now.

        Uses the custom-RGB protocol path under the hood, so the main keys
        are blanked when only side colour is applied. A future enhancement
        can fold this into the full custom-RGB editor.
        """
        s = _Section(parent, self._app, "",
                     f"{self.T('side_leds_title')}, {self._model_name}",
                     card=True, auto_pack=False)
        self._sections.append(s)
        self._side_leds_section = s

        row = ctk.CTkFrame(s.content, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 2))
        self._reg(ctk.CTkLabel(
            row, text="", text_color=FG2,
            font=(UI.FONT_FAMILY, 11)), "side_leds_color_label").pack(side="left", padx=(0, 4))
        ring = self._svc.side_ring()   # magenta and 100 until changed
        self._side_led_color = tuple(ring["color"])
        self._side_led_btn = ctk.CTkButton(
            row, text="", width=40, height=28,
            fg_color=_hex(*self._side_led_color),
            hover_color=_hex(*self._side_led_color),
            corner_radius=4, command=self._pick_side_color)
        self._side_led_btn.pack(side="left", padx=(0, 12))

        bri_row = ctk.CTkFrame(s.content, fg_color="transparent")
        bri_row.pack(fill="x", padx=10, pady=2)
        self._reg(ctk.CTkLabel(bri_row, text="", text_color=FG2,
                               font=(UI.FONT_FAMILY, 11), width=120, anchor="w"),
                  "rgb_brightness_label").pack(side="left")
        val_lbl = ctk.CTkLabel(bri_row, text=str(ring["brightness"]), text_color=FG,
                               font=(UI.FONT_FAMILY, 11), width=30)
        val_lbl.pack(side="right")
        self._side_bri_sl = ctk.CTkSlider(
            bri_row, from_=0, to=100, number_of_steps=100,
            fg_color=BG3, progress_color=BLUE,
            button_color=BLUE, button_hover_color=BLUE,
            width=180, height=16)
        self._side_bri_sl.set(ring["brightness"])
        self._side_bri_sl.pack(side="left", padx=(0, 4))
        self._side_bri_sl.configure(
            command=lambda v, l=val_lbl: l.configure(text=str(int(v))))

        self._side_leds_status = ctk.CTkLabel(
            s.content, text="", font=(UI.FONT_FAMILY, 11),
            text_color=FG2, fg_color="transparent")
        self._side_leds_status.pack(pady=(4, 0))

        self._reg(ctk.CTkButton(
            s.content, text="", height=32, corner_radius=4,
            fg_color=BLUE, hover_color="#0884be",
            text_color=FG, font=(UI.FONT_FAMILY, 11),
            command=self._apply_side_leds), "side_leds_apply"
        ).pack(fill="x", padx=10, pady=(4, 10))

        self._reg(ctk.CTkLabel(
            s.content, text="",
            font=(UI.FONT_FAMILY, 9), text_color=FG2,
            wraplength=380, justify="left"), "side_leds_hint"
        ).pack(fill="x", padx=10, pady=(0, 8))

    def _pick_side_color(self):
        from shared.ui_helpers import pick_color
        rgb = pick_color(self._app, initial_rgb=self._side_led_color,
                         title=self.T("color_picker_title"), show_brightness=False)
        if rgb is None:
            return
        self._side_led_color = rgb
        h = _hex(*rgb)
        self._side_led_btn.configure(fg_color=h, hover_color=h)

    def _apply_side_leds(self):
        """Light the side ring in one colour. The service keeps the saved key
        colours alongside it (issue #4: the ring is only reachable in custom
        mode, so a ring-only write would turn the keys off)."""
        self._svc.set_side_ring(color=self._side_led_color,
                                brightness=int(self._side_bri_sl.get()))

    def _open_custom_rgb(self):
        from shared.ui_helpers import CustomRGBWindow, _KB60_LAYOUT, _KB60_CANVAS_W, _KB60_CANVAS_H, _KB60_NUM_LEDS
        self._app.open_screen(
            "custom_rgb_60",
            lambda parent: CustomRGBWindow(
                self._app,
                parent=parent,
                on_close=lambda: self._app.close_screen("custom_rgb_60",
                                                        "everest60"),
                layout=_KB60_LAYOUT,
                canvas_w=_KB60_CANVAS_W,
                # Extra vertical space for the 44-LED side-ring strip below the keys.
                canvas_h=_KB60_CANVAS_H + 96,
                num_leds=_KB60_NUM_LEDS,
                has_side_leds=True,      # per-LED side ring painting (#4)
                num_side_leds=44,        # hw indices 126..169
                side_layout="strip",     # no per-edge ring geometry for the 60 yet
                has_numpad=False,
                has_persist=False,
                load_per_key=_load_per_key_60,
                save_per_key=_save_per_key_60,
                load_presets=_load_presets_60,
                save_presets=_save_presets_60,
                apply_cmd=lambda *a: self._app._cmd_for_device("everest60", *a),
            ),
            title=self.T("custom_rgb_title"))

    def _available_cmodes(self, name):
        """Colour-mode keys the given effect supports (subset of single/dual/rainbow)."""
        return self._rgb_effect_map.get(name, ("", (), False, False, None))[1]

    def _current_cmode(self, name):
        """Effective colour mode: the dropdown choice if the effect offers it,
        else the effect's single supported mode (None for custom/off)."""
        modes = self._available_cmodes(name)
        if not modes:
            return None
        sel = self._cmode_from_label.get(self._rgb_cmode_var.get())
        return sel if sel in modes else modes[0]

    def _on_rgb_mode_change(self):
        """Effect changed: repopulate the colour-mode dropdown with the modes
        this effect supports (keeping the current choice if still valid),
        persist the effect (#34), then refresh which controls are shown."""
        name = self._rgb_mode_var.get()
        modes = self._available_cmodes(name)
        if modes:
            self._rgb_cmode_menu.configure(values=[self._cmode_labels[m] for m in modes])
            if self._cmode_from_label.get(self._rgb_cmode_var.get()) not in modes:
                self._rgb_cmode_var.set(self._cmode_labels[modes[0]])
        try:
            self._svc.save_effect(name)
        except Exception:
            pass
        self._rgb_update_controls()

    def _on_rgb_cmode_change(self):
        """Colour-mode changed — just refresh visibility (colour pickers)."""
        self._rgb_update_controls()

    def _rgb_update_controls(self):
        """Pack exactly the controls the selected (effect, colour-mode) uses, in
        a fixed top-to-bottom order, then re-measure the accordion.

        The previous implementation toggled rows via winfo_ismapped() + re-pack,
        which (a) appended re-shown rows to the bottom, scrambling order, and
        (b) left rows hidden inside the section's fixed measured height. That is
        what produced FransM's 'no speed on breathing', 'no speed/direction on
        wave' and 'rainbow page sticks' reports (#32/#39). Re-packing everything
        in canonical order and re-measuring fixes all three at the root."""
        name = self._rgb_mode_var.get()
        sub, modes, hs, hb, dk = self._rgb_effect_map.get(
            name, ("", (), False, False, None))
        cmode = self._current_cmode(name)

        # Forget every toggleable widget, then re-pack the visible ones in order.
        for w in (self._rgb_cmode_row, self._rgb_speed_row, self._rgb_bri_row,
                  self._rgb_color_row, self._rgb_dir_row, self._rgb_status,
                  self._rgb_apply_btn, self._rgb_custom_btn, self._rgb_build_lbl,
                  self._rgb_c1_lbl, self._rgb_c1_btn,
                  self._rgb_c2_lbl, self._rgb_c2_btn):
            w.pack_forget()

        show_c1 = cmode in ("single", "dual")
        show_c2 = cmode == "dual"

        if len(modes) >= 2:                       # only when there's a real choice
            self._rgb_cmode_row.pack(fill="x", padx=10, pady=2)
        if hs:
            self._rgb_speed_row.pack(fill="x", padx=10, pady=2)
        if hb:
            self._rgb_bri_row.pack(fill="x", padx=10, pady=2)
        if show_c1 or show_c2:
            if show_c1:
                self._rgb_c1_lbl.pack(side="left", padx=(0, 4))
                self._rgb_c1_btn.pack(side="left", padx=(0, 12))
            if show_c2:
                self._rgb_c2_lbl.pack(side="left", padx=(0, 4))
                self._rgb_c2_btn.pack(side="left")
            self._rgb_color_row.pack(fill="x", padx=10, pady=2)
        if dk is not None:
            self._rgb_dir_row.pack(fill="x", padx=10, pady=2)
            dirs = self._dir_tornado if dk == "tornado" else self._dir_wave
            self._rgb_dir_menu.configure(values=dirs)
            if self._rgb_dir_var.get() not in dirs:
                self._rgb_dir_var.set(dirs[0])

        self._rgb_status.pack(pady=(4, 0))
        if sub == "custom":
            self._rgb_custom_btn.pack(fill="x", padx=10, pady=(4, 10))
        elif sub:                                 # any real effect (incl. off)
            self._rgb_apply_btn.pack(fill="x", padx=10, pady=(4, 10))
        self._rgb_build_lbl.pack(pady=(0, 8))     # version line stays at the bottom

        self._remeasure_rgb_section()

    def _remeasure_rgb_section(self):
        """Recompute the RGB accordion's content height so a changed control set
        isn't clipped or bottom-padded by the previously measured height (#32)."""
        sec = getattr(self, "_rgb_section", None)
        if sec is None:
            return
        try:
            sec.measure()
        except Exception:
            pass

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

    def _form_settings(self):
        """The lighting settings as the form shows them (the service takes the
        names and labels as well as ids)."""
        return {
            "effect": self._rgb_mode_var.get(),
            "color_mode": self._cmode_from_label.get(self._rgb_cmode_var.get()),
            "speed": int(self._rgb_speed_sl.get()),
            "brightness": int(self._rgb_bri_sl.get()),
            "color1": list(self._rgb_color1),
            "color2": list(self._rgb_color2),
            "direction": self._rgb_dir_var.get(),
        }

    def _apply_rgb(self):
        settings = self._form_settings()
        if P.rgb_args(P.normalize(settings)) is None:
            return                      # Custom: the per-key editor applies it
        self._rgb_status.configure(text=self.T("rgb_applying"), text_color=YLW)
        self._svc.apply_lighting(settings)

    def apply_saved_rgb(self):
        """Re-push the saved lighting to the keyboard (#42). The service does
        this by itself when the board appears; kept for callers."""
        try:
            self._svc.apply_saved()
        except Exception:
            pass

    # ── Connection state ──────────────────────────────────────────────────────

    def set_connected(self, connected: bool):
        # The service replays the saved lighting when the board appears (#42),
        # after a beat so the HID interface has finished enumerating. The
        # device registry tells it; this is for callers that say it directly.
        self._svc.set_present(connected)
        self._show_connected(connected)

    def _show_connected(self, connected):
        connected = bool(connected)
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
    def __init__(self, parent, app, icon, title, card=False, auto_pack=True,
                 hint=None):
        self._app       = app
        self._card      = card
        self._open      = bool(card)
        self._natural_h = 0

        self._outer = ctk.CTkFrame(
            parent, fg_color=BG2 if card else "transparent",
            corner_radius=7 if card else 0,
            border_width=1 if card else 0, border_color=BORDER)
        if auto_pack:
            self._outer.pack(fill="x", pady=2)

        self._header = ctk.CTkFrame(
            self._outer, fg_color="transparent" if card else BG2,
            corner_radius=6, cursor="arrow" if card else "hand2")
        self._header.pack(fill="x")

        if not card:
            tk.Frame(self._header, bg=YLW, width=4).pack(side="left", fill="y")
        # No icon column unless a caller actually passes one. The emoji that
        # used to sit here came from whatever emoji font was installed, brought
        # its own colour into a system that has a rule for every colour, and
        # left a missing-glyph box on machines without one.
        if icon:
            ctk.CTkLabel(self._header, text=icon, font=(UI.FONT_FAMILY, 14),
                         text_color=YLW, width=30).pack(side="left", padx=(8, 4))
        self._title_lbl = ctk.CTkLabel(self._header, text=title,
                                       font=(UI.FONT_FAMILY, 11, "bold"), text_color=FG, anchor="w")
        self._title_lbl.pack(side="left", fill="x", expand=True, padx=4, pady=12)
        # Right-hand hint in the card header: the current value, so a card
        # says what it is set to without being opened or read in full.
        self._hint_lbl = None
        if card:
            self._hint_lbl = ctk.CTkLabel(
                self._header, text=hint or "", font=(UI.FONT_FAMILY, 11),
                text_color=FG2, anchor="e")
            self._hint_lbl.pack(side="right", padx=(0, 12))

        self._chevron = ctk.CTkLabel(self._header, text="▶",
                                     font=(UI.FONT_FAMILY, 10), text_color=FG2, width=24)
        if not card:
            self._chevron.pack(side="right", padx=(0, 12))

        self._content = ctk.CTkFrame(self._outer, fg_color=BG2, corner_radius=0,
                                     **({} if card else {"height": 0}))
        self._content.pack(fill="both", expand=card, pady=(1, 0))
        if not card:
            self._content.pack_propagate(False)

        if not card:
            def _bind_all(w):
                w.bind("<Button-1>", self._toggle)
                for child in w.winfo_children():
                    _bind_all(child)
            _bind_all(self._header)

    @property
    def content(self):
        return self._content

    def set_hint(self, text):
        if self._hint_lbl is not None:
            self._hint_lbl.configure(text=text or "")

    @property
    def outer(self):
        return self._outer

    def measure(self):
        if self._card:
            return
        self._content.pack_propagate(True)
        self._app.update_idletasks()
        self._natural_h = self._content.winfo_reqheight()
        self._content.pack_propagate(False)
        self._content.configure(height=self._natural_h if self._open else 0)

    def open(self):
        if self._card or self._open:
            return
        self._open = True
        self._chevron.configure(text="▼")
        if self._natural_h > 0:
            self._content.configure(height=self._natural_h)

    def _toggle(self, _=None):
        self.close() if self._open else self.open()

    def close(self):
        if self._card or not self._open:
            return
        self._open = False
        self._chevron.configure(text="▶")
        self._content.configure(height=0)
