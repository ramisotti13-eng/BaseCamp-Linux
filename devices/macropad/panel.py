"""Mountain MacroPad screen for BaseCamp Linux.

Same chassis as the DisplayPad, six keys across and two rows, but the keycaps
are plain: no screens, so there is nothing to upload and nothing to draw. What
is left is the part the DisplayPad also has, lighting and what a key does when
it is pressed.

Key presses come in over the vendor HID collection. The report layout was
measured on two owners' pads in issue #85 and lives in the controller as
KEY_MAP; this screen only turns the decoded indices into actions.

One thread owns the device. Commands from the UI go through a queue and are
sent by that same thread between reads. The DisplayPad learned this the hard
way (issues #26 to #28): a second opener of the one command interface makes
key events and uploads eat each other.
"""
import os
import queue
import threading
import time
import tkinter as tk
import customtkinter as ctk

import shared.ui as UI
from shared.ui_helpers import (
    AccordionSection, pick_color, cap_scroll_speed, menu_order,
    BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, YLW, BORDER,
)
from shared.config import (
    _load_macropad_actions, _save_macropad_actions,
    _load_macropad_rgb, _save_macropad_rgb,
    _load_last_dir, _save_last_dir,
)
from devices.macropad import controller as mp


NUM_KEYS      = mp.NUM_KEYS
KEYS_PER_ROW  = 6      # M1 to M6 on top, M7 to M12 below, as the pad is printed
_TILE         = 84
_INSPECTOR_W  = 250

# Action types this device offers. The DisplayPad list minus everything that
# needs a screen or a page: no image, no page switch, no key redefinition.
_ACTION_TYPES = ["none", "shell", "url", "folder", "app", "obs", "macro",
                 "keypress", "text"]

# Which controls an effect actually uses. Sending a colour to an effect that
# ignores it is harmless, but showing a colour button that changes nothing is
# not, so the panel hides what the effect does not read.
#   effect id: (speed, colour 1, colour 2, direction)
_EFFECT_CONTROLS = {
    mp.EFFECT_STATIC:     (False, True,  False, False),
    mp.EFFECT_BREATHING:  (True,  True,  True,  False),
    mp.EFFECT_WAVE:       (True,  True,  False, True),
    mp.EFFECT_TORNADO:    (True,  True,  False, True),
    mp.EFFECT_MATRIX:     (True,  True,  True,  False),
    mp.EFFECT_YETI:       (True,  True,  True,  False),
    mp.EFFECT_REACTIVE_A: (True,  True,  True,  False),
    mp.EFFECT_REACTIVE_B: (True,  True,  True,  False),
    mp.EFFECT_REACTIVE_C: (True,  True,  True,  False),
    mp.EFFECT_CUSTOM:     (False, False, False, False),
    mp.EFFECT_OFF:        (False, False, False, False),
}

# Wave turns four ways, Tornado two. The wire values behind these are not
# 0 to 3; the controller maps them (#85).
_DIRECTION_KEYS = {
    mp.EFFECT_WAVE:    ("mp_dir_right", "mp_dir_left", "mp_dir_down", "mp_dir_up"),
    mp.EFFECT_TORNADO: ("mp_dir_cw", "mp_dir_ccw"),
}

# The tile colour while a key is being pressed. BG2 is darker than the tile,
# and the labels cover almost all of it anyway, so lifting only the frame to
# BG2 was a flash nobody could see.
_PRESS_BG = "#243247"
# How long a key stays deaf after it fired, so one press is one action.
_DEBOUNCE = 0.25
# How long the worker waits for a report before looking at the command queue.
_READ_MS = 120
# After a failed open, wait this long before trying again. The pad re-enumerates
# on its own now and then, and a tight retry loop would spam the log.
_RETRY_S = 2.0


def _hex(r, g, b):
    return "#%02x%02x%02x" % (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)


def _action_type_entries(app):
    """(id, label) pairs of the action menu, in menu order: None first, then
    the built-in actions and, below a separator, the plugins', each by name
    (#109). Ids and labels come from one place so they cannot drift apart."""
    def label(tid):
        return "OBS" if tid == "obs" else app.T(f"action_type_{tid}")

    entries = [(_ACTION_TYPES[0], label(_ACTION_TYPES[0]))]
    entries += sorted(((t, label(t)) for t in _ACTION_TYPES[1:]),
                      key=lambda e: menu_order(e[1]))
    pm = getattr(app, "_plugin_manager", None)
    if pm:
        plugin = sorted(pm.get_action_type_labels(), key=lambda e: menu_order(e[1]))
        if plugin:
            entries.append(("_separator", "-- Plugins --"))
            entries.extend(plugin)
    return entries


def action_type_ids(app):
    """Internal ids in menu order, with any plugin types after a separator."""
    return [tid for tid, _label in _action_type_entries(app)]


