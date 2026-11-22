"""Reusable widgets built from the tokens.

Every widget here takes text that is already translated. Nothing in this module
reaches for the language files, because it has no app reference; the caller
passes `app.T("key")`. That keeps the components usable from a dialog, a panel
or a plugin without any of them importing the other.

No pictograms. Where a mark carries meaning it is drawn on a canvas (see
StatusDot) so it takes the text colour and is the same size on every system,
instead of being rendered from whatever emoji font happens to be installed.
"""
import tkinter as tk
import customtkinter as ctk

from shared.ui import tokens as T


# ── Translation lookup ────────────────────────────────────────────────────────

def resolve_t(widget):
    """Return the nearest T() up the widget chain, or a passthrough.

    Some older dialogs were built without an app reference and therefore had
    no way to translate their own labels, which is how hardcoded English ended
    up in them. Walking up to the app is cheap and means a component can
    translate without every caller having to hand it a function.
    """
    w = widget
    while w is not None:
        fn = getattr(w, "T", None)
        if callable(fn):
            return fn
        w = getattr(w, "master", None)
    return lambda key, **kw: key


# ── Dropdown behaviour ────────────────────────────────────────────────────────

def bind_dropdown_autoclose(toplevel):
    """Close an open Tk menu when the focus leaves the application.

    The dropdowns do not use a Tk menu any more, they use the list in
    shared.ui.dropdown, so this is the safety net for the one case where that
    list could not be installed: CustomTkinter renames the class it is put in
    place of, install_inline_dropdown() declines, and the widgets fall back to
    posting a menu. It is left in place for that path, and for a plugin that
    posts a menu of its own.

    It only helps where the popup is told that the focus left, which is not
    everywhere. Measured under Wayland (XWayland, KWin): a posted popup holds a
    *global* grab, the compositor still activates the other application, and
    nothing in this process is notified. No FocusOut, no Deactivate, no pointer
    event. The popup therefore stays painted over the other application and
    there is no event left to hang a fix on (#66). A list drawn inside our own
    window instead of in a separate override-redirect window is the only real
    cure, which is what the dropdowns do now.

    Two earlier attempts are worth not repeating. Binding the window's own
    <FocusOut> breaks opening a dropdown at all: the popup takes the focus, so
    the window fires FocusOut the instant the popup appears and the menu is shut
    again by the very click that opened it. Calling `close()` on the popup does
    nothing either, because DropdownMenu has no such method; inside a try/except
    that looked like a working fix for a long time.
    """
    # the flag belongs on the interpreter root, not on the window handed in:
    # several windows call this, and one Menu binding already covers them all
    root = toplevel._root()
    if getattr(root, "_bc_dropdown_autoclose", False):
        return
    root._bc_dropdown_autoclose = True

    def _on_menu_focus_out(event):
        menu = event.widget
        try:
            # "" means the focus went to another application; a widget path
            # means it stayed in ours, and then the menu is not stranded
            if str(menu.tk.call("focus", "-displayof", menu._w)):
                return
        except Exception:
            return
        try:
            menu.grab_release()
        except Exception:
            pass
        try:
            menu.unpost()
        except Exception:
            pass

    toplevel.bind_class("Menu", "<FocusOut>", _on_menu_focus_out, add="+")


# ── Containers ────────────────────────────────────────────────────────────────

