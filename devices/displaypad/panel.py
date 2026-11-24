"""Mountain DisplayPad device panel for BaseCamp Linux hub.

The device itself (the wire protocol, the pictures it is sent) lives in
core/devices/displaypad since 4.0; this module still offers every helper
under the name it always had, so code and tests that use or replace
`panel._upload_button` and friends keep reaching what runs.
"""
import os
import re
import sys
import time
import queue
import threading
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw

import shared.ui as UI
from shared.ui_helpers import (
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER, cap_scroll_speed,
    native_open_folder, parse_desktop_apps,
    pick_dp_library_image, pick_dp_fullscreen_image,
    attach_clipboard_menu, menu_order,
)
from shared.config import (
    CONFIG_DIR, FRAME_DIR,
    _load_displaypad_buttons, _save_displaypad_buttons,
    _load_displaypad_fullscreen, _save_displaypad_fullscreen, _clear_displaypad_fullscreen,
    _load_displaypad_actions, _save_displaypad_actions,
    _load_displaypad_pages, _save_displaypad_pages,
    _load_displaypad_rotation, _save_displaypad_rotation,
    _load_displaypad_brightness, _save_displaypad_brightness,
    _load_displaypad_debounce, _save_displaypad_debounce,
    _load_displaypad_min_ms, _save_displaypad_min_ms,
    _load_displaypad_page_timeouts, _save_displaypad_page_timeouts,
    _load_displaypad_page_names, _save_displaypad_page_names,
    _load_displaypad_page_hints, _save_displaypad_page_hints,
    _load_hint_box_settings, _save_hint_box_settings,
    _create_displaypad_page, _rename_displaypad_page, _delete_displaypad_page,
    _load_displaypad_actions_dialog_size, _save_displaypad_actions_dialog_size,
    macro_names,
)
from core.devices.displaypad import images as _img
from core.devices.displaypad import protocol as _proto
from core.devices.displaypad import actions as _acts
from core.devices.displaypad.pages import DEFAULT_ACTIONS as _CORE_DEFAULT_ACTIONS, PageModel
from core import CoreApp
from core.devices.displaypad.service import DisplayPadService
from shared.ui.core_bridge import TkScheduler, subscribe_tk

# Set BASECAMP_PAGE_DEBUG=1 in the environment to trace page-switch/upload
# decisions (also toggles matching trace lines in shared/plugins.py and
# page-bound widget plugins). Off by default.
_PAGE_DEBUG = os.environ.get("BASECAMP_PAGE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(msg):
    if _PAGE_DEBUG:
        print(msg, flush=True)


from shared import hid_compat
HID_AVAILABLE = _proto.HID_AVAILABLE
PYUSB_AVAILABLE = _proto.PYUSB_AVAILABLE

# ── Protocol constants (core/devices/displaypad/protocol.py) ──────────────────

VID           = _proto.VID
PID           = _proto.PID
NUM_KEYS      = _proto.NUM_KEYS
KEYS_PER_ROW  = _proto.KEYS_PER_ROW
ICON_SIZE     = _proto.ICON_SIZE
CHUNK_SIZE    = _proto.CHUNK_SIZE
HEADER_SIZE   = _proto.HEADER_SIZE
PACKET_SIZE   = _proto.PACKET_SIZE
EP_DISPLAY    = _proto.EP_DISPLAY
_KEY_MAP      = _proto.KEY_MAP
INIT_MSG      = _proto.INIT_MSG
IMG_MSG_TEMPLATE = _proto.IMG_MSG_TEMPLATE

# The action catalogue (core/devices/displaypad/actions.py).
_ACTION_TYPES    = _acts.ACTION_TYPES
_SECONDARY_TYPES = _acts.SECONDARY_TYPES
_DEFAULT_ACTIONS = _CORE_DEFAULT_ACTIONS

# ── USB helpers ───────────────────────────────────────────────────────────────
# Module globals, looked up when they are called: the tests replace them here
# with a stand-in pad.

_set_brightness            = _proto.set_brightness
_open_interfaces           = _proto.open_interfaces
_KEY_REPORT                = _proto.KEY_REPORT
_key_report_through_driver = _proto.key_report_through_driver
_init_handshake_ctrl       = _proto.init_handshake_ctrl
_close_interfaces          = _proto.close_interfaces
_init_device               = _proto.init_device
_upload_button             = _proto.upload_button

# ── Image / GIF helpers (core/devices/displaypad/images.py) ──────────────────

TILES_DIR             = _img.TILES_DIR
_image_to_bgr102      = _img.image_to_bgr
_split_image_to_tiles = _img.split_image_to_tiles
_split_gif_to_tiles   = _img.split_gif_to_tiles
_load_gif_frames      = _img.load_gif_frames
_ICON_KINDS           = _img.ICON_KINDS
_generated_icon_name  = _img.generated_icon_name
_legacy_icon_names    = _img.legacy_icon_names
_DRAWN_NAME           = _img._DRAWN_NAME
_FRAME_HOMES          = _img._FRAME_HOMES
_widget_frame_of      = _img.widget_frame_of
_make_folder_icon     = _img.make_folder_icon
_double_caption       = _img.double_caption
_make_label_icon      = _img.make_label_icon


def _ctk_image(img, size):
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def _split_gif_display_tiles(path, size):
    """Split animated GIF into CTkImage frame lists per tile for GUI preview.
    Returns {key_idx: [(CTkImage, duration_ms), ...]} or None.
    """
    tiles = _img.gif_display_tiles(path, size)
    if tiles is None:
        return None
    return {k: [(_ctk_image(t, size), d) for t, d in frames]
            for k, frames in tiles.items()}


def _load_gif_display_frames(path, size):
    """Extract all frames as CTkImages for GUI preview. Returns [(CTkImage, duration_ms), ...] or None."""
    frames = _img.gif_display_frames(path, size)
    if frames is None:
        return None
    return [(_ctk_image(f, size), d) for f, d in frames]


def _make_thumb(path, size, rotation=0):
    return _ctk_image(_img.thumb_image(path, size, rotation), size)


def _make_gif_thumb(path, size, rotation=0):
    return _ctk_image(_img.gif_thumb_image(path, size, rotation), size)


def _make_placeholder(size):
    return _ctk_image(_img.placeholder_image(size), size)


def _bind_dropdown_autoclose(toplevel):
    UI.bind_dropdown_autoclose(toplevel)


def _prompt_page_name(app, prompt_key, title_key, initial=""):
    """Modal one-line text prompt for a page name (#52), used both to create
    a brand-new standalone page and to rename an existing one. Returns the
    entered name, or None if cancelled / left blank.

    Uses our own PromptDialog rather than CTkInputDialog: that one brings its
    own styling and its own untranslated buttons, so it stood out as a fourth
    dialog language inside a dark app."""
    from shared.ui import ask_text
    ok = app.T("ui_rename") if initial else app.T("ui_create")
    val = ask_text(app, app.T(title_key), app.T(prompt_key),
                   ok, app.T("ui_cancel"), initial=initial)
    return (val or "").strip() or None


_REF_KIND_LABELS = {
    "action": "dp_ref_kind_action",
    "also_on_press": "dp_ref_kind_also_on_press",
    "double_click": "dp_ref_kind_double_click",
    "timeout": "dp_ref_kind_timeout",
}


def _confirm_delete_page(app, panel, page_id):
    """Ask for confirmation before deleting a page (#54). If anything still
    targets it by name, list exactly what and where so the person can
    decide whether to fix those first or delete anyway. Returns True if the
    person confirmed the delete."""
    from shared.ui import ask_yes_no, show_error
    if page_id == 0:
        show_error(app, app.T("dp_delete_page_title"),
                   app.T("dp_delete_page_main_error"), app.T("ui_ok"))
        return False

    page_name = panel._get_page_name(page_id)
    refs = panel._find_page_references(page_id)
    if not refs:
        return ask_yes_no(
            app, app.T("dp_delete_page_title"),
            app.T("dp_delete_page_confirm", name=page_name),
            app.T("ui_delete"), app.T("ui_cancel"), danger=True)

    lines = []
    for r in refs[:10]:
        from_name = panel._get_page_name(r["from_page"])
        kind = app.T(_REF_KIND_LABELS.get(r["kind"], r["kind"]))
        if r["key"] is None:
            lines.append(f"\u2022 {from_name}: {kind}")
        else:
            lines.append(f"\u2022 {from_name}, K{r['key'] + 1}: {kind}")
    extra = len(refs) - len(lines)
    if extra > 0:
        lines.append(app.T("dp_delete_page_more", count=extra))
    # The list of referring keys goes into the detail box instead of being
    # glued onto the question, so the question stays one readable line.
    return ask_yes_no(
        app, app.T("dp_delete_page_title"),
        app.T("dp_delete_page_referenced_warning", name=page_name, count=len(refs)),
        app.T("ui_delete"), app.T("ui_cancel"), danger=True,
        detail="\n".join(lines))


# ── Image management dialog ───────────────────────────────────────────────────

_DIALOG_TILE  = 90   # thumbnail size in dialog
_PANEL_TILE   = 84   # key tile on the screen (was 48 in the old column)
# Shortest gap between two redraws of one key tile while a widget keeps
# writing the same file. A clock pushes once a second and wants every
# one of them; a video pushes thirty times and wants none of that (#96).
_TILE_REDRAW_MIN = 0.5
_INSPECTOR_W  = 236  # width of the key inspector beside the grid

def _action_type_entries(app, include_page=True):
    """(id, label) pairs of the action menu, in menu order, in the
    interface's language (core actions.action_type_entries, #109)."""
    pm = getattr(app, "_plugin_manager", None)
    plugin = pm.get_action_type_labels() if pm else ()
    return _acts.action_type_entries(app.T, plugin, include_page, sort_key=menu_order)


def action_type_ids(app, include_page=True):
    """Internal action type ids in menu order, plugin types after a separator.

    Lives at module level because the actions dialog and the key inspector in
    the panel show the same menu, and two copies of this list would drift the
    first time a plugin type is added.
    """
    return [tid for tid, _label in _action_type_entries(app, include_page)]


def action_type_labels(app, include_page=True):
    """Translated labels, index-aligned with action_type_ids()."""
    return [lbl for _tid, lbl in _action_type_entries(app, include_page)]


class DisplayPadImageDialog(ctk.CTkToplevel):
    """Extra window: assign images/GIFs to all 12 DisplayPad buttons."""

    def __init__(self, panel):
        super().__init__(panel._app)
        self._panel = panel
        self._app   = panel._app
        self.title(panel._app.T("dp_dialog_title"))
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._tile_imgs    = {}   # key_index -> CTkImage (for GC reference)
        self._tile_lbls    = {}   # key_index -> CTkLabel (preview)
        self._dlg_frames   = {}   # key_index -> [(CTkImage_90, duration_ms), ...]

        # Pre-load large GIF frames for individual button GIFs
        for k, path in panel._images.items():
            ki = int(k)
            if ki in panel._gif_frames and path and os.path.exists(path):
                f = _load_gif_display_frames(path, _DIALOG_TILE)
                if f:
                    self._dlg_frames[ki] = f

        # Pre-load large frames for fullscreen GIF
        if panel._fullscreen_group:
            fs_path = panel._page_fullscreen.get(panel._current_page)
            if fs_path and os.path.exists(fs_path):
                dlg_tiles = _split_gif_display_tiles(fs_path, _DIALOG_TILE)
                if dlg_tiles:
                    for ki in range(NUM_KEYS):
                        self._dlg_frames[ki] = dlg_tiles[ki]

        self._build_ui()

        self.update_idletasks()
        pw = self._app.winfo_rootx() + self._app.winfo_width() // 2
        ph = self._app.winfo_rooty() + self._app.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w//2}+{ph - h//2}")

    def _is_locked(self, idx):
        """Whether this tile's image is not user-editable.

        Since the page-model redesign (#30) every key — including K1 and any
        'page'/back button — is a normal key that CAN take a custom icon (the
        assigned image wins over the default folder/back icon in
        _inject_page_icons), so nothing is locked anymore."""
        return False

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 10))

        ctk.CTkLabel(header, text=self._app.T("dp_dialog_title"),
                     font=(UI.FONT_FAMILY, 13, "bold"), text_color=FG,
                     fg_color="transparent").pack(side="left")

        # Page selector, sorted by name (#66). The main page stays first
        # because it is the one page that always exists and is where the
        # device starts; the rest is alphabetical, which is what you scan for
        # once there are more than a handful.
        pages = self._panel._get_available_pages()
        pages = ([0] if 0 in pages else []) + sorted(
            (p for p in pages if p != 0),
            key=lambda p: self._panel._get_page_name(p).casefold())
        page_labels = [self._panel._get_page_name(p) for p in pages]
        self._page_list = pages
        newlbl = self._app.T("dp_new_page")
        self._page_selector = ctk.CTkOptionMenu(
            header, values=page_labels + [newlbl],
            fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=100, height=28,
            command=self._on_page_change)
        cur = self._panel._current_page
        self._page_selector.set(self._panel._get_page_name(cur))
        self._page_selector.pack(side="right")
        ctk.CTkButton(
            header, text=self._app.T("dp_rename_page_btn"), width=92, height=28,
            font=(UI.FONT_FAMILY, 10),
            fg_color=BG2, hover_color="#333a44", text_color=FG,
            command=self._on_rename_page).pack(side="right", padx=(0, 6))
        ctk.CTkButton(
            header, text=self._app.T("dp_delete_page_btn"), width=72, height=28,
            font=(UI.FONT_FAMILY, 10),
            fg_color=BG2, hover_color="#4a2222", text_color=RED,
            command=self._on_delete_page).pack(side="right", padx=(0, 6))

        # 6 × 2 grid of tiles
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(padx=12, pady=(0, 8))

        placeholder = _make_placeholder(_DIALOG_TILE)

        for idx in range(NUM_KEYS):
            row = idx // KEYS_PER_ROW
            col = idx % KEYS_PER_ROW

            tile = ctk.CTkFrame(grid, fg_color=BG3, corner_radius=6,
                                width=_DIALOG_TILE + 16, height=_DIALOG_TILE + 36)
            tile.grid(row=row, column=col, padx=4, pady=4)
            tile.pack_propagate(False)

            ctk.CTkLabel(tile, text=f"K{idx + 1}",
                         font=(UI.FONT_FAMILY, 10, "bold"),
                         text_color=YLW, fg_color="transparent").pack(pady=(6, 0))

            path = self._panel._images.get(str(idx))
            is_gif = idx in self._panel._gif_frames
            if idx in self._dlg_frames:
                img = self._dlg_frames[idx][0][0]  # first frame
            elif path and os.path.exists(path):
                img = (_make_gif_thumb(path, _DIALOG_TILE) if is_gif
                       else _make_thumb(path, _DIALOG_TILE))
            else:
                img = placeholder
            self._tile_imgs[idx] = img

            preview = ctk.CTkLabel(tile, image=img, text="",
                                   width=_DIALOG_TILE, height=_DIALOG_TILE,
                                   fg_color=BG2, corner_radius=4, cursor="hand2")
            preview.pack(padx=4, pady=(2, 4))
            self._tile_lbls[idx] = preview

            for w in (tile, preview):
                w.bind("<Button-1>", lambda e, i=idx: self._pick_slot(i))
            preview.bind("<Button-3>", lambda e, i=idx: self._clear_slot(i))
            # Drag-and-drop target — registered only if tkinterdnd2 is loaded
            if getattr(self._app, "_dnd_available", False):
                try:
                    from tkinterdnd2 import DND_FILES
                    preview.drop_target_register(DND_FILES)
                    preview.dnd_bind(
                        "<<Drop>>",
                        lambda e, i=idx: self._on_drop(i, e.data))
                except Exception:
                    pass

        # Hint
        ctk.CTkLabel(self, text=self._app.T("dp_dialog_hint"),
                     font=(UI.FONT_FAMILY, 10), text_color=FG2,
                     fg_color="transparent").pack(pady=(0, 4))

        # Min. ms/Frame row — own StringVar, synced to panel on change
        self._min_ms_var = ctk.StringVar(value=self._panel._min_ms_var.get())
        self._min_ms_var.trace_add("write", self._sync_min_ms)
        fps_row = ctk.CTkFrame(self, fg_color="transparent")
        fps_row.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(fps_row, text=self._app.T("dp_min_ms_frame"),
                     font=(UI.FONT_FAMILY, 11), text_color=FG2,
                     fg_color="transparent").pack(side="left")
        ctk.CTkEntry(fps_row, textvariable=self._min_ms_var,
                     width=60, height=26, font=(UI.FONT_FAMILY, 11),
                     fg_color=BG3, border_color=BORDER, text_color=FG,
                     ).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(fps_row, text=self._app.T("dp_gif_speed"),
                     font=(UI.FONT_FAMILY, 10), text_color=FG2,
                     fg_color="transparent").pack(side="left", padx=(8, 0))

        # Status line
        self._status_lbl = ctk.CTkLabel(self, text="",
                                         font=(UI.FONT_FAMILY, 11), text_color=FG2,
                                         fg_color="transparent")
        self._status_lbl.pack(pady=(0, 4))

        # Bottom buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 14))

        ctk.CTkButton(
            btn_row, text=self._app.T("dp_close"),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            font=(UI.FONT_FAMILY, 11), height=34, corner_radius=6, width=110,
            command=self.destroy,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            btn_row, text=self._app.T("dp_clear_all"),
            fg_color=BG3, hover_color=BG2, text_color=FG2,
            font=(UI.FONT_FAMILY, 11), height=34, corner_radius=6, width=100,
            command=self._clear_all,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            btn_row, text=self._app.T("dp_fullscreen"),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            font=(UI.FONT_FAMILY, 11), height=34, corner_radius=6, width=100,
            command=self._pick_fullscreen,
        ).pack(side="left")

        _bind_dropdown_autoclose(self)

    # ── Slot management ───────────────────────────────────────────────────────

    def _on_drop(self, idx, raw_data):
        """Handle a file drop on a tile. raw_data is a Tcl list string.
        We accept the first item and use it like a normal pick."""
        if self._panel._uploading or self._is_locked(idx):
            return
        # tkinterdnd2 returns a space-separated string, with paths wrapped in
        # braces if they contain spaces — use the Tcl splitter for correctness.
        try:
            paths = list(self.tk.splitlist(raw_data))
        except Exception:
            paths = [raw_data]
        path = next((p for p in paths if p and os.path.exists(p)), None)
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
            return
        from shared.config import _save_to_dp_library, DISPLAYPAD_LIBRARY_DIR
        thumb = _save_to_dp_library(path)
        final = os.path.join(DISPLAYPAD_LIBRARY_DIR, thumb) if thumb else path
        self._panel._set_button_image(idx, final)
        self._refresh_tile(idx)
        self._status_lbl.configure(
            text=f"K{idx + 1}: {os.path.basename(final)}", text_color=FG2)

    def _pick_slot(self, idx):
        if self._panel._uploading or self._is_locked(idx):
            return
        result = pick_dp_library_image(self, self._app)
        if not result:
            return
        src_path, gif_frame, thumb_fname = result
        # If browsed from disk (thumb_fname is None), save to library
        if thumb_fname is None and src_path:
            from shared.config import _save_to_dp_library
            _save_to_dp_library(src_path, gif_frame)
        # Use the library file path if selected from library, otherwise source
        if thumb_fname:
            from shared.config import DISPLAYPAD_LIBRARY_DIR
            path = os.path.join(DISPLAYPAD_LIBRARY_DIR, thumb_fname)
        else:
            path = src_path
        if not path:
            return
        self._panel._set_button_image(idx, path)
        self._refresh_tile(idx)
        self._status_lbl.configure(
            text=f"K{idx + 1}: {os.path.basename(path)}", text_color=FG2)

    def _clear_slot(self, idx):
        if self._panel._uploading or self._is_locked(idx):
            return
        # Replace with blank placeholder so the device gets the new image on next upload
        self._panel._images[str(idx)] = self._panel._blank_icon
        self._panel._take_key_back(self._panel._current_page, idx)
        self._panel._gif_frames.pop(idx, None)
        self._panel._gui_frames_sm.pop(idx, None)
        self._panel._fullscreen_group.discard(idx)
        # Through _persist_images(), because _save_displaypad_buttons() always
        # writes page 0's stored "buttons": calling it unconditionally here
        # clobbered Main's saved images whenever a slot was cleared on a
        # sub-page.
        self._panel._persist_images()
        self._dlg_frames.pop(idx, None)
        ph = _make_placeholder(_DIALOG_TILE)
        self._tile_imgs[idx] = ph
        self._tile_lbls[idx].configure(image=ph, text="")
        self._panel._refresh_panel_tile(idx)
        self._status_lbl.configure(text=self._app.T("dp_slot_cleared", k=idx+1), text_color=FG2)
        # Push the blank to the device
        if not self._panel._uploading and not self._panel._animating:
            self._panel.after(100, self._panel._start_upload)

    def _clear_all(self):
        if self._panel._uploading:
            return
        if self._panel._animating:
            self._panel._stop_animation()
            self.after(500, self._clear_all)
            return
        self._panel._clear_all()
        self._dlg_frames = {}
        ph = _make_placeholder(_DIALOG_TILE)
        for idx in range(NUM_KEYS):
            self._tile_imgs[idx] = ph
            self._tile_lbls[idx].configure(image=ph, text="")
        self._status_lbl.configure(text=self._app.T("dp_all_cleared"), text_color=FG2)

    def _sync_min_ms(self, *_):
        self._panel._min_ms_var.set(self._min_ms_var.get())

    def notify_frame(self, key_index, frame_idx):
        """Called by panel's GUI tick to sync dialog tile to current frame."""
        if key_index not in self._dlg_frames:
            return
        frames = self._dlg_frames[key_index]
        img = frames[frame_idx % len(frames)][0]
        self._tile_imgs[key_index] = img
        if key_index in self._tile_lbls:
            self._tile_lbls[key_index].configure(image=img, text="")

    def _refresh_tile(self, idx):
        path = self._panel._images.get(str(idx))
        is_gif = idx in self._panel._gif_frames
        if idx in self._dlg_frames:
            img = self._dlg_frames[idx][0][0]
        elif path and os.path.exists(path):
            if is_gif:
                img = _make_gif_thumb(path, _DIALOG_TILE)
            else:
                img = _make_thumb(path, _DIALOG_TILE)
        else:
            img = _make_placeholder(_DIALOG_TILE)
        self._tile_imgs[idx] = img
        self._tile_lbls[idx].configure(image=img, text="")

        # Also load large frames if newly assigned individual GIF
        if is_gif and idx not in self._dlg_frames and path and os.path.exists(path):
            f = _load_gif_display_frames(path, _DIALOG_TILE)
            if f:
                self._dlg_frames[idx] = f

    def _pick_fullscreen(self):
        if self._panel._uploading:
            return
        result = pick_dp_fullscreen_image(self, self._app)
        if not result:
            return
        src_path, gif_frame, thumb_fname = result
        if thumb_fname:
            from shared.config import DISPLAYPAD_FS_LIBRARY_DIR
            path = os.path.join(DISPLAYPAD_FS_LIBRARY_DIR, thumb_fname)
        else:
            path = src_path
        if not path:
            return
        # The splitting itself lives on the panel, so this window and the
        # button under the keys cannot drift apart in how they do it (#78).
        was_gif = path.lower().endswith(".gif")
        self._panel.apply_fullscreen_image(
            path,
            say=lambda text, colour: self._status_lbl.configure(
                text=text, text_color=colour),
            on_tile=self._refresh_tile)
        if was_gif and self._panel._fullscreen_group is not None:
            # This window shows bigger tiles than the panel does, so it needs
            # its own split of the animation.
            dlg_tiles = _split_gif_display_tiles(path, _DIALOG_TILE)
            if dlg_tiles:
                for idx in range(NUM_KEYS):
                    self._dlg_frames[idx] = dlg_tiles[idx]
                    self._refresh_tile(idx)

    def _on_page_change(self, label):
        """Switch the image dialog to show a different page's images, or
        create a brand-new standalone page if 'New page...' was picked (#52)."""
        if label == self._app.T("dp_new_page"):
            name = _prompt_page_name(self._app, "dp_page_name_prompt", "dp_page_name_title")
            if not name:
                self._page_selector.set(self._panel._get_page_name(self._panel._current_page))
                return
            pid = self._panel._create_named_page(name)
            self._refresh_page_selector(select=pid)
            return
        for p, lbl in zip(self._page_list,
                          [self._panel._get_page_name(x)
                           for x in self._page_list]):
            if lbl == label:
                if p != self._panel._current_page:
                    self._panel._switch_to_page(p)
                # Refresh all tiles
                self._dlg_frames.clear()
                for k, path in self._panel._images.items():
                    ki = int(k)
                    if ki in self._panel._gif_frames and path and os.path.exists(path):
                        f = _load_gif_display_frames(path, _DIALOG_TILE)
                        if f:
                            self._dlg_frames[ki] = f
                for idx in range(NUM_KEYS):
                    self._refresh_tile(idx)
                break

    def _refresh_page_selector(self, select=None):
        """Rebuild the page dropdown's values after a page was created or
        renamed (#52), and switch to `select` if given."""
        pages = self._panel._get_available_pages()
        self._page_list = pages
        page_labels = [self._panel._get_page_name(p) for p in pages]
        newlbl = self._app.T("dp_new_page")
        self._page_selector.configure(values=page_labels + [newlbl])
        target = select if select is not None else self._panel._current_page
        self._page_selector.set(self._panel._get_page_name(target))
        if select is not None and select != self._panel._current_page:
            self._on_page_change(self._panel._get_page_name(select))

    def _on_rename_page(self):
        """Rename the page currently shown in this dialog (#52). Main (page
        0) is renamable too -- it's just the page the app opens on."""
        cur = self._panel._current_page
        name = _prompt_page_name(
            self._app, "dp_page_name_prompt", "dp_rename_page_title",
            initial=self._panel._get_page_name(cur))
        if not name:
            return
        self._panel._rename_page(cur, name)
        self._refresh_page_selector()

    def _on_delete_page(self):
        """Delete the page currently shown in this dialog (#54), after
        warning if anything still points at it by name."""
        cur = self._panel._current_page
        if not _confirm_delete_page(self._app, self._panel, cur):
            return
        self._panel._delete_page(cur)
        self._refresh_page_selector()

    def destroy(self):
        # Auto-upload when dialog closes. Guard against the panel being
        # destroyed first during an app shutdown, which would raise TclError.
        try:
            if (self._panel.winfo_exists()
                    and not self._panel._uploading
                    and not self._panel._animating
                    and (self._panel._images or self._panel._gif_frames)):
                self._panel._uploading = True  # block key listener immediately
                self._panel.after(300, self._panel._start_upload)
        except tk.TclError:
            pass
        super().destroy()


