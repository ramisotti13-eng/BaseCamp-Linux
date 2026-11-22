"""Shared UI constants, helpers, and reusable widget classes for BaseCamp Linux."""
import os
import sys
import re
import math
import unicodedata
import colorsys
import threading
import time
import subprocess
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
import shared.ui as _ui
from PIL import Image, ImageTk, ImageEnhance

from shared.ui.widgets import resolve_t
from shared.ui.dropdown import InlineMenu
from shared.config import (
    ICON_LIBRARY_DIR, MAIN_LIBRARY_DIR,
    DISPLAYPAD_LIBRARY_DIR, DISPLAYPAD_FS_LIBRARY_DIR,
    _load_icon_last, _save_icon_last,
    _save_to_library, _compute_lib_hash,
)

# ── Color / style constants ────────────────────────────────────────────────────

BG     = "#0d0d14"
BG2    = "#16161f"
BG3    = "#1f1f2e"
FG     = "#e8e8ff"
FG2    = "#9090c0"
BLUE   = "#0ea5e9"
YLW    = "#f5c400"
GRN    = "#22c55e"
RED    = "#ef4444"
BORDER = "#1e1e30"

FONT      = (_ui.FONT_FAMILY, 11)
FONT_BOLD = (_ui.FONT_FAMILY, 11, "bold")
FONT_SM   = (_ui.FONT_FAMILY, 10)
FONT_LG   = (_ui.FONT_FAMILY, 13, "bold")

# Accordion animation
ANIM_STEPS = 8
ANIM_MS    = 12

# ── UI utility functions ───────────────────────────────────────────────────────

_DESKTOP_EXEC_RE = re.compile(r"%[a-zA-Z]")
_desktop_apps_cache = None


def menu_order(label):
    """Where a label sorts in a menu: by its letters, an umlaut or an accent
    with its base letter rather than after z, case ignored (#109)."""
    plain = unicodedata.normalize("NFKD", str(label))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return (plain.casefold(), str(label))


def _run_as_sudouser(cmd):
    """Run cmd as SUDO_USER (when launched via sudo) or directly.

    The env is sanitised via clean_child_env() so GTK helpers like zenity don't
    inherit the AppImage's bundled library paths and fail to launch (issue #11).
    """
    from shared.macros import clean_child_env
    import pwd as _pwd
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        uid = _pwd.getpwnam(sudo_user).pw_uid
        env = clean_child_env()
        env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
        cmd = ["sudo", "-u", sudo_user, "-E"] + cmd
        return subprocess.run(cmd, capture_output=True, text=True, env=env)
    return subprocess.run(cmd, capture_output=True, text=True, env=clean_child_env())


def native_open_image(title="Bild wählen", kind="image"):
    """Pick an image file. Remembers the last directory between runs per `kind`."""
    import shutil
    from shared.config import _load_last_dir, _save_last_dir
    desktop  = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    filter_img = "*.png *.jpg *.jpeg *.bmp *.webp *.gif"
    start_dir = _load_last_dir(kind) or os.path.expanduser("~")

    def _remember(path):
        if path:
            _save_last_dir(kind, path)
        return path or ""

    if shutil.which("kdialog") and ("KDE" in desktop or "PLASMA" in desktop):
        try:
            r = _run_as_sudouser(["kdialog", "--getopenfilename",
                                   start_dir,
                                   f"{filter_img} | Bilder", "--title", title])
            return _remember(r.stdout.strip() if r.returncode == 0 else "")
        except Exception:
            pass

    if shutil.which("zenity"):
        try:
            patterns = [f"--file-filter=Bilder | {filter_img}", "--file-filter=Alle | *"]
            r = _run_as_sudouser(["zenity", "--file-selection", f"--title={title}",
                                   f"--filename={start_dir}/"] + patterns)
            return _remember(r.stdout.strip() if r.returncode == 0 else "")
        except Exception:
            pass

    return _remember(filedialog.askopenfilename(
        title=title, initialdir=start_dir,
        filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg"),
                   ("BMP", "*.bmp"), ("WebP", "*.webp"), ("GIF", "*.gif"), ("Alle", "*.*")]))


def native_open_folder(title="Ordner wählen", kind="folder"):
    """Pick a directory. Remembers the last directory between runs per `kind`."""
    import shutil
    from shared.config import _load_last_dir, _save_last_dir
    desktop  = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    start_dir = _load_last_dir(kind) or os.path.expanduser("~")

    def _remember(path):
        if path:
            _save_last_dir(kind, path)
        return path or ""

    if shutil.which("kdialog") and ("KDE" in desktop or "PLASMA" in desktop):
        try:
            r = _run_as_sudouser(["kdialog", "--getexistingdirectory",
                                   start_dir, "--title", title])
            return _remember(r.stdout.strip() if r.returncode == 0 else "")
        except Exception:
            pass

    if shutil.which("zenity"):
        try:
            r = _run_as_sudouser(["zenity", "--file-selection", "--directory",
                                   f"--title={title}",
                                   f"--filename={start_dir}/"])
            return _remember(r.stdout.strip() if r.returncode == 0 else "")
        except Exception:
            pass

    return _remember(filedialog.askdirectory(title=title, initialdir=start_dir))


def parse_desktop_apps():
    """Scan .desktop files and return sorted list of (name, exec_cmd) for all visible apps.
    Result is cached for the lifetime of the process."""
    global _desktop_apps_cache
    if _desktop_apps_cache is not None:
        return _desktop_apps_cache

    search_dirs = [
        "/usr/share/applications",
        "/usr/local/share/applications",
        os.path.expanduser("~/.local/share/applications"),
    ]
    xdg_data = os.environ.get("XDG_DATA_DIRS", "")
    for d in xdg_data.split(":"):
        path = os.path.join(d, "applications")
        if path not in search_dirs and os.path.isdir(path):
            search_dirs.append(path)

    apps = {}
    for d in search_dirs:
        try:
            entries = os.listdir(d)
        except FileNotFoundError:
            continue
        for fname in entries:
            if not fname.endswith(".desktop"):
                continue
            path = os.path.join(d, fname)
            try:
                name = exec_cmd = None
                no_display = hidden = False
                with open(path, encoding="utf-8", errors="replace") as f:
                    in_entry = False
                    for line in f:
                        line = line.strip()
                        if line == "[Desktop Entry]":
                            in_entry = True
                        elif line.startswith("[") and line != "[Desktop Entry]":
                            in_entry = False
                        if not in_entry:
                            continue
                        if line.startswith("Name=") and name is None:
                            name = line[5:]
                        elif line.startswith("Exec=") and exec_cmd is None:
                            raw = line[5:]
                            exec_cmd = _DESKTOP_EXEC_RE.sub("", raw).strip()
                        elif line == "NoDisplay=true":
                            no_display = True
                        elif line == "Hidden=true":
                            hidden = True
                if name and exec_cmd and not no_display and not hidden:
                    apps[name] = exec_cmd
            except Exception:
                continue
    _desktop_apps_cache = sorted(apps.items(), key=lambda x: x[0].lower())
    return _desktop_apps_cache


# ── Color helper ───────────────────────────────────────────────────────────────

def _rgb_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def cap_scroll_speed(scrollable_frame, max_units=2):
    """Cap a CTkScrollableFrame's wheel scroll step to ±max_units.

    CustomTkinter's default scroll step is 5 units per wheel event, which
    feels far too fast in dense panels. Wrapping canvas.yview is the only
    reliable approach — CTk re-binds child widgets internally, so binding
    on Canvas alone is not enough.

    It is also the fix for a much worse symptom on Tk 9: X11 wheel input is
    delivered as <MouseWheel> with a delta of ±120 there (Tk 8.6 sent
    Button-4/5 with a delta of 0), and CustomTkinter 5.2.2 passes that delta
    straight through as a unit count, so a single notch scrolls 120 units,
    i.e. instantly to the very top or bottom. Horizontal scrolling gets the
    same treatment for shift-wheel.
    """
    canvas = scrollable_frame._parent_canvas

    def _cap(original):
        def capped(*args):
            if args and args[0] == "scroll":
                n    = max(-max_units, min(max_units, int(args[1])))
                what = args[2] if len(args) > 2 else "units"
                return original("scroll", n, what)
            return original(*args)
        return capped

    canvas.yview = _cap(canvas.yview)
    canvas.xview = _cap(canvas.xview)


def _install_ctk_wheel_fix(units_per_notch=2):
    """Make one wheel notch scroll a few units in every CTkScrollableFrame.

    cap_scroll_speed() has to be called per frame, and a frame that never got
    the call scrolls to the very top or bottom on a single notch under Tk 9
    (see the note there). This normalises the wheel delta once, centrally, so
    frames nobody remembered to cap behave too. Linux only: CustomTkinter
    divides the delta by 6 on Windows, where overwriting it with a small
    number would round down to no scrolling at all, and macOS already reports
    single-digit deltas.
    """
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return
    if getattr(ctk.CTkScrollableFrame, "_basecamp_wheel_fix", False):
        return
    # Reaching into a CustomTkinter internal: if a future version renames it,
    # scrolling goes back to feeling wrong, which must never be a reason for
    # the app not to start.
    try:
        original = ctk.CTkScrollableFrame._mouse_wheel_all
    except AttributeError:
        return

    def normalised(self, event):
        delta = getattr(event, "delta", 0)
        if delta:
            try:
                event.delta = units_per_notch if delta > 0 else -units_per_notch
            except Exception:
                pass
        return original(self, event)

    ctk.CTkScrollableFrame._mouse_wheel_all = normalised
    ctk.CTkScrollableFrame._basecamp_wheel_fix = True


_install_ctk_wheel_fix()