class Card(ctk.CTkFrame):
    """A titled block of related controls. Put content into `body`, not into
    the card itself, so the padding stays in one place."""

    def __init__(self, parent, title=None, hint=None, **kw):
        kw.setdefault("fg_color", T.SURFACE)
        kw.setdefault("corner_radius", T.RADIUS)
        kw.setdefault("border_width", T.BORDER_W)
        kw.setdefault("border_color", T.LINE)
        super().__init__(parent, **kw)

        self._head = None
        if title:
            self._head = ctk.CTkFrame(self, fg_color="transparent")
            self._head.pack(fill="x", padx=T.S4, pady=(T.S3, 0))
            self.title_label = ctk.CTkLabel(
                self._head, text=title, font=T.font(T.TEXT_XS, bold=True),
                text_color=T.FG, anchor="w")
            self.title_label.pack(side="left")
            if hint:
                self.hint_label = ctk.CTkLabel(
                    self._head, text=hint, font=T.font(T.TEXT_XS),
                    text_color=T.FG_FAINT, anchor="e")
                self.hint_label.pack(side="right")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=T.S4,
                       pady=(T.S3 if title else T.S4, T.S4))

    def set_hint(self, text):
        """Update the right-hand hint of the card header, if it has one."""
        if getattr(self, "hint_label", None) is not None:
            self.hint_label.configure(text=text)

    def configure(self, **kw):
        """`text` is the card's title, which lives in the header label.

        Without this a card registered for translation raises inside the
        caller's try/except on a language change and keeps its old title while
        the controls below it change over.
        """
        if "text" in kw:
            text = kw.pop("text")
            if getattr(self, "title_label", None) is not None:
                self.title_label.configure(text=text)
        if kw:
            super().configure(**kw)


class Toolbar(ctk.CTkFrame):
    """A row of controls above a work surface. Pack primary actions with
    side="right" so they sit at the end regardless of how many secondary
    ones are added later."""

    def __init__(self, parent, **kw):
        kw.setdefault("fg_color", "transparent")
        super().__init__(parent, **kw)


class SectionLabel(ctk.CTkLabel):
    """Uppercase eyebrow above a group. The only place letter spacing is used,
    and the only place TEXT_XS is allowed outside metadata."""

    def __init__(self, parent, text="", **kw):
        kw.setdefault("font", T.font(T.TEXT_XS, bold=True))
        kw.setdefault("text_color", T.FG_FAINT)
        kw.setdefault("anchor", "w")
        super().__init__(parent, text=text.upper(), **kw)

    def configure(self, **kw):
        """Uppercase here too, not only in __init__. A language change relabels
        through configure(), and without this the eyebrow would come back in
        sentence case while every other one stayed uppercase."""
        if "text" in kw:
            kw["text"] = str(kw["text"]).upper()
        super().configure(**kw)


# ── Buttons ───────────────────────────────────────────────────────────────────

def _button(parent, text, command, fg, hover, text_color, **kw):
    kw.setdefault("height", T.CTRL_H)
    kw.setdefault("corner_radius", T.RADIUS_SM)
    kw.setdefault("font", T.font(T.TEXT_XS, bold=True))
    return ctk.CTkButton(parent, text=text, command=command,
                         fg_color=fg, hover_color=hover, text_color=text_color, **kw)


def PrimaryButton(parent, text, command=None, **kw):
    """The one action a screen is about. At most one per screen."""
    return _button(parent, text, command, T.ACCENT, T.ACCENT_HOVER, T.ACCENT_TEXT, **kw)


def GhostButton(parent, text, command=None, **kw):
    """Everything else. Outlined, so it never competes with the primary."""
    kw.setdefault("border_width", T.BORDER_W)
    kw.setdefault("border_color", T.LINE)
    return _button(parent, text, command, "transparent", T.HOVER, T.FG_DIM, **kw)


def DangerButton(parent, text, command=None, **kw):
    """Destructive. Outlined here, filled only inside the confirmation."""
    kw.setdefault("border_width", T.BORDER_W)
    kw.setdefault("border_color", T.DANGER)
    return _button(parent, text, command, "transparent", T.DANGER_HOVER, T.DANGER, **kw)


# ── State ─────────────────────────────────────────────────────────────────────