# ── Per-page auto-timeout row ─────────────────────────────────────────────────

class PageTimeoutRow(ctk.CTkFrame):
    """The auto-timeout controls for one page (#45): mode, seconds, target.

    Two places show these: the DisplayPad screen, for the page on the device
    (#71), and the button-actions window, which can be pointed at a different
    page. Both share this widget so the two can never drift apart in what they
    store or how they read a legacy config.

    `get_page` is a callable, not a page id, because the owning screen changes
    pages under the row.
    """

    MODES = ["off", "after", "idle"]

    def __init__(self, parent, panel, app, get_page, fg_color=BG2):
        super().__init__(parent, fg_color=fg_color, corner_radius=6)
        self._panel = panel
        self._app = app
        self._get_page = get_page

        self._label = ctk.CTkLabel(self, text=app.T("dp_panel_timeout"),
                                   font=(UI.FONT_FAMILY, 10), text_color=FG2)
        self._label.pack(side="left", padx=(10, 4), pady=6)
        self._mode_menu = ctk.CTkOptionMenu(
            self, values=self._mode_labels(),
            fg_color=BG3, button_color=BG3, button_hover_color=BORDER,
            text_color=FG, font=(UI.FONT_FAMILY, 10), width=96, height=26,
            dynamic_resizing=False, command=lambda v: self.save())
        self._mode_menu.pack(side="left", padx=2)
        self._secs_var = tk.StringVar(value="10")
        self._secs_entry = ctk.CTkEntry(
            self, textvariable=self._secs_var, width=44, height=26,
            fg_color=BG3, text_color=FG, border_color=BORDER,
            font=(UI.FONT_FAMILY, 10))
        self._secs_entry.pack(side="left", padx=(4, 1))
        self._secs_entry.bind("<Return>",   lambda e: self.save())
        self._secs_entry.bind("<FocusOut>", lambda e: self.save())
        self._secs_label = ctk.CTkLabel(self, text=app.T("dp_timeout_secs"),
                                        font=(UI.FONT_FAMILY, 10), text_color=FG2)
        self._secs_label.pack(side="left", padx=(0, 2))
        self._target_menu = ctk.CTkOptionMenu(
            self, values=[""],
            fg_color=BG3, button_color=BG3, button_hover_color=BORDER,
            text_color=FG, font=(UI.FONT_FAMILY, 10), width=110, height=26,
            dynamic_resizing=False, command=lambda v: self.save())
        self._target_menu.pack(side="left", padx=(2, 10))

    def _mode_labels(self):
        return [self._app.T("dp_timeout_off"),
                self._app.T("dp_timeout_after"),
                self._app.T("dp_timeout_idle")]

    def _target_options(self):
        """(labels, {label: target}) for the destination picker: every page plus
        a 'previous page' entry that returns to wherever we came from."""
        mapping, labels = {}, []
        prevlbl = self._app.T("dp_timeout_prev")
        labels.append(prevlbl)
        mapping[prevlbl] = "prev"
        for p in self._panel._get_available_pages():
            lbl = self._panel._get_page_name(p)
            labels.append(lbl)
            mapping[lbl] = p
        return labels, mapping

    def load(self):
        """Fill the row from the stored config of the page it points at."""
        to = self._panel._page_timeout.get(self._get_page()) or {}
        mode = to.get("mode", "off")
        if mode not in self.MODES:
            mode = "off"
        self._mode_menu.set(self._mode_labels()[self.MODES.index(mode)])
        secs = int(to.get("seconds", 0) or 0)
        self._secs_var.set(str(secs if secs > 0 else 10))
        labels, mapping = self._target_options()
        self._target_menu.configure(values=labels)
        tgt = to.get("target", "prev")
        sel = None
        for lbl, val in mapping.items():
            if val == "prev":
                match = (tgt == "prev")
            else:
                # tgt may be a legacy int id or (#52) a persisted page name.
                match = (tgt == val or tgt == self._panel._get_page_name(val))
            if match:
                sel = lbl
                break
        self._target_menu.set(sel or self._app.T("dp_timeout_prev"))

    def save(self):
        """Persist the row into the page's config (#45)."""
        page = self._get_page()
        try:
            mode = self.MODES[self._mode_labels().index(self._mode_menu.get())]
        except (ValueError, IndexError):
            mode = "off"
        try:
            secs = max(1, int(float(self._secs_var.get())))
        except (ValueError, TypeError):
            secs = 10
        _labels, mapping = self._target_options()
        picked = mapping.get(self._target_menu.get(), "prev")
        # Store by name (#52), like every other page reference; "prev" is kept
        # as-is since it's a mode, not a page.
        tgt = picked if picked == "prev" else self._panel._get_page_name(picked)
        # Stored, and re-armed live if the page being edited is the one on
        # the device now.
        self._panel._svc.set_page_timeout(page, mode, secs, tgt)

    def apply_lang(self):
        self._label.configure(text=self._app.T("dp_panel_timeout"))
        self._secs_label.configure(text=self._app.T("dp_timeout_secs"))
        self._mode_menu.configure(values=self._mode_labels())
        self.load()


# ── Actions dialog ────────────────────────────────────────────────────────────

class PageHintBox(ctk.CTkToplevel):
    """The page's note and its keys' hints, beside whatever you are working
    in (#93).

    It must never take the keyboard from the window you are typing in, or a
    page switch would steal your typing, so it is a window the window
    manager does not manage: it is not focused, stays above, and is moved by
    its own title row. It stays until closed, or for as many seconds as set,
    and not while the pointer is over it. Built over the application's root,
    which is fine with that root hidden in the tray.
    """

    PAD = 12
    CELL_W = 74

    def __init__(self, panel, page, name, hints, settings):
        super().__init__(panel._app, fg_color=BG2)
        self._panel = panel
        self.page = page
        self._timeout = int(settings.get("timeout") or 0)
        self._close_id = None
        self._hover = False
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        T = panel._app.T

        frame = ctk.CTkFrame(self, fg_color=BG2, border_width=1,
                             border_color=BORDER, corner_radius=8)
        frame.pack(fill="both", expand=True)
        head = ctk.CTkFrame(frame, fg_color="transparent")
        head.pack(fill="x", padx=self.PAD, pady=(10, 4))
        title = ctk.CTkLabel(head, text=name, font=(UI.FONT_FAMILY, 13, "bold"),
                             text_color=FG, anchor="w")
        title.pack(side="left")
        close = ctk.CTkButton(head, text="\u2715", width=26, height=22,
                              font=(UI.FONT_FAMILY, 11), fg_color="transparent",
                              hover_color=BG3, text_color=FG2,
                              command=self.close)
        close.pack(side="right")
        for w in (head, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", self._drag_end)

        if hints["note"].strip():
            ctk.CTkLabel(frame, text=hints["note"].strip(), justify="left",
                         anchor="w", wraplength=self.CELL_W * KEYS_PER_ROW,
                         font=(UI.FONT_FAMILY, 11), text_color=FG
                         ).pack(fill="x", padx=self.PAD, pady=(0, 6))
        if hints["keys"]:
            # Laid out the way the keys are, so where a hint sits in the box is
            # where the key sits under your hand.
            grid = ctk.CTkFrame(frame, fg_color="transparent")
            grid.pack(padx=self.PAD, pady=(2, 6))
            for idx in range(NUM_KEYS):
                text = hints["keys"].get(str(idx), "")
                cell = ctk.CTkFrame(grid, fg_color=BG3 if text else BG2,
                                    corner_radius=5, width=self.CELL_W, height=46)
                cell.grid(row=idx // KEYS_PER_ROW, column=idx % KEYS_PER_ROW,
                          padx=2, pady=2)
                cell.grid_propagate(False)
                ctk.CTkLabel(cell, text=f"K{idx + 1}", font=(UI.FONT_FAMILY, 8),
                             text_color=FG2).place(x=5, y=2)
                if text:
                    ctk.CTkLabel(cell, text=text, font=(UI.FONT_FAMILY, 10),
                                 text_color=FG, wraplength=self.CELL_W - 8,
                                 justify="center").place(relx=0.5, rely=0.62,
                                                         anchor="center")
        foot = ctk.CTkFrame(frame, fg_color="transparent")
        foot.pack(fill="x", padx=self.PAD, pady=(2, 10))
        for key, cmd in (("dp_hint_never", self._never),
                         ("dp_hint_session", self._not_this_session)):
            ctk.CTkButton(foot, text=T(key), height=22, font=(UI.FONT_FAMILY, 9),
                          fg_color="transparent", hover_color=BG3,
                          text_color=FG2, command=cmd).pack(side="right", padx=(4, 0))

        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._place(settings)
        self._arm_close()

    def _place(self, settings):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = settings.get("x"), settings.get("y")
        if x is None or y is None:
            x, y = sw - w - 24, sh - h - 64
        x = max(0, min(int(x), sw - w))
        y = max(0, min(int(y), sh - h))
        self.geometry(f"+{x}+{y}")

    # Moved by its title row: an unmanaged window has no frame to drag.
    def _drag_start(self, e):
        self._drag_from = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag(self, e):
        dx, dy = getattr(self, "_drag_from", (0, 0))
        self.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _drag_end(self, _e):
        self._panel._svc.set_hint_box_settings(x=self.winfo_x(), y=self.winfo_y())

    def _set_hover(self, inside):
        self._hover = inside
        if not inside:
            self._arm_close()

    def _arm_close(self):
        if self._close_id is not None:
            try:
                self.after_cancel(self._close_id)
            except tk.TclError:
                pass
            self._close_id = None
        if self._timeout > 0:
            self._close_id = self.after(self._timeout * 1000, self._timed_out)

    def _timed_out(self):
        self._close_id = None
        if self._hover:
            return              # it is being read; leaving it arms it again
        self.close()

    def _never(self):
        self._panel._svc.set_page_note(self.page, show=False)
        self.close()

    def _not_this_session(self):
        self._panel._hint_pages_quiet.add(self.page)
        self.close()

    def close(self):
        if self._panel._hint_box is self:
            self._panel._hint_box = None
        try:
            self.destroy()
        except tk.TclError:
            pass


class PageNoteDialog(ctk.CTkToplevel):
    """Write a page's note and say whether its box comes up (#93). The key
    hints are written in the inspector beside the grid, one key at a time."""

    def __init__(self, panel, page):
        super().__init__(panel._app, fg_color=BG)
        self._panel = panel
        self._page = page
        T = panel._app.T
        self.title(T("dp_note_title", name=panel._get_page_name(page)))
        self.resizable(False, False)
        hints = _load_displaypad_page_hints(page)
        settings = _load_hint_box_settings()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=14)
        ctk.CTkLabel(body, text=T("dp_note_text"), font=(UI.FONT_FAMILY, 10),
                     text_color=FG2, justify="left", anchor="w",
                     wraplength=420).pack(fill="x")
        self._text = ctk.CTkTextbox(body, width=440, height=140, fg_color=BG3,
                                    border_color=BORDER, border_width=1,
                                    text_color=FG, font=(UI.FONT_FAMILY, 11),
                                    wrap="word")
        self._text.pack(fill="x", pady=(6, 10))
        self._text.insert("1.0", hints["note"])
        self._show = tk.BooleanVar(value=hints["show"])
        ctk.CTkCheckBox(body, text=T("dp_note_show"), variable=self._show,
                        font=(UI.FONT_FAMILY, 10), text_color=FG
                        ).pack(anchor="w")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row, text=T("dp_note_timeout"), font=(UI.FONT_FAMILY, 10),
                     text_color=FG2).pack(side="left")
        self._secs = tk.StringVar(value=str(settings["timeout"]))
        ctk.CTkEntry(row, textvariable=self._secs, width=52, height=26,
                     fg_color=BG3, border_color=BORDER, text_color=FG,
                     font=(UI.FONT_FAMILY, 10)).pack(side="left", padx=(6, 0))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))
        UI.PrimaryButton(buttons, T("dp_note_save"), self._save,
                         width=100).pack(side="right")
        UI.GhostButton(buttons, T("ui_cancel"), self.destroy,
                       width=100).pack(side="right", padx=(0, 8))

    def _save(self):
        note = self._text.get("1.0", "end-1c")
        self._panel._svc.set_page_note(self._page, note=note, show=self._show.get())
        try:
            secs = max(0, int(self._secs.get().strip() or 0))
        except ValueError:
            secs = None
        if secs is not None:
            self._panel._svc.set_hint_box_settings(timeout=secs)
        self.destroy()


