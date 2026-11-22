"""An option list that opens inside the window instead of over the desktop.

A Tk menu takes a *global* grab while it is posted. That is how it keeps
receiving clicks that land outside it, and it is also why it stays painted over
whatever the user switches to: under Wayland the compositor activates the other
application and our process is never told, so there is no event left to close
the list on (#66). Measured under KWin: no FocusOut, no Deactivate, no pointer
event at all.

A list drawn inside the window takes no grab. It is stacked with the window it
belongs to, so it goes behind the other application together with it, and it
closes on the ordinary events a widget does get. The rows live on a single
canvas rather than in a widget each, which keeps opening a list of two hundred
images as cheap as opening a list of three.

The class is a drop-in for CustomTkinter's DropdownMenu, and
install_inline_dropdown() points the option menu and the combo box at it. That
covers every dropdown in the application without touching the forty-one places
that build one, and it covers the ones added later too.
"""
import tkinter as tk

import customtkinter as ctk

from shared.ui import tokens as T

# Unscaled geometry. Everything here is multiplied by the widget scaling of the
# window the list belongs to, so a scaled UI gets a scaled list.
ROW_H  = 28   # height of one row
PAD_X  = 10   # inset of the text from the left edge
PAD_Y  = 4    # gap above the first and below the last row
MAX_H  = 380  # never taller than this; a longer list scrolls
EDGE   = 6    # window edge kept free on every side
BAR_W  = 10   # scrollbar, only present when the list scrolls
CHROME = 4    # the inset that keeps the frame's own border visible

# The one list on screen. There is never a second: opening closes whatever was
# open, which is what the global handlers below rely on.
_showing = None


def _for_mode(value):
    """CustomTkinter colours come as a single value or as (light, dark)."""
    if isinstance(value, (tuple, list)):
        if not value:
            return None
        dark = str(ctk.get_appearance_mode()).lower() == "dark"
        return value[1] if dark and len(value) > 1 else value[0]
    return value


def _scale_of(widget):
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import (
            ScalingTracker,
        )
        return float(ScalingTracker.get_widget_scaling(widget))
    except Exception:
        return 1.0


def _destroy_later(panel):
    try:
        if panel.winfo_exists():
            panel.destroy()
    except Exception:
        pass


def _inside(widget, container):
    """True when `widget` is `container` or sits inside it.

    Compared over the Tk path because an event carries the widget it happened
    on, which for our own rows is the canvas and for a click elsewhere can be
    anything at all, including widgets from another toplevel.
    """
    if container is None or widget is None:
        return False
    path, root = str(widget), str(container)
    return path == root or path.startswith(root + ".")