def attach_clipboard_menu(widget, T=None):
    """Add right-click cut/copy/paste menu + reliable Ctrl-key bindings to an Entry/Text.

    Tk on Linux does not always wire Ctrl+C/V/X to CLIPBOARD reliably (it has
    historically used PRIMARY selection only), and CTkEntry never gets a
    context menu by default. This helper fixes both for one widget.

    `widget` can be a CTkEntry/CTkTextbox or a raw tk.Entry/tk.Text.
    `T` is an optional translator callable; if None we fall back to English labels.
    """
    inner = getattr(widget, "_entry", None) or getattr(widget, "_textbox", None) or widget

    def _tr(en, de_key):
        if T:
            try:
                v = T(de_key)
                if v and v != de_key:
                    return v
            except Exception:
                pass
        return en

    def _is_text():
        return inner.winfo_class() in ("Text",)

    def _cut(_=None):
        try: inner.event_generate("<<Cut>>")
        except Exception: pass
        return "break"

    def _copy(_=None):
        try: inner.event_generate("<<Copy>>")
        except Exception: pass
        return "break"

    def _paste(_=None):
        try: inner.event_generate("<<Paste>>")
        except Exception: pass
        return "break"

    def _select_all(_=None):
        try:
            if _is_text():
                inner.tag_add("sel", "1.0", "end-1c")
            else:
                inner.select_range(0, "end")
                inner.icursor("end")
        except Exception:
            pass
        return "break"

    # Not a tk.Menu: it would take a global grab and stay over whatever the
    # user switches to, which is the dropdown's problem in an entry field
    # (#66). InlineMenu is the same list the dropdowns use. One per widget,
    # with the labels rebuilt on every open so a language change is picked up.
    menu = InlineMenu(inner, [])

    def _popup(event):
        menu.set_items([(_tr("Cut",        "clipboard_cut"),        _cut),
                        (_tr("Copy",       "clipboard_copy"),       _copy),
                        (_tr("Paste",      "clipboard_paste"),      _paste),
                        (_tr("Select All", "clipboard_select_all"), _select_all)])
        inner.focus_set()
        menu.open(event.x_root, event.y_root)

    inner.bind("<Button-3>", _popup, add="+")
    # Reliable Ctrl-key bindings (override Tk defaults so they always hit CLIPBOARD)
    for seq, fn in (("<Control-c>", _copy), ("<Control-C>", _copy),
                    ("<Control-x>", _cut), ("<Control-X>", _cut),
                    ("<Control-v>", _paste), ("<Control-V>", _paste),
                    ("<Control-a>", _select_all), ("<Control-A>", _select_all)):
        inner.bind(seq, fn, add="+")


# ── Color Picker Dialog ────────────────────────────────────────────────────────

_WHL    = 220
_WHL_R  = _WHL // 2
_WHL_BG = (18, 18, 30)


def _make_wheel_full():
    """Build HSV colour wheel at V=1.0. Called once per dialog open."""
    R = _WHL_R
    pixels = bytearray(_WHL * _WHL * 3)
    off = 0
    for y in range(_WHL):
        dy = y - R
        for x in range(_WHL):
            dx = x - R
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > R:
                pixels[off:off + 3] = _WHL_BG
            else:
                h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
                s = dist / R
                r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
                pixels[off]     = int(r * 255)
                pixels[off + 1] = int(g * 255)
                pixels[off + 2] = int(b * 255)
            off += 3
    return Image.frombytes("RGB", (_WHL, _WHL), bytes(pixels))


class ColorPickerDialog(ctk.CTkToplevel):
    """Modern HSV colour wheel dialog. result is (r,g,b) or None."""

    def __init__(self, parent, initial_rgb=(255, 255, 255), title="Pick Color",
                 show_brightness=True):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self._show_brightness = show_brightness

        r, g, b = [x / 255.0 for x in initial_rgb]
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        self._h = h
        self._s = s
        self._v = max(v, 0.02)

        self._initial_rgb = initial_rgb
        self._wheel_full  = _make_wheel_full()
        self._wheel_photo = None

        self._build_ui()
        self._refresh_wheel()
        self._update_marker()

        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width() // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        dw = self.winfo_width()
        dh = self.winfo_height()
        self.geometry(f"+{pw - dw//2}+{ph - dh//2}")

        self.attributes('-topmost', True)
        self.grab_set()
        self.focus_force()
        self.wait_window()

    def _build_ui(self):
        PAD = 16
        _t = resolve_t(self)
        self.configure(fg_color=BG2)

        self._canvas = tk.Canvas(self, width=_WHL, height=_WHL,
                                 bg=_rgb_hex(_WHL_BG), highlightthickness=0,
                                 cursor="crosshair")
        self._canvas.pack(padx=PAD, pady=(PAD, 6))
        self._wheel_item = self._canvas.create_image(0, 0, anchor="nw")
        self._canvas.bind("<Button-1>",  self._on_wheel_click)
        self._canvas.bind("<B1-Motion>", self._on_wheel_click)

        self._bri_var = tk.DoubleVar(value=self._v * 100)
        if self._show_brightness:
            bri_row = ctk.CTkFrame(self, fg_color="transparent")
            bri_row.pack(fill="x", padx=PAD, pady=2)
            ctk.CTkLabel(bri_row, text=_t("custom_rgb_brightness_short"), width=70,
                         font=FONT_SM, text_color=FG2,
                         anchor="w").pack(side="left")
            ctk.CTkSlider(bri_row, from_=0, to=100, variable=self._bri_var,
                          command=self._on_bri_change,
                          button_color=BLUE, progress_color=BG3
                          ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        hex_row = ctk.CTkFrame(self, fg_color="transparent")
        hex_row.pack(fill="x", padx=PAD, pady=4)
        ctk.CTkLabel(hex_row, text="#", width=14, text_color=FG2).pack(side="left")
        self._hex_var = tk.StringVar()
        hex_ent = ctk.CTkEntry(hex_row, textvariable=self._hex_var,
                               width=90, height=30, fg_color=BG3,
                               border_color=BORDER)
        hex_ent.pack(side="left", padx=(0, 8))
        hex_ent.bind("<Return>",   self._on_hex_commit)
        hex_ent.bind("<FocusOut>", self._on_hex_commit)

        self._swatch_before = tk.Canvas(hex_row, width=30, height=30,
                                        highlightthickness=1,
                                        highlightbackground="#444")
        self._swatch_before.configure(bg=_rgb_hex(self._initial_rgb))
        self._swatch_before.pack(side="left", padx=(0, 2))
        self._swatch_after = tk.Canvas(hex_row, width=30, height=30,
                                       highlightthickness=1,
                                       highlightbackground="#666")
        self._swatch_after.pack(side="left")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=PAD, pady=(4, PAD))
        ctk.CTkButton(btn_row, text=_t("custom_rgb_cancel"), width=90, height=32,
                      fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
                      command=self.destroy).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text=_t("ui_ok"), width=90, height=32,
                      fg_color=BLUE, hover_color="#0284c7",
                      command=self._ok).pack(side="right")

        self._sync_fields()

    def _refresh_wheel(self):
        img = ImageEnhance.Brightness(self._wheel_full).enhance(self._v)
        self._wheel_photo = ImageTk.PhotoImage(img)
        self._canvas.itemconfig(self._wheel_item, image=self._wheel_photo)

    def _update_marker(self):
        R     = _WHL_R
        angle = self._h * 2 * math.pi
        dist  = self._s * R
        mx    = R + dist * math.cos(angle)
        my    = R + dist * math.sin(angle)
        MR    = 7
        self._canvas.delete("marker")
        self._canvas.create_oval(mx - MR, my - MR, mx + MR, my + MR,
                                  outline="#ffffff", width=2, tags="marker")
        self._canvas.create_oval(mx - MR + 2, my - MR + 2, mx + MR - 2, my + MR - 2,
                                  outline="#000000", width=1, tags="marker")

    def _on_wheel_click(self, e):
        R    = _WHL_R
        dx   = e.x - R
        dy   = e.y - R
        dist = math.sqrt(dx * dx + dy * dy)
        dist = min(dist, R)
        self._h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self._s = dist / R
        self._update_marker()
        self._sync_fields()

    def _on_bri_change(self, val):
        self._v = max(float(val) / 100.0, 0.001)
        self._refresh_wheel()
        self._update_marker()
        self._sync_fields()

    def _on_hex_commit(self, _=None):
        raw = self._hex_var.get().strip().lstrip("#")
        if len(raw) != 6:
            return
        try:
            r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        except ValueError:
            return
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self._h, self._s, self._v = h, s, max(v, 0.001)
        self._bri_var.set(self._v * 100)
        self._refresh_wheel()
        self._update_marker()
        self._update_swatches()

    def _current_rgb(self):
        r, g, b = colorsys.hsv_to_rgb(self._h, self._s, self._v)
        return (int(r * 255), int(g * 255), int(b * 255))

    def _sync_fields(self):
        rgb = self._current_rgb()
        self._hex_var.set("{:02x}{:02x}{:02x}".format(*rgb))
        self._update_swatches()

    def _update_swatches(self):
        self._swatch_after.configure(bg=_rgb_hex(self._current_rgb()))

    def _ok(self):
        self.result = self._current_rgb()
        self.destroy()


def pick_color(parent, initial_rgb=(255, 255, 255), title="Pick Color", show_brightness=True):
    """Open ColorPickerDialog and return (r,g,b) or None."""
    dlg = ColorPickerDialog(parent, initial_rgb, title, show_brightness=show_brightness)
    return dlg.result


# ── Keyboard layout for CustomRGBWindow ───────────────────────────────────────