class DisplayPadActionsDialog(ctk.CTkToplevel):
    """Window: configure shell/url/folder/app/page actions for K1–K12, per page."""

    def __init__(self, panel):
        super().__init__(panel._app)
        # Built off-screen and shown once (#68). This window used to appear at
        # its default size, fill with twelve key cards, and only then jump to
        # the size it was left at, which read as the window resizing itself
        # while you watched.
        self.withdraw()
        self._panel = panel
        self._app   = panel._app
        self._page  = panel._current_page
        self.title(panel._app.T("dp_actions_title"))
        self.configure(fg_color=BG)
        self.resizable(True, True)
        self.minsize(420, 420)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._resize_save_after_id = None
        # <Configure> is bound at the very end: while the window is withdrawn
        # its size reads as 1x1, and saving that would clamp to the minimum and
        # overwrite the size the person actually chose.

        _folder_pil = Image.open(
            os.path.join(panel._res_path, "resources", "foldericon.png")).convert("RGBA")
        self._folder_img = ctk.CTkImage(
            light_image=_folder_pil, dark_image=_folder_pil, size=(24, 24))
        _dim = _folder_pil.copy()
        _dim.putalpha(_dim.getchannel("A").point(lambda v: v // 3))
        self._folder_img_dim = ctk.CTkImage(light_image=_dim, dark_image=_dim, size=(24, 24))

        # Dialog-local StringVars (loaded from page data)
        self._act_type = [tk.StringVar() for _ in range(12)]
        self._act_cmd  = [tk.StringVar() for _ in range(12)]
        self._type_menus  = []
        self._cmd_entries  = []
        self._browse_btns  = []
        self._obs_combos   = []
        self._macro_combos = []
        self._hue_combos   = []
        self._page_combos  = []   # 'page' target picker (#30 carousel)
        self._page_targets = [None] * 12  # idx -> resolved target page id or "new"
        self._plugin_combos = []  # plugin types with value_options
        self._plugin_combo_maps = {}  # idx -> {display_label: value}
        # True while this dialog is writing a key. The panel keeps the two
        # editors in step by making each one re-read after the other saves
        # (#84), but this dialog re-reading itself mid-save means every row
        # that has been typed into and not yet written is reverted (#87).
        self._saving = False
        # Per row: two Hue rows of different kinds (a light and a scene) used
        # to share one list, so a pick in the first was looked up in the
        # second's and stored as the wrong value.
        self._hue_values_maps = {}   # row -> [value per dropdown entry]
        self._hue_bri_target = {}  # idx -> "group:1" or "light:3"
        # Secondary "also on press" action (issue #16)
        self._sec_type    = [tk.StringVar() for _ in range(12)]
        self._sec_cmd     = [tk.StringVar() for _ in range(12)]
        self._sec_menus   = []
        self._sec_entries = []
        self._sec_page_combos = []  # 'page' target picker for also-on-press (existing pages only)
        # Double-click action (issue #47)
        self._dbl_type    = [tk.StringVar() for _ in range(12)]
        self._dbl_cmd     = [tk.StringVar() for _ in range(12)]
        self._dbl_menus   = []
        self._dbl_entries = []
        self._dbl_page_combos = []  # 'page' target picker for double-click (existing pages only)
        self._cards       = []

        # Size first, so the cards are laid out once, at the width they will be
        # read at, instead of being laid out narrow and reflowed a moment later.
        saved_size = _load_displaypad_actions_dialog_size()
        if saved_size:
            self.geometry(f"{saved_size[0]}x{saved_size[1]}")

        self._build_ui()
        self._load_page(self._page)

        self.update_idletasks()
        pw = self._app.winfo_rootx() + self._app.winfo_width() // 2
        ph = self._app.winfo_rooty() + self._app.winfo_height() // 2
        if saved_size:
            w, h = saved_size
        else:
            w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"{w}x{h}+{pw - w // 2}+{ph - h // 2}")
        self.deiconify()
        # Listen for resizes only once the window has settled. A window that
        # does not fit the screen is shrunk to fit right after mapping, roughly
        # 200 ms later, and saving that would replace the size the person chose
        # with the smaller one, every time: on a small screen the remembered
        # size would ratchet down and never grow back. Only a resize the person
        # performs should be remembered.
        self.after(1000, lambda: self.bind("<Configure>", self._on_configure))

    def _on_configure(self, event):
        """Debounce <Configure> events (fired continuously while dragging the
        window edge) so we only persist the size ~400ms after resizing stops."""
        if event.widget is not self:
            return
        if self._resize_save_after_id is not None:
            try:
                self.after_cancel(self._resize_save_after_id)
            except Exception:
                pass
        self._resize_save_after_id = self.after(400, self._save_current_size)

    def _save_current_size(self):
        self._resize_save_after_id = None
        try:
            _save_displaypad_actions_dialog_size(self.winfo_width(), self.winfo_height())
        except Exception:
            pass

    def _on_close(self):
        """Persist the current size immediately (in case the debounced
        <Configure> save hasn't fired yet) before closing the dialog."""
        if self._resize_save_after_id is not None:
            try:
                self.after_cancel(self._resize_save_after_id)
            except Exception:
                pass
            self._resize_save_after_id = None
        self._save_current_size()
        self.destroy()

    def _get_action_types(self, include_page=True):
        """Return list of internal action type IDs, including plugin types."""
        return action_type_ids(self._app, include_page)

    def _type_labels(self, include_page=True):
        return action_type_labels(self._app, include_page)

    def _sec_type_labels(self):
        """Labels for the secondary 'also on press' type menu (issue #16),
        index-aligned with _SECONDARY_TYPES."""
        return [self._app.T("action_type_none"),
                self._app.T("action_type_keypress"),
                self._app.T("action_type_text"),
                self._app.T("action_type_shell"),
                self._app.T("action_type_url"),
                self._app.T("action_type_page")]

    def _load_page(self, page):
        """Populate dialog StringVars from page data."""
        self._page = page
        actions = self._panel._page_actions.get(page, _DEFAULT_ACTIONS)
        # Any page can host 'page' navigation actions now (#30 carousel), so the
        # type list is the same on every page and K1 is no longer a locked back.
        labels = self._type_labels(include_page=True)
        types_for_page = self._get_action_types(include_page=True)

        for i in range(12):
            act = actions[i] if i < len(actions) else {"type": "none", "action": ""}
            btype = act.get("type", "none")
            cmd   = act.get("action", "")
            # Remember an existing 'page' target (by name, #52) so the picker
            # can preselect it -- resolved to an id right away so it behaves
            # exactly like the "new"/int values the picker itself produces.
            self._page_targets[i] = self._panel._page_target(act, i) if btype == "page" else None

            self._act_type[i].set(btype)
            self._act_cmd[i].set(cmd)

            # Secondary "also on press" action (issue #16)
            _extra = act.get("actions") if isinstance(act, dict) else None
            _sec = _extra[0] if isinstance(_extra, list) and _extra else {}
            _sectype = _sec.get("type", "none")
            if _sectype not in _SECONDARY_TYPES:
                _sectype = "none"
            self._sec_type[i].set(_sectype)
            self._sec_cmd[i].set(_sec.get("action", ""))
            self._sec_menus[i].set(
                self._sec_type_labels()[_SECONDARY_TYPES.index(_sectype)])
            self._sec_menus[i].configure(state="normal")
            self._sec_entries[i].configure(state="normal")
            self._sec_entries[i].pack_forget()
            self._sec_page_combos[i].pack_forget()
            if _sectype == "page":
                _splabels, _spmap = self._existing_page_options()
                _sel = self._sec_cmd[i].get()
                if _sel and _sel not in _spmap:
                    # Stored target isn't in the "clean" list right now (e.g. it
                    # was only ever reachable via this chain step, see
                    # _all_page_ids) — show it anyway instead of silently
                    # swapping in a different page and losing the setting.
                    _splabels = _splabels + [_sel]
                elif not _sel:
                    _sel = _splabels[0] if _splabels else ""
                    self._sec_cmd[i].set(_sel)
                self._sec_page_combos[i].configure(values=_splabels or [""])
                self._sec_page_combos[i].set(_sel)
                self._sec_page_combos[i].pack(side="left", padx=4, expand=True, fill="x")
            else:
                self._sec_entries[i].pack(side="left", padx=4, expand=True, fill="x")

            # Double-click action (issue #47)
            _dbl = act.get("double") if isinstance(act, dict) else None
            _dbltype = _dbl.get("type", "none") if isinstance(_dbl, dict) else "none"
            if _dbltype not in _SECONDARY_TYPES:
                _dbltype = "none"
            self._dbl_type[i].set(_dbltype)
            self._dbl_cmd[i].set(_dbl.get("action", "") if isinstance(_dbl, dict) else "")
            self._dbl_menus[i].set(
                self._sec_type_labels()[_SECONDARY_TYPES.index(_dbltype)])
            self._dbl_menus[i].configure(state="normal")
            self._dbl_entries[i].configure(state="normal")
            self._dbl_entries[i].pack_forget()
            self._dbl_page_combos[i].pack_forget()
            if _dbltype == "page":
                _dplabels, _dpmap = self._existing_page_options()
                _dsel = self._dbl_cmd[i].get()
                if _dsel and _dsel not in _dpmap:
                    _dplabels = _dplabels + [_dsel]
                elif not _dsel:
                    _dsel = _dplabels[0] if _dplabels else ""
                    self._dbl_cmd[i].set(_dsel)
                self._dbl_page_combos[i].configure(values=_dplabels or [""])
                self._dbl_page_combos[i].set(_dsel)
                self._dbl_page_combos[i].pack(side="left", padx=4, expand=True, fill="x")
            else:
                self._dbl_entries[i].pack(side="left", padx=4, expand=True, fill="x")

            menu = self._type_menus[i]
            menu.configure(values=labels)
            # Always reset layout
            self._obs_combos[i].pack_forget()
            self._macro_combos[i].pack_forget()
            self._hue_combos[i].pack_forget()
            self._page_combos[i].pack_forget()
            self._plugin_combos[i].pack_forget()
            self._cmd_entries[i].pack_forget()
            self._browse_btns[i].pack_forget()
            self._cmd_entries[i].pack(side="left", padx=4, expand=True, fill="x")
            self._browse_btns[i].pack(side="left", padx=(0, 4))

            menu.configure(state="normal")
            self._cmd_entries[i].configure(state="normal")
            if btype in types_for_page:
                idx_in_labels = types_for_page.index(btype)
                menu.set(labels[idx_in_labels] if idx_in_labels < len(labels) else labels[0])
            else:
                menu.set(labels[0])
            # Browse button state
            browse_active = btype in ("folder", "app")
            self._browse_btns[i].configure(
                state="normal" if browse_active else "disabled",
                image=self._folder_img if browse_active else self._folder_img_dim)
            # "page" type: target picker first, then the key's caption (#50).
            # The entry is the text drawn on the key, not the page it goes to
            # and not the name of a page, which is what its old placeholder
            # said and what it was read as.
            if btype == "page":
                self._cmd_entries[i].configure(
                    placeholder_text=self._app.T("dp_page_caption_hint"))
                _plabels, _pmap = self._page_target_options()
                self._page_combos[i].configure(values=_plabels)
                _sel = None
                for _lbl, _pid in _pmap.items():
                    if _pid == self._page_targets[i]:
                        _sel = _lbl
                        break
                self._page_combos[i].set(_sel or self._app.T("dp_new_page"))
                self._page_combos[i].pack(side="left", padx=(0, 4))
                # Where you go is chosen before how the key is labelled, so the
                # picker goes to the left of the entry.
                self._cmd_entries[i].pack_forget()
                self._cmd_entries[i].pack(side="left", padx=4, expand=True,
                                          fill="x")
            # "keypress" type: show placeholder hint
            elif btype == "keypress":
                self._cmd_entries[i].configure(
                    placeholder_text=self._app.T("dp_keypress_hint"))
            # "text" type: text to be typed out
            elif btype == "text":
                self._cmd_entries[i].configure(
                    placeholder_text=self._app.T("action_type_text_hint"))
            # "set_key" type: redefine another key on press (issue #18/#29).
            # The entry holds a small JSON object describing the target.
            elif btype == "set_key":
                self._cmd_entries[i].configure(
                    placeholder_text=self._app.T("action_type_set_key_hint"))
            # "obs" type: show OBS combo instead of entry
            elif btype == "obs":
                self._cmd_entries[i].pack_forget()
                self._browse_btns[i].pack_forget()
                obs_panel = self._app._obs_panel
                scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
                self._obs_combos[i].configure(values=scenes + ["OBS: Record", "OBS: Stream"])
                if cmd.startswith("scene:"):
                    self._obs_combos[i].set(cmd[6:])
                elif cmd in ("record", "stream"):
                    self._obs_combos[i].set(f"OBS: {cmd.capitalize()}")
                elif scenes:
                    self._obs_combos[i].set(scenes[0])
                self._obs_combos[i].pack(side="left", padx=4, expand=True, fill="x")
            # "macro" type: show macro picker instead of entry
            elif btype == "macro":
                self._cmd_entries[i].pack_forget()
                self._browse_btns[i].pack_forget()
                self._populate_macro_combo(self._macro_combos[i], cmd, btn_idx=i)
                self._macro_combos[i].pack(side="left", padx=4, expand=True, fill="x")
            # "hue_toggle" / "hue_scene": show hue picker
            elif btype in ("hue_toggle", "hue_scene"):
                self._cmd_entries[i].pack_forget()
                self._browse_btns[i].pack_forget()
                self._populate_hue_combo(self._hue_combos[i], btype, cmd, btn_idx=i, row=i)
                self._hue_combos[i].pack(side="left", padx=4, expand=True, fill="x")
            # "hue_bri": combo for light/group + entry for %
            elif btype == "hue_bri":
                self._browse_btns[i].pack_forget()
                # Split "group:1:50" into target "group:1" + pct "50"
                if cmd.count(":") >= 2:
                    target = cmd.rsplit(":", 1)[0]
                    pct = cmd.rsplit(":", 1)[1]
                else:
                    target, pct = "", "50"
                self._hue_bri_target[i] = target
                self._populate_hue_combo(self._hue_combos[i], btype, target, btn_idx=None, row=i)
                self._hue_combos[i].pack(side="left", padx=4, expand=True, fill="x")
                self._cmd_entries[i].configure(state="normal", placeholder_text="%")
                self._act_cmd[i].set(pct)
            # Plugin action with value_options: editable combo prefilled by plugin
            else:
                pm = getattr(self._app, "_plugin_manager", None)
                opts = pm.get_action_value_options(btype) if pm else None
                if opts is not None:
                    self._cmd_entries[i].pack_forget()
                    self._browse_btns[i].pack_forget()
                    # NB: don't reuse `labels` here — it holds the action-type
                    # labels for the type dropdown of every remaining button.
                    opt_labels = [lbl for lbl, _v in opts]
                    self._plugin_combo_maps[i] = {lbl: val for lbl, val in opts}
                    self._plugin_combos[i].configure(values=opt_labels)
                    # If current cmd matches one of the values, show that label
                    shown = cmd
                    for lbl, val in opts:
                        if val == cmd:
                            shown = lbl
                            break
                    self._plugin_combos[i].set(shown)
                    self._plugin_combos[i].pack(side="left", padx=4, expand=True, fill="x")

        # Update page selector
        self._page_selector.set(self._panel._get_page_name(page))
        # Refresh the per-page auto-timeout row for the selected page (#45).
        self._timeout_row.load()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 4))

        ctk.CTkLabel(header, text=self._app.T("dp_actions_title"),
                     font=(UI.FONT_FAMILY, 13, "bold"), text_color=FG,
                     fg_color="transparent").pack(side="left")

        # Page selector
        pages = self._panel._get_available_pages()
        page_labels = [self._panel._get_page_name(p) for p in pages]
        newlbl = self._app.T("dp_new_page")
        self._page_selector = ctk.CTkOptionMenu(
            header, values=page_labels + [newlbl],
            fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
            text_color=FG, font=(UI.FONT_FAMILY, 11), width=100, height=28,
            command=self._on_page_change)
        self._page_selector.pack(side="right")
        ctk.CTkButton(
            header, text=self._app.T("dp_rename_page_btn"), width=92, height=28,
            font=(UI.FONT_FAMILY, 10),
            fg_color=BG2, hover_color="#333a44", text_color=FG,
            command=self._on_rename_page).pack(side="right", padx=(0, 6))
        ctk.CTkButton(
            header, text=self._app.T("dp_delete_page_btn"), width=72, height=28,
            font=(UI.FONT_FAMILY, 10),
            fg_color=BG2, hover_color="#4a2222", text_color=RED,
            command=self._on_delete_page).pack(side="right", padx=(0, 6))
        self._page_list = pages

        # ── Per-page auto-timeout (issue #45) ─────────────────────────────────
        # Applies to the page currently selected above, which is not always the
        # page on the device, so this window keeps its own copy of the row that
        # the screen also shows (#71).
        self._timeout_row = PageTimeoutRow(
            self, self._panel, self._app, lambda: self._page)
        self._timeout_row.pack(fill="x", padx=12, pady=(0, 6))

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=6,
                                        width=480, height=460)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        cap_scroll_speed(scroll)

        # Fixed widths so the label + dropdown column lines up identically
        # across all three rows of a key card (action / also-on-press / double-click).
        _ROW_LABEL_W = 130
        _ROW_MENU_W  = 130

        for i in range(12):
            card = ctk.CTkFrame(scroll, fg_color=BG3, corner_radius=4)
            card.pack(fill="x", padx=4, pady=2)
            self._cards.append(card)

            ctk.CTkLabel(card, text=f"K{i+1}", font=(UI.FONT_FAMILY, 10, "bold"),
                         text_color=YLW).pack(anchor="w", padx=8, pady=(5, 0))

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=(2, 6))

            ctk.CTkLabel(row, text=self._app.T("action_label"),
                         font=(UI.FONT_FAMILY, 10), text_color=FG2,
                         width=_ROW_LABEL_W, anchor="w").pack(side="left", padx=(4, 2))

            type_menu = ctk.CTkOptionMenu(
                row, values=self._type_labels(),
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=(UI.FONT_FAMILY, 11), width=_ROW_MENU_W, height=30,
                dynamic_resizing=False,
                command=lambda val, ix=i: self._on_type_change(val, ix))
            type_menu.pack(side="left", padx=(2, 2))
            self._type_menus.append(type_menu)

            entry = ctk.CTkEntry(row, textvariable=self._act_cmd[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=(UI.FONT_FAMILY, 11), height=30)
            entry.pack(side="left", padx=4, expand=True, fill="x")
            entry.bind("<Return>", lambda e, ix=i: self._apply(ix))
            entry.bind("<FocusOut>", lambda e, ix=i: self._apply(ix))
            attach_clipboard_menu(entry, self._app.T)
            self._cmd_entries.append(entry)

            # OBS action combo (hidden by default, shown when type=obs)
            obs_combo = ctk.CTkComboBox(
                row, values=[], width=140, height=30,
                font=(UI.FONT_FAMILY, 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=i: self._on_obs_select(val, ix))
            self._obs_combos.append(obs_combo)

            macro_combo = ctk.CTkComboBox(
                row, values=[], width=140, height=30,
                font=(UI.FONT_FAMILY, 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=i: self._on_macro_select(val, ix))
            self._macro_combos.append(macro_combo)
            # not packed yet — shown only when macro type selected

            # 'page' target picker (#30): choose which page this button jumps to
            # (any existing page, or a freshly minted one for a carousel).
            page_combo = ctk.CTkOptionMenu(
                row, values=[""], width=110, height=30,
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=(UI.FONT_FAMILY, 11), dynamic_resizing=False,
                command=lambda val, ix=i: self._on_page_target_select(val, ix))
            self._page_combos.append(page_combo)
            # not packed yet — shown only when 'page' type selected

            hue_combo = ctk.CTkComboBox(
                row, values=[], width=140, height=30,
                font=(UI.FONT_FAMILY, 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=i: self._on_hue_select(val, ix))
            self._hue_combos.append(hue_combo)

            plugin_combo = ctk.CTkComboBox(
                row, values=[], width=160, height=30,
                font=(UI.FONT_FAMILY, 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=i: self._on_plugin_value_select(val, ix))
            # Also capture free-text edits (user types a custom value) — CTkComboBox
            # only fires `command` on dropdown select, so bind on the inner entry too.
            plugin_combo._entry.bind(
                "<FocusOut>",
                lambda e, ix=i, c=plugin_combo: self._on_plugin_value_select(c.get(), ix))
            plugin_combo._entry.bind(
                "<Return>",
                lambda e, ix=i, c=plugin_combo: self._on_plugin_value_select(c.get(), ix))
            self._plugin_combos.append(plugin_combo)
            attach_clipboard_menu(plugin_combo, self._app.T)

            folder_btn = ctk.CTkButton(
                row, text="", image=self._folder_img_dim,
                width=30, height=30,
                command=lambda ix=i: self._browse(ix),
                fg_color="transparent", hover_color=BG3, corner_radius=4,
                state="disabled")
            folder_btn.pack(side="left", padx=(0, 4))
            self._browse_btns.append(folder_btn)

            # Secondary "also on press" action (issue #16) — keeps monitor /
            # plugin keys from being dead: they can additionally fire a
            # keypress (e.g. F12 via ydotool), text, shell or url on press.
            sec_row = ctk.CTkFrame(card, fg_color="transparent")
            sec_row.pack(fill="x", padx=4, pady=(0, 6))
            ctk.CTkLabel(sec_row, text=self._app.T("dp_also_on_press"),
                         font=(UI.FONT_FAMILY, 10), text_color=FG2,
                         width=_ROW_LABEL_W, anchor="w").pack(side="left", padx=(4, 2))
            sec_menu = ctk.CTkOptionMenu(
                sec_row, values=self._sec_type_labels(),
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=(UI.FONT_FAMILY, 11), width=_ROW_MENU_W, height=28,
                dynamic_resizing=False,
                command=lambda val, ix=i: self._on_sec_type_change(val, ix))
            sec_menu.pack(side="left", padx=(2, 2))
            self._sec_menus.append(sec_menu)
            sec_entry = ctk.CTkEntry(sec_row, textvariable=self._sec_cmd[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=(UI.FONT_FAMILY, 11), height=28)
            sec_entry.pack(side="left", padx=4, expand=True, fill="x")
            sec_entry.bind("<Return>",   lambda e, ix=i: self._apply(ix))
            sec_entry.bind("<FocusOut>", lambda e, ix=i: self._apply(ix))
            attach_clipboard_menu(sec_entry, self._app.T)
            self._sec_entries.append(sec_entry)

            sec_page_combo = ctk.CTkOptionMenu(
                sec_row, values=[""], width=140, height=28,
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=(UI.FONT_FAMILY, 11), dynamic_resizing=False,
                command=lambda val, ix=i: self._on_sec_page_select(val, ix))
            self._sec_page_combos.append(sec_page_combo)
            # not packed yet — shown only when 'page' type selected

            # Double-click action (issue #47) — a distinct action on a quick
            # second press. When set, the primary is held until the click window
            # elapses; when 'none', the key stays instant.
            dbl_row = ctk.CTkFrame(card, fg_color="transparent")
            dbl_row.pack(fill="x", padx=4, pady=(0, 6))
            ctk.CTkLabel(dbl_row, text=self._app.T("dp_on_double_click"),
                         font=(UI.FONT_FAMILY, 10), text_color=FG2,
                         width=_ROW_LABEL_W, anchor="w").pack(side="left", padx=(4, 2))
            dbl_menu = ctk.CTkOptionMenu(
                dbl_row, values=self._sec_type_labels(),
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=(UI.FONT_FAMILY, 11), width=_ROW_MENU_W, height=28,
                dynamic_resizing=False,
                command=lambda val, ix=i: self._on_dbl_type_change(val, ix))
            dbl_menu.pack(side="left", padx=(2, 2))
            self._dbl_menus.append(dbl_menu)
            dbl_entry = ctk.CTkEntry(dbl_row, textvariable=self._dbl_cmd[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=(UI.FONT_FAMILY, 11), height=28)
            dbl_entry.pack(side="left", padx=4, expand=True, fill="x")
            dbl_entry.bind("<Return>",   lambda e, ix=i: self._apply(ix))
            dbl_entry.bind("<FocusOut>", lambda e, ix=i: self._apply(ix))
            attach_clipboard_menu(dbl_entry, self._app.T)
            self._dbl_entries.append(dbl_entry)

            dbl_page_combo = ctk.CTkOptionMenu(
                dbl_row, values=[""], width=140, height=28,
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=(UI.FONT_FAMILY, 11), dynamic_resizing=False,
                command=lambda val, ix=i: self._on_dbl_page_select(val, ix))
            self._dbl_page_combos.append(dbl_page_combo)
            # not packed yet — shown only when 'page' type selected

        self._info_lbl = ctk.CTkLabel(self, text="",
                                      font=(UI.FONT_FAMILY, 11), text_color=GRN)
        self._info_lbl.pack(pady=(0, 4))

        ctk.CTkButton(
            self, text=self._app.T("dp_close"),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            font=(UI.FONT_FAMILY, 11), height=34, corner_radius=6,
            command=self._apply_all_and_close,
        ).pack(fill="x", padx=12, pady=(0, 12))

        _bind_dropdown_autoclose(self)

    def _on_page_change(self, label):
        if label == self._app.T("dp_new_page"):
            name = _prompt_page_name(self._app, "dp_page_name_prompt", "dp_page_name_title")
            if not name:
                self._page_selector.set(self._panel._get_page_name(self._page))
                return
            pid = self._panel._create_named_page(name)
            self._refresh_page_selector(select=pid)
            return
        for p, lbl in zip(self._page_list,
                          [self._panel._get_page_name(x)
                           for x in self._page_list]):
            if lbl == label:
                self._load_page(p)
                # Also display this page on the device so the editor view
                # mirrors what the user sees on the DisplayPad — eliminates
                # the "buttons don't match the editor" surprise.
                try:
                    self._panel._switch_to_page(p)
                except Exception:
                    pass
                break

    def _refresh_page_selector(self, select=None):
        """Rebuild the page dropdown's values after a page was created or
        renamed (#52), and switch the editor to `select` if given."""
        pages = self._panel._get_available_pages()
        self._page_list = pages
        page_labels = [self._panel._get_page_name(p) for p in pages]
        newlbl = self._app.T("dp_new_page")
        self._page_selector.configure(values=page_labels + [newlbl])
        target = select if select is not None else self._page
        self._page_selector.set(self._panel._get_page_name(target))
        if select is not None and select != self._page:
            self._on_page_change(self._panel._get_page_name(select))

    def _on_rename_page(self):
        """Rename the page currently open in the editor (#52). Main (page 0)
        is renamable too -- it's just the page the app opens on."""
        name = _prompt_page_name(
            self._app, "dp_page_name_prompt", "dp_rename_page_title",
            initial=self._panel._get_page_name(self._page))
        if not name:
            return
        self._panel._rename_page(self._page, name)
        self._refresh_page_selector()

    def _on_delete_page(self):
        """Delete the page currently open in the editor (#54), after
        warning if anything still points at it by name. Always falls back
        to showing Main afterward, since the page being edited is gone."""
        page_id = self._page
        if not _confirm_delete_page(self._app, self._panel, page_id):
            return
        self._panel._delete_page(page_id)
        self._refresh_page_selector(select=0)

    def _existing_page_options(self):
        """(labels, {label: page_id}) of every existing page, no 'New page'
        entry — used by the also-on-press / double-click page pickers, which
        can only jump to a page that already exists (issue #16/#47)."""
        mapping, labels = {}, []
        for p in self._sorted_pages():
            lbl = self._panel._get_page_name(p)
            labels.append(lbl)
            mapping[lbl] = p
        return labels, mapping

    def _sorted_pages(self):
        """Page ids with the main page first and the rest by name (#66)."""
        pages = self._panel._get_available_pages()
        return ([0] if 0 in pages else []) + sorted(
            (p for p in pages if p != 0),
            key=lambda p: self._panel._get_page_name(p).casefold())

    def _page_target_options(self):
        """(labels, {label: target}) for the page-target picker: every existing
        page, plus a 'New page' entry that mints a fresh one (#30)."""
        mapping, labels = {}, []
        for p in self._sorted_pages():
            lbl = self._panel._get_page_name(p)
            labels.append(lbl)
            mapping[lbl] = p
        newlbl = self._app.T("dp_new_page")
        labels.append(newlbl)
        mapping[newlbl] = "new"
        return labels, mapping

    def _on_page_target_select(self, val, idx):
        """User picked a destination page (or 'New page') for a 'page' button."""
        _labels, mapping = self._page_target_options()
        self._page_targets[idx] = mapping.get(val, "new")
        self._apply(idx)

    def _on_type_change(self, label, idx):
        # 'page' is offered on every page now (#30), so the type/label lists must
        # always include it — matching what _load_page built the dropdown with.
        # Using the shorter (no-page) list here would make selecting 'Switch page'
        # on a sub-page fail to resolve and silently reset the key to 'none'.
        old_type = self._act_type[idx].get()
        types = self._get_action_types(include_page=True)
        labels = self._type_labels(include_page=True)
        try:
            internal = types[labels.index(label)]
        except (ValueError, IndexError):
            internal = "none"
        if internal == "_separator":
            # A heading, not a type. Matched by its text, which had changed
            # under this check, so picking it made "_separator" the key's type.
            if old_type in types:
                self._type_menus[idx].set(labels[types.index(old_type)])
            return
        self._act_type[idx].set(internal)
        # A value carried over from the previous type is meaningless for the new
        # one (a mountpoint like /home/frans left over after switching from
        # 'Monitor: Disk' to 'Monitor: Disks (cycle)', which wants seconds, not a
        # path). Clear it so each branch below can fill its own default (issue #9).
        if internal != old_type:
            self._act_cmd[idx].set("")
        btn = self._browse_btns[idx]
        if internal in ("folder", "app"):
            btn.configure(state="normal", image=self._folder_img)
        else:
            btn.configure(state="disabled", image=self._folder_img_dim)

        # Show/hide OBS combo / macro combo / hue combo / page picker vs entry+browse
        self._obs_combos[idx].pack_forget()
        self._macro_combos[idx].pack_forget()
        self._hue_combos[idx].pack_forget()
        self._page_combos[idx].pack_forget()
        self._plugin_combos[idx].pack_forget()
        self._cmd_entries[idx].pack_forget()
        self._browse_btns[idx].pack_forget()
        if internal == "obs":
            obs_panel = self._app._obs_panel
            scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
            obs_values = scenes + ["OBS: Record", "OBS: Stream"]
            self._obs_combos[idx].configure(values=obs_values)
            self._obs_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
            cur = self._act_cmd[idx].get()
            if cur.startswith("scene:"):
                self._obs_combos[idx].set(cur[6:])
            elif cur in ("record", "stream"):
                self._obs_combos[idx].set(f"OBS: {cur.capitalize()}")
            elif scenes:
                self._obs_combos[idx].set(scenes[0])
                self._act_cmd[idx].set(f"scene:{scenes[0]}")
        elif internal == "macro":
            self._populate_macro_combo(self._macro_combos[idx], self._act_cmd[idx].get(), btn_idx=idx)
            self._macro_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
        elif internal in ("hue_toggle", "hue_scene"):
            self._populate_hue_combo(self._hue_combos[idx], internal, self._act_cmd[idx].get(), btn_idx=idx, row=idx)
            self._hue_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
        elif internal == "hue_bri":
            # Split stored value "group:1:50" into target "group:1" + pct "50"
            cur = self._act_cmd[idx].get()
            parts = cur.rsplit(":", 1) if cur.count(":") >= 2 else (cur, "50")
            target, pct = parts[0], parts[1] if len(parts) > 1 else "50"
            self._populate_hue_combo(self._hue_combos[idx], internal, target, btn_idx=None, row=idx)
            self._hue_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
            self._cmd_entries[idx].pack(side="left", padx=(0, 4), fill="x")
            self._cmd_entries[idx].configure(state="normal", placeholder_text="%")
            self._act_cmd[idx].set(pct)
            # Rebuild full value when combo changes
            self._hue_bri_target[idx] = target
        else:
            # Plugin action types may declare `value_options` (e.g. 'Monitor:
            # Disk' offers a mountpoint dropdown). Types without them — including
            # sibling plugin types like 'Monitor: Disks (cycle)' — fall through to
            # the plain entry. Without mirroring _load_page here, switching to a
            # no-options type left the previous dropdown packed (issue #9).
            pm = getattr(self._app, "_plugin_manager", None)
            opts = pm.get_action_value_options(internal) if pm else None
            if opts is not None:
                opt_labels = [lbl for lbl, _v in opts]
                self._plugin_combo_maps[idx] = {lbl: val for lbl, val in opts}
                self._plugin_combos[idx].configure(values=opt_labels)
                cur = self._act_cmd[idx].get()
                shown = cur
                for lbl, val in opts:
                    if val == cur:
                        shown = lbl
                        break
                self._plugin_combos[idx].set(shown)
                self._plugin_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
            else:
                self._cmd_entries[idx].pack(side="left", padx=4, expand=True, fill="x")
                self._browse_btns[idx].pack(side="left", padx=(0, 4))

        # "page" type: label entry (button caption) + target picker (#30).
        if internal == "page":
            self._cmd_entries[idx].configure(state="normal")
            cur = self._act_cmd[idx].get()
            if not cur or cur.startswith("→") or cur.startswith("/") or cur.startswith("scene:"):
                self._act_cmd[idx].set(f"Page {idx + 1}")
            labels, mapping = self._page_target_options()
            self._page_combos[idx].configure(values=labels)
            if self._page_targets[idx] is None:
                self._page_targets[idx] = "new"   # a fresh 'page' key mints a page
            sel = self._app.T("dp_new_page")
            for lbl, pid in mapping.items():
                if pid == self._page_targets[idx]:
                    sel = lbl
                    break
            self._page_combos[idx].set(sel)
            self._page_combos[idx].pack(side="left", padx=(0, 4))
            self._cmd_entries[idx].configure(
                placeholder_text=self._app.T("dp_page_caption_hint"))
            # Target first, caption second (#50).
            self._cmd_entries[idx].pack_forget()
            self._cmd_entries[idx].pack(side="left", padx=4, expand=True,
                                        fill="x")
        elif internal == "keypress":
            self._cmd_entries[idx].configure(state="normal",
                placeholder_text=self._app.T("dp_keypress_hint"))
            cur = self._act_cmd[idx].get()
        elif internal != "obs":
            self._cmd_entries[idx].configure(state="normal", placeholder_text="")
            cur = self._act_cmd[idx].get()
            if cur.startswith("→") or cur.startswith("scene:"):
                self._act_cmd[idx].set("")
        self._apply(idx)

    def _on_sec_type_change(self, label, idx):
        """Secondary 'also on press' action type changed (issue #16)."""
        labels = self._sec_type_labels()
        try:
            internal = _SECONDARY_TYPES[labels.index(label)]
        except (ValueError, IndexError):
            internal = "none"
        self._sec_type[idx].set(internal)
        self._sec_entries[idx].pack_forget()
        self._sec_page_combos[idx].pack_forget()
        if internal == "page":
            plabels, pmap = self._existing_page_options()
            cur = self._sec_cmd[idx].get()
            if cur and cur not in pmap:
                plabels = plabels + [cur]
                sel = cur
            else:
                sel = cur if cur in pmap else (plabels[0] if plabels else "")
                self._sec_cmd[idx].set(sel)
            self._sec_page_combos[idx].configure(values=plabels or [""])
            self._sec_page_combos[idx].set(sel)
            self._sec_page_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
        else:
            if internal == "keypress":
                self._sec_entries[idx].configure(
                    placeholder_text=self._app.T("dp_keypress_hint_short"))
            elif internal == "text":
                self._sec_entries[idx].configure(
                    placeholder_text=self._app.T("action_type_text_hint"))
            elif internal == "none":
                self._sec_cmd[idx].set("")
                self._sec_entries[idx].configure(placeholder_text="")
            else:
                self._sec_entries[idx].configure(placeholder_text="")
            self._sec_entries[idx].pack(side="left", padx=4, expand=True, fill="x")
        self._apply(idx)

    def _on_sec_page_select(self, val, idx):
        """User picked a destination page for the 'also on press' action."""
        self._sec_cmd[idx].set(val)
        self._apply(idx)

    def _on_dbl_type_change(self, label, idx):
        """Double-click action type changed (issue #47)."""
        labels = self._sec_type_labels()
        try:
            internal = _SECONDARY_TYPES[labels.index(label)]
        except (ValueError, IndexError):
            internal = "none"
        self._dbl_type[idx].set(internal)
        self._dbl_entries[idx].pack_forget()
        self._dbl_page_combos[idx].pack_forget()
        if internal == "page":
            plabels, pmap = self._existing_page_options()
            cur = self._dbl_cmd[idx].get()
            if cur and cur not in pmap:
                plabels = plabels + [cur]
                sel = cur
            else:
                sel = cur if cur in pmap else (plabels[0] if plabels else "")
                self._dbl_cmd[idx].set(sel)
            self._dbl_page_combos[idx].configure(values=plabels or [""])
            self._dbl_page_combos[idx].set(sel)
            self._dbl_page_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
        else:
            if internal == "keypress":
                self._dbl_entries[idx].configure(
                    placeholder_text=self._app.T("dp_keypress_hint_short"))
            elif internal == "text":
                self._dbl_entries[idx].configure(
                    placeholder_text=self._app.T("action_type_text_hint"))
            elif internal == "none":
                self._dbl_cmd[idx].set("")
                self._dbl_entries[idx].configure(placeholder_text="")
            else:
                self._dbl_entries[idx].configure(placeholder_text="")
            self._dbl_entries[idx].pack(side="left", padx=4, expand=True, fill="x")
        self._apply(idx)

    def _on_dbl_page_select(self, val, idx):
        """User picked a destination page for the double-click action."""
        self._dbl_cmd[idx].set(val)
        self._apply(idx)

    def _take_obs_value(self, val, idx):
        if val == "OBS: Record":
            self._act_cmd[idx].set("record")
        elif val == "OBS: Stream":
            self._act_cmd[idx].set("stream")
        else:
            self._act_cmd[idx].set(f"scene:{val}")

    def _on_obs_select(self, val, idx):
        """Called when user picks a scene or record/stream from OBS combo."""
        self._take_obs_value(val, idx)
        self._apply(idx)

    def _macro_names(self):
        """{uuid: name}, from the Macros screen while it exists and from the
        saved macros before it is first opened (see config.macro_names)."""
        macro_panel = getattr(self._app, "_macro_panel", None)
        if macro_panel is not None:
            return macro_panel.get_macro_names()
        return macro_names()

    def _populate_macro_combo(self, combo, current_uuid="", btn_idx=None):
        names = self._macro_names()
        self._macro_uuid_list = list(names.keys())
        display = list(names.values())
        no_macros = self._app.T("macro_none_available")
        combo.configure(values=display if display else [no_macros])
        if current_uuid and current_uuid in names:
            combo.set(names[current_uuid])
        elif self._macro_uuid_list:
            combo.set(display[0])
            if btn_idx is not None:
                self._act_cmd[btn_idx].set(self._macro_uuid_list[0])
        else:
            # Say so, rather than leaving the widget's own placeholder on screen.
            combo.set(no_macros)

    def _take_macro_value(self, val, idx):
        names = self._macro_names()
        display = list(names.values())
        uuids = list(names.keys())
        try:
            pos = display.index(val)
            self._act_cmd[idx].set(uuids[pos])
        except (ValueError, IndexError):
            pass

    def _on_macro_select(self, val, idx):
        self._take_macro_value(val, idx)
        self._apply(idx)

    def _take_plugin_value(self, val, idx):
        # Map display label back to internal value (free text falls through verbatim)
        mapped = self._plugin_combo_maps.get(idx, {}).get(val, val)
        self._act_cmd[idx].set(mapped)

    def _on_plugin_value_select(self, val, idx):
        self._take_plugin_value(val, idx)
        self._apply(idx)

    def _populate_hue_combo(self, combo, hue_type, current_val="", btn_idx=None,
                            row=None, _asked=False):
        """Build dropdown values for hue_toggle or hue_scene from the Hue plugin state."""
        pm = getattr(self._app, "_plugin_manager", None)
        hue = None
        if pm:
            hue = pm._instances.get("hue_control")
        items = []       # display labels
        values_map = []  # internal values (light:1, group:2, ...)
        if hue_type == "hue_toggle":
            # Groups first, then lights
            if hue and hue._groups:
                for gid in sorted(hue._groups, key=lambda g: hue._groups[g].get("name", "")):
                    name = hue._groups[gid].get("name", f"Group {gid}")
                    items.append(f"\u25CF {name}")
                    values_map.append(f"group:{gid}")
            if hue and hue._lights:
                for lid in sorted(hue._lights, key=lambda l: hue._lights[l].get("name", "")):
                    name = hue._lights[lid].get("name", f"Light {lid}")
                    items.append(f"\u2022 {name}")
                    values_map.append(f"light:{lid}")
        elif hue_type == "hue_scene":
            if hue and hue._scenes:
                for sid in sorted(hue._scenes, key=lambda s: hue._scenes[s].get("name", "")):
                    sinfo = hue._scenes[sid]
                    name = sinfo.get("name", f"Scene {sid}")
                    gid = sinfo.get("group", "")
                    gname = hue._groups.get(gid, {}).get("name", "") if hue else ""
                    lbl = f"{name} ({gname})" if gname else name
                    items.append(lbl)
                    values_map.append(f"{gid}:{sid}")
        elif hue_type == "hue_bri":
            # Same list as toggle — just groups + lights
            if hue and hue._groups:
                for gid in sorted(hue._groups, key=lambda g: hue._groups[g].get("name", "")):
                    name = hue._groups[gid].get("name", f"Group {gid}")
                    items.append(f"\u25CF {name}")
                    values_map.append(f"group:{gid}")
            if hue and hue._lights:
                for lid in sorted(hue._lights, key=lambda l: hue._lights[l].get("name", "")):
                    name = hue._lights[lid].get("name", f"Light {lid}")
                    items.append(f"\u2022 {name}")
                    values_map.append(f"light:{lid}")

        self._hue_values_maps[row] = values_map
        if not items:
            # Nothing to offer, which is not a value: a combo given only a list
            # keeps the text it was built with, and CustomTkinter's is its own
            # class name, "CTkComboBox" (#108). Say why there is nothing, and
            # leave a stored value standing where it can be read.
            paired = bool(hue and getattr(hue, "_api_key", ""))
            asking = (paired and not _asked and hasattr(hue, "refresh_async")
                      and hue.refresh_async(lambda: self._repopulate_hue_combo(
                          combo, hue_type, current_val, btn_idx, row)))
            hint = self._app.T("dp_hue_loading" if asking
                               else "dp_hue_none" if paired else "dp_hue_unpaired")
            combo.configure(values=[hint])
            combo.set(current_val or hint)
            return
        combo.configure(values=items)
        # Restore current selection
        if current_val and current_val in values_map:
            combo.set(items[values_map.index(current_val)])
        else:
            combo.set(items[0])
            if btn_idx is not None:
                self._act_cmd[btn_idx].set(values_map[0])

    def _repopulate_hue_combo(self, combo, hue_type, current_val, btn_idx, row):
        """Fill a Hue dropdown in again once the bridge has answered, if the
        row still asks for the same thing. Asked once: a bridge that answers
        with nothing gets the hint rather than another question."""
        try:
            if not combo.winfo_exists():
                return
            if row is not None and self._act_type[row].get() != hue_type:
                return
        except Exception:
            return
        self._populate_hue_combo(combo, hue_type, current_val, btn_idx,
                                 row=row, _asked=True)

    def _take_hue_value(self, val, idx):
        items = self._hue_combos[idx].cget("values")
        values_map = self._hue_values_maps.get(idx, [])
        try:
            pos = list(items).index(val) if isinstance(items, (list, tuple)) else 0
            if pos < len(values_map):
                target = values_map[pos]
                if self._act_type[idx].get() == "hue_bri":
                    # Store target, entry field holds the %
                    self._hue_bri_target[idx] = target
                    self._assemble_hue_bri(idx)
                else:
                    self._act_cmd[idx].set(target)
        except (ValueError, IndexError):
            pass

    def _on_hue_select(self, val, idx):
        self._take_hue_value(val, idx)
        self._apply(idx)

    def _assemble_hue_bri(self, idx):
        """Combine hue_bri target + entry percentage into full value."""
        target = self._hue_bri_target.get(idx, "")
        pct = self._act_cmd[idx].get().strip().replace("%", "")
        try:
            pct = max(0, min(100, int(pct)))
        except ValueError:
            pct = 50
        # Temporarily store full value — entry shows only the number
        self._act_cmd[idx].set(str(pct))
        # The actual saved value needs target:pct
        return f"{target}:{pct}" if target else ""

    def _browse(self, idx):
        btype = self._act_type[idx].get()
        if btype == "folder":
            path = native_open_folder(title=self._app.T("ui_pick_folder"))
            if path:
                self._act_cmd[idx].set(path)
                self._apply(idx)
        elif btype == "app":
            self._show_app_picker(idx)  # auto-saves via _select→_apply

    def _show_app_picker(self, idx):
        apps = parse_desktop_apps()
        if not apps:
            return
        dlg = ctk.CTkToplevel(self._app)
        dlg.title(self._app.T("app_picker_title"))
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.geometry("360x480")
        dlg.update_idletasks()
        dlg.grab_set()

        search_var = tk.StringVar()
        ctk.CTkEntry(dlg, textvariable=search_var,
                     placeholder_text=self._app.T("app_picker_search"),
                     fg_color=BG2, text_color=FG, border_color=BORDER,
                     font=(UI.FONT_FAMILY, 12), height=34,
                     ).pack(fill="x", padx=12, pady=(12, 6))

        list_frame = ctk.CTkScrollableFrame(dlg, fg_color=BG2, corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        cap_scroll_speed(list_frame)

        _btn_refs = []

        def _select(exec_cmd):
            self._act_cmd[idx].set(exec_cmd)
            dlg.destroy()
            self._apply(idx)

        def _rebuild(filter_text=""):
            for b in _btn_refs:
                b.destroy()
            _btn_refs.clear()
            ft = filter_text.lower()
            for name, exec_cmd in apps:
                if ft and ft not in name.lower():
                    continue
                b = ctk.CTkButton(list_frame, text=name, anchor="w",
                                  fg_color="transparent", text_color=FG,
                                  hover_color=BG3, font=(UI.FONT_FAMILY, 11),
                                  height=30, corner_radius=4,
                                  command=lambda e=exec_cmd: _select(e))
                b.pack(fill="x", pady=1)
                _btn_refs.append(b)

        _rebuild()
        search_var.trace_add("write", lambda *_: _rebuild(search_var.get()))

    def _flush_value_widget(self, idx):
        """Copy what the visible value widget holds into this row's variable.

        The plain entries are bound to their variable and need nothing. The
        four combo boxes are not: they only hand over what was typed into them
        when the person picks from the list or the widget loses focus. Clicking
        Apply moves no focus, so a typed value was simply thrown away (#87),
        and forcing a focus change first does not help either, because a focus
        event is not an idle task and is still in the queue when Apply reads
        the variable. So Apply asks the widget instead of hoping.
        """
        btype = self._act_type[idx].get()
        try:
            if btype == "obs" and idx < len(self._obs_combos) \
                    and self._obs_combos[idx].winfo_ismapped():
                self._take_obs_value(self._obs_combos[idx].get(), idx)
            elif btype == "macro" and idx < len(self._macro_combos) \
                    and self._macro_combos[idx].winfo_ismapped():
                self._take_macro_value(self._macro_combos[idx].get(), idx)
            elif btype in ("hue_toggle", "hue_scene", "hue_bri") \
                    and idx < len(self._hue_combos) \
                    and self._hue_combos[idx].winfo_ismapped():
                self._take_hue_value(self._hue_combos[idx].get(), idx)
            elif idx < len(self._plugin_combos) \
                    and self._plugin_combos[idx].winfo_ismapped():
                self._take_plugin_value(self._plugin_combos[idx].get(), idx)
        except Exception:
            # A widget that is being rebuilt underneath us is not worth
            # losing the rest of the save over.
            pass

    def _apply(self, idx):
        self._flush_value_widget(idx)
        btype  = self._act_type[idx].get()
        # What gets stored is decided in the core (actions.build_action): the
        # also-on-press and double-click steps, a hue_bri "target:pct", the
        # page picked for a page key.
        built = _acts.build_action(
            btype, self._act_cmd[idx].get(),
            sec_type=self._sec_type[idx].get() if idx < len(self._sec_type) else "none",
            sec_value=self._sec_cmd[idx].get() if idx < len(self._sec_cmd) else "",
            dbl_type=self._dbl_type[idx].get() if idx < len(self._dbl_type) else "none",
            dbl_value=self._dbl_cmd[idx].get() if idx < len(self._dbl_cmd) else "",
            page_target=self._page_targets[idx],
            hue_target=self._hue_bri_target.get(idx, ""),
            page_exists=lambda name: self._panel._page_id_by_name(name) is not None)
        action, extra = built["action"], built["extra"]
        target, double = built["target"], built["double"]
        self._saving = True
        try:
            self._panel._save_page_action(self._page, idx, btype, action, extra,
                                          target=target, double=double)
        finally:
            self._saving = False
        self._panel._gc_orphan_pages()
        self._info_lbl.configure(
            text=self._app.T("dp_act_saved", k=idx + 1), text_color=GRN)
        # Refresh page selector (new pages may have been created / renamed)
        pages = self._panel._get_available_pages()
        self._page_list = pages
        page_labels = [self._panel._get_page_name(p) for p in pages]
        self._page_selector.configure(values=page_labels)
        self._page_selector.set(self._panel._get_page_name(self._page))

    def _apply_all_and_close(self):
        """Save all 12 button actions (catches unsaved entries) and close.

        Two things had to be true for this to keep what was typed (#87), and
        both are handled in _apply() itself: it reads the visible widget rather
        than waiting for a focus event that clicking a button never sends, and
        it holds off this dialog's own re-read while it writes, which is what
        used to revert every row the loop had not reached yet.
        """
        try:
            self.focus_set()
            self.update_idletasks()
        except Exception:
            pass
        for i in range(12):
            self._apply(i)
        if self._resize_save_after_id is not None:
            try:
                self.after_cancel(self._resize_save_after_id)
            except Exception:
                pass
            self._resize_save_after_id = None
        self._save_current_size()
        self.destroy()


# ── Panel ─────────────────────────────────────────────────────────────────────

def _model_attr(name):
    """A panel attribute that is the page model's: the same object, so code
    and plugins that write into it (dp._images[k] = ...) write the model's."""
    return property(lambda self: getattr(self._svc.model, name),
                    lambda self, value: setattr(self._svc.model, name, value))


def _svc_attr(name):
    """A panel attribute that is the DisplayPad service's (its flags, locks,
    events and settings), under the name it had here."""
    return property(lambda self: getattr(self._svc, name),
                    lambda self, value: setattr(self._svc, name, value))


class _PanelTransport:
    """The service's transport: core/devices/displaypad/protocol.py, with
    the I/O functions looked up on this module each time they are used.

    The tests replace _open_interfaces, _init_device, _close_interfaces and
    _upload_button here with a stand-in pad, and the switching test fails a
    write only when it comes straight from a function named _worker: the
    lookup happens before the call, so the call is still made from the
    service's _worker itself.
    """
    _HERE = {"open_interfaces": "_open_interfaces", "init_device": "_init_device",
             "close_interfaces": "_close_interfaces", "upload_button": "_upload_button",
             "set_brightness": "_set_brightness"}

    def __getattr__(self, name):
        local = self._HERE.get(name)
        if local is not None:
            return getattr(sys.modules[__name__], local)
        return getattr(_proto, name)


def _displaypad_service(app, widget):
    """The application's DisplayPad service (core/devices/displaypad), added
    when the core does not have one yet."""
    core = getattr(app, "core", None)
    if core is None:
        core = CoreApp(scheduler=TkScheduler(widget.winfo_toplevel()))
        core.start()
    svc = core.service("displaypad")
    if svc is None:
        svc = core.add_service(DisplayPadService(core))
    return svc


class DisplayPadPanel(ctk.CTkFrame):
    """Panel for Mountain DisplayPad (12 display buttons, GIF animation support).

    The pad, its pages and its workers are the DisplayPad service's
    (core/devices/displaypad/service.py). This panel draws them and edits
    them; its old attribute names are views onto the service.
    """

    VID = VID
    PID = PID

    # The pages live in core/devices/displaypad/pages.py (PageModel). These
    # names are what gui.py, the plugins, the dialogs below and the tests
    # have always used.
    _page_actions     = _model_attr("page_actions")
    _page_images      = _model_attr("page_images")
    _page_fullscreen  = _model_attr("page_fullscreen")
    _page_timeout     = _model_attr("page_timeout")
    _images           = _model_attr("images")
    _gif_frames       = _model_attr("gif_frames")
    _fullscreen_group = _model_attr("fullscreen_group")
    _current_page     = _model_attr("current_page")
    _prev_page        = _model_attr("prev_page")
    _deleted_page_ids = _model_attr("deleted_page_ids")
    _plugin_frame_keys = _model_attr("plugin_frame_keys")
    _res_path         = _model_attr("res_path")
    _folder_icon      = _model_attr("folder_icon")
    _back_icon        = _model_attr("back_icon")
    _blank_icon       = _model_attr("blank_icon")

    # The action runner's state, under the names it had here.
    _dc_timers = property(lambda self: self._runner.dc_timers)
    _dc_window = property(lambda self: self._runner.dc_window,
                          lambda self, v: setattr(self._runner, "dc_window", v))
    _dc_antibounce = property(lambda self: self._runner.dc_antibounce,
                              lambda self, v: setattr(self._runner, "dc_antibounce", v))
    _timeout_after_id = property(lambda self: self._runner.timeout_handle)
    _macro_toggle_events = property(lambda self: self._runner.macro_toggle_events)

    _model  = property(lambda self: self._svc.model)
    _runner = property(lambda self: self._svc.runner)

    # The pad's session and settings, the service's.
    _uploading          = _svc_attr("uploading")
    _animating          = _svc_attr("animating")
    _page_switch_waiting = _svc_attr("page_switch_waiting")
    _device_present     = _svc_attr("device_present")
    _dp_path            = _svc_attr("dp_path")
    _pad_ready          = _svc_attr("pad_ready")
    _warned_quirk       = _svc_attr("warned_quirk")
    _anim_thread        = _svc_attr("anim_thread")
    _anim_stop          = _svc_attr("anim_stop")
    _upload_queue       = _svc_attr("upload_queue")
    _usb_lock           = _svc_attr("usb_lock")
    _key_released       = _svc_attr("key_released")
    _plugin_worker_stop = _svc_attr("plugin_worker_stop")
    _monitor_stop       = _svc_attr("monitor_stop")
    _closing            = _svc_attr("closing")
    _last_plugin_error  = _svc_attr("last_plugin_error")
    _min_frame_ms       = _svc_attr("min_frame_ms")
    _rotation           = _svc_attr("rotation")
    _brightness         = _svc_attr("brightness")
    _debounce           = _svc_attr("debounce")
    _svc_sync_id        = _svc_attr("svc_sync_handle")
    _hint_pages_quiet   = _svc_attr("hint_pages_quiet")

    # What this panel passes as the origin of a change it shows itself, so
    # it can leave the service's event about it alone.
    _ORIGIN = "tk-panel"

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app         = app
        self._svc = _displaypad_service(app, self)
        # What the service says (uploads, the pad coming and going, widget
        # frames), on this thread; ends with this panel.
        subscribe_tk(self._svc.app, "displaypad.*", self, self._on_core_event)
        self._tile_imgs   = {}   # key grid: key_index -> CTkImage
        self._tile_lbls   = {}   # key grid: key_index -> CTkLabel
        self._tile_cells  = {}   # key grid: key_index -> frame (selection border)
        self._selected_key = 0   # key shown in the inspector
        self._insp_loading = False
        self._dialog_win       = None
        # Per key, the image path the grid was last redrawn for, and when.
        # A widget that keeps writing the same file name still changes what
        # the key shows, so the path alone cannot decide this (#96).
        self._tile_shown = {}     # key idx -> path last drawn
        self._tile_drawn = {}     # key idx -> time.monotonic() of that draw
        # Noted by the plugin threads, drawn by the interface thread, so the
        # note and the taking of it are under one lock.
        self._tile_lock = threading.Lock()
        self._tile_dirty = set()  # keys whose picture changed, not yet drawn
        # Tiles left undrawn while nobody could see the grid (#105), drawn
        # once it shows: this panel being packed again, or the window coming
        # back from the tray. A toplevel binding fires for its descendants
        # too, which is what catches the window, and costs a flag check.
        self._tiles_waiting = False
        self._hint_box = None            # the page's box, if one is up (#93)
        self.bind("<Map>", self._on_grid_mapped, add="+")
        self.winfo_toplevel().bind("<Map>", self._on_grid_mapped, add="+")
        self._tile_pass_due = False   # a pass is booked and has not run yet
        # GUI preview animation
        self._gui_frames_sm  = {}
        self._gui_fidx       = {}
        self._gui_next       = {}
        self._gui_tick_id    = None

        # The pad is this panel's to show: the service loads every page from
        # the configuration (old ones brought up to date, #30, #95, #111),
        # starts the device monitor and the device worker, and sends Main to
        # the pad, if it had not been started before.
        self._svc.attach_host(
            transport=_PanelTransport(),
            translate=self.T,
            plugin_types=self._plugin_type_ids,
            sync_services=self._sync_services_for,
            quirk_check=lambda: self._maybe_warn_usb_quirk())
        self._rebuild_gui_frames(0)

        self._min_ms_var = ctk.StringVar(value=str(self._svc.min_ms))
        self._min_ms_var.trace_add("write", self._on_min_ms_change)
        self._build_ui()

        # Refresh tiles immediately for any pre-loaded GIF frames
        for idx in self._gui_frames_sm:
            self._refresh_panel_tile(idx)

        if self._gui_frames_sm:
            self.after(200, self._gui_tick)

        self.bind("<Destroy>", lambda e: (self._monitor_stop.set(),
                                           self._plugin_worker_stop.set()), add="+")
        _bind_dropdown_autoclose(self.winfo_toplevel())

    def _sync_services_for(self, page):
        """Start and stop the widget services for `page` (the plugin manager
        looked up when asked, so a test's stand-in for it is what runs)."""
        pm = getattr(self._app, "_plugin_manager", None)
        if pm:
            pm.sync_services_for_page(page)

    def _plugin_type_ids(self):
        """The action type ids plugins provide, None without a plugin manager."""
        pm = getattr(self._app, "_plugin_manager", None)
        if pm is None:
            return None
        return pm.get_action_type_ids()

    def _rebuild_gui_frames(self, page):
        """The editor's animated previews for the GIFs on the page on the pad:
        one per key with a GIF of its own, and a fullscreen GIF's tiles."""
        self._gui_frames_sm = {}
        self._gui_fidx = {}
        self._gui_next = {}
        for k, path in self._images.items():
            if int(k) in self._gif_frames and path and path.lower().endswith('.gif') \
                    and os.path.exists(path):
                gui_f = _load_gif_display_frames(path, _PANEL_TILE)
                if gui_f:
                    self._gui_frames_sm[int(k)] = gui_f
                    self._gui_fidx[int(k)] = 0
                    self._gui_next[int(k)] = time.monotonic()
        fs = self._page_fullscreen.get(page) if self._fullscreen_group else None
        if fs and os.path.exists(fs):
            gui_tiles = _split_gif_display_tiles(fs, _PANEL_TILE)
            if gui_tiles:
                for idx in range(NUM_KEYS):
                    self._gui_frames_sm[idx] = gui_tiles[idx]
                    self._gui_fidx[idx] = 0
                    self._gui_next[idx] = time.monotonic()

    def T(self, key, **kwargs):
        return self._app.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        self._scroll = scroll

        cap_scroll_speed(scroll)

        content = scroll

        # Section heading + rotation
        head_row = ctk.CTkFrame(content, fg_color="transparent")
        head_row.pack_forget()   # its contents live in the screen header now

        # Kept for the language refresh; the screen header shows the name.
        self._heading_lbl = ctk.CTkLabel(
            head_row, text=self.T("dp_title"),
            font=(UI.FONT_FAMILY, 14, "bold"), text_color=FG,
            fg_color="transparent", anchor="w")

        # ── Page tabs ──────────────────────────────────────────────────────
        # The named pages from 2.1.7 were only visible after opening a dialog
        # and pulling down a combo box. As tabs they are simply there, which
        # is also what dp_page switches from the outside.
        self._pagebar = ctk.CTkFrame(content, fg_color="transparent")
        self._pagebar.pack(fill="x", padx=16, pady=(10, 0))
        self._page_tabs = {}
        self._page_lbl = ctk.CTkLabel(self._pagebar, text="")      # i18n refresh only
        self._page_back_btn = ctk.CTkButton(self._pagebar, text="")  # legacy, unused

        # ── Key grid and inspector ─────────────────────────────────────────
        body = ctk.CTkFrame(content, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(10, 0))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=_INSPECTOR_W)
        body.grid_rowconfigure(0, weight=1)

        grid_wrap = ctk.CTkFrame(body, fg_color="transparent")
        grid_wrap.grid(row=0, column=0, sticky="nsew")
        overview = ctk.CTkFrame(grid_wrap, fg_color="transparent")
        overview.pack(anchor="n")
        self._keygrid = overview

        ph = _make_placeholder(_PANEL_TILE)
        for idx in range(NUM_KEYS):
            row = idx // KEYS_PER_ROW
            col = idx % KEYS_PER_ROW
            path = self._images.get(str(idx))
            is_gif = idx in self._gif_frames
            if path and os.path.exists(path):
                img = (_make_gif_thumb(path, _PANEL_TILE, self._rotation) if is_gif
                       else _make_thumb(path, _PANEL_TILE, self._rotation))
            else:
                img = self._make_action_tile(idx) if hasattr(self, "_make_action_tile") else ph
            self._tile_imgs[idx] = img

            cell = ctk.CTkFrame(overview, fg_color=BG3, corner_radius=6,
                                border_width=2, border_color=BG3)
            cell.grid(row=row * 2, column=col, padx=4, pady=(4, 0))
            lbl = ctk.CTkLabel(cell, image=img, text="",
                               width=_PANEL_TILE, height=_PANEL_TILE,
                               fg_color=BG3, corner_radius=4)
            lbl.pack(padx=2, pady=2)
            self._tile_lbls[idx] = lbl
            self._tile_cells[idx] = cell
            for w in (cell, lbl):
                w.bind("<Button-1>", lambda _e, i=idx: self._select_key(i))
                w.bind("<Button-3>", lambda _e, i=idx: self._clear_slot(i))

            ctk.CTkLabel(overview, text=f"K{idx + 1}",
                         font=(UI.FONT_FAMILY, 9), text_color=FG2,
                         fg_color="transparent").grid(row=row * 2 + 1, column=col)

        # ── Page settings, directly under the keys (#71) ────────────────────
        # Everything here belongs to the page on the device: what it is called,
        # whether it exists, and when it hands over to another page. It used to
        # be reachable only through the button-actions window, which is about
        # keys, not pages.
        # Gridded into the key grid itself, not packed under it, so it lines up
        # with the keys instead of with the wider column they sit in.
        settings = ctk.CTkFrame(overview, fg_color=BG2, corner_radius=6)
        settings.grid(row=(NUM_KEYS // KEYS_PER_ROW) * 2, column=0,
                      columnspan=KEYS_PER_ROW, sticky="ew", pady=(12, 0))

        page_row = ctk.CTkFrame(settings, fg_color="transparent")
        page_row.pack(fill="x", padx=10, pady=(8, 2))
        self._page_name_lbl = ctk.CTkLabel(
            page_row, text="", font=(UI.FONT_FAMILY, 11, "bold"),
            text_color=FG, anchor="w")
        self._page_name_lbl.pack(side="left")
        ctk.CTkButton(
            page_row, text=self.T("dp_delete_page_btn"), width=72,
            height=UI.CTRL_H_SM, font=(UI.FONT_FAMILY, 10),
            fg_color=BG3, hover_color="#4a2222", text_color=RED,
            command=self._on_delete_current_page).pack(side="right")
        ctk.CTkButton(
            page_row, text=self.T("dp_rename_page_btn"), width=92,
            height=UI.CTRL_H_SM, font=(UI.FONT_FAMILY, 10),
            fg_color=BG3, hover_color="#333a44", text_color=FG,
            command=self._on_rename_current_page).pack(side="right", padx=(0, 6))
        ctk.CTkButton(
            page_row, text=self.T("dp_page_note_btn"), width=72,
            height=UI.CTRL_H_SM, font=(UI.FONT_FAMILY, 10),
            fg_color=BG3, hover_color="#333a44", text_color=FG,
            command=self._open_page_note).pack(side="right", padx=(0, 6))

        self._page_timeout_row = PageTimeoutRow(
            settings, self, self._app, lambda: self._current_page,
            fg_color="transparent")
        self._page_timeout_row.pack(fill="x", padx=0, pady=(0, 2))

        # Minimum milliseconds per GIF frame. This was on this screen until the
        # 3.0 redesign and only survived in the multi-upload window, where
        # nobody looks for it (#73). It is not page-scoped, so it sits apart.
        fps_row = ctk.CTkFrame(settings, fg_color="transparent")
        fps_row.pack(fill="x", padx=10, pady=(0, 8))
        self._min_ms_lbl = ctk.CTkLabel(fps_row, text=self.T("dp_min_ms_frame"),
                                        font=(UI.FONT_FAMILY, 10), text_color=FG2)
        self._min_ms_lbl.pack(side="left")
        ctk.CTkEntry(fps_row, textvariable=self._min_ms_var,
                     width=52, height=26, font=(UI.FONT_FAMILY, 10),
                     fg_color=BG3, border_color=BORDER, text_color=FG,
                     ).pack(side="left", padx=(6, 0))
        self._gif_speed_lbl = ctk.CTkLabel(fps_row, text=self.T("dp_gif_speed"),
                                           font=(UI.FONT_FAMILY, 10), text_color=FG2)
        self._gif_speed_lbl.pack(side="left", padx=(8, 0))

        # One image across all twelve keys, straight from here (#78). It is a
        # property of the page you are looking at, so it belongs in this row
        # rather than in the header beside the device-wide controls.
        self._fs_btn = ctk.CTkButton(
            fps_row, text=self.T("dp_fullscreen"), width=96,
            height=UI.CTRL_H_SM, font=(UI.FONT_FAMILY, 10),
            fg_color=BG3, hover_color="#333a44", text_color=FG,
            command=self._pick_fullscreen)
        self._fs_btn.pack(side="right")
        self._reg(self._fs_btn, "dp_fullscreen")

        hint = ctk.CTkLabel(grid_wrap, text=self.T("dp_grid_hint"),
                            font=(UI.FONT_FAMILY, 10), text_color=FG2)
        hint.pack(pady=(10, 0))
        self._reg(hint, "dp_grid_hint")
        self._reg(self._min_ms_lbl, "dp_min_ms_frame")
        self._reg(self._gif_speed_lbl, "dp_gif_speed")

        self._build_inspector(body)

        # Info label, kept for status text from the upload worker
        self._info_label = ctk.CTkLabel(
            content, text="",
            font=(UI.FONT_FAMILY, 11), text_color=FG2, fg_color="transparent")
        self._info_label.pack(pady=(8, 8))

        self._rebuild_page_tabs()
        self._select_key(0)

    # ── Key inspector ─────────────────────────────────────────────────────────

    def _build_inspector(self, parent):
        """Everything about the selected key, in one column beside the grid.

        This replaces the "Configure Button Actions" window for the common
        case. The rarely used parts of that window (also on press, double
        click, the per-page timeout) are still reachable through the button at
        the bottom, so nothing was lost while it is being absorbed.
        """
        insp = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=7,
                            border_width=1, border_color=BORDER)
        insp.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        self._inspector = insp

        self._insp_title = ctk.CTkLabel(
            insp, text="", font=(UI.FONT_FAMILY, 12, "bold"), text_color=FG, anchor="w")
        self._insp_title.pack(fill="x", padx=12, pady=(12, 8))

        self._insp_type_lbl = ctk.CTkLabel(insp, text=self.T("dp_insp_action"),
                                           font=(UI.FONT_FAMILY, 10), text_color=FG2,
                                           anchor="w")
        self._insp_type_lbl.pack(fill="x", padx=12)
        self._reg(self._insp_type_lbl, "dp_insp_action")
        self._insp_type = ctk.CTkOptionMenu(
            insp, values=action_type_labels(self._app), width=_INSPECTOR_W - 24,
            height=28, font=(UI.FONT_FAMILY, 11), fg_color=BG3, button_color=BORDER,
            button_hover_color=BORDER, text_color=FG,
            command=self._on_insp_type_change)
        self._insp_type.pack(padx=12, pady=(2, 8))

        self._insp_value_lbl = ctk.CTkLabel(insp, text=self.T("dp_insp_value"),
                                            font=(UI.FONT_FAMILY, 10), text_color=FG2,
                                            anchor="w")
        self._insp_value_lbl.pack(fill="x", padx=12)
        self._reg(self._insp_value_lbl, "dp_insp_value")
        self._insp_value_var = tk.StringVar()
        self._insp_value = ctk.CTkEntry(
            insp, textvariable=self._insp_value_var, width=_INSPECTOR_W - 24,
            height=28, font=(UI.FONT_FAMILY, 11), fg_color=BG3, text_color=FG,
            border_color=BORDER)
        self._insp_value.pack(padx=12, pady=(2, 4))
        self._insp_value.bind("<FocusOut>", lambda _e: self._save_inspector())
        self._insp_value.bind("<Return>", lambda _e: self._save_inspector())
        # What the key does, in a few words, for the page's box (#93). Kept
        # apart from the action, so it outlives a change of action.
        self._insp_hint_lbl = ctk.CTkLabel(insp, text=self.T("dp_insp_hint"),
                                           font=(UI.FONT_FAMILY, 10), text_color=FG2,
                                           anchor="w")
        self._insp_hint_lbl.pack(fill="x", padx=12, pady=(6, 0))
        self._reg(self._insp_hint_lbl, "dp_insp_hint")
        self._insp_hint_var = tk.StringVar()
        self._insp_hint = ctk.CTkEntry(
            insp, textvariable=self._insp_hint_var, width=_INSPECTOR_W - 24,
            height=28, font=(UI.FONT_FAMILY, 11), fg_color=BG3, text_color=FG,
            border_color=BORDER)
        self._insp_hint.pack(padx=12, pady=(2, 4))
        self._insp_hint.bind("<FocusOut>", self._save_key_hint)
        self._insp_hint.bind("<Return>", self._save_key_hint)
        self._insp_page = ctk.CTkOptionMenu(
            insp, values=[""], width=_INSPECTOR_W - 24, height=28,
            font=(UI.FONT_FAMILY, 11), fg_color=BG3, button_color=BORDER,
            button_hover_color=BORDER, text_color=FG,
            command=lambda _v: self._save_inspector())

        self._insp_browse = UI.GhostButton(
            insp, self.T("dp_insp_browse"), self._insp_do_browse,
            width=_INSPECTOR_W - 24, height=UI.CTRL_H_SM)

        self._insp_img_lbl = ctk.CTkLabel(insp, text=self.T("dp_insp_image"),
                                          font=(UI.FONT_FAMILY, 10), text_color=FG2,
                                          anchor="w")
        self._insp_img_lbl.pack(fill="x", padx=12, pady=(8, 2))
        self._reg(self._insp_img_lbl, "dp_insp_image")
        img_row = ctk.CTkFrame(insp, fg_color="transparent")
        img_row.pack(fill="x", padx=12)
        pick = UI.GhostButton(img_row, self.T("dp_insp_pick_image"),
                              self._insp_pick_image, width=_INSPECTOR_W - 96,
                              height=UI.CTRL_H_SM)
        self._reg(pick, "dp_insp_pick_image")
        pick.pack(side="left")
        clear = UI.DangerButton(img_row, self.T("dp_insp_clear"),
                                lambda: self._clear_slot(self._selected_key),
                                width=64, height=UI.CTRL_H_SM)
        self._reg(clear, "dp_insp_clear")
        clear.pack(side="right")

        self._insp_more = UI.GhostButton(
            insp, self.T("dp_insp_more"), self._open_actions_dialog,
            width=_INSPECTOR_W - 24, height=UI.CTRL_H_SM)
        self._reg(self._insp_more, "dp_insp_more")
        self._insp_more.pack(side="bottom", padx=12, pady=12, fill="x")

    def _select_key(self, idx):
        """Mark a key in the grid and show it in the inspector."""
        if hasattr(self, "_insp_hint_var"):
            self._save_key_hint()       # clicking a tile moves no focus
        self._selected_key = idx
        for i, cell in self._tile_cells.items():
            cell.configure(border_color=BLUE if i == idx else BG3)
        self._load_inspector()

    def _load_inspector(self):
        idx = self._selected_key
        act = self._get_action_dict(idx)
        btype = act.get("type", "none")
        self._insp_loading = True
        try:
            ids = action_type_ids(self._app)
            labels = action_type_labels(self._app)
            self._insp_title.configure(text=f"K{idx + 1}")
            if btype in ids:
                self._insp_type.set(labels[ids.index(btype)])
            else:
                self._insp_type.set(labels[0])
            self._insp_value_var.set(act.get("action", ""))
            if hasattr(self, "_insp_hint_var"):
                hints = _load_displaypad_page_hints(self._current_page)["keys"]
                self._insp_hint_var.set(hints.get(str(idx), ""))
                # Whose text this is. Focus can leave the field after another
                # key or page has been chosen, and the save must not go there.
                self._insp_hint_owner = (self._current_page, idx)
            self._apply_inspector_type(btype, act)
        finally:
            self._insp_loading = False

    def _apply_inspector_type(self, btype, act=None):
        """Show the input that fits the chosen type: a page picker for page
        actions, a browse button for folders and apps, a plain field for the
        rest."""
        act = act if act is not None else self._get_action_dict(self._selected_key)
        self._insp_value.pack_forget()
        self._insp_page.pack_forget()
        self._insp_browse.pack_forget()
        if btype == "none":
            self._insp_value_lbl.pack_forget()
            return
        self._insp_value_lbl.pack(fill="x", padx=12, after=self._insp_type)
        self._insp_value_lbl.configure(
            text=self.T("dp_insp_target") if btype == "page"
            else self.T("dp_insp_value"))
        if btype == "page":
            # A key cannot usefully navigate to the page it is already on, so
            # the current page is not offered. With no other page yet, the
            # only sensible choice is to make one.
            names = _load_displaypad_page_names()
            here = names.get(self._current_page)
            options = sorted(n for pid, n in names.items() if pid != self._current_page)
            options.append(self.T("dp_new_page"))
            cur = act.get("target") if isinstance(act.get("target"), str) else None
            self._insp_page.configure(values=options)
            self._insp_page.set(cur if cur in options and cur != here else options[0])
            self._insp_page.pack(padx=12, pady=(2, 4), after=self._insp_value_lbl)
        else:
            self._insp_value.pack(padx=12, pady=(2, 4), after=self._insp_value_lbl)
            if btype in ("folder", "app"):
                self._insp_browse.pack(padx=12, pady=(0, 4), after=self._insp_value)

    def _on_insp_type_change(self, label):
        labels = action_type_labels(self._app)
        ids = action_type_ids(self._app)
        try:
            btype = ids[labels.index(label)]
        except ValueError:
            return
        if btype == "_separator":
            self._load_inspector()
            return
        # A value belongs to the type it was typed for: "F6" means nothing once
        # the key becomes a clock, and the old value was being saved under the
        # new type rather than just displayed (#84). The actions dialog has
        # cleared it since #9; this is the same rule for the inspector.
        if btype != self._get_action_dict(self._selected_key).get("type", "none"):
            self._insp_value_var.set("")
        self._apply_inspector_type(btype)
        self._save_inspector(btype)

    def _save_inspector(self, btype=None):
        if getattr(self, "_insp_loading", False):
            return
        idx = self._selected_key
        if btype is None:
            labels = action_type_labels(self._app)
            ids = action_type_ids(self._app)
            try:
                btype = ids[labels.index(self._insp_type.get())]
            except ValueError:
                return
        if btype == "_separator":
            return
        target = None
        value = self._insp_value_var.get()
        if btype == "page":
            value, target = _acts.inspector_page_choice(
                self._insp_page.get(), self.T("dp_new_page"),
                self.T("dp_page_main"), self._page_id_by_name)
        self._save_page_action(self._current_page, idx, btype, value, target=target)
        self._refresh_panel_tile(idx)
        self._rebuild_page_tabs()

    def _insp_do_browse(self):
        labels = action_type_labels(self._app)
        ids = action_type_ids(self._app)
        try:
            btype = ids[labels.index(self._insp_type.get())]
        except ValueError:
            return
        if btype == "folder":
            path = native_open_folder(title=self._app.T("ui_pick_folder"))
            if path:
                self._insp_value_var.set(path)
                self._save_inspector(btype)
        elif btype == "app":
            self._open_app_picker()

    def _insp_pick_image(self):
        """Pick an image for the selected key.

        The picker returns (source path, gif frame, library file name), not a
        path. Writing the whole triple into the image map put a list where
        every reader expects a string, which the next start then tripped over
        while loading GIF frames.
        """
        from shared.config import _save_to_dp_library, DISPLAYPAD_LIBRARY_DIR
        result = pick_dp_library_image(self, self._app)
        if not result:
            return
        src_path, gif_frame, thumb_fname = result
        if thumb_fname is None and src_path:
            _save_to_dp_library(src_path, gif_frame)
        path = (os.path.join(DISPLAYPAD_LIBRARY_DIR, thumb_fname)
                if thumb_fname else src_path)
        if not path:
            return
        self._set_button_image(self._selected_key, path)
        self._persist_images()
        self._refresh_panel_tile(self._selected_key)
        self._start_upload()

    # ── Page tabs ─────────────────────────────────────────────────────────────

    def _rebuild_page_tabs(self):
        """One tab per page, in id order, plus the button that adds one."""
        if not hasattr(self, "_pagebar"):
            return
        for w in list(self._page_tabs.values()):
            w.destroy()
        self._page_tabs.clear()
        for w in self._pagebar.winfo_children():
            if getattr(w, "_is_tab_extra", False):
                w.destroy()
        names = _load_displaypad_page_names()
        order = ([0] if 0 in names else []) + sorted(
            (p for p in names if p != 0), key=lambda p: names[p].casefold())
        for pid in order:
            active = pid == self._current_page
            btn = ctk.CTkButton(
                self._pagebar, text=names[pid], font=(UI.FONT_FAMILY, 11,
                                                      "bold" if active else "normal"),
                fg_color=BG2 if active else "transparent",
                hover_color=BG2, text_color=FG if active else FG2,
                border_width=1, border_color=BORDER if active else BG,
                height=UI.CTRL_H_SM, corner_radius=5,
                width=max(64, len(names[pid]) * 8 + 20),
                command=lambda p=pid: self._switch_to_page(p))
            btn.pack(side="left", padx=(0, 4))
            self._page_tabs[pid] = btn
        add = ctk.CTkButton(
            self._pagebar, text=self.T("dp_new_page"), font=(UI.FONT_FAMILY, 11),
            fg_color="transparent", hover_color=BG2, text_color=FG2,
            height=UI.CTRL_H_SM, corner_radius=5, width=90,
            command=self._on_new_page)
        add._is_tab_extra = True
        add.pack(side="left")
        # The page-settings row below the keys follows the tabs (#71): every
        # path that changes or renames a page already comes through here.
        self._refresh_page_settings()

    def _on_new_page(self):
        name = _prompt_page_name(self._app, "dp_page_name_prompt", "dp_page_name_title")
        if not name:
            return
        pid = self._svc.add_page(name, back_to=self._current_page, origin=self._ORIGIN)
        self._rebuild_page_tabs()
        self._switch_to_page(pid)

    def apply_fullscreen_image(self, path, say=None, on_tile=None):
        """Split one image or GIF across all twelve keys of the current page
        (DisplayPadService.apply_fullscreen).

        Shared by the button under the keys and by the assign-images window, so
        the two cannot end up splitting an image differently. `say` reports
        progress, `on_tile` is called per key for anything that draws its own
        preview. Returns True if something was applied.
        """
        colours = {"info": FG2, "ok": GRN, "warn": YLW, "error": RED}

        def _say(code, level, **kw):
            if say:
                say(self.T(code, **kw), colours.get(level, FG2))
            if code == "dp_gif_splitting":
                self.update_idletasks()

        gif_shown = []

        def _tile(idx):
            if self._fullscreen_group:
                # A fullscreen GIF: its previews, cut once for all twelve.
                if not gif_shown:
                    gif_shown.append(True)
                    self._rebuild_gui_frames(self._current_page)
                    if self._gui_frames_sm and self._gui_tick_id is None \
                            and self._tile_lbls:
                        self._gui_tick_id = self.after(50, self._gui_tick)
            else:
                self._gui_frames_sm.pop(idx, None)
                self._gui_fidx.pop(idx, None)
                self._gui_next.pop(idx, None)
            if self._tile_lbls:
                self._refresh_panel_tile(idx)
            if on_tile:
                on_tile(idx)

        return self._svc.apply_fullscreen(path, say=_say, on_tile=_tile)

    def _pick_fullscreen(self):
        """Fullscreen straight from the screen: pick a file, split it, done.

        It used to be reachable only by opening the assign-images window first,
        which is a detour past twelve key slots you did not want to touch (#78).
        """
        if self._uploading or self._animating:
            return
        result = pick_dp_fullscreen_image(self, self._app)
        if not result:
            return
        src_path, _gif_frame, thumb_fname = result
        if thumb_fname:
            from shared.config import DISPLAYPAD_FS_LIBRARY_DIR
            path = os.path.join(DISPLAYPAD_FS_LIBRARY_DIR, thumb_fname)
        else:
            path = src_path
        if not path:
            return
        self.apply_fullscreen_image(
            path,
            say=lambda text, colour: self._info_label.configure(
                text=text, text_color=colour),
            on_tile=self._refresh_panel_tile)
        self.after(200, self._start_upload)

    def _on_rename_current_page(self):
        """Rename the page on the device (#71). Main is renamable too, it is
        just the page the app opens on."""
        name = _prompt_page_name(
            self._app, "dp_page_name_prompt", "dp_rename_page_title",
            initial=self._get_page_name(self._current_page))
        if not name:
            return
        self._rename_page(self._current_page, name)
        self._rebuild_page_tabs()
        self._refresh_page_settings()

    def _on_delete_current_page(self):
        """Delete the page on the device (#71), after warning if anything still
        points at it. Falls back to Main, since the page shown is gone."""
        page_id = self._current_page
        if not _confirm_delete_page(self._app, self, page_id):
            return
        self._delete_page(page_id)
        self._rebuild_page_tabs()
        if self._current_page == page_id:
            self._switch_to_page(0)
        else:
            self._refresh_page_settings()

    def _refresh_page_settings(self):
        """Point the page-settings row at the page currently on the device."""
        if not hasattr(self, "_page_name_lbl"):
            return
        try:
            self._page_name_lbl.configure(
                text=self._get_page_name(self._current_page))
            self._page_timeout_row.load()
        except tk.TclError:
            pass

    def header_actions(self, parent):
        """Fill the screen header: device controls left of the primary action.

        The shell calls this every time the screen is shown and clears the
        strip when leaving, so the widgets are rebuilt rather than kept alive
        somewhere invisible.
        """
        UI.PrimaryButton(parent, self.T("dp_header_upload"),
                         self._start_upload, width=110,
                         height=UI.CTRL_H_SM).pack(side="right", padx=(UI.S2, 0))
        UI.GhostButton(parent, self.T("dp_header_assign"),
                       self._open_dialog, width=130,
                       height=UI.CTRL_H_SM).pack(side="right", padx=(UI.S2, 0))

        self._rot_menu = ctk.CTkOptionMenu(
            parent, values=["0°", "90°", "180°", "270°"],
            fg_color=BG2, button_color=BG3, button_hover_color=BORDER,
            text_color=FG, font=(UI.FONT_FAMILY, 10), width=64,
            height=UI.CTRL_H_SM, command=self._on_rotation_change)
        self._rot_menu.set(f"{self._rotation}°")
        self._rot_menu.pack(side="right", padx=(UI.S2, 0))

        self._bri_menu = ctk.CTkOptionMenu(
            parent, values=["0%", "25%", "50%", "75%", "100%"],
            fg_color=BG2, button_color=BG3, button_hover_color=BORDER,
            text_color=FG, font=(UI.FONT_FAMILY, 10), width=76,
            height=UI.CTRL_H_SM, command=self._on_brightness_change)
        self._bri_menu.set(f"{self._brightness}%")
        self._bri_menu.pack(side="right", padx=(UI.S2, 0))
        ctk.CTkLabel(parent, text=self.T("dp_brightness_label"),
                     font=(UI.FONT_FAMILY, 10), text_color=FG2).pack(side="right")

        self._deb_menu = ctk.CTkOptionMenu(
            parent, values=["0.2s", "0.4s", "0.6s", "0.8s", "1.0s"],
            fg_color=BG2, button_color=BG3, button_hover_color=BORDER,
            text_color=FG, font=(UI.FONT_FAMILY, 10), width=76,
            height=UI.CTRL_H_SM, command=self._on_debounce_change)
        self._deb_menu.set(f"{self._debounce}s")
        self._deb_menu.pack(side="right", padx=(UI.S2, 0))
        ctk.CTkLabel(parent, text=self.T("dp_debounce_label"),
                     font=(UI.FONT_FAMILY, 10), text_color=FG2).pack(side="right")

    def refresh(self):
        """Shown again: the page tabs and the inspector may be stale after a
        page switch triggered from a key press or from dp_page."""
        self._rebuild_page_tabs()
        self._select_key(self._selected_key)

    def _on_brightness_change(self, val):
        self._svc.set_brightness(int(val.replace("%", "")))

    def _on_debounce_change(self, val):
        self._svc.set_debounce(float(val.replace("s", "")))

    def _on_min_ms_change(self, *_):
        """Keep the GIF frame floor across restarts (#73). Silently ignores
        half-typed values -- this fires on every keystroke."""
        self._svc.set_min_frame_ms(self._min_ms_var.get())

    def _on_rotation_change(self, val):
        # Stored, every key without a picture given the blank, and the page
        # sent to the pad again, turned.
        self._svc.set_rotation(int(val.replace("°", "")))
        # Refresh all preview tiles with new rotation
        for idx in range(NUM_KEYS):
            self._refresh_panel_tile(idx)

    def _open_actions_dialog(self):
        if hasattr(self, "_actions_dialog_win") and \
                self._actions_dialog_win is not None and \
                self._actions_dialog_win.winfo_exists():
            self._actions_dialog_win.focus()
            return
        self._actions_dialog_win = DisplayPadActionsDialog(self)

    # ── The pages (core/devices/displaypad/pages.py) ──────────────────────────

    def _page_target(self, act, idx):
        return self._model.page_target(act, idx)

    def _all_page_ids(self):
        return self._model.all_page_ids()

    def _mint_page_id(self):
        return self._model.mint_page_id()

    def _folder_icon_name(self, page, idx):
        return self._model.folder_icon_name(page, idx)

    def _folder_icon_drawn(self, page, idx):
        return self._model.folder_icon_drawn(page, idx)

    def _migrate_generated_icon_names(self):
        self._model.migrate_generated_icon_names()

    def _inject_page_icons(self, page):
        self._model.inject_page_icons(page)

    def _ensure_page(self, page_id, back_to=0):
        self._model.ensure_page(page_id, back_to=back_to)

    def _gc_orphan_pages(self):
        self._model.gc_orphan_pages()

    def _is_nav_icon(self, path):
        return self._model.is_nav_icon(path)

    def _save_page_action(self, page, idx, btype, action, extra=None,
                          target=None, icon=None, double=None):
        """Save one button's action (DisplayPadService.save_page_action: the
        model, an upload of the page on the pad, its widget services), then
        show it: the key's tile, and the editor that did not write it."""
        res = self._svc.save_page_action(page, idx, btype, action, extra,
                                         target=target, icon=icon, double=double,
                                         origin=self._ORIGIN)
        if res.label_drawn and self._tile_lbls:
            # A label drawn for a key of a sub-page on the pad shows at once.
            self._refresh_panel_tile(idx)
        if res.current and btype == "page":
            self._gui_frames_sm.pop(idx, None)
        if res.current and self._tile_lbls:
            self._refresh_panel_tile(idx)
        self._sync_editors(page)

    def _schedule_service_sync(self):
        """The page's widget services brought in line, shortly (#97)."""
        self._svc.schedule_service_sync()

    def _sync_page_services(self):
        self._svc.sync_page_services()

    def _sync_editors(self, page):
        """Show the stored action in the editor that did not just write it.

        The key inspector beside the grid and the actions dialog are two views
        of the same action and both save through _save_page_action, so neither
        noticed the other's edits and whichever you looked at second was wrong
        (#84). This is the one place both go through, so it is the one place
        that can keep them agreeing.
        """
        if page != self._current_page or getattr(self, "_syncing_editors", False):
            return
        self._syncing_editors = True
        try:
            dlg = getattr(self, "_actions_dialog_win", None)
            try:
                # Not while that dialog is the one saving: it does not need
                # telling what it just wrote, and re-reading itself would
                # revert every other row that has been typed into and not
                # written yet (#87).
                if dlg is not None and dlg.winfo_exists() \
                        and not getattr(dlg, "_saving", False):
                    dlg._load_page(dlg._page)
            except Exception:
                pass
            try:
                if self._tile_lbls and self._selected_key is not None:
                    self._load_inspector()
            except Exception:
                pass
        finally:
            self._syncing_editors = False

    def _redraw_double_captions(self):
        self._model.redraw_double_captions()

    # Plugin frames are not key icons: see PageModel (#69/#70, #104).

    def _mark_plugin_frame(self, idx):
        self._model.mark_plugin_frame(idx)

    def _take_key_back(self, page, idx):
        self._model.take_key_back(page, idx)

    def _stored_icons(self, page):
        return self._model.stored_icons(page)

    def _icons_restored(self, page, live, keep=()):
        return self._model.icons_restored(page, live, keep)

    def _persistable_images(self, page, live=None):
        return self._model.persistable_images(page, live)

    def _images_for_page(self, page):
        return self._model.images_for_page(page)

    def _sync_live_images(self, page=None):
        self._model.sync_live_images(page)

    def _save_sub_pages(self):
        self._model.save_sub_pages()

    def _switch_to_page(self, page_num, _retry=0, show_hints=False):
        """Switch the pad to another page (DisplayPadService.switch_page).
        What the switch looks like here follows from its page_switched
        event, whoever made it: a tab, a key on the pad, a timeout, dp_page.

        `show_hints` is for a switch somebody made, a key on the pad, the tray
        or a script, as opposed to one the application made (a timeout, the
        editor following its tabs). Only those bring up the page's box (#93).
        """
        self._svc.switch_page(page_num, show_hints=show_hints, _retry=_retry)

    def _on_page_switched(self, page, show_hints):
        """The pad is on another page: tiles, previews, tabs, inspector."""
        self._rebuild_gui_frames(self._current_page)
        for idx in range(NUM_KEYS):
            self._refresh_panel_tile(idx)
        if self._gui_frames_sm and self._gui_tick_id is None:
            self._gui_tick_id = self.after(50, self._gui_tick)

        # Update page indicator
        if hasattr(self, "_page_lbl"):
            name = self._get_page_name(page)
            self._page_lbl.configure(text=f"{self.T('dp_page_label')} {name}")
        # The tabs are the page indicator now, and the inspector has to follow
        # the new page: a switch can come from a key press or from dp_page,
        # not just from clicking a tab.
        self._rebuild_page_tabs()
        self._select_key(self._selected_key)

        self._info_label.configure(
            text=self.T("dp_page_switching", p=page if page else self.T("dp_page_main")),
            text_color=FG2)
        if not show_hints and self._hint_box is not None and self._hint_box.page != page:
            # The application switched by itself: the box describes keys that
            # are no longer under your hand.
            self._hint_box.close()

    # ── The page's box: its note and its keys' hints (#93) ─────────────────────

    def _show_page_hints(self, page):
        """Bring up `page`'s box, if it has anything to say and is wanted
        (decided by the service, shown from its hints_show event)."""
        self._svc.show_hints(page)

    def _hint_box_for(self, p):
        if self._hint_box is not None:
            self._hint_box.close()
        hints = {"note": p.get("note", ""), "keys": p.get("keys", {}),
                 "show": p.get("show", True)}
        try:
            self._hint_box = PageHintBox(self, p["page"], p["name"], hints,
                                         _load_hint_box_settings())
        except tk.TclError as e:
            print(f"[DisplayPad] page box: {e}", flush=True)
            self._hint_box = None

    def _open_page_note(self):
        PageNoteDialog(self, self._current_page)

    def _save_key_hint(self, _event=None):
        owner = getattr(self, "_insp_hint_owner", None)
        if getattr(self, "_insp_loading", False) or owner is None:
            return
        page, idx = owner
        self._svc.set_key_hint(page, idx, self._insp_hint_var.get())

    def _get_available_pages(self):
        return self._model.available_pages()

    def _get_page_name(self, p):
        """The page's name, stored first if it has none yet: the tabs, the
        pickers and dp_page read the registry itself, so a page shown by
        name has to be in it (PageModel.ensure_page_name)."""
        return self._model.ensure_page_name(p)

    def _page_id_by_name(self, name):
        return self._model.page_id_by_name(name)

    def _unique_page_name(self, name, exclude_id=None):
        return self._model.unique_page_name(name, exclude_id)

    def _create_named_page(self, name):
        return self._svc.create_page(name, origin=self._ORIGIN)

    def _rename_page(self, page_id, name):
        self._svc.rename_page(page_id, name, origin=self._ORIGIN)

    def _find_page_references(self, page_id):
        return self._model.find_page_references(page_id)

    def _delete_page(self, page_id):
        """Remove a page, leaving it first if it is the one on the pad, and
        close its box (PageModel.delete_page)."""
        page_id = int(page_id)
        if self._hint_box is not None and self._hint_box.page == page_id:
            self._hint_box.close()
        return self._svc.delete_page(page_id, origin=self._ORIGIN)

    def _get_extra_actions(self, idx):
        return self._model.get_extra_actions(idx)

    # ── Running actions (core/devices/displaypad/actions.py ActionRunner) ─────

    def _execute_action_k(self, idx):
        self._runner.key_pressed(idx)

    def _dc_fire_single(self, idx):
        self._runner._dc_fire_single(idx)

    def _fire_action_chain(self, idx):
        self._runner.fire_chain(idx)

    def _get_double_action(self, idx):
        return self._model.get_double_action(idx)

    def _is_double_key(self, k):
        return self._model.is_double_key(k)

    # ── Per-page auto-timeout (issue #45) ─────────────────────────────────────
    def _arm_page_timeout(self, page):
        self._runner.arm_page_timeout(page)

    def _fire_page_timeout(self, page):
        self._runner._fire_page_timeout(page)

    def _note_keypress(self):
        self._runner.note_keypress()

    def _get_action_dict(self, idx, page=None):
        """Full action dict for button idx on `page` (current page if None)."""
        return self._model.get_action_dict(idx, page)

    def _run_one_action(self, btype, action, idx=0, step=None):
        self._runner.run_one(btype, action, idx, step)

    def _apply_set_key_action(self, action_json, host_idx):
        """Redefine a key in response to a 'set_key' action (issue #18);
        shown here from the service's key_changed event."""
        self._svc.apply_set_key(action_json, host_idx)

    # NOTE: key-event listening used to be a separate persistent thread here
    # (_key_event_loop) that opened and closed interface 3 every time it
    # traded places with an upload/animation/plugin session. That's now
    # folded into _plugin_upload_worker below: reading key events is just
    # what that thread does while idle, inside the same open session as
    # plugin pushes, so interface 3 no longer gets closed and reopened for
    # the handoff between "listening" and "pushing a plugin frame".

    def apply_lang(self):
        """Called by App when the language changes.

        Only widgets this panel still owns: the buttons that opened the two
        old windows are gone, and configuring them here raised AttributeError
        the moment somebody switched language. The tabs and the inspector are
        rebuilt instead, which picks up the new strings wholesale.
        """
        self._heading_lbl.configure(text=self.T("dp_title"))
        self._page_timeout_row.apply_lang()
        self._rebuild_page_tabs()
        self._select_key(self._selected_key)

    # ── Dialog ────────────────────────────────────────────────────────────────

    def _open_dialog(self):
        if self._dialog_win is not None and self._dialog_win.winfo_exists():
            self._dialog_win.focus()
            return
        self._dialog_win = DisplayPadImageDialog(self)

    # ── Tile management ───────────────────────────────────────────────────────

    def _set_button_image(self, key_index, path):
        """Give a key a picture or a GIF (DisplayPadService.set_button_image
        stores and sends it), and show it."""
        frames = self._svc.set_button_image(key_index, path)
        if frames:
            gui_f = _load_gif_display_frames(path, _PANEL_TILE)
            if gui_f:
                self._gui_frames_sm[key_index] = gui_f
                self._gui_fidx[key_index] = 0
                self._gui_next[key_index] = time.monotonic()
                if self._gui_tick_id is None:
                    self._gui_tick_id = self.after(50, self._gui_tick)
        else:
            self._gui_frames_sm.pop(key_index, None)
            self._gui_fidx.pop(key_index, None)
            self._gui_next.pop(key_index, None)
        self._refresh_panel_tile(key_index)

    def _note_plugin_tile(self, idx):
        """Redraw a key's tile when a widget's frame changes what it shows.

        The grid draws whatever `_images` holds. A plugin writes its frame in
        there itself, but nothing ever told the grid, so the tile kept showing
        the icon stored for that key while the pad showed the widget. After a
        restart that is the stored icon, which for a key that was cleared
        before the widget was assigned is the blank one, and it stayed blank
        in the editor for as long as nothing else happened to redraw it (#90).

        A change of path is drawn at once. The same path is drawn again at
        most a few times a second: a clock or a system monitor rewrites one
        file name every second and only its content changes, so waiting for
        the path to change meant the editor kept the first frame it ever drew
        while the pad showed the current one, until something unrelated
        happened to redraw that tile (#96). The floor is what keeps a video
        pushing thirty frames a second from costing thirty redraws.
        """
        # No path yet is no reason to skip it. A widget may file its frame
        # under the key only after pushing it, and the pass below reads the
        # map when it runs, not now: skipping here lost that first frame, and
        # a widget whose value then held still left a grey tile for good
        # (#107).
        path = self._images.get(str(idx))
        now = time.monotonic()
        if path and self._tile_shown.get(idx) == path and \
                now - self._tile_drawn.get(idx, 0.0) < _TILE_REDRAW_MIN:
            return
        self._tile_shown[idx] = path
        self._tile_drawn[idx] = now
        # Noted, not drawn: a page of widgets pushes a dozen frames a second
        # between them, and one scheduled redraw each would put a dozen file
        # reads and resizes a second on the interface thread. One pass draws
        # whatever has piled up.
        with self._tile_lock:
            self._tile_dirty.add(idx)
            if self._tile_pass_due:
                return
            # Marked before the call, not after it. Tk runs the callback on
            # its own thread, and it can have run and cleared this again
            # before after() has even returned here: storing the answer
            # afterwards would leave a mark for a pass that is already over,
            # and no pass would ever be asked for again.
            self._tile_pass_due = True
        try:
            self.after(0, self._draw_dirty_tiles)
        except Exception:
            # The window is going away; a tile nobody will see is no loss.
            with self._tile_lock:
                self._tile_pass_due = False
                self._tile_dirty.discard(idx)

    def _draw_dirty_tiles(self):
        """Redraw every key noted since the last pass."""
        with self._tile_lock:
            self._tile_pass_due = False
            dirty, self._tile_dirty = self._tile_dirty, set()
        owned = self._plugin_key_slots()
        for idx in dirty:
            self._drop_foreign_frame(idx, owned)
        if not self._grid_on_screen():
            # A clock with seconds redrew its tile every second, reading and
            # scaling a file each time, with the window in the tray (#105).
            # What changed is remembered, and drawn once when it shows.
            with self._tile_lock:
                self._tile_dirty |= dirty
            self._tiles_waiting = True
            return
        for idx in sorted(dirty):
            try:
                self._refresh_panel_tile(idx)
            except Exception:
                pass

    def _grid_on_screen(self):
        """Whether the key grid can be seen: this screen is the one shown and
        the window is neither in the tray nor minimised."""
        try:
            return bool(self.winfo_viewable())
        except Exception:
            return False

    def _on_grid_mapped(self, _event=None):
        if not self._tiles_waiting:
            return
        self._tiles_waiting = False
        with self._tile_lock:
            if self._tile_pass_due or not self._tile_dirty:
                return
            self._tile_pass_due = True
        try:
            self.after(0, self._draw_dirty_tiles)
        except Exception:
            with self._tile_lock:
                self._tile_pass_due = False

    def _drop_foreign_frame(self, idx, owned=None):
        """Give key `idx` its icon back if the live map holds a widget frame
        that is not this key's on this page (#99, #112). Returns whether
        anything was changed."""
        return self._svc.drop_foreign_frame(idx, owned)

    def _refresh_panel_tile(self, idx):
        rot = self._rotation
        is_gif = idx in self._gif_frames
        if is_gif and idx in self._gui_frames_sm:
            img = self._gui_frames_sm[idx][0][0]
            # GIF preview frames aren't rotated (too expensive), skip
        else:
            path = self._images.get(str(idx))
            if path and os.path.exists(path):
                img = (_make_gif_thumb(path, _PANEL_TILE, rot) if is_gif
                       else _make_thumb(path, _PANEL_TILE, rot))
            else:
                img = self._make_action_tile(idx)
        self._tile_imgs[idx] = img
        self._tile_lbls[idx].configure(image=img)

    def _make_action_tile(self, idx):
        """Create a tile preview showing the action type icon if a plugin action
        is assigned, otherwise show the default placeholder."""
        from shared.config import _load_displaypad_actions
        try:
            actions = _load_displaypad_actions()
            act = actions[idx] if idx < len(actions) else {}
        except Exception:
            act = {}
        btype = act.get("type", "none")

        # Check if it's a plugin action type
        pm = getattr(self._app, "_plugin_manager", None)
        if pm and btype in pm.get_action_type_ids():
            # Get the label for this plugin action
            label = btype
            for tid, lbl in pm.get_action_type_labels():
                if tid == btype:
                    label = lbl
                    break
            return self._render_plugin_tile(label)

        return _make_placeholder(_PANEL_TILE)

    def _render_plugin_tile(self, label):
        """Render a small tile with a plugin icon and label."""
        return _ctk_image(_img.plugin_tile_image(label, _PANEL_TILE), _PANEL_TILE)

    def _load_fullscreen_gif(self, path, save=True):
        """Split fullscreen GIF into 12 tile frame lists, populate state.
        Fullscreen covers every button — page/back actions still fire on
        key press but visually the whole panel is occupied by the GIF."""
        gui_tiles = _split_gif_display_tiles(path, _PANEL_TILE)
        if not self._model.load_fullscreen_gif(path, save=save):
            return False
        for idx in range(NUM_KEYS):
            if gui_tiles:
                self._gui_frames_sm[idx] = gui_tiles[idx]
                self._gui_fidx[idx]  = 0
                self._gui_next[idx]  = time.monotonic()
            if self._tile_lbls:
                self._refresh_panel_tile(idx)
        if gui_tiles and self._gui_tick_id is None and self._tile_lbls:
            self._gui_tick_id = self.after(50, self._gui_tick)
        return True

    def _persist_images(self):
        """Write the current page's images where that page keeps them."""
        self._model.persist_images()

    def _clear_slot(self, idx):
        """Blank one key: image back to the blank icon, action to none."""
        if idx is None:
            return
        self._model.clear_key_image(idx)
        self._save_page_action(self._current_page, idx, "none", "")
        self._refresh_panel_tile(idx)
        # The key you just cleared is the one you are working on, so the
        # inspector moves to it. It used to stay on whichever key was selected
        # before, which read as the clear having hit the wrong key (#98).
        self._select_key(idx)
        self._start_upload()

    def _open_app_picker(self):
        """Pick an installed application for an 'app' action."""
        from shared.ui_helpers import parse_desktop_apps
        apps = parse_desktop_apps()
        if not apps:
            return
        names = sorted(apps.keys())
        dlg = ctk.CTkToplevel(self._app)
        dlg.title(self._app.T("app_picker_title"))
        dlg.geometry("420x460")
        dlg.transient(self._app)
        box = ctk.CTkScrollableFrame(dlg, fg_color=BG)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        cap_scroll_speed(box)

        def _take(name):
            self._insp_value_var.set(apps[name])
            self._save_inspector("app")
            dlg.destroy()

        for name in names:
            ctk.CTkButton(box, text=name, anchor="w", height=28,
                          fg_color=BG2, hover_color=BG3, text_color=FG,
                          font=(UI.FONT_FAMILY, 11),
                          command=lambda n=name: _take(n)).pack(fill="x", pady=1)
        dlg.after(20, dlg.grab_set)

    def _clear_all(self):
        if self._uploading:
            return
        if self._animating:
            self._stop_animation()
            self.after(500, self._clear_all)
            return
        # Every key blank and every action reset, except the page keys of
        # the main page, so navigation between pages still works; stored,
        # the widget services brought in line and the blanks sent
        # (DisplayPadService.clear_page_now).
        self._svc.clear_page_now(origin=self._ORIGIN)
        self._on_page_cleared()

    def _on_page_cleared(self):
        self._gui_frames_sm = {}
        self._gui_fidx = {}
        self._gui_next = {}
        for idx in range(NUM_KEYS):
            self._refresh_panel_tile(idx)
        self._info_label.configure(text=self.T("dp_all_cleared"), text_color=FG2)

    # ── GUI preview animation ─────────────────────────────────────────────────

    def _gui_tick(self):
        if not self._gui_frames_sm:
            self._gui_tick_id = None
            return
        if not self._grid_on_screen():
            # The GIF previews animate thirty times a second for nobody while
            # the grid cannot be seen (#105). Look in again now and then.
            self._gui_tick_id = self.after(500, self._gui_tick)
            return
        now = time.monotonic()
        for k, frames in self._gui_frames_sm.items():
            if now >= self._gui_next.get(k, 0):
                idx = (self._gui_fidx.get(k, 0) + 1) % len(frames)
                self._gui_fidx[k] = idx
                img, dur = frames[idx]
                self._tile_imgs[k] = img
                if k in self._tile_lbls:
                    self._tile_lbls[k].configure(image=img)
                self._gui_next[k] = now + dur / 1000.0
                # Notify open dialog to update its tile too
                if self._dialog_win and self._dialog_win.winfo_exists():
                    self._dialog_win.notify_frame(k, idx)
        self._gui_tick_id = self.after(33, self._gui_tick)  # ~30 fps check rate

    # ── Upload / Animation (core/devices/displaypad/service.py) ───────────────

    def _start_upload(self, _retry=0):
        self._svc.start_upload(_retry)

    def _stop_animation(self):
        self._svc.stop_animation()

    def _maybe_warn_usb_quirk(self):
        """The usbhid interface-order quirk check (#36). The service's device
        monitor calls it through here, so it can be replaced on this class."""
        self._svc.maybe_warn_usb_quirk()

    # ── Plugin API ────────────────────────────────────────────────────────────

    def push_plugin_image(self, key_index, pil_image):
        """Upload a 102x102 PIL Image to a DisplayPad button. Thread-safe.
        Called by plugins to display live widgets on buttons.
        pil_image: PIL Image (any size, will be resized to 102x102).
        key_index: 0-11 (K1-K12).

        The tile is noted here, at once, as it always was; the frame goes to
        the service, which keeps it out of the stored icons (#69) and queues
        it for the device worker.
        """
        if not (0 <= key_index <= 11):
            return
        self._note_plugin_tile(key_index)
        self._svc.push_plugin_image(key_index, pil_image)

    def _plugin_key_slots(self):
        """Key indexes on the live page that a plugin action type owns, or
        None when that cannot be told (PageModel.plugin_key_slots)."""
        return self._model.plugin_key_slots()

    # ── What the service says ─────────────────────────────────────────────────

    def _say(self, text, colour=FG2):
        self._info_label.configure(text=text, text_color=colour)

    def _on_core_event(self, topic, p):
        """An event of the DisplayPad service, on the interface thread."""
        what = topic.rsplit(".", 1)[-1]
        if what == "key_frame":
            # Mostly noted already by push_plugin_image; a frame that came
            # to the service another way is drawn all the same, and the
            # note's own floor keeps one frame from being drawn twice.
            self._note_plugin_tile(int(p.get("idx", 0)))
            return
        if not hasattr(self, "_info_label"):
            return
        mine = p.get("origin") == self._ORIGIN
        if what == "page_switched":
            self._on_page_switched(p["page"], p.get("show_hints", False))
        elif what == "hints_show":
            self._hint_box_for(p)
        elif what == "hints_hide":
            if self._hint_box is not None:
                self._hint_box.close()
        elif what == "key_changed":
            if mine:
                return          # shown already, by _save_page_action
            page, idx = p["page"], p["idx"]
            if page == self._current_page:
                if idx not in self._gif_frames:
                    self._gui_frames_sm.pop(idx, None)
                if self._tile_lbls:
                    self._refresh_panel_tile(idx)
            self._sync_editors(page)
        elif what == "pages_changed":
            if not mine:
                self._rebuild_page_tabs()
        elif what == "page_deleted":
            if self._hint_box is not None and self._hint_box.page == p["page"]:
                self._hint_box.close()
        elif what == "page_cleared":
            if not mine and p["page"] == self._current_page:
                self._on_page_cleared()
        elif what == "upload_started":
            self._say(self.T("dp_connecting_pad"))
        elif what == "upload_progress":
            self._say(self.T("dp_uploading_key", k=p["key"] + 1, n=p["n"],
                             total=p["total"]))
        elif what == "animating":
            self._say(self.T("dp_animating", n=p["count"]), GRN)
        elif what == "upload_finished":
            # A result is news, not state: it goes as a toast and leaves the
            # layout alone instead of parking the last message under the grid
            # for the rest of the session.
            self._say("")
            if p.get("ok"):
                self._app.toast(self.T("dp_done"), kind="ok", ms=2000)
            else:
                self._app.toast(self.T("dp_error", err=p.get("error", "")),
                                kind="bad", ms=6000)
        elif what == "upload_empty":
            self._say(self.T("dp_no_images"), YLW)
        elif what == "device_connected":
            if p.get("has_content"):
                self._say(self.T("dp_reconnected"))
        elif what == "device_disconnected":
            if not p.get("busy"):
                self._say(self.T("dp_disconnected"))
        elif what == "quirk":
            self._say(self.T("dp_quirk_hint"), YLW)