class InlineDropdown:
    """The value list of one option menu or combo box.

    Nothing is built until the list is opened, and everything is destroyed
    again when it closes. A dropdown that is never opened therefore costs a few
    attributes, which matters because the DisplayPad key editor builds one per
    key.
    """

    def __init__(self, master=None, values=None, command=None,
                 fg_color=None, hover_color=None, text_color=None,
                 font=None, min_character_width=18, **_unused):
        self._anchor = master
        self._values = list(values) if values else []
        self._command = command
        # The tokens rather than CustomTkinter's theme, which paints a grey
        # that is nowhere else in this application.
        self._fg_color = fg_color if fg_color is not None else T.SURFACE_2
        self._hover_color = hover_color if hover_color is not None else T.HOVER_SOFT
        self._text_color = text_color if text_color is not None else T.FG
        self._font = font
        self._min_character_width = min_character_width

        self._panel = None
        self._canvas = None
        self._bar = None
        self._active = None      # row under the pointer or the keyboard cursor
        self._hover = T.HOVER_SOFT
        self._plain = T.SURFACE_2
        self._pointer_in = False  # see _on_release
        self._click_armed = False
        self._watching = False
        self._row_h = ROW_H
        self._pad_y = PAD_Y
        self._content_h = 0

    # ── what CustomTkinter calls ──────────────────────────────────────────

    def open(self, x, y, *_args, **_kwargs):
        """Show the list. `x`/`y` are the screen position of the lower left
        corner of the control, which is what CTkOptionMenu passes."""
        global _showing
        if self._panel is not None:      # a second click on the control closes
            self.close()
            return
        if _showing is not None:
            _showing.close()
        if not self._values or self._anchor is None:
            return
        if not self._watching:
            # Not in __init__: CTkOptionMenu builds its dropdown before its own
            # canvas exists, and bind() is forwarded to that canvas.
            try:
                self._anchor.bind("<Destroy>", self._anchor_destroyed, add="+")
            except Exception:
                pass
            self._watching = True
        try:
            self._build(int(x), int(y))
        except Exception:
            self.close()
            raise
        _showing = self
        top = self._anchor.winfo_toplevel()
        _bind_window(top)
        # The click that opened the list is still being dispatched and would
        # reach the window handler below, which would close it again. An idle
        # callback runs once that dispatch is finished.
        self._click_armed = False
        top.after_idle(self._arm)

    def _arm(self):
        self._click_armed = True

    def is_open(self):
        """Asked by CustomTkinter 6 whenever the pointer enters the control (#116)."""
        return self._panel is not None

    def close(self, *_args):
        panel = self._drop()
        if panel is not None:
            try:
                panel.destroy()
            except Exception:
                pass

    def _drop(self):
        """Forget the list without touching its widgets, and hand it back."""
        global _showing
        if _showing is self:
            _showing = None
        # CustomTkinter 6 remembers on the control that its list is open, so
        # that the next click closes it, and only asks again when the pointer
        # enters the control. Closed by Escape, the wheel or a pick with the
        # pointer still on it, the control would spend the next click closing
        # a list that is already gone.
        if getattr(self._anchor, "_close_on_next_click", False):
            self._anchor._close_on_next_click = False
        panel, self._panel, self._canvas, self._bar = self._panel, None, None, None
        self._active = None
        self._pointer_in = False
        self._click_armed = False
        return panel

    def _anchor_destroyed(self, _event=None):
        """The control is going away and takes its list with it.

        The list is not destroyed here. Tk is walking the children of whatever
        is being torn down, and removing one out from under that walk unmaps a
        window that is already gone, which ends the process with a BadWindow
        rather than an exception. Once the teardown is over it is safe, and by
        then the list is usually gone anyway: it is a child of the same window.
        """
        panel = self._drop()
        if panel is None:
            return
        try:
            panel.after_idle(_destroy_later, panel)
        except Exception:
            pass

    def configure(self, **kwargs):
        if "values" in kwargs:
            self._values = list(kwargs.pop("values") or [])
            self.close()          # the open list would show the old values
        if "fg_color" in kwargs:
            self._fg_color = kwargs.pop("fg_color")
        if "hover_color" in kwargs:
            self._hover_color = kwargs.pop("hover_color")
        if "text_color" in kwargs:
            self._text_color = kwargs.pop("text_color")
        if "font" in kwargs:
            self._font = kwargs.pop("font")
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        kwargs.pop("min_character_width", None)

    def cget(self, attribute_name):
        return {
            "values": self._values,
            "command": self._command,
            "fg_color": self._fg_color,
            "hover_color": self._hover_color,
            "text_color": self._text_color,
            "font": self._font,
            "min_character_width": self._min_character_width,
        }[attribute_name]

    def destroy(self):
        self.close()

    # ── building ──────────────────────────────────────────────────────────

    def _row_font(self, scale=1.0):
        """The control's own font, so a compact dropdown stays compact."""
        font = self._font
        if font is None:
            try:
                font = self._anchor.cget("font")
            except Exception:
                font = None
        if not font:
            font = T.font(T.TEXT_SM)
        if scale != 1.0 and isinstance(font, (tuple, list)) and len(font) > 1:
            font = (font[0], int(round(font[1] * scale))) + tuple(font[2:])
        return font

    def _build(self, x, y):
        top = self._anchor.winfo_toplevel()
        scale = _scale_of(self._anchor)
        row_h = max(1, int(round(ROW_H * scale)))
        pad_x = int(round(PAD_X * scale))
        pad_y = int(round(PAD_Y * scale))
        max_h = int(round(MAX_H * scale))
        edge  = int(round(EDGE * scale))
        bar_w = int(round(BAR_W * scale))

        fg    = _for_mode(self._fg_color) or T.SURFACE_2
        text  = _for_mode(self._text_color) or T.FG
        self._hover = _for_mode(self._hover_color) or T.HOVER_SOFT
        self._plain = fg

        self._panel = ctk.CTkFrame(top, fg_color=fg, corner_radius=T.RADIUS_SM,
                                   border_width=1, border_color=T.LINE)
        self._canvas = tk.Canvas(self._panel, bg=fg, highlightthickness=0, bd=0,
                                 takefocus=0)

        current = self._current_value()
        font = self._row_font(scale)
        # The row runs the full width of the canvas, which is not known yet: it
        # comes from the widest label. A row as wide as the screen is always
        # wide enough and the canvas clips it.
        full = top.winfo_screenwidth()
        for i, value in enumerate(self._values):
            y0 = pad_y + i * row_h
            self._canvas.create_rectangle(0, y0, full, y0 + row_h, width=0,
                                          fill=fg, tags=("row", "row%d" % i))
            self._canvas.create_text(pad_x, y0 + row_h // 2, text=value,
                                     anchor="w", font=font,
                                     fill=T.ACCENT if value == current else text,
                                     tags=("label", "label%d" % i))

        self._row_h = row_h
        self._pad_y = pad_y
        self._content_h = len(self._values) * row_h + 2 * pad_y

        bbox = self._canvas.bbox("label")
        content_w = (bbox[2] + pad_x) if bbox else 0
        room = max(row_h, top.winfo_width() - 2 * edge)
        width = min(max(self._min_width(), content_w), room)

        x_local = x - top.winfo_rootx()
        y_local = y - top.winfo_rooty()
        anchor_top = self._anchor.winfo_rooty() - top.winfo_rooty()
        below = top.winfo_height() - y_local - edge
        above = anchor_top - edge

        # Below the control if it fits, above if it fits there instead, and
        # otherwise on the roomier side with the list scrolling.
        fit = min(self._content_h + CHROME, max_h)
        if fit <= below:
            height, y_at = fit, y_local
        elif fit <= above:
            height, y_at = fit, anchor_top - fit
        elif below >= above:
            height, y_at = max(below, row_h), y_local
        else:
            height = max(above, row_h)
            y_at = max(edge, anchor_top - height)

        scrolls = self._content_h > height - CHROME
        if scrolls:
            width = min(width + bar_w, room)   # room for the bar, not under it
        x_at = max(edge, min(x_local, top.winfo_width() - width - edge))

        # The size goes on the frame itself and the propagation off, because a
        # CustomTkinter widget refuses width and height in place() and would
        # otherwise shrink back onto the canvas inside it.
        self._panel.configure(width=width, height=height)
        self._panel.pack_propagate(False)

        if scrolls:
            self._bar = ctk.CTkScrollbar(self._panel, width=bar_w,
                                         command=self._canvas.yview)
            self._bar.pack(side="right", fill="y", padx=(0, 2), pady=2)
            self._canvas.configure(yscrollcommand=self._bar.set)
        self._canvas.pack(side="left", fill="both", expand=True,
                          padx=(2, 0 if scrolls else 2), pady=2)
        self._canvas.configure(scrollregion=(0, 0, width, self._content_h),
                               yscrollincrement=row_h)

        self._canvas.bind("<Motion>", self._on_motion, add="+")
        self._canvas.bind("<Leave>", self._on_leave, add="+")
        self._canvas.bind("<ButtonRelease-1>", self._on_release, add="+")
        self._canvas.bind("<MouseWheel>", self._on_wheel, add="+")
        self._canvas.bind("<Button-4>", lambda e: self._scroll(-1, e), add="+")
        self._canvas.bind("<Button-5>", lambda e: self._scroll(1, e), add="+")

        self._panel.place(x=x_at, y=y_at)
        self._panel.lift()

    def _current_value(self):
        """The value the control carries, marked in the list."""
        try:
            return self._anchor.get()
        except Exception:
            return None

    def _min_width(self):
        """A list is never narrower than the control it belongs to."""
        try:
            return self._anchor.winfo_width()
        except Exception:
            return 0

    # ── pointer ───────────────────────────────────────────────────────────

    def _row_at(self, y):
        if self._canvas is None:
            return None
        idx = int((self._canvas.canvasy(y) - self._pad_y) // self._row_h)
        return idx if 0 <= idx < len(self._values) else None

    def _highlight(self, idx):
        if self._canvas is None or idx == self._active:
            return
        if self._active is not None:
            self._canvas.itemconfigure("row%d" % self._active, fill=self._plain)
        self._active = idx
        if idx is not None:
            self._canvas.itemconfigure("row%d" % idx, fill=self._hover)

    def _on_motion(self, event):
        self._pointer_in = True
        self._highlight(self._row_at(event.y))

    def _on_leave(self, _event=None):
        self._highlight(None)

    def _on_release(self, event):
        if not self._pointer_in:
            # Nothing has moved over the list yet, so this is the release of
            # the click that opened it, arriving here because the list had to
            # be placed above the control and landed under the pointer. Picking
            # on it would choose a row at random.
            self._pointer_in = True
            return
        idx = self._row_at(event.y)
        if idx is not None:
            self._pick(idx)

    def _on_wheel(self, event):
        """The wheel under Tk 9, where X11 input arrives as MouseWheel."""
        delta = getattr(event, "delta", 0)
        if delta:
            self._scroll(-1 if delta > 0 else 1, event)
        return "break"                # do not let the window close the list

    def _scroll(self, direction, event=None):
        """Three rows per notch, and the direction from the binding rather than
        from event.num: Tk 9 renumbers the X11 buttons, so a Button-5 binding
        reports 9 there, and the wheel delta is 120 on one version and 0 on the
        other (see cap_scroll_speed in ui_helpers)."""
        if self._canvas is not None:
            self._canvas.yview_scroll(direction * 3, "units")
            if event is not None:
                self._highlight(self._row_at(event.y))
        return "break"

    def _pick(self, idx):
        if not (0 <= idx < len(self._values)):
            return
        value = self._values[idx]
        self.close()                  # before the command: it may rebuild the panel
        if self._command is not None:
            self._command(value)

    # ── keyboard ──────────────────────────────────────────────────────────

    def _step(self, delta):
        if not self._values:
            return
        if self._active is None:
            try:
                start = self._values.index(self._current_value())
            except ValueError:
                start = -1 if delta > 0 else len(self._values)
            idx = start + delta
        else:
            idx = self._active + delta
        idx = max(0, min(len(self._values) - 1, idx))
        self._highlight(idx)
        self._see(idx)

    def _see(self, idx):
        if self._canvas is None:
            return
        y0 = self._pad_y + idx * self._row_h
        view_top = self._canvas.canvasy(0)
        view_h = self._canvas.winfo_height()
        if y0 < view_top:
            self._canvas.yview_moveto(y0 / max(1, self._content_h))
        elif y0 + self._row_h > view_top + view_h:
            self._canvas.yview_moveto(
                (y0 + self._row_h - view_h) / max(1, self._content_h))

    def _on_key(self, keysym):
        if keysym == "Escape":
            self.close()
        elif keysym == "Up":
            self._step(-1)
        elif keysym == "Down":
            self._step(1)
        elif keysym == "Prior":
            self._step(-max(1, self._page_rows()))
        elif keysym == "Next":
            self._step(max(1, self._page_rows()))
        elif keysym == "Home":
            self._highlight(0)
            self._see(0)
        elif keysym == "End":
            last = len(self._values) - 1
            self._highlight(last)
            self._see(last)
        elif keysym in ("Return", "KP_Enter"):
            # Only once a row is marked, so the Return that confirms an entry
            # in a combo box is not swallowed by a list nobody navigated.
            if self._active is None:
                return False
            self._pick(self._active)
        else:
            return False
        return True

    def _page_rows(self):
        if self._canvas is None:
            return 1
        return max(1, self._canvas.winfo_height() // self._row_h)


class InlineMenu(InlineDropdown):
    """The same list used as a context menu.

    It opens where the pointer is rather than under a control, it is as wide as
    its own entries, and nothing in it is the current value. A Tk menu would
    grab here exactly as the dropdown did, so the right click in an entry field
    had the same problem as the dropdown (#66).
    """

    def __init__(self, master, items):
        self._actions = dict(items)
        super().__init__(master=master, values=[label for label, _ in items],
                         command=self._run)

    def set_items(self, items):
        """Replace the entries, so labels can be built fresh on every open and
        a language change during the session is picked up."""
        self._actions = dict(items)
        self.configure(values=[label for label, _ in items])

    def _run(self, label):
        action = self._actions.get(label)
        if action is not None:
            action()

    def _current_value(self):
        return None

    def _min_width(self):
        return 0


# ── window level handlers ─────────────────────────────────────────────────
# Bound once per window and left in place. Unbinding per open would be the
# obvious alternative, but tkinter's unbind(sequence, funcid) has a long
# standing habit of removing every binding for that sequence, and the window
# has bindings of its own.

def _bind_window(top):
    if getattr(top, "_bc_inline_dropdown", False):
        return
    top._bc_inline_dropdown = True
    top.bind("<Button-1>", _click_elsewhere, add="+")
    top.bind("<Button-3>", _click_elsewhere, add="+")
    top.bind("<Configure>", _window_moved, add="+")
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        top.bind(seq, _wheel_elsewhere, add="+")
    for seq in ("<Escape>", "<Up>", "<Down>", "<Prior>", "<Next>",
                "<Home>", "<End>", "<Return>", "<KP_Enter>"):
        top.bind(seq, _key, add="+")


def _click_elsewhere(event):
    dd = _showing
    if dd is None or not dd._click_armed:
        return
    if _inside(event.widget, dd._panel):
        return
    dd.close()


def _wheel_elsewhere(_event):
    # The list is placed at fixed coordinates, so whatever scrolled underneath
    # has just moved the control out from under it.
    if _showing is not None:
        _showing.close()


def _window_moved(event):
    # Only the window itself. A <Configure> from any widget inside it reaches
    # this binding too, and a panel that is placed fires one on the spot.
    if _showing is None:
        return
    try:
        if str(event.widget) == str(event.widget.winfo_toplevel()):
            _showing.close()
    except Exception:
        pass


def _key(event):
    dd = _showing
    if dd is None:
        return
    if dd._on_key(event.keysym):
        return "break"


# ── installation ──────────────────────────────────────────────────────────

def install_inline_dropdown():
    """Point CustomTkinter's option menu and combo box at the list above.

    Reaching into a CustomTkinter internal, the same way the wheel fix in
    ui_helpers does: if a future version renames the class or hands it
    different arguments, the dropdown has to fall back to the Tk menu rather
    than stop the application from starting. Returns whether it took.
    """
    try:
        from customtkinter.windows.widgets import ctk_combobox, ctk_optionmenu
    except Exception:
        return False
    modules = (ctk_optionmenu, ctk_combobox)
    if any(getattr(m, "DropdownMenu", None) is None for m in modules):
        return False
    if all(getattr(m, "_basecamp_inline_dropdown", False) for m in modules):
        return True
    for module in modules:
        module._basecamp_stock_dropdown = module.DropdownMenu
        module.DropdownMenu = InlineDropdown
        module._basecamp_inline_dropdown = True
    return True