def _build_kb_layout():
    """Return list of (label, led_idx_or_None, x, y, w, h) for all keys."""
    SC = 0.82
    KH = int(30 * SC)
    RS = int(32 * SC)
    IW = int(510 * SC)
    FW = int(616 * SC)
    OX = 14 + int(26 * SC)
    OY = 14 + int(12 * SC)
    NP_X0 = 14 + int(642 * SC) + 32
    NPS   = int(33 * SC)
    NPG   = int(7 * SC)
    MS  = 25
    MG  = 4
    MX  = OX + IW + 8
    NPTAL = KH + RS

    def sbet(specs, inner_w, y):
        total = sum(int(w * SC) for _, _, w in specs)
        gap   = (inner_w - total) / max(1, len(specs) - 1)
        res, x = [], OX
        for lbl, idx, cw in specs:
            pw = int(cw * SC)
            res.append((lbl, idx, int(x), y, pw, KH))
            x += pw + gap
        return res

    L = []
    L += sbet([
        ('ESC',0,30),('F1',9,30),('F2',18,30),('F3',27,30),
        ('F4',36,30),('F5',45,30),('F6',54,30),('F7',63,30),
        ('F8',72,30),('F9',81,30),('F10',90,30),('F11',99,30),
        ('F12',108,30),('PrtSc',117,30),('ScrLk',114,30),('Pause',123,30),
    ], FW, OY)

    y1 = OY + RS
    L += sbet([
        ('`',1,30),('1',10,30),('2',19,30),('3',28,30),('4',37,30),
        ('5',46,30),('6',55,30),('7',64,30),('8',73,30),('9',82,30),
        ('0',91,30),('-',100,30),('=',109,30),('⌫',87,68),
    ], IW, y1)
    L += [('Ins',96,MX+MS+MG,y1,MS,KH), ('Del',88,MX+2*(MS+MG),y1,MS,KH)]

    y2 = OY + 2 * RS
    L += sbet([
        ('Tab',2,50),('Q',11,30),('W',20,30),('E',29,30),('R',38,30),
        ('T',47,30),('Y',56,30),('U',65,30),('I',74,30),('O',83,30),
        ('P',92,30),('[',101,30),(']',110,30),('\\',119,50),
    ], IW, y2)
    L += [('Home',105,MX+MS+MG,y2,MS,KH), ('PgUp',115,MX+2*(MS+MG),y2,MS,KH)]

    y3 = OY + 3 * RS
    L += sbet([
        ('Caps',3,60),('A',12,30),('S',21,30),('D',30,30),('F',39,30),
        ('G',48,30),('H',57,30),('J',66,30),('K',75,30),('L',84,30),
        (';',93,30),("'",102,30),('↵',120,73),
    ], IW, y3)
    L += [('End',97,MX+MS+MG,y3,MS,KH), ('PgDn',106,MX+2*(MS+MG),y3,MS,KH)]

    y4 = OY + 4 * RS
    L += sbet([
        ('⇧',4,80),('Z',22,30),('X',31,30),('C',40,30),('V',49,30),
        ('B',58,30),('N',67,30),('M',76,30),(',',85,30),('.',94,30),
        ('/',103,30),('⇧',121,88),
    ], IW, y4)
    L.append(('↑', 124, MX + MS + MG, y4, MS, KH))

    y5 = OY + 5 * RS
    L += sbet([
        ('Ctrl',5,42),('⊞',14,42),('Alt',23,42),(' ',41,210),
        ('Alt',68,42),('⊞',77,42),('FN',86,42),('≡',None,42),('Ctrl',95,42),
    ], IW, y5)
    L += [
        ('←',104,MX,y5,MS,KH),
        ('↓',113,MX+MS+MG,y5,MS,KH),
        ('→',122,MX+2*(MS+MG),y5,MS,KH),
    ]

    npx = NP_X0 + int(5 * SC)
    npy = OY
    for i, (lbl, idx) in enumerate([('NumLk',6),('/',24),('*',16),('-',15)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy, NPS, KH))
    for i, (lbl, idx) in enumerate([('7',61),('8',69),('9',70)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+RS, NPS, KH))
    L.append(('+', 7, npx + 3*(NPS+NPG), npy+RS, NPS, NPTAL))
    for i, (lbl, idx) in enumerate([('4',51),('5',52),('6',60)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+2*RS, NPS, KH))
    for i, (lbl, idx) in enumerate([('1',34),('2',42),('3',43)]):
        L.append((lbl, idx, npx + i*(NPS+NPG), npy+3*RS, NPS, KH))
    L.append(('↵', 33, npx + 3*(NPS+NPG), npy+3*RS, NPS, NPTAL))
    L.append(('0', 78, npx, npy+4*RS, 2*NPS+NPG, KH))
    L.append(('.', 79, npx + 2*NPS+2*NPG, npy+4*RS, NPS, KH))

    return L


_KB_LAYOUT   = _build_kb_layout()
_KB_CANVAS_W = 14 + int(642 * 0.82) + 32 + int(166 * 0.82) + 14
_KB_CANVAS_H = (14 + int(12 * 0.82)) + 5 * int(32 * 0.82) + int(30 * 0.82) + 18 + 24
_SIDE_SZ     = 9
_SIDE_OFFSET = 12


def _build_kb60_layout():
    """Return Everest 60 key layout: (label, led_idx, x, y, w, h).
    64 keys total — no backtick key on Everest 60.
    Row 3 has small right shift + arrow up + Del.
    Row 4 has arrow left/down/right aligned under row 3.
    """
    SC = 0.82
    KH = int(30 * SC)
    RS = int(32 * SC)
    IW = int(460 * SC)
    OX = 14
    OY = 14

    def sbet(specs, inner_w, y):
        total = sum(int(w * SC) for _, _, w in specs)
        gap   = (inner_w - total) / max(1, len(specs) - 1)
        res, x = [], OX
        for lbl, idx, cw in specs:
            pw = int(cw * SC)
            res.append((lbl, idx, int(x), y, pw, KH))
            x += pw + gap
        return res

    KW = int(30 * SC)     # standard key width
    KG = int(2 * SC)      # gap between keys

    L = []
    # Row 0: Esc 1-0 - = Backspace (14 keys, idx 0-13) — no backtick
    L += sbet([('Esc',0,30),('1',1,30),('2',2,30),('3',3,30),('4',4,30),
               ('5',5,30),('6',6,30),('7',7,30),('8',8,30),('9',9,30),
               ('0',10,30),('-',11,30),('=',12,30),('⌫',13,60)], IW, OY)
    # Row 1: Tab Q-P [ ] \ (14 keys, idx 14-27)
    L += sbet([('Tab',14,45),('Q',15,30),('W',16,30),('E',17,30),('R',18,30),
               ('T',19,30),('Y',20,30),('U',21,30),('I',22,30),('O',23,30),
               ('P',24,30),('[',25,30),(']',26,30),('\\',27,45)], IW, OY+RS)
    # Row 2: Caps A-L ; ' Enter (13 keys, idx 28-40)
    L += sbet([('Caps',28,53),('A',29,30),('S',30,30),('D',31,30),('F',32,30),
               ('G',33,30),('H',34,30),('J',35,30),('K',36,30),('L',37,30),
               (';',38,30),("'",39,30),('↵',40,67)], IW, OY+2*RS)
    # Row 3: Shift Z-/ small-Shift ↑ Del (14 keys, idx 41-54)
    L += sbet([('⇧',41,60),('Z',42,30),('X',43,30),('C',44,30),('V',45,30),
               ('B',46,30),('N',47,30),('M',48,30),(',',49,30),('.',50,30),
               ('/',51,30),('⇧',52,30),('↑',53,30),('Del',54,30)], IW, OY+3*RS)
    # Row 4: Ctrl Win Alt Space Alt Fn ← ↓ → (9 keys, idx 55-63)
    # Get arrow positions from row 3 first, then fit left keys up to ←
    arrow_up_x = next(x for lbl, _, x, _, _, _ in L if lbl == '↑')
    del_x      = next(x for lbl, _, x, _, _, _ in L if lbl == 'Del')
    left_arrow_x = arrow_up_x - KW - KG
    # Distribute left 6 keys up to the ← position
    left_iw = left_arrow_x - OX - KG
    L += sbet([('Ctrl',55,38),('⊞',56,30),('Alt',57,38),(' ',58,194),
               ('Alt',59,30),('Fn',60,30)], left_iw, OY+4*RS)
    L.append(('←', 61, left_arrow_x, OY+4*RS, KW, KH))
    L.append(('↓', 62, arrow_up_x, OY+4*RS, KW, KH))
    L.append(('→', 63, del_x, OY+4*RS, KW, KH))
    return L


_KB60_LAYOUT   = _build_kb60_layout()
_KB60_CANVAS_W = max(x + w for _, _, x, _, w, _ in _KB60_LAYOUT) + 14
_KB60_CANVAS_H = 14 + 4 * int(32 * 0.82) + int(30 * 0.82) + 18
_KB60_NUM_LEDS = 64

_QUICK_COLORS = [
    ("#ff0000", (255, 0, 0)), ("#ff8800", (255, 136, 0)),
    ("#ffff00", (255, 255, 0)), ("#00ff00", (0, 255, 0)),
    ("#00ffff", (0, 255, 255)), ("#0088ff", (0, 136, 255)),
    ("#8800ff", (136, 0, 255)), ("#ff00ff", (255, 0, 255)),
    ("#ffffff", (255, 255, 255)), ("#000000", (0, 0, 0)),
]

# QWERTY → QWERTZ label substitution (German keyboard layout)
_QWERTZ_MAP = {
    'Y': 'Z', 'Z': 'Y',
    '`': '^', '-': 'ß', '=': '´',
    '[': 'ü', ']': '+', '\\': '#',
    ';': 'ö', "'": 'ä', '/': '-',
}

_SIDE_ZONE_INDICES = [
    [13,14,15,7,6,5,4,3,2,1,0],
    [9,8,10,11],
    [20,21,22,23,24,25,26,27,28,29,30,12],
    [16,17,18,19],
    [31,44,43,42],
    [41,40,39],
    [35,36,37,38],
    [32,33,34],
]


_RGB_RAIL_W = 236   # colour column beside the keyboard


# ── CustomRGBWindow ────────────────────────────────────────────────────────────