class StatusDot(tk.Canvas):
    """A drawn state mark, not a character. `state` is one of ok, warn, bad,
    off, update. Drawn so it cannot turn into a missing-glyph box on a system
    without an emoji font, and so it stays the same size at every font
    scaling."""

    _COLORS = {"ok": T.OK, "warn": T.WARN, "bad": T.DANGER, "off": T.LINE,
               "update": T.ACCENT}

    def __init__(self, parent, state="off", size=8, bg=None, **kw):
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         bd=0, bg=bg or T.SURFACE, **kw)
        self._size = size
        self._state = None
        self._item = self.create_oval(1, 1, size - 1, size - 1,
                                      fill=self._COLORS["off"], outline="")
        self.set_state(state)

    def set_state(self, state):
        # Repainting an unchanged dot is not free: the device scan runs every
        # five seconds and mostly finds the same devices, so the whole sidebar
        # was being redrawn on a timer for nothing.
        if state == self._state:
            return
        self._state = state
        self.itemconfigure(self._item, fill=self._COLORS.get(state, T.LINE))


class StatusPill(ctk.CTkFrame):
    """Dot plus one word: connected, busy, not ready. Reads at a glance and
    survives translation, which a coloured square alone does not."""

    def __init__(self, parent, text="", state="off", **kw):
        kw.setdefault("fg_color", T.SURFACE_2)
        kw.setdefault("corner_radius", 999)
        super().__init__(parent, **kw)
        self._dot = StatusDot(self, state=state, size=7, bg=T.SURFACE_2)
        self._dot.pack(side="left", padx=(T.S2, T.S1), pady=T.S1)
        self._label = ctk.CTkLabel(self, text=text, font=T.font(T.TEXT_XS),
                                   text_color=T.FG_DIM)
        self._label.pack(side="left", padx=(0, T.S2))

    def set(self, text=None, state=None):
        if text is not None:
            self._label.configure(text=text)
        if state is not None:
            self._dot.set_state(state)


class NavItem(ctk.CTkFrame):
    """One row of the sidebar: a state dot, a label, a selection marker.

    The marker is a bar on the left rather than a filled row, so a selected
    item and a device that happens to be green do not fight each other for
    the same signal. `state` is the device state (see StatusDot) or None for
    entries that are not a device.
    """

    def __init__(self, parent, text="", command=None, state=None,
                 bg=T.SURFACE, **kw):
        kw.setdefault("corner_radius", 0)
        kw.setdefault("fg_color", bg)
        super().__init__(parent, height=30, **kw)
        self.pack_propagate(False)
        self._command = command
        self._selected = False
        self._rest = bg          # colour of the container it sits in
        self._bg = bg

        self._marker = tk.Frame(self, bg=self._bg, width=2)
        self._marker.pack(side="left", fill="y")

        self._dot = None
        if state is not None:
            self._dot = StatusDot(self, state=state, size=7, bg=self._bg)
            self._dot.pack(side="left", padx=(T.S2, 0))

        self._label = ctk.CTkLabel(
            self, text=text, font=T.font(T.TEXT_XS), text_color=T.FG_DIM,
            anchor="w")
        self._label.pack(side="left", fill="x", expand=True,
                         padx=(T.S2 if self._dot else T.S3, T.S2))

        for w in (self, self._label, self._marker):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
        if self._dot:
            self._dot.bind("<Button-1>", self._on_click)

    def configure(self, **kw):
        """`text` belongs to the inner label, not to the frame.

        The shell relabels on a language change by calling configure(text=...)
        on whatever it registered. Without this the call raises inside the
        caller's try/except and the entry silently keeps the old language while
        everything around it changes.
        """
        if "text" in kw:
            self._label.configure(text=kw.pop("text"))
        if kw:
            super().configure(**kw)

    # ── behaviour ─────────────────────────────────────────────────────────────

    def _on_click(self, _e=None):
        if self._command:
            self._command()

    def _on_enter(self, _e=None):
        if not self._selected:
            self._paint(T.SURFACE_2)

    def _on_leave(self, _e=None):
        self._paint(T.SURFACE_2 if self._selected else self._rest)

    def _paint(self, bg):
        self._bg = bg
        self.configure(fg_color=bg)
        self._marker.configure(bg=T.ACCENT if self._selected else bg)
        if self._dot:
            self._dot.configure(bg=bg)

    # ── state ─────────────────────────────────────────────────────────────────

    def set_selected(self, selected):
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self._label.configure(
            text_color=T.FG if selected else T.FG_DIM,
            font=T.font(T.TEXT_XS, bold=selected))
        self._paint(T.SURFACE_2 if selected else self._rest)

    def set_text(self, text):
        if text == self._label.cget("text"):
            return
        self._label.configure(text=text)

    def set_state(self, state):
        if self._dot is not None:
            self._dot.set_state(state)