def action_type_labels(app):
    """Translated labels, index aligned with action_type_ids()."""
    return [lbl for _tid, lbl in _action_type_entries(app)]


class MacroPadPanel(ctk.CTkFrame):
    """Lighting and key actions for the Mountain MacroPad."""

    VID = mp.VID
    PID = mp.PID

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app        = app
        self._connected  = False
        self._sections   = []
        self._i18n       = []          # (widget, attr, key)
        self._selected   = 0           # key the inspector is editing
        self._loading    = False       # true while a key is filling the fields
        self._dirty      = False       # true once the fields have been edited

        self._actions = _load_macropad_actions()
        self._rgb     = _load_macropad_rgb()

        # Device thread and the jobs the UI hands it.
        self._jobs      = queue.Queue()
        self._stop      = threading.Event()
        self._worker    = None
        self._held      = set()        # keys currently down, for edge detection
        self._last_fire = {}
        # Toggle macros keep their stop events here, the same contract the
        # DisplayPad has: pressing the key again ends a running toggle macro.
        self._macro_toggles = {}
        self._flash_jobs = {}          # per key, the pending un-flash timer

        self._build_ui()

    # ── i18n ─────────────────────────────────────────────────────────────────

    def T(self, key, **kw):
        text = self._app._lang.get(key, key)
        return text.format(**kw) if kw else text

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
        # The menus are built from the language file every time they are
        # filled, so re-filling them is all a language change needs here.
        self._refresh_type_menu()
        self._refresh_effect_menu()
        for index in range(NUM_KEYS):
            self._refresh_tile(index)
        self._show_key(self._selected)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._banner = ctk.CTkFrame(self, fg_color="#3b1515", corner_radius=6)
        self._banner_lbl = ctk.CTkLabel(
            self._banner, text=self.T("device_not_connected", model="MacroPad"),
            font=(UI.FONT_FAMILY, 11), text_color=RED)
        self._banner_lbl.pack(pady=8, padx=16)
        if not self._connected:
            self._banner.pack(fill="x", padx=12, pady=(8, 4))

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        cap_scroll_speed(scroll)

        cards = ctk.CTkFrame(scroll, fg_color="transparent")
        cards.pack(fill="both", expand=True, padx=12, pady=8)

        # Not two columns: six keys across plus the inspector is wider than
        # half a window, and squeezing the lighting card into the other half
        # clipped its controls. One card under the other, each full width,
        # which is also how the DisplayPad lays its key grid out.
        self._build_keys_section(cards)
        self._sections[-1].outer.pack(fill="x", pady=(0, 12))
        self._build_rgb_section(cards)
        self._sections[-1].outer.pack(fill="x", pady=(0, 12))

        self._app.update_idletasks()

    # ── Keys ─────────────────────────────────────────────────────────────────

    def _build_keys_section(self, parent):
        section = AccordionSection(parent, self._app, "", "mp_keys_title",
                                   card=True, auto_pack=False)
        self._sections.append(section)
        self._build_keys_content(section.content)

    def _build_keys_content(self, parent):
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(side="left", anchor="n")

        # The fixed size sits on the labels, not on the tile frame: a frame
        # told to keep a size and to stop propagating draws its rounded border
        # at the size its canvas ended up with, which leaves the bottom strip
        # hanging outside the outline. Letting the frame size itself to fixed
        # size children keeps the border around all of it.
        self._tiles = []
        self._tile_names = []
        self._tile_labels = []
        self._tile_bars = []
        for index in range(NUM_KEYS):
            row, col = divmod(index, KEYS_PER_ROW)
            tile = ctk.CTkFrame(grid, fg_color=BG3, corner_radius=6,
                                border_width=2, border_color=BG3)
            tile.grid(row=row, column=col, padx=3, pady=3)

            name = ctk.CTkLabel(tile, text="M%d" % (index + 1),
                                font=(UI.FONT_FAMILY, 12, "bold"), text_color=FG,
                                width=_TILE, height=18, fg_color=BG3)
            name.pack(padx=3, pady=(5, 0))
            summary = ctk.CTkLabel(tile, text="", font=(UI.FONT_FAMILY, 9),
                                   text_color=FG2, width=_TILE, height=30,
                                   wraplength=_TILE - 6, fg_color=BG3)
            summary.pack(padx=3)
            # The strip along the bottom is the colour this key gets from the
            # Custom effect, so the grid doubles as the per key colour map.
            bar = ctk.CTkLabel(tile, text="", width=_TILE, height=5,
                               corner_radius=3, fg_color=BG3)
            bar.pack(padx=3, pady=(2, 6))

            for widget in (tile, name, summary, bar):
                widget.bind("<Button-1>", lambda _e, i=index: self._show_key(i))
            self._tiles.append(tile)
            self._tile_names.append(name)
            self._tile_labels.append(summary)
            self._tile_bars.append(bar)

        self._build_inspector(body)
        for index in range(NUM_KEYS):
            self._refresh_tile(index)
        self._show_key(0)

    def _build_inspector(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(side="left", anchor="n", padx=(16, 0))

        self._insp_title = ctk.CTkLabel(box, text="M1",
                                        font=(UI.FONT_FAMILY, 12, "bold"),
                                        text_color=FG, anchor="w")
        self._insp_title.pack(fill="x")

        self._reg(ctk.CTkLabel(box, text="", font=(UI.FONT_FAMILY, 11),
                               text_color=FG2, anchor="w"),
                  "action_label").pack(fill="x", pady=(8, 2))

        self._type_var = tk.StringVar()
        self._type_menu = ctk.CTkOptionMenu(
            box, variable=self._type_var, values=[""],
            command=lambda _v: self._on_type_change(chosen=True),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), height=32,
            width=_INSPECTOR_W)
        self._type_menu.pack(fill="x")

        self._value_var = tk.StringVar()
        self._value_var.trace_add("write", lambda *_a: self._mark_dirty())
        self._value_entry = ctk.CTkEntry(
            box, textvariable=self._value_var, fg_color=BG3, border_color=BORDER,
            text_color=FG, font=(UI.FONT_FAMILY, 11), height=32,
            width=_INSPECTOR_W)
        self._value_entry.pack(fill="x", pady=(6, 0))
        self._value_entry.bind("<Return>", lambda _e: self._commit())
        self._value_entry.bind("<FocusOut>", lambda _e: self._commit())

        self._value_hint = ctk.CTkLabel(box, text="", font=(UI.FONT_FAMILY, 9),
                                        text_color=FG2, anchor="w",
                                        wraplength=_INSPECTOR_W - 8, justify="left")
        self._value_hint.pack(fill="x", pady=(3, 0))

        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        self._browse_btn = self._reg(
            ctk.CTkButton(row, text="", command=self._browse,
                          fg_color=BG3, hover_color=BG2, text_color=FG,
                          font=(UI.FONT_FAMILY, 11), height=30, width=90),
            "mp_browse")
        self._browse_btn.pack(side="left")
        self._reg(ctk.CTkButton(row, text="", command=self._save_action,
                                fg_color=BLUE, hover_color=BLUE, text_color="#ffffff",
                                font=(UI.FONT_FAMILY, 11), height=30),
                  "mp_save_action").pack(side="left", fill="x", expand=True, padx=(6, 0))

        self._reg(ctk.CTkLabel(box, text="", font=(UI.FONT_FAMILY, 11),
                               text_color=FG2, anchor="w"),
                  "custom_rgb_key_color").pack(fill="x", pady=(14, 2))
        color_row = ctk.CTkFrame(box, fg_color="transparent")
        color_row.pack(fill="x")
        self._key_color_btn = ctk.CTkButton(
            color_row, text="", width=48, height=30, command=self._pick_key_color,
            fg_color="#ffffff", hover_color="#ffffff",
            border_width=1, border_color=BORDER)
        self._key_color_btn.pack(side="left")
        self._reg(ctk.CTkButton(color_row, text="", command=self._apply_key_colors,
                                fg_color=BG3, hover_color=BG2, text_color=FG,
                                font=(UI.FONT_FAMILY, 11), height=30),
                  "mp_apply_colors").pack(side="left", fill="x", expand=True, padx=(6, 0))

        self._key_status = ctk.CTkLabel(box, text="", font=(UI.FONT_FAMILY, 10),
                                        text_color=FG2, anchor="w",
                                        wraplength=_INSPECTOR_W - 8, justify="left")
        self._key_status.pack(fill="x", pady=(10, 0))

        self._refresh_type_menu()

    def _refresh_type_menu(self):
        if not hasattr(self, "_type_menu"):
            return
        self._type_ids = action_type_ids(self._app)
        labels = action_type_labels(self._app)
        self._type_menu.configure(values=labels)
        self._type_labels = labels

    def _label_for_type(self, type_id):
        try:
            return self._type_labels[self._type_ids.index(type_id)]
        except (ValueError, AttributeError, IndexError):
            return self._app.T("action_type_none")

    def _type_from_label(self, label):
        try:
            return self._type_ids[self._type_labels.index(label)]
        except (ValueError, AttributeError, IndexError):
            return "none"

    def _show_key(self, index):
        """Point the inspector at key `index` and mark its tile.

        Leaving a key commits whatever is in its fields. Without that, typing
        a command and then clicking the next key silently threw the command
        away: clicking a tile does not move keyboard focus, so there is no
        focus-out to hang the save on either.
        """
        if index != self._selected:
            self._commit()
        self._selected = index
        action = self._actions[index]
        self._loading = True
        try:
            self._insp_title.configure(text="M%d" % (index + 1))
            self._type_var.set(self._label_for_type(action.get("type", "none")))
            self._value_var.set(action.get("action", ""))
            self._on_type_change()
        finally:
            self._loading = False
            self._dirty = False

        stored_type = action.get("type", "none")
        if stored_type not in self._type_ids:
            # Saying so beats a menu that reads "does nothing" over a key that
            # does something.
            self._set_key_status(self.T("mp_type_missing", type=stored_type), YLW)
        else:
            self._set_key_status("")

        color = self._rgb["colors"][index]
        self._key_color_btn.configure(fg_color=_hex(*color), hover_color=_hex(*color))

        for i, tile in enumerate(self._tiles):
            tile.configure(border_color=BLUE if i == index else BG3)

    def _mark_dirty(self):
        if not self._loading:
            self._dirty = True

    def _on_type_change(self, chosen=False):
        """Show the field the chosen type needs and describe what goes in it.

        `chosen` is True when the person picked from the menu, as opposed to a
        key being loaded into the fields.
        """
        if chosen:
            self._mark_dirty()
        type_id = self._type_from_label(self._type_var.get())
        if type_id == "_separator":
            # The separator is a heading, not a choice. Put the key's own type
            # back rather than leaving the menu on it.
            self._type_var.set(self._label_for_type(
                self._actions[self._selected].get("type", "none")))
            return
        # A value belongs to the type it was typed for: "ctrl+shift+m" means
        # nothing once the key becomes a folder, and it would be saved under
        # the new type rather than merely displayed. Same rule the DisplayPad
        # inspector got in #84. Only when the person changed it, not while a
        # key is being loaded into the fields.
        if not self._loading and type_id != self._actions[self._selected].get("type"):
            self._value_var.set("")

        hints = {
            "none": "", "shell": "mp_hint_shell", "url": "mp_hint_url",
            "folder": "mp_hint_folder", "app": "mp_hint_app",
            "obs": "mp_hint_obs", "macro": "mp_hint_macro",
            "keypress": "mp_hint_keypress", "text": "action_type_text_hint",
        }
        key = hints.get(type_id)
        self._value_hint.configure(text=self.T(key) if key else "")
        state = "disabled" if type_id == "none" else "normal"
        self._value_entry.configure(state=state)
        self._browse_btn.configure(
            state="normal" if type_id in ("app", "folder", "shell") else "disabled")
        if not self._loading:
            self._commit()

    def _browse(self):
        """Pick a program or a folder, whichever the type asks for."""
        from tkinter import filedialog
        type_id = self._type_from_label(self._type_var.get())
        start = _load_last_dir("macropad", os.path.expanduser("~"))
        if type_id == "folder":
            path = filedialog.askdirectory(parent=self._app, initialdir=start,
                                           title=self.T("mp_browse"))
        else:
            path = filedialog.askopenfilename(parent=self._app, initialdir=start,
                                              title=self.T("mp_browse"))
        if not path:
            return
        _save_last_dir("macropad", path if os.path.isdir(path) else os.path.dirname(path))
        self._value_var.set(path)
        self._commit()

    def _pending_action(self):
        """What the inspector fields currently say the key should do."""
        type_id = self._type_from_label(self._type_var.get())
        value = self._value_var.get().strip()
        # A key that does nothing carries no leftover text with it.
        return {"type": type_id, "action": "" if type_id == "none" else value}

    def _commit(self, announce=False):
        """Write the fields into the selected key, if they say something new.

        Called on every way out of the fields: picking another key, pressing
        Return, leaving the entry, changing the type, and the Save button.

        Only what was actually edited is written. A key bound to a plugin
        action type whose plugin has since been removed cannot be shown in
        these fields, so the menu falls back to "does nothing"; without this
        guard, merely clicking that key and then the next one would save that
        fallback over the binding.
        """
        if self._loading or not self._dirty:
            if announce:
                self._set_key_status(
                    self.T("mp_act_saved", k=self._selected + 1), GRN)
            return
        index = self._selected
        pending = self._pending_action()
        if pending["type"] == "_separator":
            return
        self._dirty = False
        if pending == self._actions[index]:
            if announce:
                self._set_key_status(self.T("mp_act_saved", k=index + 1), GRN)
            return
        self._actions[index] = pending
        try:
            _save_macropad_actions(self._actions)
        except Exception as exc:
            self._set_key_status(self.T("mp_save_failed", err=str(exc)), RED)
            return
        self._refresh_tile(index)
        self._set_key_status(self.T("mp_act_saved", k=index + 1), GRN)

    def _save_action(self):
        self._commit(announce=True)

    def _refresh_tile(self, index):
        action = self._actions[index]
        type_id = action.get("type", "none")
        value = action.get("action", "")
        if type_id == "none" or not value:
            text = self.T("mp_unassigned")
        else:
            text = value if len(value) <= 34 else value[:33] + "…"
        self._tile_labels[index].configure(text=text)
        color = self._rgb["colors"][index]
        self._tile_bars[index].configure(fg_color=_hex(*color))

    def _set_key_status(self, text, color=FG2):
        self._key_status.configure(text=text, text_color=color)

    # ── Lighting ─────────────────────────────────────────────────────────────

    def _build_rgb_section(self, parent):
        section = AccordionSection(parent, self._app, "", "rgb_title",
                                   card=True, auto_pack=False)
        self._sections.append(section)
        self._build_rgb_content(section.content)

    def _build_rgb_content(self, outer):
        # The card is as wide as the window; the controls are not. Everything
        # goes in a column of its own so the sliders stay a readable length
        # instead of stretching across the screen.
        parent = ctk.CTkFrame(outer, fg_color="transparent")
        parent.pack(anchor="w")
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 2))
        self._reg(ctk.CTkLabel(row, text="", font=(UI.FONT_FAMILY, 11),
                               text_color=FG2, width=110, anchor="w"),
                  "rgb_mode_label").pack(side="left")
        self._effect_var = tk.StringVar()
        self._effect_menu = ctk.CTkOptionMenu(
            row, variable=self._effect_var, values=[""],
            command=lambda _v: self._on_effect_change(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=180, height=32)
        self._effect_menu.pack(side="left")

        def slider(key, initial, callback):
            line = ctk.CTkFrame(parent, fg_color="transparent")
            line.pack(fill="x", padx=10, pady=2)
            self._reg(ctk.CTkLabel(line, text="", text_color=FG2,
                                   font=(UI.FONT_FAMILY, 11), width=110,
                                   anchor="w"), key).pack(side="left")
            value_lbl = ctk.CTkLabel(line, text="%d" % initial, text_color=FG,
                                     font=(UI.FONT_FAMILY, 11), width=34)
            control = ctk.CTkSlider(line, from_=0, to=100, number_of_steps=100,
                                    width=240,
                                    command=lambda v: (value_lbl.configure(text="%d" % int(v)),
                                                       callback(int(v))))
            control.set(initial)
            control.pack(side="left", padx=(0, 6))
            value_lbl.pack(side="left")
            return line, control

        self._bri_row, _ = slider(
            "rgb_brightness_label", int(self._rgb["brightness"]),
            lambda v: self._set_rgb("brightness", v))
        self._speed_row, _ = slider(
            "rgb_speed_label", int(self._rgb["speed"]),
            lambda v: self._set_rgb("speed", v))

        line = ctk.CTkFrame(parent, fg_color="transparent")
        self._reg(ctk.CTkLabel(line, text="", text_color=FG2,
                               font=(UI.FONT_FAMILY, 11), width=110,
                               anchor="w"), "rgb_direction_label").pack(side="left")
        self._dir_var = tk.StringVar()
        self._dir_menu = ctk.CTkOptionMenu(
            line, variable=self._dir_var, values=[""],
            command=lambda _v: self._on_direction_change(),
            fg_color=BG3, button_color=BG3, button_hover_color=BG2,
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=180, height=32)
        self._dir_menu.pack(side="left")
        self._dir_row = line

        self._color_rows = {}
        for slot, key in ((1, "rgb_color1_label"), (2, "rgb_color2_label")):
            line = ctk.CTkFrame(parent, fg_color="transparent")
            line.pack(fill="x", padx=10, pady=2)
            self._reg(ctk.CTkLabel(line, text="", text_color=FG2,
                                   font=(UI.FONT_FAMILY, 11), width=110,
                                   anchor="w"), key).pack(side="left")
            color = self._rgb["color%d" % slot]
            button = ctk.CTkButton(line, text="", width=48, height=28,
                                   command=lambda s=slot: self._pick_effect_color(s),
                                   fg_color=_hex(*color), hover_color=_hex(*color),
                                   border_width=1, border_color=BORDER)
            button.pack(side="left")
            self._color_rows[slot] = (line, button)

        self._custom_hint = ctk.CTkLabel(
            parent, text=self.T("mp_custom_hint"), font=(UI.FONT_FAMILY, 9),
            text_color=FG2, anchor="w", justify="left", wraplength=320)
        self._reg(self._custom_hint, "mp_custom_hint")

        buttons = ctk.CTkFrame(parent, fg_color="transparent")
        self._rgb_buttons = buttons
        buttons.pack(fill="x", padx=10, pady=(10, 4))
        self._reg(ctk.CTkButton(buttons, text="", command=self._apply_lighting,
                                fg_color=BLUE, hover_color=BLUE,
                                text_color="#ffffff", font=(UI.FONT_FAMILY, 11),
                                height=30), "rgb_apply").pack(side="left")
        self._reg(ctk.CTkButton(buttons, text="", command=self._save_to_device,
                                fg_color=BG3, hover_color=BG2, text_color=FG,
                                font=(UI.FONT_FAMILY, 11), height=30),
                  "mp_save_device").pack(side="left", padx=(6, 0))

        self._rgb_status = ctk.CTkLabel(parent, text="", font=(UI.FONT_FAMILY, 10),
                                        text_color=FG2, anchor="w",
                                        justify="left", wraplength=320)
        self._rgb_status.pack(fill="x", padx=10, pady=(2, 10))

        self._refresh_effect_menu()
        self._refresh_direction_menu(self._current_effect())
        self._update_rgb_controls()

    def _refresh_effect_menu(self):
        if not hasattr(self, "_effect_menu"):
            return
        self._effect_ids = [value for value, _key in mp.EFFECTS]
        self._effect_names = [self.T("mp_effect_" + key) for _value, key in mp.EFFECTS]
        self._effect_menu.configure(values=self._effect_names)
        current = int(self._rgb.get("effect", mp.EFFECT_STATIC))
        if current not in self._effect_ids:
            current = mp.EFFECT_STATIC
        self._effect_var.set(self._effect_names[self._effect_ids.index(current)])

    def _current_effect(self):
        try:
            return self._effect_ids[self._effect_names.index(self._effect_var.get())]
        except (ValueError, AttributeError):
            return mp.EFFECT_STATIC

    def _on_effect_change(self):
        self._rgb["effect"] = self._current_effect()
        self._update_rgb_controls()
        self._store_rgb()

    def _update_rgb_controls(self):
        """Show only what the chosen effect reads.

        The whole stack is re-packed rather than each row hidden where it
        stands: pack() appends, so a row taken out and put back would come
        back underneath the Apply button instead of where it belongs.
        """
        effect = self._current_effect()
        has_speed, has_c1, has_c2, has_dir = _EFFECT_CONTROLS.get(
            effect, (True, True, True, False))
        if has_dir:
            self._refresh_direction_menu(effect)
        rows = ((self._bri_row, True),
                (self._speed_row, has_speed),
                (self._dir_row, has_dir),
                (self._color_rows[1][0], has_c1),
                (self._color_rows[2][0], has_c2))
        for widget, _visible in rows:
            widget.pack_forget()
        self._custom_hint.pack_forget()
        self._rgb_buttons.pack_forget()
        self._rgb_status.pack_forget()

        for widget, visible in rows:
            if visible:
                widget.pack(fill="x", padx=10, pady=2)
        if effect == mp.EFFECT_CUSTOM:
            self._custom_hint.pack(fill="x", padx=10, pady=(6, 0))
        self._rgb_buttons.pack(fill="x", padx=10, pady=(10, 4))
        self._rgb_status.pack(fill="x", padx=10, pady=(2, 10))

    def _refresh_direction_menu(self, effect):
        """Fill the direction menu for the effect that is showing."""
        keys = _DIRECTION_KEYS.get(effect, ())
        self._dir_labels = [self.T(k) for k in keys]
        self._dir_menu.configure(values=self._dir_labels or [""])
        index = int(self._rgb.get("direction", 0))
        if index >= len(self._dir_labels):
            index = 0
        if self._dir_labels:
            self._dir_var.set(self._dir_labels[index])

    def _on_direction_change(self):
        try:
            self._rgb["direction"] = self._dir_labels.index(self._dir_var.get())
        except (ValueError, AttributeError):
            self._rgb["direction"] = 0
        self._store_rgb()

    def _set_rgb(self, key, value):
        self._rgb[key] = value
        self._store_rgb()

    def _store_rgb(self):
        try:
            _save_macropad_rgb(self._rgb)
        except Exception as exc:
            self._set_rgb_status(self.T("mp_save_failed", err=str(exc)), RED)

    def _pick_effect_color(self, slot):
        initial = tuple(self._rgb["color%d" % slot])
        rgb = pick_color(self._app, initial_rgb=initial,
                         title=self.T("color_picker_title"), show_brightness=False)
        if rgb is None:
            return
        self._rgb["color%d" % slot] = list(rgb)
        self._color_rows[slot][1].configure(fg_color=_hex(*rgb), hover_color=_hex(*rgb))
        self._store_rgb()

    def _pick_key_color(self):
        index = self._selected
        initial = tuple(self._rgb["colors"][index])
        rgb = pick_color(self._app, initial_rgb=initial,
                         title=self.T("color_picker_title"), show_brightness=False)
        if rgb is None:
            return
        self._rgb["colors"][index] = list(rgb)
        self._key_color_btn.configure(fg_color=_hex(*rgb), hover_color=_hex(*rgb))
        self._refresh_tile(index)
        self._store_rgb()

    def _set_rgb_status(self, text, color=FG2):
        self._rgb_status.configure(text=text, text_color=color)

    # ── Talking to the pad ───────────────────────────────────────────────────

    def _apply_lighting(self):
        """Send the current effect. Custom uploads the 12 colours instead."""
        effect = self._current_effect()
        brightness = int(self._rgb["brightness"])
        if effect == mp.EFFECT_CUSTOM:
            self._apply_key_colors()
            return
        _has_speed, has_c1, has_c2, has_dir = _EFFECT_CONTROLS.get(
            effect, (True, True, True, False))
        kwargs = {"brightness": brightness, "speed": int(self._rgb["speed"])}
        if has_c1:
            kwargs["color1"] = tuple(self._rgb["color1"])
        if has_c2:
            kwargs["color2"] = tuple(self._rgb["color2"])
        if has_dir:
            kwargs["direction"] = int(self._rgb.get("direction", 0))
        self._set_rgb_status(self.T("rgb_applying"))
        self._submit("effect", effect=effect, kwargs=kwargs)

    def _apply_key_colors(self):
        """Send the 12 key colours. The pad switches to the Custom effect to
        show them, so the effect menu has to follow, or the screen claims one
        effect while the pad runs another."""
        if self._current_effect() != mp.EFFECT_CUSTOM:
            self._effect_var.set(
                self._effect_names[self._effect_ids.index(mp.EFFECT_CUSTOM)])
            self._on_effect_change()
        self._set_rgb_status(self.T("rgb_applying"))
        if self._submit("colors",
                        colors=[tuple(c) for c in self._rgb["colors"]],
                        brightness=int(self._rgb["brightness"])):
            self._set_key_status(self.T("rgb_applying"))
        else:
            # The button that was pressed is in the inspector, so the reason
            # nothing happened belongs next to it as well.
            self._set_key_status(
                self.T("device_not_connected", model="MacroPad"), RED)

    def _save_to_device(self):
        """Write the current state into the pad's flash, so it survives a
        replug without the app running."""
        self._set_rgb_status(self.T("mp_saving"))
        self._submit("save")

    def _submit(self, kind, **payload):
        """Hand a job to the device thread. False when there is no pad to
        hand it to, so a caller can say so where the button was pressed."""
        if not self._connected:
            self._set_rgb_status(self.T("device_not_connected", model="MacroPad"), RED)
            return False
        payload["kind"] = kind
        self._jobs.put(payload)
        return True

    # ── The one thread that owns the device ──────────────────────────────────

    def set_connected(self, connected):
        """Called by the device scan. Owning the pad is what makes key presses
        arrive, so the thread runs whenever the pad is here, not only while
        this screen is the one on show."""
        connected = bool(connected)
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            self._banner.pack_forget()
            self._start_worker()
        else:
            self._banner.pack(fill="x", padx=12, pady=(8, 4))
            self._stop_worker()

    def _start_worker(self):
        """Start the device thread, waiting out any predecessor first.

        A pad unplugged and plugged back in within a second or two would
        otherwise leave two threads on the one command interface: the outgoing
        one has not reached its next stop check yet. Each worker owns its own
        stop event, so a stopped one can never be revived, and the retry runs
        from the Tk loop rather than blocking it on a join.

        The "already serving" case has to end the chain rather than book
        another retry, or a pad flapping twice leaves a 500 ms timer waking the
        application forever, one more chain per flap.
        """
        if not self._connected:
            return
        previous = self._worker
        if previous is not None and previous.is_alive():
            if not self._stop.is_set():
                return                      # serving already, nothing to do
            self.after(500, self._start_worker)
            return
        stop = threading.Event()
        self._stop = stop
        self._worker = threading.Thread(target=self._worker_loop, args=(stop,),
                                        daemon=True)
        self._worker.start()

    def _stop_worker(self):
        # The reference stays, so _start_worker can see the thread is still on
        # its way out. is_alive() is what tells the two apart.
        self._stop.set()
        self._held.clear()

    def _worker_loop(self, stop):
        while not stop.is_set():
            pad = None
            try:
                pad = mp.MacroPad()
                pad.init()
            except Exception as exc:
                if pad is not None:
                    pad.close()
                self._post_status(str(exc), RED)
                # Whatever was queued was meant for a pad that is not
                # answering. Dropping it keeps a person clicking Apply at an
                # unreachable pad from building a backlog that all fires at
                # once the moment it comes back.
                self._drop_jobs()
                # A pad that is present but not readable is almost always the
                # udev rule; the app says so once through _check_device_access.
                stop.wait(_RETRY_S)
                continue
            self._post_status(self.T("mp_ready"), GRN)
            try:
                self._serve(pad, stop)
            except Exception as exc:
                self._post_status(str(exc), RED)
                stop.wait(_RETRY_S)
            finally:
                pad.close()
                self._held.clear()

    def _drop_jobs(self):
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                return

    def _serve(self, pad, stop):
        """Send what the UI queued, read what the pad says, until told to stop."""
        while not stop.is_set():
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                self._run_job(pad, job)
            data = pad.dev.read(mp.PAYLOAD_LEN, timeout=_READ_MS)
            if data:
                self._on_report(data)
            # A command reply arriving with key reports behind it parks them
            # on the device object rather than dropping them.
            for event in pad.drain_key_events():
                self._on_report(event)

    def _run_job(self, pad, job):
        kind = job.get("kind")
        try:
            if kind == "effect":
                # Wave and Tornado ride a different command with a different
                # struct; the controller picks (#85).
                pad.set_effect_auto(job["effect"], **job["kwargs"])
                self._post_status(self.T("rgb_applied"), GRN)
            elif kind == "colors":
                pad.set_key_colors(job["colors"], brightness=job["brightness"])
                self._post_status(self.T("rgb_applied"), GRN)
            elif kind == "save":
                pad.save()
                self._post_status(self.T("mp_saved"), GRN)
        except Exception as exc:
            self._post_status(self.T("dp_error", err=str(exc)), RED)

    def _on_report(self, data):
        """One input report: fire the actions for keys that just went down."""
        if not mp.is_key_event(data):
            return
        pressed = mp.decode_key_event(data)
        new = pressed - self._held
        self._held = pressed
        now = time.monotonic()
        for index in sorted(new):
            if now - self._last_fire.get(index, 0.0) < _DEBOUNCE:
                continue
            self._last_fire[index] = now
            try:
                self.after(0, lambda i=index: self._fire(i))
            except Exception:
                # The window is going away underneath us. Reports arriving in
                # that moment are not worth taking the thread down for, and
                # without this the failure surfaces as a device error and the
                # worker starts reopening a pad nobody is watching.
                return

    def _post_status(self, text, color=FG2):
        """Status from the worker thread, applied on the UI thread."""
        try:
            self.after(0, lambda: self._set_rgb_status(text, color))
        except Exception:
            pass

    # ── Actions ──────────────────────────────────────────────────────────────

    def _fire(self, index):
        action = self._actions[index] if index < len(self._actions) else {}
        self._flash_tile(index)
        self._run_action(action.get("type", "none"), action.get("action", ""))

    def _flash_tile(self, index):
        """Light the tile of the key that was pressed, so the screen shows the
        pad is reaching it even before an action does anything visible.

        The labels have to change with the frame: they carry their own
        background and cover all but a few pixels of it.
        """
        widgets = (self._tiles[index], self._tile_names[index],
                   self._tile_labels[index])
        pending = self._flash_jobs.pop(index, None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except Exception:
                pass
        for widget in widgets:
            widget.configure(fg_color=_PRESS_BG)
        self._flash_jobs[index] = self.after(
            160, lambda i=index, w=widgets: self._unflash_tile(i, w))

    def _unflash_tile(self, index, widgets):
        self._flash_jobs.pop(index, None)
        for widget in widgets:
            try:
                widget.configure(fg_color=BG3)
            except Exception:
                pass

    def _run_action(self, type_id, value):
        """Run one action. The types are the DisplayPad's, minus the ones that
        need a page or a screen, and they route to the same helpers."""
        if type_id == "obs" and value:
            obs = getattr(self._app, "_obs_panel", None)
            if obs is None:
                return
            if value.startswith("scene:"):
                obs.execute_action("scene", value[6:])
            elif value in ("record", "stream"):
                obs.execute_action(value)
            return
        if type_id == "macro" and value:
            from shared.macros import execute_macro
            from shared.config import load_macros
            macro = load_macros().get("macros", {}).get(value)
            if not macro:
                return
            stop_event = None
            if macro.get("repeat_mode") == "toggle":
                if value in self._macro_toggles:
                    self._macro_toggles.pop(value).set()
                    return
                stop_event = threading.Event()
                self._macro_toggles[value] = stop_event
            threading.Thread(target=execute_macro, args=(macro, stop_event),
                             daemon=True).start()
            return
        if type_id == "keypress" and value:
            from shared.macros import simulate_keypress
            threading.Thread(target=simulate_keypress, args=(value,),
                             daemon=True).start()
            return
        if type_id == "text" and value:
            from shared.macros import simulate_text
            threading.Thread(target=simulate_text, args=(value,), daemon=True).start()
            return
        pm = getattr(self._app, "_plugin_manager", None)
        if pm:
            handler = pm.get_action_handler(type_id)
            if handler:
                threading.Thread(target=handler, args=(value,), daemon=True).start()
                return
        if type_id == "none" or not value:
            return
        # Only the built-in types reach a shell. A key still bound to a plugin
        # type whose plugin has been removed used to fall through to here, and
        # its stored value, which was written for that plugin and not for a
        # shell, was run as a command.
        if type_id not in ("shell", "app", "url", "folder"):
            print("[MacroPad] key action type %r is not installed, "
                  "doing nothing" % type_id, flush=True)
            return
        from shared.macros import _run_shell, _run_xdg_open
        try:
            if type_id in ("url", "folder"):
                _run_xdg_open(value)
            else:
                _run_shell(value)
        except Exception:
            pass