class CustomRGBWindow(ctk.CTkFrame):
    """The per-key colour editor.

    It used to be a window on top of the app, with the colour picker as a
    third window on top of that, which regularly covered the very keyboard
    someone was painting. It is a screen now: `parent` is the content area and
    `on_close` takes the caller back to the device it came from. The name is
    kept because six hundred lines below refer to it.
    """

    def __init__(self, app, layout=None, canvas_w=None, canvas_h=None,
                 num_leds=126, has_side_leds=True, num_side_leds=45,
                 has_numpad=True, has_persist=True, side_layout="ring",
                 load_per_key=None, save_per_key=None,
                 load_presets=None, save_presets=None,
                 apply_cmd=None, parent=None, on_close=None):
        super().__init__(parent if parent is not None else app, fg_color=BG)
        self._app = app
        self._lang = getattr(app, '_lang', {})
        self._on_close_cb = on_close

        self._layout     = layout    if layout    is not None else _KB_LAYOUT
        self._canvas_w   = canvas_w  if canvas_w  is not None else _KB_CANVAS_W
        self._canvas_h   = canvas_h  if canvas_h  is not None else _KB_CANVAS_H
        self._num_leds   = num_leds
        self._has_side   = has_side_leds
        self._num_side   = num_side_leds
        self._has_numpad = has_numpad
        self._has_persist = has_persist
        # side_layout: "ring" = the Everest Max ring drawn around the keyboard;
        # "strip" = a plain per-LED strip below the keyboard for the Everest 60's
        # 44-LED ring, which has no per-edge geometry data yet (#4).
        self._side_layout = side_layout
        self._side_yo    = 0 if side_layout == "strip" else (_SIDE_OFFSET if has_side_leds else 0)
        self._side_sz    = _SIDE_SZ     if has_side_leds else 0
        self._apply_cmd  = apply_cmd

        from shared.config import (_load_per_key as _lpk, _save_per_key as _spk,
                                    _load_presets as _lpr, _save_presets as _spr)
        self._load_per_key = load_per_key or _lpk
        self._save_per_key = save_per_key or _spk
        self._load_presets = load_presets or _lpr
        self._save_presets = save_presets or _spr

        self._leds, raw_side, self._bri = self._load_per_key()
        self._leds      = (list(self._leds) + [(20, 20, 20)] * num_leds)[:num_leds]
        # Always length-normalise the side ring so the strip/ring drawing can
        # index every LED without an IndexError when the saved data is short.
        self._side_leds = ((list(raw_side) + [(20, 20, 20)] * num_side_leds)[:num_side_leds]
                           if has_side_leds else [])
        self._selected   = set()
        self._fill_rgb   = (255, 0, 0)
        self._drag_rect  = None
        self._item_led   = {}
        self._led_item   = {}
        self._undo_stack = []
        self._kb_layout_mode = "QWERTY"
        self._bri_debounce_id = None

        self._build_ui()
        self.after(50, self._draw_keys)

    def _T(self, key, **kw):
        s = self._lang.get(key, key) if self._lang else key
        return s.format(**kw) if kw else s

    def _build_ui(self):
        PAD = 12
        self.configure(fg_color=BG)

        # Toolbar over the stage, everything about colour in a column on the
        # right, the one filled action in the screen header. The old layout
        # stacked six rows of controls under the keyboard, so the thing being
        # painted and the tools painting it were never in one glance.
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=PAD, pady=(PAD, 0))
        for key, cmd in (("custom_rgb_select_all", self._select_all),
                         ("custom_rgb_deselect", self._deselect_all),
                         ("custom_rgb_all_black", lambda: self._fill_all((0, 0, 0))),
                         ("custom_rgb_all_white", lambda: self._fill_all((255, 255, 255)))):
            _ui.GhostButton(bar, self._T(key), cmd, width=104,
                            height=_ui.CTRL_H_SM).pack(side="left", padx=(0, 6))
        self._eyedrop_mode = False
        self._eyedrop_btn = _ui.GhostButton(
            bar, self._T("custom_rgb_eyedropper_btn"), self._toggle_eyedrop,
            width=96, height=_ui.CTRL_H_SM)
        self._eyedrop_btn.pack(side="left", padx=(0, 6))
        _ui.GhostButton(bar, self._T("custom_rgb_undo"), self._undo, width=104,
                        height=_ui.CTRL_H_SM).pack(side="left")
        self._sel_lbl = ctk.CTkLabel(bar, text=self._T("custom_rgb_selected", n=0),
                                     text_color=FG2, font=(_ui.FONT_FAMILY, 11))
        self._sel_lbl.pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=_RGB_RAIL_W)
        body.grid_rowconfigure(0, weight=1)

        stage = ctk.CTkFrame(body, fg_color="transparent")
        stage.grid(row=0, column=0, sticky="nsew")

        self._cv = tk.Canvas(stage, width=self._canvas_w, height=self._canvas_h,
                             bg="#111118", highlightthickness=0, bd=0)
        self._cv.pack(anchor="n")
        self._cv.bind("<Button-1>",        self._on_click)
        self._cv.bind("<B1-Motion>",       self._on_drag)
        self._cv.bind("<ButtonRelease-1>", self._on_release)
        self._cv.bind("<Double-Button-1>", self._on_dbl)
        self._cv.bind("<Button-3>",        self._on_rclick)
        self._cv.bind("<Shift-Button-1>",   self._on_eyedrop)
        self.bind("<Control-z>", self._undo)
        self.bind("<Control-Z>", self._undo)

        kb_bar = ctk.CTkFrame(stage, fg_color="transparent")
        kb_bar.pack(pady=(8, 0))
        ctk.CTkLabel(kb_bar, text=self._T("custom_rgb_kb_layout"),
                     text_color=FG2, font=(_ui.FONT_FAMILY, 11)).pack(side="left", padx=(0, 6))
        self._kb_seg = ctk.CTkSegmentedButton(
            kb_bar, values=["QWERTY", "QWERTZ"], height=26,
            font=(_ui.FONT_FAMILY, 11),
            selected_color=BLUE, unselected_color=BG3,
            text_color=FG, unselected_hover_color="#2a2a3a",
            command=self._switch_kb_layout)
        self._kb_seg.set("QWERTY")
        self._kb_seg.pack(side="left")

        rail = ctk.CTkFrame(body, fg_color=BG2, corner_radius=7,
                            border_width=1, border_color=BORDER)
        rail.grid(row=0, column=1, sticky="nsew", padx=(PAD, 0))

        _ui.SectionLabel(rail, text=self._T("custom_rgb_colour")).pack(
            fill="x", padx=12, pady=(12, 6))
        sw_row = ctk.CTkFrame(rail, fg_color="transparent")
        sw_row.pack(fill="x", padx=12)
        self._fill_swatch = tk.Canvas(sw_row, width=30, height=30,
                                      bg=_rgb_hex(self._fill_rgb),
                                      highlightthickness=1,
                                      highlightbackground="#555")
        self._fill_swatch.pack(side="left")
        _ui.GhostButton(sw_row, self._T("custom_rgb_pick"), self._pick_fill,
                        width=_RGB_RAIL_W - 74,
                        height=_ui.CTRL_H_SM).pack(side="left", padx=(8, 0))

        quick = ctk.CTkFrame(rail, fg_color="transparent")
        quick.pack(fill="x", padx=12, pady=(8, 0))
        for i, (hex_c, rgb) in enumerate(_QUICK_COLORS):
            btn = tk.Canvas(quick, width=22, height=22, bg=hex_c,
                            highlightthickness=1, highlightbackground="#333",
                            cursor="hand2")
            btn.grid(row=i // 5, column=i % 5, padx=2, pady=2)
            btn.bind("<Button-1>", lambda e, c=rgb: self._set_fill(c))
        _ui.PrimaryButton(rail, self._T("custom_rgb_fill"), self._fill_selected,
                          width=_RGB_RAIL_W - 24,
                          height=_ui.CTRL_H_SM).pack(fill="x", padx=12, pady=(10, 0))

        _ui.SectionLabel(rail, text=self._T("custom_rgb_brightness_short")).pack(
            fill="x", padx=12, pady=(14, 4))
        bri_row = ctk.CTkFrame(rail, fg_color="transparent")
        bri_row.pack(fill="x", padx=12)
        self._bri_sl = ctk.CTkSlider(bri_row, from_=10, to=100, number_of_steps=90,
                                     fg_color=BG3, progress_color=BLUE,
                                     button_color=BLUE, button_hover_color=BLUE,
                                     height=16)
        self._bri_sl.set(self._bri)
        self._bri_sl.configure(command=self._on_bri_change)
        self._bri_sl.pack(side="left", fill="x", expand=True)
        self._bri_val = ctk.CTkLabel(bri_row, text=str(self._bri), text_color=FG,
                                     font=(_ui.FONT_FAMILY, 11), width=32)
        self._bri_val.pack(side="right")

        _ui.SectionLabel(rail, text=self._T("custom_rgb_presets")).pack(
            fill="x", padx=12, pady=(14, 4))
        self._preset_var = tk.StringVar()
        self._preset_combo = ctk.CTkComboBox(
            rail, variable=self._preset_var, values=[],
            width=_RGB_RAIL_W - 24, height=_ui.CTRL_H_SM,
            fg_color=BG3, border_color=BORDER, button_color=BG3,
            dropdown_fg_color=BG2, text_color=FG, font=(_ui.FONT_FAMILY, 11))
        self._preset_combo.pack(padx=12)
        pre_row = ctk.CTkFrame(rail, fg_color="transparent")
        pre_row.pack(fill="x", padx=12, pady=(6, 0))
        _ui.GhostButton(pre_row, self._T("custom_rgb_load"), self._preset_load,
                        width=62, height=_ui.CTRL_H_SM).pack(side="left")
        _ui.GhostButton(pre_row, self._T("custom_rgb_save_as"), self._preset_save_as,
                        width=84, height=_ui.CTRL_H_SM).pack(side="left", padx=6)
        _ui.DangerButton(pre_row, self._T("custom_rgb_delete"), self._preset_delete,
                         width=62, height=_ui.CTRL_H_SM).pack(side="left")
        self._preset_status = ctk.CTkLabel(rail, text="", text_color=FG2,
                                           font=(_ui.FONT_FAMILY, 10), anchor="w")
        self._preset_status.pack(fill="x", padx=12, pady=(4, 0))
        self._preset_refresh()

        prof = ctk.CTkFrame(rail, fg_color="transparent")
        prof.pack(fill="x", padx=12, pady=(14, 12), side="bottom")
        _ui.GhostButton(prof, self._T("custom_rgb_save_profile"), self._save_profile,
                        width=_RGB_RAIL_W - 24,
                        height=_ui.CTRL_H_SM).pack(fill="x")
        _ui.GhostButton(prof, self._T("custom_rgb_load_profile"), self._load_profile,
                        width=_RGB_RAIL_W - 24,
                        height=_ui.CTRL_H_SM).pack(fill="x", pady=(6, 0))
        self._status = ctk.CTkLabel(prof, text="", text_color=FG2,
                                    font=(_ui.FONT_FAMILY, 11), anchor="w")
        self._status.pack(fill="x", pady=(6, 0))

    def _toggle_eyedrop(self):
        """The eyedropper was a grey hint saying "Shift+click"; whoever did not
        read it never found it. As a button that latches it is a tool."""
        self._eyedrop_mode = not self._eyedrop_mode
        self._eyedrop_btn.configure(
            border_color=BLUE if self._eyedrop_mode else BORDER,
            text_color=BLUE if self._eyedrop_mode else FG2)

    def _draw_keys(self):
        self._cv.delete("all")
        self._item_led.clear()
        self._led_item.clear()

        SC  = 0.82
        YO  = self._side_yo
        SZ  = self._side_sz
        GAP = 2

        strip_mode = self._has_side and self._side_layout == "strip"
        bx1  = 11;  by1 = 11 + YO
        bx2  = 14 + int(642 * SC) + 4 if self._has_numpad else self._canvas_w - 11
        if strip_mode:
            # Keyboard box ends at its own content bottom; the side strip fills
            # the reserved space below it (the caller sizes the canvas taller).
            by2 = max((y + h) for (_, _, _, y, _, h) in self._layout) + YO + 4
        else:
            by2 = self._canvas_h - YO - 6

        self._cv.create_rectangle(bx1, by1, bx2, by2,
                                  fill="#1a1a22", outline="#333", width=1)

        if self._has_numpad:
            npx   = 14 + int(642 * SC) + 32
            npbx1 = npx - 3
            npbx2 = npx + int(166 * SC) + 3
            self._cv.create_rectangle(npbx1, by1, npbx2, by2,
                                      fill="#1a1a22", outline="#333", width=1)

        qz = _QWERTZ_MAP if self._kb_layout_mode == "QWERTZ" else {}
        for (lbl, idx, x, y, w, h) in self._layout:
            yo    = y + YO
            color = _rgb_hex(self._leds[idx]) if idx is not None and 0 <= idx < self._num_leds else "#252530"
            sel   = idx in self._selected
            item  = self._cv.create_rectangle(
                x, yo, x + w, yo + h,
                fill=color,
                outline="#00d4ff" if sel else "#111",
                width=2 if sel else 1,
            )
            font_size = 6 if w < 22 else 7
            draw_lbl = qz.get(lbl, lbl)
            self._cv.create_text(x + w // 2, yo + h // 2, text=draw_lbl,
                                 fill="#cccccc", font=(_ui.FONT_FAMILY, font_size),
                                 anchor="center")
            if idx is not None:
                self._item_led[item] = idx
                self._led_item[idx]  = item

        if strip_mode:
            self._draw_side_strip(bx1, bx2, by2 + 14)
        elif self._has_side:
            def hstrip(indices, x1, x2, y):
                n = len(indices)
                for i, si in enumerate(indices):
                    px = int(x1 + i * (x2 - x1 - SZ) / max(1, n - 1)) if n > 1 else (x1 + x2 - SZ) // 2
                    color = _rgb_hex(self._side_leds[si])
                    sel   = (200 + si) in self._selected
                    item  = self._cv.create_rectangle(px, y, px + SZ, y + SZ,
                                fill=color,
                                outline="#00d4ff" if sel else "#555",
                                width=2 if sel else 1)
                    self._item_led[item]     = 200 + si
                    self._led_item[200 + si] = item

            def vstrip(indices, y1, y2, x):
                n = len(indices)
                for i, si in enumerate(indices):
                    py = int(y1 + i * (y2 - y1 - SZ) / max(1, n - 1)) if n > 1 else (y1 + y2 - SZ) // 2
                    color = _rgb_hex(self._side_leds[si])
                    sel   = (200 + si) in self._selected
                    item  = self._cv.create_rectangle(x, py, x + SZ, py + SZ,
                                fill=color,
                                outline="#00d4ff" if sel else "#555",
                                width=2 if sel else 1)
                    self._item_led[item]     = 200 + si
                    self._led_item[200 + si] = item

            hstrip([13,14,15,7,6,5,4,3,2,1,0],            bx1, bx2,   by1 - GAP - SZ)
            vstrip([9,8,10,11],                             by1, by2,   bx2 + GAP)
            hstrip([20,21,22,23,24,25,26,27,28,29,30,12],  bx1, bx2,   by2 + GAP)
            vstrip([16,17,18,19],                           by1, by2,   bx1 - GAP - SZ)
            if self._has_numpad:
                hstrip([44,43,42],      npbx1, npbx2, by1 - GAP - SZ)
                vstrip([41,40,39,38],   by1,   by2,   npbx2 + GAP)
                hstrip([35,36,37],      npbx1, npbx2, by2 + GAP)
                vstrip([31,32,33,34],   by1,   by2,   npbx1 - GAP - SZ)

    def _draw_side_strip(self, x1, x2, y0):
        """Draw the Everest 60 side ring as a labelled two-row per-LED strip
        below the keyboard (#4). Index 0 is top-left above ESC, running clockwise
        to 43; these map to hardware LEDs 126..169 in the controller. It's a plain
        per-LED editor, not a physical ring — the exact ring geometry isn't known
        without the hardware, so the strip lets every LED be painted regardless."""
        n = self._num_side
        cols = (n + 1) // 2                      # 22 per row for 44
        gap = 3
        sz = min(16, max(9, int((x2 - x1 - (cols - 1) * gap) / cols)))
        self._cv.create_text(x1, y0 - 3, anchor="sw", text=self._T("side_leds_title"),
                             fill="#8892a6", font=(_ui.FONT_FAMILY, 9))
        row_h = sz + gap + 9
        for si in range(n):
            row, col = divmod(si, cols)
            px = x1 + col * (sz + gap)
            py = y0 + 4 + row * row_h
            sel  = (200 + si) in self._selected
            item = self._cv.create_rectangle(
                px, py, px + sz, py + sz,
                fill=_rgb_hex(self._side_leds[si]),
                outline="#00d4ff" if sel else "#555", width=2 if sel else 1)
            self._item_led[item]     = 200 + si
            self._led_item[200 + si] = item
            # Sparse position ticks so the user can orient (1, 12, 24, 36, 44).
            if si == 0 or si == n - 1 or (si + 1) % 12 == 0:
                self._cv.create_text(px + sz // 2, py + sz + 5, text=str(si + 1),
                                     fill="#556", font=(_ui.FONT_FAMILY, 7))

    def _switch_kb_layout(self, value):
        self._kb_layout_mode = value
        self._draw_keys()

    def _refresh_key(self, idx):
        item = self._led_item.get(idx)
        if item is None:
            return
        sel = idx in self._selected
        if self._has_side and 200 <= idx < 200 + self._num_side:
            color   = _rgb_hex(self._side_leds[idx - 200])
            outline = "#00d4ff" if sel else "#555"
        else:
            color   = _rgb_hex(self._leds[idx]) if 0 <= idx < self._num_leds else "#252530"
            outline = "#00d4ff" if sel else "#111"
        self._cv.itemconfigure(item, fill=color, outline=outline, width=2 if sel else 1)

    def _key_at(self, ex, ey):
        items = self._cv.find_overlapping(ex, ey, ex, ey)
        for item in reversed(items):
            if item in self._item_led:
                return self._item_led[item]
        return None

    def _on_click(self, e):
        if getattr(self, "_eyedrop_mode", False):
            self._on_eyedrop(e)
            self._toggle_eyedrop()      # one pick, then back to selecting
            return
        ctrl = (e.state & 0x0004) != 0
        idx  = self._key_at(e.x, e.y)
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
            self._refresh_key(idx)
        else:
            old = set(self._selected)
            self._selected = {idx}
            for i in old:
                self._refresh_key(i)
            self._refresh_key(idx)
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
        dx, dy = abs(e.x - x0), abs(e.y - y0)
        if dx < 5 and dy < 5:
            return
        rx1, rx2 = min(x0, e.x), max(x0, e.x)
        ry1, ry2 = min(y0, e.y), max(y0, e.y)
        ctrl = (e.state & 0x0004) != 0
        if not ctrl:
            old = set(self._selected)
            self._selected.clear()
            for i in old:
                self._refresh_key(i)
        for item, idx in self._item_led.items():
            coords = self._cv.coords(item)
            if coords[0] < rx2 and coords[2] > rx1 and \
               coords[1] < ry2 and coords[3] > ry1:
                self._selected.add(idx)
        for idx in self._selected:
            self._refresh_key(idx)
        self._update_sel_lbl()

    def _on_dbl(self, e):
        idx = self._key_at(e.x, e.y)
        if idx is not None and idx not in self._selected:
            self._selected.add(idx)
            self._refresh_key(idx)
        if self._selected:
            self._pick_fill()

    def _on_rclick(self, e):
        idx = self._key_at(e.x, e.y)
        if idx is None:
            return
        if idx in self._selected:
            self._selected.discard(idx)
        else:
            self._selected.add(idx)
        self._refresh_key(idx)
        self._update_sel_lbl()

    def _set_fill(self, rgb):
        self._fill_rgb = rgb
        self._fill_swatch.configure(bg=_rgb_hex(rgb))

    def _pick_fill(self):
        rgb = pick_color(self, initial_rgb=tuple(self._fill_rgb), title=self._T("custom_rgb_key_color"))
        if rgb is None:
            return
        self._set_fill(rgb)
        if self._selected:
            self._fill_selected()

    def _fill_selected(self):
        self._push_undo()
        for idx in self._selected:
            if 0 <= idx < self._num_leds:
                self._leds[idx] = self._fill_rgb
            elif self._has_side and 200 <= idx < 200 + self._num_side:
                self._side_leds[idx - 200] = self._fill_rgb
            self._refresh_key(idx)

    def _fill_all(self, rgb):
        self._push_undo()
        self._leds = [rgb] * self._num_leds
        self._side_leds = [rgb] * self._num_side if self._has_side else []
        self._draw_keys()

    def _select_all(self):
        self._selected = {idx for _, idx, *_ in self._layout if idx is not None and 0 <= idx < self._num_leds}
        if self._has_side:
            self._selected.update(200 + i for i in range(self._num_side))
        self._draw_keys()
        self._update_sel_lbl()

    def _deselect_all(self):
        old = set(self._selected)
        self._selected.clear()
        for i in old:
            self._refresh_key(i)
        self._update_sel_lbl()

    def _update_sel_lbl(self):
        n = len(self._selected)
        self._sel_lbl.configure(text=self._T("custom_rgb_selected", n=n))

    def _push_undo(self):
        self._undo_stack.append((list(self._leds), list(self._side_leds)))
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)

    def _undo(self, event=None):
        if not self._undo_stack:
            return
        self._leds, self._side_leds = self._undo_stack.pop()
        self._draw_keys()

    def _on_eyedrop(self, e):
        idx = self._key_at(e.x, e.y)
        if idx is None:
            return
        if self._has_side and 200 <= idx < 200 + self._num_side:
            col = self._side_leds[idx - 200]
        elif 0 <= idx < self._num_leds:
            col = self._leds[idx]
        else:
            return
        self._set_fill(col)

    def _preset_refresh(self):
        names = sorted(self._load_presets().keys())
        self._preset_combo.configure(values=names)
        if names and not self._preset_var.get():
            self._preset_combo.set(names[0])

    def _preset_load(self):
        name = self._preset_var.get().strip()
        presets = self._load_presets()
        if name not in presets:
            self._preset_status.configure(text=self._T("custom_rgb_not_found"), text_color=RED)
            return
        self._push_undo()
        d = presets[name]
        leds = [tuple(c) for c in d.get("leds", [])]
        self._leds = (leds + [(20, 20, 20)] * self._num_leds)[:self._num_leds]
        if self._has_side:
            raw = d.get("side", [])
            if isinstance(raw, list) and len(raw) == self._num_side:
                self._side_leds = [tuple(c) for c in raw]
        bri = int(d.get("brightness", 100))
        self._bri_sl.set(bri)
        self._bri_val.configure(text=str(bri))
        self._draw_keys()
        self._preset_status.configure(text=self._T("custom_rgb_preset_loaded", name=name), text_color=GRN)

    def _preset_save_as(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title(self._T("custom_rgb_preset_save_title"))
        dlg.resizable(False, False)
        dlg.geometry("300x110")
        dlg.grab_set()
        dlg.configure(fg_color=BG)
        ctk.CTkLabel(dlg, text=self._T("custom_rgb_preset_name"), text_color=FG,
                     font=(_ui.FONT_FAMILY, 12)).pack(pady=(14, 4))
        var = tk.StringVar(value=self._preset_var.get())
        entry = ctk.CTkEntry(dlg, textvariable=var, width=200, height=30,
                             fg_color=BG2, text_color=FG, border_color=BORDER,
                             font=(_ui.FONT_FAMILY, 12))
        entry.pack()
        entry.focus()
        def _save():
            name = var.get().strip()
            if not name:
                return
            presets = self._load_presets()
            entry = {"leds": [list(c) for c in self._leds],
                     "brightness": self._current_bri()}
            if self._has_side:
                entry["side"] = [list(c) for c in self._side_leds]
            presets[name] = entry
            self._save_presets(presets)
            self._preset_refresh()
            self._preset_combo.set(name)
            self._preset_status.configure(text=self._T("custom_rgb_preset_saved", name=name), text_color=GRN)
            dlg.destroy()
        entry.bind("<Return>", lambda e: _save())
        ctk.CTkButton(dlg, text=self._T("custom_rgb_preset_save_btn"), width=80, height=28,
                      fg_color=BLUE, text_color=FG, command=_save).pack(pady=8)

    def _preset_delete(self):
        name = self._preset_var.get().strip()
        presets = self._load_presets()
        if name not in presets:
            self._preset_status.configure(text=self._T("custom_rgb_not_found"), text_color=RED)
            return
        del presets[name]
        self._save_presets(presets)
        self._preset_refresh()
        remaining = sorted(presets.keys())
        self._preset_combo.set(remaining[0] if remaining else "")
        self._preset_status.configure(text=self._T("custom_rgb_preset_deleted", name=name), text_color=FG2)

    def _current_bri(self):
        return int(self._bri_sl.get())

    def _on_bri_change(self, v):
        self._bri_val.configure(text=str(int(float(v))))
        if self._bri_debounce_id is not None:
            self.after_cancel(self._bri_debounce_id)
        self._bri_debounce_id = self.after(300, self._apply_brightness_live)

    def _apply_brightness_live(self):
        self._bri_debounce_id = None
        payload = self._build_payload()
        def run():
            try:
                subprocess.run(self._cmd("per-key-rgb", payload),
                               capture_output=True)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _build_payload(self):
        import json as _j
        d = {"leds": [list(c) for c in self._leds], "brightness": self._current_bri()}
        if self._has_side:
            d["side"] = [list(c) for c in self._side_leds]
        return _j.dumps(d)

    def _cmd(self, *args):
        if self._apply_cmd:
            return self._apply_cmd(*args)
        return self._app._cmd(*args)

    def _apply(self):
        self._status.configure(text=self._T("custom_rgb_sending"), text_color=YLW)
        self.update_idletasks()
        payload = self._build_payload()
        was_running = self._app._stop_cpu_proc()
        def run():
            r = subprocess.run(self._cmd("per-key-rgb", payload), capture_output=True)
            ok = r.returncode == 0
            err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            self._save_per_key(self._leds, self._side_leds, self._current_bri())
            def finish():
                self._status.configure(
                    text=self._T("custom_rgb_applied") if ok else self._T("custom_rgb_error", err=err[:40]),
                    text_color=GRN if ok else RED)
                if was_running:
                    self._app._start_cpu_auto()
            self.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _persist(self):
        self._status.configure(text=self._T("custom_rgb_persisting"), text_color=YLW)
        self.update_idletasks()
        payload = self._build_payload()
        was_running = self._app._stop_cpu_proc()
        def run():
            r = subprocess.run(self._cmd("per-key-rgb", payload, "--persist"), capture_output=True)
            ok = r.returncode == 0
            if ok:
                self._save_per_key(self._leds, self._side_leds, self._current_bri())
            err = (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
            def finish():
                self._status.configure(
                    text=self._T("custom_rgb_persisted") if ok else self._T("custom_rgb_error", err=err[:40]),
                    text_color=GRN if ok else RED)
                if was_running:
                    self._app._start_cpu_auto()
            self.after(0, finish)
        threading.Thread(target=run, daemon=True).start()

    def _save_profile(self):
        import tkinter.filedialog as _fd
        import json as _j
        from shared.config import _load_last_dir, _save_last_dir
        path = _fd.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialdir=_load_last_dir("rgb_profile") or os.path.expanduser("~"),
            filetypes=[("JSON Profile", "*.json"), ("All", "*.*")],
            title=self._T("custom_rgb_save_profile"))
        if not path:
            return
        _save_last_dir("rgb_profile", path)
        profile = {"leds": [list(c) for c in self._leds],
                   "brightness": self._current_bri()}
        if self._has_side:
            profile["side"] = [list(c) for c in self._side_leds]
        with open(path, "w") as f:
            f.write(_j.dumps(profile, indent=2))
        self._status.configure(text=self._T("custom_rgb_saved"), text_color=GRN)

    def _load_profile(self):
        import tkinter.filedialog as _fd
        import json as _j
        from shared.config import _load_last_dir, _save_last_dir
        path = _fd.askopenfilename(
            parent=self,
            initialdir=_load_last_dir("rgb_profile") or os.path.expanduser("~"),
            filetypes=[("JSON Profile", "*.json"), ("All", "*.*")],
            title=self._T("custom_rgb_load_profile"))
        if not path:
            return
        _save_last_dir("rgb_profile", path)
        try:
            d = _j.loads(open(path).read())
            leds = [tuple(c) for c in d.get("leds", [])]
            self._leds = (leds + [(20, 20, 20)] * self._num_leds)[:self._num_leds]
            if self._has_side:
                raw = d.get("side", [])
                if isinstance(raw, list) and len(raw) == self._num_side:
                    self._side_leds = [tuple(c) for c in raw]
                else:
                    self._side_leds = [(255, 255, 255)] * self._num_side
            self._bri_sl.set(int(d.get("brightness", 100)))
            self._bri_val.configure(text=str(int(d.get("brightness", 100))))
            self._draw_keys()
            self._status.configure(text=self._T("custom_rgb_loaded"), text_color=GRN)
        except Exception as ex:
            self._status.configure(text=self._T("custom_rgb_load_error", err=str(ex)), text_color=RED)

    def header_actions(self, parent):
        """The screen's actions belong here: one filled primary on the right,
        the rest as outlines. The editor had no close control of its own
        before either, it relied on a window title bar a screen does not have.
        """
        _ui.PrimaryButton(parent, self._T("custom_rgb_apply"), self._apply,
                          width=170, height=_ui.CTRL_H_SM).pack(
            side="right", padx=(_ui.S2, 0))
        if self._has_persist:
            _ui.GhostButton(parent, self._T("custom_rgb_persist"), self._persist,
                            width=150, height=_ui.CTRL_H_SM).pack(
                side="right", padx=(_ui.S2, 0))
        _ui.GhostButton(parent, self._T("ui_back"), self._on_close,
                        width=90, height=_ui.CTRL_H_SM).pack(side="right")

    def _on_close(self):
        if self._bri_debounce_id is not None:
            self.after_cancel(self._bri_debounce_id)
            self._bri_debounce_id = None
        if self._on_close_cb:
            self._on_close_cb()


# ── Library Picker Dialog ──────────────────────────────────────────────────────

class LibraryPickerDialog(ctk.CTkToplevel):
    """Show icon library thumbnails + Browse button. result=(src_path, gif_frame, thumb_fname)."""

    def __init__(self, parent, app, lib_dir=None, thumb_w=64, thumb_h=64, cols=4,
                 skip_gif_picker=False):
        super().__init__(parent)
        self._app      = app
        self._lib_dir  = lib_dir or ICON_LIBRARY_DIR
        self._thumb_w  = thumb_w
        self._thumb_h  = thumb_h
        self._cols     = cols
        self._skip_gif = skip_gif_picker
        self._cell_w   = thumb_w + 14
        self._cell_h   = thumb_h + 22
        self.result    = None
        self.title(app.T("multi_upload_pick_title"))
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._thumb_imgs = []

        self._build_ui()

        mx    = parent.winfo_pointerx()
        my    = parent.winfo_pointery()
        dlg_w = cols * (self._cell_w + 4) + 40
        self.after(10, lambda: self.geometry(f"{dlg_w}x520+{mx - dlg_w//2}+{my - 260}"))

        self.grab_set()
        self.wait_window()

    def _build_ui(self):
        ctk.CTkButton(
            self, text=self._app.T("multi_upload_browse"),
            fg_color=BLUE, hover_color="#0884be", text_color=FG,
            font=(_ui.FONT_FAMILY, 11), height=34, corner_radius=6,
            command=self._browse_file,
        ).pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(
            self, text=self._app.T("multi_upload_library"),
            font=(_ui.FONT_FAMILY, 10), text_color=FG2,
        ).pack(padx=14, anchor="w")

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=6, height=300)
        scroll.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        cap_scroll_speed(scroll)

        self._load_grid(scroll)

    def _load_grid(self, container):
        try:
            files = sorted(f for f in os.listdir(self._lib_dir)
                          if f.lower().endswith((".png", ".gif", ".jpg", ".jpeg", ".bmp")))
        except FileNotFoundError:
            files = []

        if not files:
            ctk.CTkLabel(container, text=self._app.T("multi_upload_empty"),
                         font=(_ui.FONT_FAMILY, 11), text_color=FG2).pack(pady=20)
            return

        row_frame = None
        for i, fname in enumerate(files):
            if i % self._cols == 0:
                row_frame = ctk.CTkFrame(container, fg_color="transparent")
                row_frame.pack(fill="x", pady=2)

            fpath = os.path.join(self._lib_dir, fname)
            cell  = ctk.CTkFrame(row_frame, fg_color=BG3, corner_radius=4,
                                 width=self._cell_w, height=self._cell_h,
                                 cursor="hand2")
            cell.pack(side="left", padx=2)
            cell.pack_propagate(False)

            try:
                img = Image.open(fpath).convert("RGB").resize(
                    (self._thumb_w, self._thumb_h), Image.LANCZOS)
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                       size=(self._thumb_w, self._thumb_h))
                self._thumb_imgs.append(ctk_img)
                img_lbl = ctk.CTkLabel(cell, image=ctk_img, text="", cursor="hand2")
                img_lbl.pack(pady=(4, 0))
                for w in (cell, img_lbl):
                    w.bind("<Button-1>", lambda e, fp=fpath, fn=fname: self._select(fp, fn))
            except Exception:
                ctk.CTkLabel(cell, text="?", text_color=FG2).pack(pady=16)
                cell.bind("<Button-1>", lambda e, fp=fpath, fn=fname: self._select(fp, fn))

            ctk.CTkButton(
                cell, text="✕", width=20, height=16,
                fg_color="transparent", hover_color=RED, text_color=FG2,
                font=(_ui.FONT_FAMILY, 9), corner_radius=2,
                command=lambda fn=fname, c=cell: self._delete(fn, c),
            ).pack()

    def _select(self, fpath, fname):
        self.result = (fpath, 0, fname)
        self.destroy()

    def _delete(self, fname, cell):
        try:
            os.remove(os.path.join(self._lib_dir, fname))
        except Exception:
            pass
        cell.destroy()

    def _browse_file(self):
        self.grab_release()
        path = native_open_image(title=self._app.T("multi_upload_pick"))
        if not path:
            self.grab_set()
            return
        gif_frame = 0
        if path.lower().endswith(".gif") and not self._skip_gif:
            try:
                n = Image.open(path).n_frames
                if n > 1:
                    chosen = self._app._pick_gif_frame(path, n)
                    if chosen is None:
                        return
                    gif_frame = chosen
            except Exception:
                pass
        self.result = (path, gif_frame, None)
        self.destroy()