class Field(ctk.CTkFrame):
    """Label above an entry. `var` is optional; without one the widget keeps
    its own StringVar, reachable through .get() and .set()."""

    def __init__(self, parent, label="", var=None, placeholder="", width=200, **kw):
        kw.setdefault("fg_color", "transparent")
        super().__init__(parent, **kw)
        self.var = var if var is not None else tk.StringVar()
        if label:
            ctk.CTkLabel(self, text=label, font=T.font(T.TEXT_XS),
                         text_color=T.FG_DIM, anchor="w").pack(fill="x", pady=(0, T.S1))
        self.entry = ctk.CTkEntry(
            self, textvariable=self.var, width=width, height=T.CTRL_H,
            font=T.font(T.TEXT_XS), fg_color=T.SURFACE_2, text_color=T.FG,
            border_color=T.LINE, border_width=T.BORDER_W,
            corner_radius=T.RADIUS_SM, placeholder_text=placeholder)
        self.entry.pack(fill="x")

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value)


class Toast(ctk.CTkFrame):
    """A short message that appears over the screen and goes away by itself.

    Replaces the coloured status labels that sat in the layout permanently,
    occupying a row whether or not there was anything to say, and staying on
    screen long after the thing they described. `kind` is ok, warn, bad or
    info and only tints the left edge, so the text itself stays readable.
    """

    _EDGE = {"ok": T.OK, "warn": T.WARN, "bad": T.DANGER, "info": T.ACCENT}

    def __init__(self, parent, text, kind="info", ms=3500, **kw):
        kw.setdefault("fg_color", T.SURFACE)
        kw.setdefault("corner_radius", T.RADIUS)
        kw.setdefault("border_width", T.BORDER_W)
        kw.setdefault("border_color", T.LINE)
        super().__init__(parent, **kw)
        tk.Frame(self, bg=self._EDGE.get(kind, T.ACCENT), width=3).pack(
            side="left", fill="y")
        ctk.CTkLabel(self, text=text, font=T.font(T.TEXT_XS), text_color=T.FG,
                     anchor="w", justify="left", wraplength=520).pack(
            side="left", padx=T.S3, pady=T.S3)
        self.place(relx=0.5, rely=1.0, anchor="s", y=-T.S4)
        self.lift()
        self.after(ms, self._close)

    def _close(self):
        try:
            self.destroy()
        except Exception:
            pass


# ── Dialogs ───────────────────────────────────────────────────────────────────

class _ModalBase(ctk.CTkToplevel):
    """Shared plumbing for our own modals: centred on the parent, Escape
    closes, the parent is blocked while it is open, and the result is read
    from .result after the call returns."""

    def __init__(self, parent, title=""):
        super().__init__(parent)
        self.result = None
        self._parent = parent
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=T.BG)
        try:
            self.transient(parent)
        except Exception:
            pass
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _run(self):
        """Centre on the parent, take the grab, block until closed."""
        self.update_idletasks()
        try:
            px = self._parent.winfo_rootx() + self._parent.winfo_width() // 2
            py = self._parent.winfo_rooty() + self._parent.winfo_height() // 2
            self.geometry(f"+{px - self.winfo_width() // 2}"
                          f"+{py - self.winfo_height() // 2}")
        except Exception:
            pass
        # Grabbing before the window is mapped fails on X11, hence the delay.
        self.after(20, self._take_grab)
        self.wait_window()
        return self.result

    def _take_grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass


