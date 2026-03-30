"""Macro editor panel for BaseCamp Linux hub."""
import os
import time
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
import json

from shared.config import load_macros, save_macros
from shared.macros import (
    generate_macro_id, ACTION_TYPES, ACTION_LABELS, REPEAT_MODES,
    KEYSYM_TO_FRIENDLY, check_macro_tools, get_mouse_location,
    save_mouse_recording, list_mouse_recordings,
)
from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, BORDER


def _placeholder_for_type(atype):
    return {
        "key_down": "ctrl, a, f1...",
        "key_up": "ctrl, a, f1...",
        "key_tap": "ctrl, a, f1...",
        "mouse_click": "left / right / middle",
        "mouse_move": "x, y",
        "mouse_path": "recording.json",
        "mouse_scroll": "up 3 / down 5",
    }.get(atype, "")


class MacroPanel(ctk.CTkFrame):
    """Macro editor panel — create, edit, and manage macros."""

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app = app
        self._macros = load_macros().get("macros", {})
        self._selected_id = None
        self._action_rows = []  # list of action row widget sets
        self._build_ui()
        self._refresh_macro_list()

    def T(self, key, **kwargs):
        return self._app.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_macro_names(self):
        """Return {uuid: name} dict for macro picker combos in other panels."""
        return {uid: m.get("name", uid) for uid, m in self._macros.items()}

    def apply_lang(self):
        """Refresh i18n labels."""
        self._title_lbl.configure(text=self.T("macro_title"))
        self._new_btn.configure(text="+ " + self.T("macro_new"))
        if self._selected_id:
            self._refresh_editor()

    # ── Save / Load ──────────────────────────────────────────────────────────

    def _save(self):
        save_macros({"macros": self._macros})

    def _auto_save_current(self):
        """Save the current editor state to the selected macro and persist."""
        if getattr(self, "_populating", False):
            return
        if not self._selected_id or self._selected_id not in self._macros:
            return
        m = self._macros[self._selected_id]
        m["name"] = self._name_var.get().strip() or "Macro"
        m["repeat_mode"] = self._repeat_label_to_internal(self._repeat_var.get())
        try:
            m["repeat_count"] = max(1, int(self._count_var.get()))
        except ValueError:
            m["repeat_count"] = 1
        m["actions"] = self._collect_actions()
        self._save()
        # Update macro list button text
        self._refresh_macro_list(keep_selection=True)

    def _collect_actions(self):
        """Read action data from the editor rows."""
        # Reverse map: display label → internal type
        label_to_type = {v: k for k, v in ACTION_LABELS.items()}
        actions = []
        for row in self._action_rows:
            label = row["type_var"].get()
            atype = label_to_type.get(label, label)  # fallback to raw value
            value = row["value_var"].get()
            delay = row["delay_var"].get()
            try:
                delay = int(delay)
            except ValueError:
                delay = 0
            actions.append({"type": atype, "value": value, "delay": delay})
        return actions

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))

        # Cap scroll speed
        _c = scroll._parent_canvas
        _orig = _c.yview
        def _capped(*args):
            if args and args[0] == "scroll":
                n = max(-2, min(2, int(args[1])))
                w = args[2] if len(args) > 2 else "units"
                return _orig("scroll", n, w)
            return _orig(*args)
        _c.yview = _capped

        # Header
        hdr = ctk.CTkFrame(scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(10, 4))

        self._title_lbl = ctk.CTkLabel(
            hdr, text=self.T("macro_title"),
            font=("Helvetica", 14, "bold"), text_color=FG)
        self._title_lbl.pack(side="left")

        self._new_btn = ctk.CTkButton(
            hdr, text="+ " + self.T("macro_new"),
            font=("Helvetica", 11, "bold"), fg_color=BLUE,
            hover_color="#0884be", text_color=FG,
            height=28, corner_radius=4, width=110,
            command=self._new_macro)
        self._new_btn.pack(side="right")

        # Macro list area (scrollable, max height 120px)
        self._list_frame = ctk.CTkScrollableFrame(
            scroll, fg_color=BG3, corner_radius=4, height=100)
        self._list_frame.pack(fill="x", padx=12, pady=(0, 6))
        # Cap scroll speed on macro list
        _lc = self._list_frame._parent_canvas
        _lorig = _lc.yview
        def _lcapped(*args):
            if args and args[0] == "scroll":
                n = max(-2, min(2, int(args[1])))
                w = args[2] if len(args) > 2 else "units"
                return _lorig("scroll", n, w)
            return _lorig(*args)
        _lc.yview = _lcapped
        self._list_inner = self._list_frame  # buttons go directly into scrollable

        self._empty_lbl = ctk.CTkLabel(
            self._list_inner, text=self.T("macro_no_macros"),
            font=("Helvetica", 11), text_color=FG2)

        # Editor area
        self._editor_frame = ctk.CTkFrame(scroll, fg_color=BG3, corner_radius=4)
        self._editor_frame.pack(fill="x", padx=12, pady=(0, 6))

        # Name row
        name_row = ctk.CTkFrame(self._editor_frame, fg_color="transparent")
        name_row.pack(fill="x", padx=8, pady=(8, 2))
        ctk.CTkLabel(name_row, text=self.T("macro_name_label"),
                     font=("Helvetica", 11), text_color=FG2).pack(side="left")
        self._name_var = tk.StringVar()
        self._name_entry = ctk.CTkEntry(
            name_row, textvariable=self._name_var, width=240, height=28,
            fg_color=BG2, text_color=FG, border_color=BORDER,
            font=("Helvetica", 11))
        self._name_entry.pack(side="left", padx=(4, 0))
        self._name_entry.bind("<FocusOut>", lambda e: self._auto_save_current())
        self._name_entry.bind("<Return>", lambda e: self._auto_save_current())

        # Repeat row
        rep_row = ctk.CTkFrame(self._editor_frame, fg_color="transparent")
        rep_row.pack(fill="x", padx=8, pady=(2, 2))
        ctk.CTkLabel(rep_row, text=self.T("macro_repeat_label"),
                     font=("Helvetica", 11), text_color=FG2).pack(side="left")
        self._repeat_var = tk.StringVar(value="once")
        self._repeat_menu = ctk.CTkOptionMenu(
            rep_row, variable=self._repeat_var,
            values=self._repeat_labels(),
            width=100, height=28,
            fg_color=BG2, text_color=FG, button_color=BG2,
            font=("Helvetica", 11),
            command=lambda _: self._on_repeat_change())
        self._repeat_menu.pack(side="left", padx=(4, 8))

        ctk.CTkLabel(rep_row, text=self.T("macro_count_label"),
                     font=("Helvetica", 11), text_color=FG2).pack(side="left")
        self._count_var = tk.StringVar(value="1")
        self._count_entry = ctk.CTkEntry(
            rep_row, textvariable=self._count_var, width=50, height=28,
            fg_color=BG2, text_color=FG, border_color=BORDER,
            font=("Helvetica", 11))
        self._count_entry.pack(side="left", padx=(4, 0))

        # Actions label
        ctk.CTkLabel(self._editor_frame, text=self.T("macro_actions_label"),
                     font=("Helvetica", 12, "bold"), text_color=FG
                     ).pack(padx=8, pady=(6, 2), anchor="w")

        # Actions container
        self._actions_frame = ctk.CTkFrame(self._editor_frame, fg_color="transparent")
        self._actions_frame.pack(fill="x", padx=4, pady=(0, 2))

        # Add action / record mouse buttons
        self._btn_row = ctk.CTkFrame(self._editor_frame, fg_color="transparent")
        self._btn_row.pack(pady=(2, 6))

        self._add_action_btn = ctk.CTkButton(
            self._btn_row, text="+ " + self.T("macro_add_action"),
            font=("Helvetica", 11), fg_color=BG2, hover_color="#333348",
            text_color=FG, height=28, corner_radius=4, width=130,
            command=self._add_action)
        self._add_action_btn.pack(side="left", padx=(0, 4))

        self._rec_mouse_btn = ctk.CTkButton(
            self._btn_row, text=self.T("macro_rec_mouse"),
            font=("Helvetica", 11), fg_color=BG2, hover_color="#333348",
            text_color=FG, height=28, corner_radius=4, width=130,
            command=self._start_mouse_record)
        self._rec_mouse_btn.pack(side="left")

        # Bottom buttons
        bottom = ctk.CTkFrame(self._editor_frame, fg_color="transparent")
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        self._del_btn = ctk.CTkButton(
            bottom, text=self.T("macro_delete"),
            font=("Helvetica", 11, "bold"), fg_color=RED, hover_color="#cc3333",
            text_color=FG, height=28, corner_radius=4, width=80,
            command=self._delete_macro)
        self._del_btn.pack(side="left", padx=(0, 4))

        self._dup_btn = ctk.CTkButton(
            bottom, text=self.T("macro_duplicate"),
            font=("Helvetica", 11), fg_color=BG2, hover_color="#333348",
            text_color=FG, height=28, corner_radius=4, width=80,
            command=self._duplicate_macro)
        self._dup_btn.pack(side="left", padx=(0, 4))

        self._export_btn = ctk.CTkButton(
            bottom, text=self.T("macro_export"),
            font=("Helvetica", 11), fg_color=BG2, hover_color="#333348",
            text_color=FG, height=28, corner_radius=4, width=70,
            command=self._export_macro)
        self._export_btn.pack(side="left", padx=(0, 4))

        self._import_btn = ctk.CTkButton(
            bottom, text=self.T("macro_import"),
            font=("Helvetica", 11), fg_color=BG2, hover_color="#333348",
            text_color=FG, height=28, corner_radius=4, width=70,
            command=self._import_macro)
        self._import_btn.pack(side="left")

        # Initially hide editor if no macros
        if not self._macros:
            self._editor_frame.pack_forget()

    # ── Repeat mode helpers ──────────────────────────────────────────────────

    def _repeat_labels(self):
        return [self.T("macro_repeat_once"), self.T("macro_repeat_n"),
                self.T("macro_repeat_toggle")]

    def _repeat_label_to_internal(self, label):
        labels = self._repeat_labels()
        modes = REPEAT_MODES
        for l, m in zip(labels, modes):
            if l == label:
                return m
        return "once"

    def _repeat_internal_to_label(self, mode):
        labels = self._repeat_labels()
        modes = REPEAT_MODES
        for l, m in zip(labels, modes):
            if m == mode:
                return l
        return labels[0]

    def _on_repeat_change(self):
        label = self._repeat_var.get()
        mode = self._repeat_label_to_internal(label)
        # Show/hide count entry
        if mode == "repeat":
            self._count_entry.configure(state="normal")
        else:
            self._count_entry.configure(state="disabled")
        # Full save (including current actions/name/count)
        self._auto_save_current()

    # ── Macro list ───────────────────────────────────────────────────────────

    def _refresh_macro_list(self, keep_selection=False):
        for w in self._list_inner.winfo_children():
            w.destroy()

        if not self._macros:
            self._empty_lbl = ctk.CTkLabel(
                self._list_inner, text=self.T("macro_no_macros"),
                font=("Helvetica", 11), text_color=FG2)
            self._empty_lbl.pack(pady=6)
            self._editor_frame.pack_forget()
            return

        for uid, m in self._macros.items():
            name = m.get("name", uid)
            is_sel = uid == self._selected_id
            row = ctk.CTkFrame(self._list_inner, fg_color="transparent")
            row.pack(fill="x", padx=2, pady=1)
            btn = ctk.CTkButton(
                row, text=name, anchor="w",
                font=("Helvetica", 11, "bold") if is_sel else ("Helvetica", 11),
                fg_color=BLUE if is_sel else BG2,
                hover_color="#0884be" if is_sel else "#333348",
                text_color=FG, height=28, corner_radius=4,
                command=lambda u=uid: self._select_macro(u))
            btn.pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row, text="✕", width=28, height=28, corner_radius=4,
                fg_color=RED, hover_color="#cc3333", text_color=FG,
                font=("Helvetica", 10),
                command=lambda u=uid: self._quick_delete(u)
            ).pack(side="right", padx=(2, 0))

        if not keep_selection:
            return
        # Re-show editor if we have a selection
        if self._selected_id:
            try:
                self._editor_frame.pack(fill="x", padx=12, pady=(0, 6))
            except Exception:
                pass

    def _select_macro(self, macro_id):
        self._selected_id = macro_id
        self._refresh_macro_list(keep_selection=True)
        self._refresh_editor()
        # Make sure editor is visible
        self._editor_frame.pack_forget()
        self._editor_frame.pack(fill="x", padx=12, pady=(0, 6))

    def _refresh_editor(self):
        """Populate the editor with the selected macro's data."""
        if not self._selected_id or self._selected_id not in self._macros:
            return
        m = self._macros[self._selected_id]

        self._name_var.set(m.get("name", ""))
        mode = m.get("repeat_mode", "once")
        self._repeat_var.set(self._repeat_internal_to_label(mode))
        self._count_var.set(str(m.get("repeat_count", 1)))
        self._count_entry.configure(state="normal" if mode == "repeat" else "disabled")

        # Rebuild action rows
        self._clear_action_rows()
        for act in m.get("actions", []):
            self._add_action_row(act)

    # ── Action rows ──────────────────────────────────────────────────────────

    def _clear_action_rows(self):
        # Destroy the entire actions container and recreate it —
        # avoids customtkinter trace conflicts when destroying individual widgets
        self._actions_frame.destroy()
        self._actions_frame = ctk.CTkFrame(self._editor_frame, fg_color="transparent")
        # Re-pack in the right position (after the "Actions" label, before the buttons)
        self._actions_frame.pack(fill="x", padx=4, pady=(0, 2),
                                 before=self._btn_row)
        self._action_rows = []

    def _add_action(self):
        """Add a new empty action and save."""
        self._add_action_row({"type": "key_tap", "value": "", "delay": 10})
        self._auto_save_current()

    def _add_action_row(self, act_data=None):
        if act_data is None:
            act_data = {"type": "key_tap", "value": "", "delay": 10}

        idx = len(self._action_rows)
        frame = ctk.CTkFrame(self._actions_frame, fg_color=BG2, corner_radius=4)
        frame.pack(fill="x", padx=4, pady=2)

        # Row 1: number + type + value + delay
        r1 = ctk.CTkFrame(frame, fg_color="transparent")
        r1.pack(fill="x", padx=4, pady=(4, 0))

        num_lbl = ctk.CTkLabel(r1, text=f"{idx + 1}.",
                               font=("Helvetica", 11, "bold"), text_color=FG2,
                               width=24)
        num_lbl.pack(side="left")

        type_var = tk.StringVar(value=ACTION_LABELS.get(act_data["type"], act_data["type"]))
        type_labels = [ACTION_LABELS.get(t, t) for t in ACTION_TYPES]
        type_menu = ctk.CTkOptionMenu(
            r1, variable=type_var, values=type_labels,
            width=90, height=26, fg_color=BG3, text_color=FG,
            button_color=BG3, font=("Helvetica", 10),
            command=lambda val, i=idx: self._on_action_type_change(i, val))
        type_menu.pack(side="left", padx=(2, 4))

        value_var = tk.StringVar(value=act_data.get("value", ""))

        atype = act_data["type"]
        is_key_type = atype in ("key_down", "key_up", "key_tap")
        is_mouse_click = atype == "mouse_click"
        is_mouse_path = atype == "mouse_path"
        placeholder = _placeholder_for_type(atype)
        has_btn = is_key_type or is_mouse_click or is_mouse_path

        value_widget = ctk.CTkEntry(
            r1, textvariable=value_var,
            width=90 if has_btn else 110,
            height=26, fg_color=BG3, text_color=FG,
            border_color=BORDER, font=("Helvetica", 10),
            placeholder_text=placeholder)
        if is_mouse_path:
            value_widget.configure(state="readonly")
        value_widget.pack(side="left", padx=(0, 2))

        # Rec/Pick button depending on action type
        rec_btn = None
        if is_key_type:
            rec_btn = ctk.CTkButton(
                r1, text="Rec", width=34, height=26, corner_radius=3,
                fg_color=BG3, hover_color="#333348", text_color=FG2,
                font=("Helvetica", 9),
                command=lambda i=idx: self._start_key_record(i))
            rec_btn.pack(side="left", padx=(0, 2))
        elif is_mouse_click:
            rec_btn = ctk.CTkButton(
                r1, text="Rec", width=34, height=26, corner_radius=3,
                fg_color=BG3, hover_color="#333348", text_color=FG2,
                font=("Helvetica", 9),
                command=lambda i=idx: self._start_click_record(i))
            rec_btn.pack(side="left", padx=(0, 2))
        elif is_mouse_path:
            rec_btn = ctk.CTkButton(
                r1, text="...", width=34, height=26, corner_radius=3,
                fg_color=BG3, hover_color="#333348", text_color=FG2,
                font=("Helvetica", 9),
                command=lambda i=idx: self._pick_mouse_path(i))
            rec_btn.pack(side="left", padx=(0, 2))

        # Delay (hidden for "delay" type)
        delay_frame = ctk.CTkFrame(r1, fg_color="transparent")
        delay_frame.pack(side="left", padx=(0, 2))
        ctk.CTkLabel(delay_frame, text="ms:", font=("Helvetica", 10),
                     text_color=FG2).pack(side="left")
        delay_var = tk.StringVar(value=str(act_data.get("delay", 0)))
        delay_entry = ctk.CTkEntry(
            delay_frame, textvariable=delay_var, width=45, height=26,
            fg_color=BG3, text_color=FG, border_color=BORDER,
            font=("Helvetica", 10))
        delay_entry.pack(side="left", padx=2)

        # Save on focus-out or Enter for value and delay fields
        value_widget.bind("<FocusOut>", lambda e: self._on_value_edit())
        value_widget.bind("<Return>", lambda e: self._on_value_edit())
        delay_entry.bind("<FocusOut>", lambda e: self._on_value_edit())
        delay_entry.bind("<Return>", lambda e: self._on_value_edit())

        if atype == "delay":
            delay_frame.pack_forget()

        # Row 2: move up / move down / delete
        r2 = ctk.CTkFrame(frame, fg_color="transparent")
        r2.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkButton(
            r2, text="▲", width=30, height=22, corner_radius=3,
            fg_color=BG3, hover_color="#333348", text_color=FG2,
            font=("Helvetica", 10),
            command=lambda i=idx: self._move_action(i, -1)
        ).pack(side="left", padx=(24, 2))

        ctk.CTkButton(
            r2, text="▼", width=30, height=22, corner_radius=3,
            fg_color=BG3, hover_color="#333348", text_color=FG2,
            font=("Helvetica", 10),
            command=lambda i=idx: self._move_action(i, 1)
        ).pack(side="left", padx=(0, 2))

        ctk.CTkButton(
            r2, text="✕", width=30, height=22, corner_radius=3,
            fg_color=RED, hover_color="#cc3333", text_color=FG,
            font=("Helvetica", 10),
            command=lambda i=idx: self._remove_action(i)
        ).pack(side="left", padx=(0, 2))

        row_data = {
            "frame": frame,
            "type_var": type_var,
            "value_var": value_var,
            "delay_var": delay_var,
            "value_widget": value_widget,
            "rec_btn": rec_btn,
            "delay_frame": delay_frame,
            "num_lbl": num_lbl,
        }
        self._action_rows.append(row_data)

    def _on_action_type_change(self, idx, label):
        """When action type dropdown changes, swap value widget and save."""
        # Map label back to internal type
        atype = "key_tap"
        for t in ACTION_TYPES:
            if ACTION_LABELS.get(t, t) == label:
                atype = t
                break

        row = self._action_rows[idx]
        old_widget = row["value_widget"]
        old_widget.destroy()
        if row.get("rec_btn"):
            row["rec_btn"].destroy()
            row["rec_btn"] = None

        r1 = row["frame"].winfo_children()[0]  # first row frame
        is_key_type = atype in ("key_down", "key_up", "key_tap")
        is_mouse_click = atype == "mouse_click"
        is_mouse_path = atype == "mouse_path"
        placeholder = _placeholder_for_type(atype)
        has_btn = is_key_type or is_mouse_click or is_mouse_path

        new_widget = ctk.CTkEntry(
            r1, textvariable=row["value_var"],
            width=90 if has_btn else 110,
            height=26, fg_color=BG3, text_color=FG,
            border_color=BORDER, font=("Helvetica", 10),
            placeholder_text=placeholder)
        if is_mouse_path:
            new_widget.configure(state="readonly")
        new_widget.pack(side="left", padx=(0, 2), after=r1.winfo_children()[1])
        row["value_widget"] = new_widget

        # Rec/Pick button
        if is_key_type:
            rec_btn = ctk.CTkButton(
                r1, text="Rec", width=34, height=26, corner_radius=3,
                fg_color=BG3, hover_color="#333348", text_color=FG2,
                font=("Helvetica", 9),
                command=lambda i=idx: self._start_key_record(i))
            rec_btn.pack(side="left", padx=(0, 2), after=new_widget)
            row["rec_btn"] = rec_btn
        elif is_mouse_click:
            rec_btn = ctk.CTkButton(
                r1, text="Rec", width=34, height=26, corner_radius=3,
                fg_color=BG3, hover_color="#333348", text_color=FG2,
                font=("Helvetica", 9),
                command=lambda i=idx: self._start_click_record(i))
            rec_btn.pack(side="left", padx=(0, 2), after=new_widget)
            row["rec_btn"] = rec_btn
        elif is_mouse_path:
            rec_btn = ctk.CTkButton(
                r1, text="...", width=34, height=26, corner_radius=3,
                fg_color=BG3, hover_color="#333348", text_color=FG2,
                font=("Helvetica", 9),
                command=lambda i=idx: self._pick_mouse_path(i))
            rec_btn.pack(side="left", padx=(0, 2), after=new_widget)
            row["rec_btn"] = rec_btn

        # Show/hide delay for "delay" type
        if atype == "delay":
            row["delay_frame"].pack_forget()
        else:
            try:
                row["delay_frame"].pack(side="left", padx=(0, 2),
                                        after=row.get("rec_btn") or new_widget)
            except Exception:
                row["delay_frame"].pack(side="left", padx=(0, 2))

        self._auto_save_current()

    def _select_recording(self, idx, filename, dlg):
        row = self._action_rows[idx]
        row["value_widget"].configure(state="normal")
        row["value_var"].set(filename)
        row["value_widget"].configure(state="readonly")
        self._auto_save_current()
        dlg.destroy()

    def _start_key_record(self, idx):
        """Capture the next keypress and write the friendly name into the value field."""
        row = self._action_rows[idx]
        rec_btn = row.get("rec_btn")
        if not rec_btn:
            return
        # Visual feedback — highlight button red
        rec_btn.configure(fg_color=RED, text_color=FG, text="...")

        def _on_key(event):
            # Unbind immediately
            self._app.unbind("<KeyPress>", bind_id)
            keysym = event.keysym.lower()
            friendly = KEYSYM_TO_FRIENDLY.get(keysym, keysym)
            row["value_var"].set(friendly)
            rec_btn.configure(fg_color=BG3, text_color=FG2, text="Rec")
            self._auto_save_current()

        bind_id = self._app.bind("<KeyPress>", _on_key)

    def _start_click_record(self, idx):
        """Show a dialog — click left/right/middle to record that button."""
        row = self._action_rows[idx]
        rec_btn = row.get("rec_btn")
        if not rec_btn:
            return
        rec_btn.configure(fg_color=RED, text_color=FG, text="...")

        dlg = ctk.CTkToplevel(self._app)
        dlg.title("")
        dlg.geometry("240x80")
        dlg.attributes("-topmost", True)
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.update_idletasks()
        dlg.grab_set()

        ctk.CTkLabel(dlg, text=self.T("macro_rec_click_hint"),
                     font=("Helvetica", 11), text_color=FG).pack(pady=(10, 6))

        # Quick-pick buttons for side buttons that tkinter can't capture
        side_row = ctk.CTkFrame(dlg, fg_color="transparent")
        side_row.pack()
        for name in ("back", "forward"):
            ctk.CTkButton(
                side_row, text=name, width=60, height=24, corner_radius=3,
                fg_color=BG3, hover_color="#333348", text_color=FG2,
                font=("Helvetica", 10),
                command=lambda n=name: _pick(n)
            ).pack(side="left", padx=2)

        def _pick(name):
            row["value_var"].set(name)
            rec_btn.configure(fg_color=BG3, text_color=FG2, text="Rec")
            self._auto_save_current()
            dlg.destroy()

        def _on_click(event):
            _BTN_MAP = {1: "left", 2: "middle", 3: "right"}
            _pick(_BTN_MAP.get(event.num, "left"))

        dlg.bind("<Button-1>", _on_click)
        dlg.bind("<Button-2>", _on_click)
        dlg.bind("<Button-3>", _on_click)
        dlg.protocol("WM_DELETE_WINDOW", lambda: (
            rec_btn.configure(fg_color=BG3, text_color=FG2, text="Rec"),
            dlg.destroy()))

    def _on_value_edit(self):
        if getattr(self, "_populating", False):
            return
        self._auto_save_current()

    def _move_action(self, idx, direction):
        if not self._selected_id or self._selected_id not in self._macros:
            return
        # Collect current state from UI, then swap
        self._macros[self._selected_id]["actions"] = self._collect_actions()
        actions = self._macros[self._selected_id]["actions"]
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(actions):
            return
        actions[idx], actions[new_idx] = actions[new_idx], actions[idx]
        self._save()
        self._refresh_editor()

    def _remove_action(self, idx):
        if not self._selected_id or self._selected_id not in self._macros:
            return
        # Collect current state from UI, then remove
        self._macros[self._selected_id]["actions"] = self._collect_actions()
        actions = self._macros[self._selected_id]["actions"]
        if idx < 0 or idx >= len(actions):
            return
        actions.pop(idx)
        self._save()
        self._refresh_editor()

    # ── Macro CRUD ───────────────────────────────────────────────────────────

    def _next_macro_name(self):
        existing = {m.get("name", "") for m in self._macros.values()}
        base = "Macro"
        if base not in existing:
            return base
        n = 1
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"

    def _new_macro(self):
        uid = generate_macro_id()
        self._macros[uid] = {
            "name": self._next_macro_name(),
            "actions": [{"type": "key_tap", "value": "a", "delay": 10}],
            "repeat_mode": "once",
            "repeat_count": 1,
        }
        self._save()
        self._select_macro(uid)

    def _quick_delete(self, uid):
        """Delete a macro directly from the list via its ✕ button."""
        self._macros.pop(uid, None)
        if self._selected_id == uid:
            self._selected_id = None
            self._clear_action_rows()
            self._editor_frame.pack_forget()
        self._save()
        self._refresh_macro_list()

    def _delete_macro(self):
        if not self._selected_id:
            return
        self._quick_delete(self._selected_id)

    def _duplicate_macro(self):
        if not self._selected_id or self._selected_id not in self._macros:
            return
        import copy
        uid = generate_macro_id()
        m = copy.deepcopy(self._macros[self._selected_id])
        m["name"] = m["name"] + " (Copy)"
        self._macros[uid] = m
        self._save()
        self._select_macro(uid)

    def _export_macro(self):
        if not self._selected_id or self._selected_id not in self._macros:
            return
        m = self._macros[self._selected_id]
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"{m.get('name', 'macro')}.json")
        if not path:
            return
        with open(path, "w") as f:
            json.dump(m, f, indent=2)

    def _import_macro(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path) as f:
                m = json.load(f)
            if "name" not in m or "actions" not in m:
                return
            uid = generate_macro_id()
            self._macros[uid] = m
            self._save()
            self._select_macro(uid)
        except Exception:
            pass

    # ── Mouse recording ──────────────────────────────────────────────────────

    def _start_mouse_record(self):
        """Open a small floating stop window and record mouse movement."""
        if not self._selected_id:
            return

        self._mouse_recording = False
        self._mouse_positions = []

        # Take screenshot of desktop, then show fullscreen overlay with it as background
        # This way user sees their desktop but we capture Motion events
        import subprocess as _sp, shutil
        _shot = "/tmp/_basecamp_rec_bg.png"
        try:
            if shutil.which("spectacle"):        # KDE
                _sp.run(["spectacle", "-b", "-n", "-o", _shot],
                        capture_output=True, timeout=5)
            elif shutil.which("grim"):            # Sway/wlroots
                _sp.run(["grim", _shot], capture_output=True, timeout=5)
            elif shutil.which("gnome-screenshot"): # GNOME
                _sp.run(["gnome-screenshot", "-f", _shot],
                        capture_output=True, timeout=5)
            elif shutil.which("scrot"):            # X11 fallback
                _sp.run(["scrot", _shot], capture_output=True, timeout=5)
        except Exception:
            pass

        overlay = tk.Toplevel(self._app)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg="black", cursor="crosshair")
        overlay.lift()
        overlay.focus_force()

        # Set screenshot as background
        try:
            from PIL import Image, ImageTk
            img = Image.open(_shot)
            self._rec_bg_photo = ImageTk.PhotoImage(img)
            bg_label = tk.Label(overlay, image=self._rec_bg_photo, bd=0)
            bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        except Exception:
            pass

        # Small info label on top of the screenshot
        info_frame = tk.Frame(overlay, bg="#1a1a2e", padx=12, pady=8)
        info_frame.place(x=20, y=20)
        info_frame.lift()

        self._rec_status_lbl = tk.Label(
            info_frame, text=self.T("macro_rec_press_space"),
            font=("Helvetica", 11), fg="white", bg="#1a1a2e")
        self._rec_status_lbl.pack()

        tk.Label(info_frame, text=self.T("macro_rec_space_hint"),
                 font=("Helvetica", 9), fg="#888", bg="#1a1a2e").pack()

        self._rec_click_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            info_frame, text=self.T("macro_rec_add_click"),
            variable=self._rec_click_var,
            font=("Helvetica", 9), fg="white", bg="#1a1a2e",
            selectcolor="#333", activebackground="#1a1a2e",
            activeforeground="white").pack(pady=(4, 0))

        self._rec_overlay = overlay
        self._rec_cooldown = 0

        def _on_motion(event):
            if not self._mouse_recording:
                return
            t = time.monotonic()
            x, y = event.x_root, event.y_root
            if not self._mouse_positions or (x, y) != (self._mouse_positions[-1][0], self._mouse_positions[-1][1]):
                self._mouse_positions.append((x, y, t))

        def _on_space(event):
            now = time.monotonic()
            if now - self._rec_cooldown < 0.5:
                return
            self._rec_cooldown = now

            if self._mouse_recording:
                self._stop_mouse_record(overlay)
            else:
                self._mouse_recording = True
                self._mouse_positions = []
                self._rec_status_lbl.configure(
                    text=self.T("macro_rec_recording"), fg="red")

        overlay.bind("<Motion>", _on_motion)
        overlay.bind("<KeyPress-space>", _on_space)
        overlay.bind("<Escape>", lambda e: self._stop_mouse_record(overlay))

    def _show_info_dialog(self, msg):
        dlg = ctk.CTkToplevel(self._app)
        dlg.title("")
        dlg.geometry("320x80")
        dlg.attributes("-topmost", True)
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        ctk.CTkLabel(dlg, text=msg, font=("Helvetica", 11), text_color=FG,
                     wraplength=290).pack(pady=(12, 6))
        ctk.CTkButton(dlg, text="OK", width=60, height=28,
                      fg_color=BG3, hover_color="#333348", text_color=FG,
                      command=dlg.destroy).pack()

    def _show_tool_warning(self):
        """Show a warning if no input tool (xdotool/ydotool) is installed."""
        dlg = ctk.CTkToplevel(self._app)
        dlg.title("")
        dlg.geometry("320x100")
        dlg.attributes("-topmost", True)
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        ctk.CTkLabel(dlg, text=self.T("macro_no_tool"),
                     font=("Helvetica", 11), text_color=RED,
                     wraplength=290).pack(pady=(12, 6))
        ctk.CTkButton(dlg, text="OK", width=60, height=28,
                      fg_color=BG3, hover_color="#333348", text_color=FG,
                      command=dlg.destroy).pack()

    def _stop_mouse_record(self, overlay):
        """Stop recording, save as file, insert mouse_path action into macro."""
        self._mouse_recording = False
        self._rec_bg_photo = None  # release image memory
        add_click = getattr(self, "_rec_click_var", None)
        add_click = add_click.get() if add_click else False
        try:
            overlay.destroy()
        except Exception:
            pass
        try:
            os.remove("/tmp/_basecamp_rec_bg.png")
        except Exception:
            pass

        if not self._selected_id:
            return
        if not self._mouse_positions:
            self._show_info_dialog(self.T("macro_rec_empty"))
            return

        # Generate unique recording name
        existing = [f for f, _ in list_mouse_recordings()]
        base = "recording"
        n = 1
        while f"{base}_{n}.json" in existing:
            n += 1
        rec_name = f"{base}_{n}"

        # Save positions to file
        positions = [[x, y, t] for x, y, t in self._mouse_positions]
        filename = save_mouse_recording(rec_name, positions, click_at_end=add_click)

        # Add a single mouse_path action to the macro
        m = self._macros[self._selected_id]
        m.setdefault("actions", []).append(
            {"type": "mouse_path", "value": filename, "delay": 0})
        self._save()
        self._refresh_editor()

    def _pick_mouse_path(self, idx):
        """Show a picker dialog to select a saved mouse recording."""
        recordings = list_mouse_recordings()
        if not recordings:
            self._show_info_dialog(self.T("macro_rec_no_recordings"))
            return

        row = self._action_rows[idx]
        dlg = ctk.CTkToplevel(self._app)
        dlg.title(self.T("macro_pick_recording"))
        dlg.geometry("300x250")
        dlg.attributes("-topmost", True)
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.update_idletasks()
        dlg.grab_set()

        scroll = ctk.CTkScrollableFrame(dlg, fg_color=BG2, corner_radius=4)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        def _rebuild_list():
            for w in scroll.winfo_children():
                w.destroy()
            for filename, display in list_mouse_recordings():
                row = ctk.CTkFrame(scroll, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkButton(
                    row, text=display, anchor="w",
                    font=("Helvetica", 11), fg_color="transparent",
                    hover_color=BG3, text_color=FG,
                    height=28, corner_radius=4,
                    command=lambda f=filename: self._select_recording(idx, f, dlg)
                ).pack(side="left", fill="x", expand=True)
                ctk.CTkButton(
                    row, text="✕", width=28, height=28, corner_radius=4,
                    fg_color=RED, hover_color="#cc3333", text_color=FG,
                    font=("Helvetica", 10),
                    command=lambda f=filename: _delete_rec(f)
                ).pack(side="right", padx=(2, 0))

        def _delete_rec(filename):
            from shared.config import MOUSE_RECORDINGS_DIR
            try:
                os.remove(os.path.join(MOUSE_RECORDINGS_DIR, filename))
            except Exception:
                pass
            _rebuild_list()

        _rebuild_list()