def pick_library_image(parent, app):
    """Open LibraryPickerDialog for D1–D4 icons (72×72). Returns (src_path, gif_frame, thumb_fname) or None."""
    dlg = LibraryPickerDialog(parent, app)
    return dlg.result


def pick_main_library_image(parent, app):
    """Open LibraryPickerDialog for main display images (96×82, 3 cols). Returns (src_path, gif_frame, thumb_fname) or None."""
    dlg = LibraryPickerDialog(parent, app, lib_dir=MAIN_LIBRARY_DIR,
                              thumb_w=96, thumb_h=82, cols=3)
    return dlg.result


def _ensure_dp_bundled_icons():
    """Copy bundled DisplayPad icons into the user library on first use."""
    import sys, shutil
    _HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _FROZEN = getattr(sys, "frozen", False)
    _RES = getattr(sys, "_MEIPASS", _HERE) if _FROZEN else _HERE
    src_dir = os.path.join(_RES, "resources", "dp_icons")
    if not os.path.isdir(src_dir):
        return
    os.makedirs(DISPLAYPAD_LIBRARY_DIR, exist_ok=True)
    marker = os.path.join(DISPLAYPAD_LIBRARY_DIR, ".bundled_v1")
    if os.path.exists(marker):
        return
    for f in os.listdir(src_dir):
        if f.endswith(".png"):
            dst = os.path.join(DISPLAYPAD_LIBRARY_DIR, f)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(src_dir, f), dst)
    open(marker, "w").write("1")