class ConfirmDialog(_ModalBase):
    """Yes/no question in our own language instead of the system's message box.

    `danger=True` fills the confirming button red: that is the one place a
    filled destructive colour is allowed, because at that point the user is
    being asked to confirm exactly that.
    """

    def __init__(self, parent, title, message, ok_text, cancel_text=None,
                 danger=False, detail=None):
        super().__init__(parent, title)

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=T.S5, pady=T.S5)

        # The window manager already shows `title`. Repeating it inside is the
        # same duplicated-chrome problem the redesign is meant to remove, so
        # the question itself is the prominent text here.
        ctk.CTkLabel(wrap, text=message, font=T.font(T.TEXT_SM, bold=True),
                     text_color=T.FG, anchor="w", justify="left",
                     wraplength=380).pack(fill="x")
        if detail:
            box = ctk.CTkFrame(wrap, fg_color=T.SURFACE, corner_radius=T.RADIUS_SM,
                               border_width=T.BORDER_W, border_color=T.LINE)
            box.pack(fill="x", pady=(T.S3, 0))
            ctk.CTkLabel(box, text=detail, font=T.font(T.TEXT_XS),
                         text_color=T.FG_DIM, anchor="w", justify="left",
                         wraplength=356).pack(fill="x", padx=T.S3, pady=T.S3)

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(T.S5, 0))
        if danger:
            confirm = _button(row, ok_text, self._ok, T.DANGER, T.DANGER_HOVER,
                              T.DANGER_TEXT, width=110)
        else:
            confirm = PrimaryButton(row, ok_text, self._ok, width=110)
        confirm.pack(side="right")
        # A statement (show_error) passes no cancel text and gets one button.
        if cancel_text:
            GhostButton(row, cancel_text, self._cancel,
                        width=100).pack(side="right", padx=(0, T.S2))

        self.bind("<Return>", lambda _e: self._ok())
        self._run()

    def _ok(self):
        self.result = True
        self._close()


class PromptDialog(_ModalBase):
    """One line of text input. Replaces CTkInputDialog, which brings its own
    styling and its own English button labels."""

    def __init__(self, parent, title, message, ok_text, cancel_text,
                 initial="", placeholder=""):
        super().__init__(parent, title)

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=T.S5, pady=T.S5)

        # Same rule as ConfirmDialog: no second copy of the window title.
        if message:
            ctk.CTkLabel(wrap, text=message, font=T.font(T.TEXT_SM, bold=True),
                         text_color=T.FG, anchor="w", justify="left",
                         wraplength=340).pack(fill="x")

        self._field = Field(wrap, placeholder=placeholder, width=340)
        self._field.pack(fill="x", pady=(T.S3, 0))
        self._field.set(initial)

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(T.S5, 0))
        PrimaryButton(row, ok_text, self._ok, width=110).pack(side="right")
        GhostButton(row, cancel_text, self._cancel, width=100).pack(side="right", padx=(0, T.S2))

        self.bind("<Return>", lambda _e: self._ok())
        self.after(60, self._focus_entry)
        self._run()

    def _focus_entry(self):
        try:
            self._field.entry.focus_set()
            self._field.entry.select_range(0, "end")
        except Exception:
            pass

    def _ok(self):
        self.result = self._field.get().strip()
        self._close()


# ── Convenience ───────────────────────────────────────────────────────────────

def ask_yes_no(parent, title, message, ok_text, cancel_text, danger=False,
               detail=None):
    """True if confirmed, False otherwise. Never raises: a dialog that cannot
    open must not take an action with it, so it answers False."""
    try:
        return bool(ConfirmDialog(parent, title, message, ok_text, cancel_text,
                                  danger=danger, detail=detail).result)
    except Exception:
        return False


def ask_text(parent, title, message, ok_text, cancel_text, initial="", placeholder=""):
    """The entered string, or None when cancelled."""
    try:
        return PromptDialog(parent, title, message, ok_text, cancel_text,
                            initial=initial, placeholder=placeholder).result
    except Exception:
        return None


def show_error(parent, title, message, ok_text):
    """A statement, not a question: one button, same shape as the confirmation
    so the two do not look like different products."""
    try:
        ConfirmDialog(parent, title, message, ok_text, cancel_text=None)
    except Exception:
        pass