def pick_dp_library_image(parent, app):
    """Open LibraryPickerDialog for DisplayPad buttons (102×102, 4 cols). Returns (src_path, gif_frame, thumb_fname) or None."""
    _ensure_dp_bundled_icons()
    dlg = LibraryPickerDialog(parent, app, lib_dir=DISPLAYPAD_LIBRARY_DIR,
                              thumb_w=48, thumb_h=48, cols=6,
                              skip_gif_picker=True)
    return dlg.result


def pick_dp_fullscreen_image(parent, app):
    """Open LibraryPickerDialog for DisplayPad fullscreen (153×51 thumbs, 3 cols). Returns (src_path, gif_frame, thumb_fname) or None."""
    os.makedirs(DISPLAYPAD_FS_LIBRARY_DIR, exist_ok=True)
    dlg = LibraryPickerDialog(parent, app, lib_dir=DISPLAYPAD_FS_LIBRARY_DIR,
                              thumb_w=153, thumb_h=51, cols=3,
                              skip_gif_picker=True)
    return dlg.result


# ── Multi Upload Dialog ────────────────────────────────────────────────────────

class MultiUploadDialog(ctk.CTkToplevel):
    """Upload images to D1–D4 in one go, with library thumbnails per slot."""

    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self.title(app.T("multi_upload_title"))
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._selections  = [None] * 4
        self._tile_imgs   = [None] * 4
        self._tile_lbls   = [None] * 4
        self._status_lbls = [None] * 4
        self._status_bars = [None] * 4
        self._upload_btn  = None

        self._load_initial_thumbs()
        self._build_ui()

        self.update_idletasks()
        pw = app.winfo_rootx() + app.winfo_width() // 2
        ph = app.winfo_rooty() + app.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w//2}+{ph - h//2}")

    def _load_initial_thumbs(self):
        last = _load_icon_last()
        for i in range(4):
            fname = last.get(str(i))
            if fname:
                fpath = os.path.join(ICON_LIBRARY_DIR, fname)
                if os.path.exists(fpath):
                    self._selections[i] = (fpath, 0, fname)

    def _build_ui(self):
        ctk.CTkLabel(self, text=self._app.T("multi_upload_title"),
                     font=(_ui.FONT_FAMILY, 13, "bold"), text_color=FG).pack(
                         padx=16, pady=(14, 10), anchor="w")

        tile_row = ctk.CTkFrame(self, fg_color="transparent")
        tile_row.pack(fill="x", padx=12, pady=(0, 8))

        for i in range(4):
            tile = ctk.CTkFrame(tile_row, fg_color=BG3, corner_radius=6,
                                width=106, height=148)
            tile.pack(side="left", padx=4)
            tile.pack_propagate(False)

            ctk.CTkLabel(tile, text=f"D{i+1}", font=(_ui.FONT_FAMILY, 10, "bold"),
                         text_color=YLW).pack(pady=(6, 0))

            preview = ctk.CTkLabel(tile, text="+", text_color=FG2,
                                   font=(_ui.FONT_FAMILY, 20), width=80, height=72,
                                   fg_color=BG2, corner_radius=4, cursor="hand2")
            preview.pack(padx=4, pady=2)
            self._tile_lbls[i] = preview

            for w in (tile, preview):
                w.bind("<Button-1>", lambda e, ix=i: self._pick_slot(ix))

            ctk.CTkButton(
                tile, text="↑", height=22, corner_radius=4, width=80,
                fg_color=BLUE, hover_color="#0884be", text_color=FG,
                font=(_ui.FONT_FAMILY, 11, "bold"),
                command=lambda ix=i: self._upload_single(ix),
            ).pack(padx=4, pady=(0, 6))

            if self._selections[i]:
                self._update_tile_thumb(i, self._selections[i][0])

        status_frame = ctk.CTkFrame(self, fg_color=BG2, corner_radius=6)
        status_frame.pack(fill="x", padx=12, pady=(0, 8))

        for i in range(4):
            row = ctk.CTkFrame(status_frame, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=(4 if i == 0 else 0, 0))

            ctk.CTkLabel(row, text=f"D{i+1}:", font=(_ui.FONT_FAMILY, 10, "bold"),
                         text_color=YLW, width=28, anchor="w").pack(side="left")

            lbl = ctk.CTkLabel(row, text="—", font=(_ui.FONT_FAMILY, 10),
                               text_color=FG2, anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            self._status_lbls[i] = lbl

            bar = ctk.CTkProgressBar(row, mode="determinate",
                                     progress_color=BLUE, fg_color=BG3,
                                     height=4, corner_radius=0, width=80)
            bar.set(0)
            bar.pack(side="right", padx=(8, 0))
            self._status_bars[i] = bar

        ctk.CTkFrame(status_frame, fg_color="transparent", height=6).pack()

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 14))

        ctk.CTkButton(
            btn_row, text=self._app.T("gif_frame_cancel"),
            fg_color=BG3, hover_color="#2a2a3a", text_color=FG,
            font=(_ui.FONT_FAMILY, 11), height=34, corner_radius=6, width=110,
            command=self.destroy,
        ).pack(side="right", padx=(6, 0))

        self._upload_btn = ctk.CTkButton(
            btn_row, text=self._app.T("multi_upload_start"),
            fg_color=BLUE, hover_color="#0884be", text_color=FG,
            font=(_ui.FONT_FAMILY, 11, "bold"), height=34, corner_radius=6,
            command=self._start_upload,
        )
        self._upload_btn.pack(side="right")

    def _update_tile_thumb(self, idx, fpath):
        try:
            img     = Image.open(fpath).convert("RGB").resize((72, 72), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(72, 72))
            self._tile_imgs[idx] = ctk_img
            self._tile_lbls[idx].configure(image=ctk_img, text="")
        except Exception:
            pass

    def _pick_slot(self, idx):
        result = pick_library_image(self, self._app)
        if result is None:
            return
        self._selections[idx] = result
        self._update_tile_thumb(idx, result[0])
        self._status_lbls[idx].configure(text="—", text_color=FG2)

    def _upload_single(self, idx):
        if not self._selections[idx]:
            self._pick_slot(idx)
        if not self._selections[idx]:
            return
        self._upload_btn.configure(state="disabled")
        was_running = self._app._stop_cpu_proc()
        items = [(idx, *self._selections[idx])]
        threading.Thread(
            target=self._upload_loop, args=(items, was_running), daemon=True
        ).start()

    def _start_upload(self):
        items = [(i, *self._selections[i]) for i in range(4)
                 if self._selections[i] is not None]
        if not items:
            return
        self._upload_btn.configure(state="disabled")
        was_running = self._app._stop_cpu_proc()
        threading.Thread(
            target=self._upload_loop, args=(items, was_running), daemon=True
        ).start()

    def _upload_loop(self, items, was_running):
        stored = _load_icon_last()
        first  = True
        for idx, src, gif_frame, thumb_fname in items:
            resolved = thumb_fname or _compute_lib_hash(src, gif_frame)
            if resolved and resolved == stored.get(str(idx)):
                self.after(0, lambda i=idx: self._set_status(i, "skipped"))
                continue

            self.after(0, lambda i=idx: self._set_status(i, "uploading"))
            time.sleep(2.5 if (first and was_running) else 0.5)
            first = False

            cmd = self._app._cmd("upload", str(idx), src)
            if gif_frame:
                cmd = self._app._cmd("upload", str(idx), src, "--frame", str(gif_frame))

            bar  = self._status_bars[idx]
            # Merge stderr into stdout to avoid full-buffer deadlock during wait().
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            last_non_progress = ""
            for line in proc.stdout:
                if line.startswith("PROGRESS:"):
                    try:
                        pct = int(line.strip()[9:])
                        self.after(0, lambda v=pct, b=bar: b.set(v / 100.0))
                    except ValueError:
                        pass
                else:
                    stripped = line.rstrip()
                    if stripped:
                        last_non_progress = stripped
            proc.wait()
            ok       = proc.returncode == 0
            err_hint = last_non_progress

            if ok:
                new_fname = thumb_fname or _save_to_library(src, gif_frame)
                if new_fname:
                    _save_icon_last(idx, new_fname)

            self.after(0, lambda i=idx, s=ok, e=err_hint: self._set_status(i, s, e))
            self.after(0, lambda b=bar: b.set(0))

        if was_running:
            self.after(0, self._app._start_cpu_auto)
        self.after(0, lambda: self._upload_btn.configure(state="normal"))

    def _set_status(self, idx, state, err=""):
        lbl = self._status_lbls[idx]
        if state == "uploading":
            lbl.configure(text=self._app.T("image_uploading", d=idx+1),
                          text_color=BLUE)
        elif state == "skipped":
            lbl.configure(text=self._app.T("image_unchanged", d=idx+1),
                          text_color=FG2)
        elif state is True:
            lbl.configure(text=self._app.T("image_uploaded", d=idx+1),
                          text_color=GRN)
        else:
            text = (f"D{idx+1}: {err}" if err
                    else self._app.T("image_error", d=idx+1))
            lbl.configure(text=text, text_color=RED)


# ── Accordion ─────────────────────────────────────────────────────────────────

class CardColumns:
    """Two columns that fill themselves, so cards keep their own height.

    A grid ties the two cards of a row together: the shorter one either
    stretches to match its neighbour or leaves a hole under itself before the
    next row starts. Asking for the next column before building each card
    means every card is packed directly under the one above it in its own
    column, which is the layout the design shows.
    """

    def __init__(self, parent, gap=12):
        self._gap = gap
        self.left = ctk.CTkFrame(parent, fg_color="transparent")
        self.left.pack(side="left", fill="both", expand=True, padx=(0, gap // 2))
        self.right = ctk.CTkFrame(parent, fg_color="transparent")
        self.right.pack(side="left", fill="both", expand=True, padx=(gap // 2, 0))
        self._n = 0

    def next(self):
        """Parent for the next card. Alternates left, right, left, ..."""
        col = self.left if self._n % 2 == 0 else self.right
        self._n += 1
        return col

    def place(self, section):
        """Pack a card that was built with auto_pack=False into its column."""
        section.outer.pack(fill="x", pady=(0, self._gap))


class AccordionSection:
    """A titled block. In `card` mode it is simply a card that is always open.

    The accordion made sense in a 480px column where five sections could not
    be on screen at once. In a resizable window they fit side by side, and
    collapsing them only hides what someone came to change. The class keeps
    its open/close API so existing callers work either way.
    """

    def __init__(self, parent, app, icon, title_key, on_open=None, on_close=None,
                 card=False, auto_pack=True, hint=None):
        self._app      = app
        self._card     = card
        self._open     = bool(card)
        self._natural_h = 0
        self._anim_id  = None
        self._on_open  = on_open
        self._on_close = on_close

        self._outer = ctk.CTkFrame(
            parent, fg_color=BG2 if card else "transparent",
            corner_radius=7 if card else 0,
            border_width=1 if card else 0, border_color=BORDER)
        if auto_pack:
            self._outer.pack(fill="x", pady=2)

        self._header = ctk.CTkFrame(self._outer, fg_color="transparent" if card else BG2,
                                    corner_radius=6,
                                    cursor="arrow" if card else "hand2")
        self._header.pack(fill="x")

        # The yellow bar was on every section, so it distinguished nothing.
        accent = tk.Frame(self._header, bg=YLW, width=4)
        if not card:
            accent.pack(side="left", fill="y")

        # No icon column unless a caller actually passes one. The emoji that
        # used to sit here came from whatever emoji font was installed, brought
        # its own colour into a system that has a rule for every colour, and
        # left a missing-glyph box on machines without one.
        if icon:
            ctk.CTkLabel(self._header, text=icon, font=(_ui.FONT_FAMILY, 14),
                         text_color=YLW, width=30).pack(side="left", padx=(8, 4))

        self._title_lbl = ctk.CTkLabel(self._header, text=app.T(title_key),
                                        font=(_ui.FONT_FAMILY, 11, "bold"),
                                        text_color=FG, anchor="w")
        self._title_lbl.pack(side="left", fill="x", expand=True, padx=4, pady=12)
        app._reg(self._title_lbl, title_key)

        # Right-hand hint in the card header: the current value, so a card
        # says what it is set to without being opened or read in full.
        self._hint_lbl = None
        if card:
            self._hint_lbl = ctk.CTkLabel(
                self._header, text=hint or "", font=(_ui.FONT_FAMILY, 11),
                text_color=FG2, anchor="e")
            self._hint_lbl.pack(side="right", padx=(0, 12))

        self._chevron = ctk.CTkLabel(self._header, text="▶",
                                      font=(_ui.FONT_FAMILY, 10), text_color=FG2, width=24)
        if not card:
            self._chevron.pack(side="right", padx=(0, 12))

        # A card sizes to its content; the accordion needs a driven height.
        self._content = ctk.CTkFrame(self._outer, fg_color=BG2, corner_radius=0,
                                     **({} if card else {"height": 0}))
        self._content.pack(fill="both", expand=card, pady=(1, 0))
        if not card:
            self._content.pack_propagate(False)

        if not card:
            for w in (self._header, accent, self._chevron, self._title_lbl):
                w.bind("<Button-1>", self._toggle)

    @property
    def content(self):
        return self._content

    def set_hint(self, text):
        if self._hint_lbl is not None:
            self._hint_lbl.configure(text=text or "")

    @property
    def outer(self):
        """The frame to place when the caller does its own layout."""
        return self._outer

    def measure(self):
        if self._card:
            return
        self._content.pack_propagate(True)
        self._app.update_idletasks()
        self._natural_h = self._content.winfo_reqheight()
        self._content.pack_propagate(False)
        self._content.configure(height=0)

    def open(self):
        if self._card or self._open:
            return
        self._open = True
        self._chevron.configure(text="▼")
        if self._natural_h > 0:
            self._animate(self._content.winfo_height(), self._natural_h)
        if self._on_open:
            self._on_open()

    def close(self):
        if self._card or not self._open:
            return
        self._open = False
        self._chevron.configure(text="▶")
        self._animate(self._content.winfo_height(), 0)
        if self._on_close:
            self._on_close()

    def _toggle(self, event=None):
        self.close() if self._open else self.open()

    def _animate(self, current, target):
        if self._anim_id:
            self._app.after_cancel(self._anim_id)
            self._anim_id = None
        if current == target:
            return
        step  = (target - current) / ANIM_STEPS
        new_h = current + step
        if abs(new_h - target) < 1:
            new_h = target
        else:
            new_h = int(new_h)
            if new_h == current:
                new_h = current + (1 if target > current else -1)
        self._content.configure(height=new_h)
        if new_h != target:
            self._anim_id = self._app.after(ANIM_MS,
                                             lambda: self._animate(new_h, target))
