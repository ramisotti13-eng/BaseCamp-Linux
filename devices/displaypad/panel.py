"""Mountain DisplayPad device panel for BaseCamp Linux hub.

Protocol (reverse-engineered from JeLuF/mountain-displaypad + MountainDisplayPadWorker.exe):
  VID=0x3282, PID=0x0009
  Interface 1 — display writes (raw Interrupt OUT EP 0x02, 512 B max-packet)
  Interface 3 — commands + key events (hidraw, EP 0x04 OUT / EP 0x83 IN)
  Button images: 102×102 BGR, 31 chunks × 1024 bytes (no report-ID prefix)
  Key events: data[42] bits (keys 1–7), data[47] bits (keys 8–12)
"""
import gc
import os
import sys
import pwd as _pwd
import time
import queue
import threading
import subprocess
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw

from shared.ui_helpers import (
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER,
    native_open_image, native_open_folder, parse_desktop_apps,
    pick_dp_library_image, pick_dp_fullscreen_image,
)
from shared.config import (
    CONFIG_DIR,
    _load_displaypad_buttons, _save_displaypad_buttons,
    _load_displaypad_fullscreen, _save_displaypad_fullscreen, _clear_displaypad_fullscreen,
    _load_displaypad_actions, _save_displaypad_actions,
    _load_displaypad_pages, _save_displaypad_pages,
    _load_displaypad_rotation, _save_displaypad_rotation,
)

try:
    import hid
    HID_AVAILABLE = True
except ImportError:
    HID_AVAILABLE = False

try:
    import usb.core
    import usb.util
    PYUSB_AVAILABLE = True
except ImportError:
    PYUSB_AVAILABLE = False

# ── Protocol constants ────────────────────────────────────────────────────────

VID  = 0x3282
PID  = 0x0009

NUM_KEYS      = 12
KEYS_PER_ROW  = 6
ICON_SIZE     = 102
CHUNK_SIZE    = 1024
HEADER_SIZE   = 306
PACKET_SIZE   = 31438   # total payload = 31744 = 31 × 1024
EP_DISPLAY    = 0x02

# Key-event byte/bit map: K1-K7 → data[42], K8-K12 → data[47]
_KEY_MAP = (
    [(42, m) for m in (0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80)] +
    [(47, m) for m in (0x01, 0x02, 0x04, 0x08, 0x10)]
)

_ACTION_TYPES = ["none", "shell", "url", "folder", "app", "page", "obs"]

_DEFAULT_ACTIONS = [{"type": "none", "action": ""} for _ in range(12)]

INIT_MSG = bytes.fromhex(
    "0011800000010000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000"
    "0000"
)
IMG_MSG_TEMPLATE = bytearray.fromhex(
    "0021000000FF3d00006565000000000000000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000"
    "0000"
)

# ── USB helpers ───────────────────────────────────────────────────────────────

def _open_interfaces():
    if not HID_AVAILABLE:
        raise RuntimeError("hidapi not installed (pip install hid)")
    if not PYUSB_AVAILABLE:
        raise RuntimeError("PyUSB not installed (pip install pyusb)")
    # Force GC so previous Device wrappers are fully freed before find() — prevents
    # libusb refcount assertion when old Python objects haven't been collected yet.
    gc.collect()
    device_path = None
    for d in hid.enumerate(VID, PID):
        if d['interface_number'] == 3:
            device_path = d['path']
            break
    if device_path is None:
        raise RuntimeError("DisplayPad Interface 3 not found")
    hid_dev = hid.Device(path=device_path)
    hid_dev.nonblocking = False
    usb_dev = usb.core.find(idVendor=VID, idProduct=PID)
    if usb_dev is None:
        hid_dev.close()
        raise RuntimeError("DisplayPad not found via PyUSB")
    usb.util.claim_interface(usb_dev, 1)
    return usb_dev, hid_dev


def _close_interfaces(usb_dev, hid_dev):
    try:
        usb.util.release_interface(usb_dev, 1)
    except Exception:
        pass
    try:
        usb.util.dispose_resources(usb_dev)
    except Exception:
        pass
    try:
        hid_dev.close()
    except Exception:
        pass


def _init_device(hid_dev):
    for attempt in range(4):
        if attempt > 0:
            time.sleep(1.0)
        hid_dev.write(INIT_MSG)
        for _ in range(50):
            resp = hid_dev.read(64, timeout=200)
            if resp and resp[0] == 0x11:
                return
    raise RuntimeError("DisplayPad did not respond to INIT")


def _upload_button(usb_dev, hid_dev, key_index, bgr_pixels, key_events=None):
    """Upload a single button image. If key_events list is provided,
    any HID key-event packets (data[0]==0x01) encountered during
    upload are appended to it instead of being discarded."""
    msg = bytearray(IMG_MSG_TEMPLATE)
    msg[5] = key_index
    hid_dev.write(bytes(msg))
    for _ in range(50):
        resp = hid_dev.read(64, timeout=200)
        if resp and resp[0] == 0x21 and resp[1] == 0x00 and resp[2] == 0x00:
            break
        if key_events is not None and resp and len(resp) >= 48 and resp[0] == 0x01:
            key_events.append(list(resp))
    else:
        raise RuntimeError(f"No ready response for key {key_index}")
    payload = bytearray(HEADER_SIZE + PACKET_SIZE)
    payload[HEADER_SIZE:HEADER_SIZE + len(bgr_pixels)] = bgr_pixels
    for i in range(0, len(payload), CHUNK_SIZE):
        usb_dev.write(EP_DISPLAY, bytes(payload[i:i + CHUNK_SIZE]), timeout=2000)
    for _ in range(100):
        resp = hid_dev.read(64, timeout=200)
        if resp and resp[0] == 0x21 and resp[1] == 0x00 and resp[2] == 0xFF:
            return
        if key_events is not None and resp and len(resp) >= 48 and resp[0] == 0x01:
            key_events.append(list(resp))
    raise RuntimeError(f"Transfer not confirmed for key {key_index}")


# ── Image / GIF helpers ───────────────────────────────────────────────────────

TILES_DIR = os.path.join(CONFIG_DIR, "displaypad_tiles")

def _image_to_bgr102(path, rotation=0):
    img = Image.open(path).convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    if rotation:
        img = img.rotate(-rotation, expand=False)  # PIL rotates CCW, we want CW
    r, g, b = img.split()
    return Image.merge("RGB", (b, g, r)).tobytes()


def _split_image_to_tiles(path):
    """Split a full image into 12 tiles, save as PNGs, return list of 12 paths."""
    os.makedirs(TILES_DIR, exist_ok=True)
    grid_w = ICON_SIZE * KEYS_PER_ROW
    grid_h = ICON_SIZE * (NUM_KEYS // KEYS_PER_ROW)
    img = Image.open(path).convert("RGB").resize((grid_w, grid_h), Image.LANCZOS)
    paths = []
    for idx in range(NUM_KEYS):
        row = idx // KEYS_PER_ROW
        col = idx % KEYS_PER_ROW
        x, y = col * ICON_SIZE, row * ICON_SIZE
        tile = img.crop((x, y, x + ICON_SIZE, y + ICON_SIZE))
        tile_path = os.path.join(TILES_DIR, f"tile_{idx}.png")
        tile.save(tile_path)
        paths.append(tile_path)
    return paths


def _split_gif_to_tiles(path):
    """Split animated GIF into 12 synchronized tile frame lists.
    Returns {key_idx: [(bgr_bytes, duration_ms), ...]} or None if not animated.
    """
    try:
        img = Image.open(path)
        if not getattr(img, 'is_animated', False) and getattr(img, 'n_frames', 1) <= 1:
            return None
    except Exception:
        return None
    grid_w = ICON_SIZE * KEYS_PER_ROW
    grid_h = ICON_SIZE * (NUM_KEYS // KEYS_PER_ROW)
    result = {k: [] for k in range(NUM_KEYS)}
    try:
        for i in range(img.n_frames):
            img.seek(i)
            duration = max(img.info.get('duration', 100), 20)
            frame = img.convert("RGB").resize((grid_w, grid_h), Image.LANCZOS)
            for idx in range(NUM_KEYS):
                row = idx // KEYS_PER_ROW
                col = idx % KEYS_PER_ROW
                x, y = col * ICON_SIZE, row * ICON_SIZE
                tile = frame.crop((x, y, x + ICON_SIZE, y + ICON_SIZE))
                r, g, b = tile.split()
                result[idx].append((Image.merge("RGB", (b, g, r)).tobytes(), duration))
    except EOFError:
        pass
    return result if result[0] and len(result[0]) > 1 else None


def _split_gif_display_tiles(path, size):
    """Split animated GIF into CTkImage frame lists per tile for GUI preview.
    Returns {key_idx: [(CTkImage, duration_ms), ...]} or None.
    """
    try:
        img = Image.open(path)
        if not getattr(img, 'is_animated', False) and getattr(img, 'n_frames', 1) <= 1:
            return None
    except Exception:
        return None
    tile_size = size
    grid_w = tile_size * KEYS_PER_ROW
    grid_h = tile_size * (NUM_KEYS // KEYS_PER_ROW)
    result = {k: [] for k in range(NUM_KEYS)}
    try:
        for i in range(img.n_frames):
            img.seek(i)
            duration = max(img.info.get('duration', 100), 20)
            frame = img.convert("RGB").resize((grid_w, grid_h), Image.LANCZOS)
            for idx in range(NUM_KEYS):
                row = idx // KEYS_PER_ROW
                col = idx % KEYS_PER_ROW
                x, y = col * tile_size, row * tile_size
                tile = frame.crop((x, y, x + tile_size, y + tile_size))
                result[idx].append((
                    ctk.CTkImage(light_image=tile, dark_image=tile, size=(tile_size, tile_size)),
                    duration))
    except EOFError:
        pass
    return result if result[0] and len(result[0]) > 1 else None


def _load_gif_frames(path):
    """Extract all frames from a GIF. Returns [(bgr_bytes, duration_ms), ...] or None."""
    try:
        img = Image.open(path)
        if not getattr(img, 'is_animated', False) and getattr(img, 'n_frames', 1) <= 1:
            return None
    except Exception:
        return None
    frames = []
    try:
        for i in range(img.n_frames):
            img.seek(i)
            duration = max(img.info.get('duration', 100), 20)
            frame = img.convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            r, g, b = frame.split()
            bgr = Image.merge("RGB", (b, g, r)).tobytes()
            frames.append((bgr, duration))
    except EOFError:
        pass
    return frames if len(frames) > 1 else None


def _load_gif_display_frames(path, size):
    """Extract all frames as CTkImages for GUI preview. Returns [(CTkImage, duration_ms), ...] or None."""
    try:
        img = Image.open(path)
        if not getattr(img, 'is_animated', False) and getattr(img, 'n_frames', 1) <= 1:
            return None
    except Exception:
        return None
    frames = []
    try:
        for i in range(img.n_frames):
            img.seek(i)
            duration = max(img.info.get('duration', 100), 20)
            frame = img.convert("RGB").resize((size, size), Image.LANCZOS)
            frames.append((ctk.CTkImage(light_image=frame, dark_image=frame, size=(size, size)),
                           duration))
    except EOFError:
        pass
    return frames if len(frames) > 1 else None


def _make_thumb(path, size, rotation=0):
    try:
        img = Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)
    except Exception:
        img = Image.new("RGB", (size, size), (40, 40, 50))
    if rotation:
        img = img.rotate(-rotation, expand=False)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def _make_gif_thumb(path, size, rotation=0):
    try:
        img = Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)
    except Exception:
        img = Image.new("RGB", (size, size), (40, 40, 50))
    if rotation:
        img = img.rotate(-rotation, expand=False)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, size - 14, 28, size], fill=(20, 20, 20))
    draw.text((3, size - 13), "GIF", fill=(80, 220, 80))
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def _make_folder_icon(base_path, label, out_path):
    """Render label text on top of the DPFolder.png icon and save to out_path."""
    from PIL import ImageFont
    img = Image.open(base_path).convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    if label:
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf", 16)
            except Exception:
                font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        x = max(2, (ICON_SIZE - tw) // 2)
        # Draw with shadow for readability
        draw.text((x + 1, 5), label, fill=(0, 0, 0), font=font)
        draw.text((x, 4), label, fill=(255, 255, 255), font=font)
    img.save(out_path, "PNG")
    return out_path


def _make_placeholder(size):
    img = Image.new("RGB", (size, size), (40, 40, 50))
    draw = ImageDraw.Draw(img)
    draw.text((size // 2 - 6, size // 2 - 6), "+", fill=(100, 100, 120))
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


# ── Image management dialog ───────────────────────────────────────────────────

_DIALOG_TILE  = 90   # thumbnail size in dialog
_PANEL_TILE   = 48   # thumbnail size in compact panel overview

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
        """Return True if this tile should not be editable (back/folder icons)."""
        p = self._panel._current_page
        if p != 0 and idx == 0:
            return True  # K1 = back on sub-pages
        if p == 0:
            actions = self._panel._page_actions.get(0, _DEFAULT_ACTIONS)
            if idx < len(actions) and actions[idx].get("type") == "page":
                return True  # folder button on main
        return False

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 10))

        ctk.CTkLabel(header, text=self._app.T("dp_dialog_title"),
                     font=("Helvetica", 13, "bold"), text_color=FG,
                     fg_color="transparent").pack(side="left")

        # Page selector
        pages = self._panel._get_available_pages()
        page_labels = [self._app.T("dp_page_main") if p == 0 else f"Page {p}" for p in pages]
        self._page_list = pages
        self._page_selector = ctk.CTkOptionMenu(
            header, values=page_labels,
            fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
            text_color=FG, font=("Helvetica", 11), width=100, height=28,
            command=self._on_page_change)
        cur = self._panel._current_page
        self._page_selector.set(
            self._app.T("dp_page_main") if cur == 0 else f"Page {cur}")
        self._page_selector.pack(side="right")

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
                         font=("Helvetica", 10, "bold"),
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

        # Hint
        ctk.CTkLabel(self, text=self._app.T("dp_dialog_hint"),
                     font=("Helvetica", 10), text_color=FG2,
                     fg_color="transparent").pack(pady=(0, 4))

        # Min. ms/Frame row — own StringVar, synced to panel on change
        self._min_ms_var = ctk.StringVar(value=self._panel._min_ms_var.get())
        self._min_ms_var.trace_add("write", self._sync_min_ms)
        fps_row = ctk.CTkFrame(self, fg_color="transparent")
        fps_row.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(fps_row, text=self._app.T("dp_min_ms_frame"),
                     font=("Helvetica", 11), text_color=FG2,
                     fg_color="transparent").pack(side="left")
        ctk.CTkEntry(fps_row, textvariable=self._min_ms_var,
                     width=60, height=26, font=("Helvetica", 11),
                     fg_color=BG3, border_color=BORDER, text_color=FG,
                     ).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(fps_row, text=self._app.T("dp_gif_speed"),
                     font=("Helvetica", 10), text_color=FG2,
                     fg_color="transparent").pack(side="left", padx=(8, 0))

        # Status line
        self._status_lbl = ctk.CTkLabel(self, text="",
                                         font=("Helvetica", 11), text_color=FG2,
                                         fg_color="transparent")
        self._status_lbl.pack(pady=(0, 4))

        # Bottom buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 14))

        ctk.CTkButton(
            btn_row, text=self._app.T("dp_close"),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            font=("Helvetica", 11), height=34, corner_radius=6, width=110,
            command=self.destroy,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            btn_row, text=self._app.T("dp_clear_all"),
            fg_color=BG3, hover_color=BG2, text_color=FG2,
            font=("Helvetica", 11), height=34, corner_radius=6, width=100,
            command=self._clear_all,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            btn_row, text=self._app.T("dp_fullscreen"),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            font=("Helvetica", 11), height=34, corner_radius=6, width=100,
            command=self._pick_fullscreen,
        ).pack(side="left")

    # ── Slot management ───────────────────────────────────────────────────────

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
        self._panel._images.pop(str(idx), None)
        self._panel._gif_frames.pop(idx, None)
        _save_displaypad_buttons(self._panel._images)
        ph = _make_placeholder(_DIALOG_TILE)
        self._tile_imgs[idx] = ph
        self._tile_lbls[idx].configure(image=ph, text="")
        self._panel._refresh_panel_tile(idx)
        self._status_lbl.configure(text=self._app.T("dp_slot_cleared", k=idx+1), text_color=FG2)

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
        # Auto-save to fullscreen library
        from shared.config import _save_to_dp_fs_library
        _save_to_dp_fs_library(path)
        is_gif = path.lower().endswith('.gif')
        if is_gif:
            self._status_lbl.configure(text=self._app.T("dp_gif_splitting"), text_color=FG2)
            self.update()
            ok = self._panel._load_fullscreen_gif(path, save=True)
            if not ok:
                self._status_lbl.configure(
                    text=self._app.T("dp_gif_not_animated"), text_color=YLW)
                is_gif = False
            else:
                for idx in range(NUM_KEYS):
                    self._refresh_tile(idx)
                    # Also load large display frames for dialog
                    if idx not in self._dlg_frames:
                        pass  # will be built via notify_frame
                # Re-build large dialog frames from fullscreen GIF
                dlg_tiles = _split_gif_display_tiles(path, _DIALOG_TILE)
                if dlg_tiles:
                    for idx in range(NUM_KEYS):
                        self._dlg_frames[idx] = dlg_tiles[idx]
                        self._refresh_tile(idx)
                self._status_lbl.configure(
                    text=self._app.T("dp_fullscreen_gif", name=os.path.basename(path)), text_color=GRN)
                return
        if not is_gif:
            try:
                tile_paths = _split_image_to_tiles(path)
            except Exception as e:
                self._status_lbl.configure(text=self._app.T("dp_error", err=str(e)), text_color=RED)
                return
            self._panel._fullscreen_group = set()
            for idx, tile_path in enumerate(tile_paths):
                self._panel._set_button_image(idx, tile_path)
                self._refresh_tile(idx)
            self._status_lbl.configure(
                text=self._app.T("dp_fullscreen_static", name=os.path.basename(path)), text_color=GRN)

    def _on_page_change(self, label):
        """Switch the image dialog to show a different page's images."""
        for p, lbl in zip(self._page_list,
                          [self._app.T("dp_page_main") if x == 0 else f"Page {x}"
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

    def destroy(self):
        # Auto-upload when dialog closes
        if not self._panel._uploading and not self._panel._animating:
            if self._panel._images or self._panel._gif_frames:
                self._panel._uploading = True  # block key listener immediately
                self._panel.after(300, self._panel._start_upload)
        super().destroy()


# ── Actions dialog ────────────────────────────────────────────────────────────

class DisplayPadActionsDialog(ctk.CTkToplevel):
    """Window: configure shell/url/folder/app/page actions for K1–K12, per page."""

    def __init__(self, panel):
        super().__init__(panel._app)
        self._panel = panel
        self._app   = panel._app
        self._page  = panel._current_page
        self.title(panel._app.T("dp_actions_title"))
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

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
        self._cmd_entries = []
        self._browse_btns = []
        self._obs_combos  = []
        self._cards       = []

        self._build_ui()
        self._load_page(self._page)

        self.update_idletasks()
        pw = self._app.winfo_rootx() + self._app.winfo_width() // 2
        ph = self._app.winfo_rooty() + self._app.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w//2}+{ph - h//2}")

    def _type_labels(self, include_page=True):
        labels = [self._app.T("action_type_none"), self._app.T("action_type_shell"),
                  self._app.T("action_type_url"),  self._app.T("action_type_folder"),
                  self._app.T("action_type_app")]
        if include_page:
            labels.append(self._app.T("action_type_page"))
        labels.append("OBS")
        return labels

    def _load_page(self, page):
        """Populate dialog StringVars from page data."""
        self._page = page
        actions = self._panel._page_actions.get(page, _DEFAULT_ACTIONS)
        is_sub = page != 0
        include_page = not is_sub  # "page" type only on main
        labels = self._type_labels(include_page)
        types_for_page = _ACTION_TYPES if not is_sub else [t for t in _ACTION_TYPES if t != "page"]

        for i in range(12):
            act = actions[i] if i < len(actions) else {"type": "none", "action": ""}
            btype = act.get("type", "none")
            cmd   = act.get("action", "")

            # Sub-page K1 = back (locked)
            if is_sub and i == 0:
                btype, cmd = "back", ""

            self._act_type[i].set(btype)
            self._act_cmd[i].set(cmd)

            menu = self._type_menus[i]
            menu.configure(values=labels)
            # Always reset layout
            self._obs_combos[i].pack_forget()
            self._cmd_entries[i].pack_forget()
            self._browse_btns[i].pack_forget()
            self._cmd_entries[i].pack(side="left", padx=4, expand=True, fill="x")
            self._browse_btns[i].pack(side="left", padx=(0, 4))

            if is_sub and i == 0:
                menu.set(self._app.T("dp_page_back"))
                menu.configure(state="disabled")
                self._cmd_entries[i].configure(state="disabled")
                self._browse_btns[i].configure(state="disabled", image=self._folder_img_dim)
            else:
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
                # "page" type: entry is for label text
                if btype == "page":
                    self._cmd_entries[i].configure(
                        placeholder_text=self._app.T("dp_page_name_hint"))
                # "obs" type: show OBS combo instead of entry
                elif btype == "obs":
                    self._cmd_entries[i].pack_forget()
                    self._browse_btns[i].pack_forget()
                    obs_panel = self._app._obs_panel
                    scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
                    self._obs_combos[i].configure(values=scenes + ["— Record", "— Stream"])
                    if cmd.startswith("scene:"):
                        self._obs_combos[i].set(cmd[6:])
                    elif cmd in ("record", "stream"):
                        self._obs_combos[i].set(f"— {cmd.capitalize()}")
                    elif scenes:
                        self._obs_combos[i].set(scenes[0])
                    self._obs_combos[i].pack(side="left", padx=4, expand=True, fill="x")

        # Update page selector
        self._page_selector.set(
            self._app.T("dp_page_main") if page == 0 else f"Page {page}")

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 4))

        ctk.CTkLabel(header, text=self._app.T("dp_actions_title"),
                     font=("Helvetica", 13, "bold"), text_color=FG,
                     fg_color="transparent").pack(side="left")

        # Page selector
        pages = self._panel._get_available_pages()
        page_labels = [self._app.T("dp_page_main") if p == 0 else f"Page {p}" for p in pages]
        self._page_selector = ctk.CTkOptionMenu(
            header, values=page_labels,
            fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
            text_color=FG, font=("Helvetica", 11), width=100, height=28,
            command=self._on_page_change)
        self._page_selector.pack(side="right")
        self._page_list = pages

        scroll = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=6,
                                        width=480, height=460)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        for i in range(12):
            card = ctk.CTkFrame(scroll, fg_color=BG3, corner_radius=4)
            card.pack(fill="x", padx=4, pady=2)
            self._cards.append(card)

            ctk.CTkLabel(card, text=f"K{i+1}", font=("Helvetica", 10, "bold"),
                         text_color=YLW).pack(anchor="w", padx=8, pady=(5, 0))

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=(2, 6))

            ctk.CTkLabel(row, text=self._app.T("action_label"),
                         font=("Helvetica", 10), text_color=FG2,
                         width=50, anchor="w").pack(side="left", padx=(4, 2))

            type_menu = ctk.CTkOptionMenu(
                row, values=self._type_labels(),
                fg_color=BG2, button_color=BLUE, button_hover_color="#0884be",
                text_color=FG, font=("Helvetica", 11), width=88, height=30,
                dynamic_resizing=False,
                command=lambda val, ix=i: self._on_type_change(val, ix))
            type_menu.pack(side="left", padx=(2, 2))
            self._type_menus.append(type_menu)

            entry = ctk.CTkEntry(row, textvariable=self._act_cmd[i],
                         fg_color=BG2, text_color=FG, border_color=BORDER,
                         font=("Helvetica", 11), height=30)
            entry.pack(side="left", padx=4, expand=True, fill="x")
            entry.bind("<Return>", lambda e, ix=i: self._apply(ix))
            entry.bind("<FocusOut>", lambda e, ix=i: self._apply(ix))
            self._cmd_entries.append(entry)

            # OBS action combo (hidden by default, shown when type=obs)
            obs_combo = ctk.CTkComboBox(
                row, values=[], width=140, height=30,
                font=("Helvetica", 11),
                fg_color=BG2, button_color=BLUE, border_color=BORDER,
                text_color=FG, dropdown_fg_color=BG2, dropdown_text_color=FG,
                dropdown_hover_color=BG3,
                command=lambda val, ix=i: self._on_obs_select(val, ix))
            self._obs_combos.append(obs_combo)
            # not packed yet — shown only when obs type selected

            folder_btn = ctk.CTkButton(
                row, text="", image=self._folder_img_dim,
                width=30, height=30,
                command=lambda ix=i: self._browse(ix),
                fg_color="transparent", hover_color=BG3, corner_radius=4,
                state="disabled")
            folder_btn.pack(side="left", padx=(0, 4))
            self._browse_btns.append(folder_btn)

        self._info_lbl = ctk.CTkLabel(self, text="",
                                      font=("Helvetica", 11), text_color=GRN)
        self._info_lbl.pack(pady=(0, 4))

        ctk.CTkButton(
            self, text=self._app.T("dp_close"),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            font=("Helvetica", 11), height=34, corner_radius=6,
            command=self.destroy,
        ).pack(fill="x", padx=12, pady=(0, 12))

    def _on_page_change(self, label):
        for p, lbl in zip(self._page_list,
                          [self._app.T("dp_page_main") if x == 0 else f"Page {x}"
                           for x in self._page_list]):
            if lbl == label:
                self._load_page(p)
                break

    def _on_type_change(self, label, idx):
        is_sub = self._page != 0
        types = [t for t in _ACTION_TYPES if t != "page"] if is_sub else _ACTION_TYPES
        labels = self._type_labels(include_page=not is_sub)
        try:
            internal = types[labels.index(label)]
        except (ValueError, IndexError):
            internal = "none"
        self._act_type[idx].set(internal)
        btn = self._browse_btns[idx]
        if internal in ("folder", "app"):
            btn.configure(state="normal", image=self._folder_img)
        else:
            btn.configure(state="disabled", image=self._folder_img_dim)

        # Show/hide OBS combo vs entry+browse
        self._obs_combos[idx].pack_forget()
        self._cmd_entries[idx].pack_forget()
        self._browse_btns[idx].pack_forget()
        if internal == "obs":
            obs_panel = self._app._obs_panel
            scenes = obs_panel.get_scenes() if obs_panel.is_connected() else []
            obs_values = scenes + ["— Record", "— Stream"]
            self._obs_combos[idx].configure(values=obs_values)
            self._obs_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
            cur = self._act_cmd[idx].get()
            if cur.startswith("scene:"):
                self._obs_combos[idx].set(cur[6:])
            elif cur in ("record", "stream"):
                self._obs_combos[idx].set(f"— {cur.capitalize()}")
            elif scenes:
                self._obs_combos[idx].set(scenes[0])
                self._act_cmd[idx].set(f"scene:{scenes[0]}")
        else:
            self._cmd_entries[idx].pack(side="left", padx=4, expand=True, fill="x")
            self._browse_btns[idx].pack(side="left", padx=(0, 4))

        # "page" type: entry is for the label text (shown on folder icon)
        if internal == "page":
            self._cmd_entries[idx].configure(state="normal")
            cur = self._act_cmd[idx].get()
            if not cur or cur.startswith("→") or cur.startswith("/") or cur.startswith("scene:"):
                self._act_cmd[idx].set(f"Page {idx + 1}")
        elif internal != "obs":
            self._cmd_entries[idx].configure(state="normal", placeholder_text="")
            cur = self._act_cmd[idx].get()
            if cur.startswith("→") or cur.startswith("scene:"):
                self._act_cmd[idx].set("")
        self._apply(idx)

    def _on_obs_select(self, val, idx):
        """Called when user picks a scene or record/stream from OBS combo."""
        if val == "— Record":
            self._act_cmd[idx].set("record")
        elif val == "— Stream":
            self._act_cmd[idx].set("stream")
        else:
            self._act_cmd[idx].set(f"scene:{val}")
        self._apply(idx)

    def _browse(self, idx):
        btype = self._act_type[idx].get()
        if btype == "folder":
            path = native_open_folder()
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
                     font=("Helvetica", 12), height=34,
                     ).pack(fill="x", padx=12, pady=(12, 6))

        list_frame = ctk.CTkScrollableFrame(dlg, fg_color=BG2, corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

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
                                  hover_color=BG3, font=("Helvetica", 11),
                                  height=30, corner_radius=4,
                                  command=lambda e=exec_cmd: _select(e))
                b.pack(fill="x", pady=1)
                _btn_refs.append(b)

        _rebuild()
        search_var.trace_add("write", lambda *_: _rebuild(search_var.get()))

    def _apply(self, idx):
        btype  = self._act_type[idx].get()
        action = self._act_cmd[idx].get().strip()
        self._panel._save_page_action(self._page, idx, btype, action)
        self._info_lbl.configure(
            text=self._app.T("dp_act_saved", k=idx + 1), text_color=GRN)
        # Refresh page selector (new pages may have been created)
        pages = self._panel._get_available_pages()
        self._page_list = pages
        page_labels = [self._app.T("dp_page_main") if p == 0 else f"Page {p}" for p in pages]
        self._page_selector.configure(values=page_labels)


# ── Panel ─────────────────────────────────────────────────────────────────────

class DisplayPadPanel(ctk.CTkFrame):
    """Panel for Mountain DisplayPad (12 display buttons, GIF animation support)."""

    VID = VID
    PID = PID

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app         = app
        self._images      = {}
        self._gif_frames  = {}
        self._tile_imgs   = {}   # compact overview: key_index -> CTkImage
        self._tile_lbls   = {}   # compact overview: key_index -> CTkLabel
        self._uploading        = False
        self._animating        = False
        self._anim_stop        = threading.Event()
        self._anim_thread      = None
        self._min_frame_ms     = 50
        self._dialog_win       = None
        self._upload_queue     = queue.Queue()
        self._fullscreen_group = set()   # key indices that form a synced fullscreen GIF
        self._rotation         = _load_displaypad_rotation()
        # GUI preview animation
        self._gui_frames_sm  = {}
        self._gui_fidx       = {}
        self._gui_next       = {}
        self._gui_tick_id    = None
        # Device presence monitor
        self._monitor_stop    = threading.Event()
        self._device_present  = False
        # Key event listener
        self._key_stop        = threading.Event()
        self._key_thread      = None
        # Multi-page state
        self._current_page    = 0
        self._page_actions    = {0: _load_displaypad_actions()}
        self._page_images     = {0: _load_displaypad_buttons()}
        self._page_fullscreen = {0: _load_displaypad_fullscreen()}
        self._page_gif_frames = {}   # page -> {idx: frames}
        self._page_gui_frames = {}   # page -> {idx: gui_frames}
        # Load sub-pages from config
        for ps, pdata in _load_displaypad_pages().items():
            p = int(ps)
            self._page_actions[p]    = pdata.get("actions", [dict(a) for a in _DEFAULT_ACTIONS])
            self._page_images[p]     = pdata.get("buttons", {})
            self._page_fullscreen[p] = pdata.get("fullscreen")
        # Resource paths for folder/back icons
        _HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _FROZEN = getattr(sys, "frozen", False)
        self._res_path = getattr(sys, "_MEIPASS", _HERE) if _FROZEN else _HERE
        self._folder_icon = os.path.join(self._res_path, "resources", "DPFolder.png")
        self._back_icon   = os.path.join(self._res_path, "resources", "DPBack.png")
        # Blank placeholder for empty buttons (device keeps old image unless overwritten)
        self._blank_icon = os.path.join(CONFIG_DIR, "dp_blank.png")
        if not os.path.exists(self._blank_icon):
            Image.new("RGB", (ICON_SIZE, ICON_SIZE), (0, 0, 0)).save(self._blank_icon)

        self._images = dict(self._page_images.get(0, {}))
        for k, path in self._images.items():
            if path and os.path.exists(path) and path.lower().endswith('.gif'):
                frames = _load_gif_frames(path)
                if frames:
                    self._gif_frames[int(k)] = frames
                    gui_f = _load_gif_display_frames(path, _PANEL_TILE)
                    if gui_f:
                        self._gui_frames_sm[int(k)] = gui_f

        # Inject folder icons (with label) for "page" buttons on main page
        for i, act in enumerate(self._page_actions.get(0, _DEFAULT_ACTIONS)):
            if act.get("type") == "page":
                labeled = os.path.join(CONFIG_DIR, f"dp_folder_{i}.png")
                if os.path.exists(labeled):
                    self._images[str(i)] = labeled
                elif os.path.exists(self._folder_icon):
                    self._images[str(i)] = self._folder_icon

        # Restore fullscreen GIF if saved
        fs_path = self._page_fullscreen.get(0)
        if fs_path and os.path.exists(fs_path):
            self._load_fullscreen_gif(fs_path, save=False)

        self._min_ms_var = ctk.StringVar(value="50")
        self._build_ui()

        # Refresh tiles immediately for any pre-loaded GIF frames
        for idx in self._gui_frames_sm:
            self._refresh_panel_tile(idx)

        if self._gui_frames_sm:
            self.after(200, self._gui_tick)
        if self._images or self._gif_frames:
            self.after(1500, self._start_upload)

        # Start device presence monitor and key event listener
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        threading.Thread(target=self._key_event_loop, daemon=True).start()
        self.bind("<Destroy>", lambda e: (self._monitor_stop.set(), self._key_stop.set()))

    def T(self, key, **kwargs):
        return self._app.T(key, **kwargs)

    def _reg(self, widget, key, attr="text"):
        return self._app._reg(widget, key, attr)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        self._scroll = scroll

        # Cap scroll speed
        _c = scroll._parent_canvas
        _orig_yview = _c.yview
        def _capped_yview(*args):
            if args and args[0] == "scroll":
                n    = max(-2, min(2, int(args[1])))
                what = args[2] if len(args) > 2 else "units"
                return _orig_yview("scroll", n, what)
            return _orig_yview(*args)
        _c.yview = _capped_yview

        content = scroll

        # Section heading + rotation
        head_row = ctk.CTkFrame(content, fg_color="transparent")
        head_row.pack(padx=16, pady=(14, 0))

        self._heading_lbl = ctk.CTkLabel(
            head_row, text=self.T("dp_title"),
            font=("Helvetica", 14, "bold"), text_color=FG,
            fg_color="transparent", anchor="w")
        self._heading_lbl.pack(side="left")

        self._rot_menu = ctk.CTkOptionMenu(
            head_row, values=["0°", "90°", "180°", "270°"],
            fg_color=BG2, button_color=BG3, button_hover_color=BORDER,
            text_color=FG, font=("Helvetica", 10), width=60, height=24,
            command=self._on_rotation_change)
        self._rot_menu.set(f"{self._rotation}°")
        self._rot_menu.pack(side="right")

        # Page indicator bar
        page_bar = ctk.CTkFrame(content, fg_color="transparent")
        page_bar.pack(padx=16, pady=(4, 0))
        self._page_lbl = ctk.CTkLabel(
            page_bar, text=f"{self.T('dp_page_label')} {self.T('dp_page_main')}",
            font=("Helvetica", 11), text_color=FG2, anchor="w")
        self._page_lbl.pack(side="left")
        self._page_back_btn = ctk.CTkButton(
            page_bar, text=self.T("dp_page_back"),
            font=("Helvetica", 10), fg_color=BG3, hover_color=BG2,
            text_color=FG, height=24, corner_radius=4, width=120,
            command=lambda: self._switch_to_page(0))
        # hidden by default (only visible on sub-pages)

        # Compact 6×2 overview grid
        overview = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
        overview.pack(pady=(6, 6))

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
                img = ph
            self._tile_imgs[idx] = img

            lbl = ctk.CTkLabel(overview, image=img, text="",
                               width=_PANEL_TILE, height=_PANEL_TILE,
                               fg_color=BG3, corner_radius=4)
            lbl.grid(row=row * 2, column=col, padx=3, pady=(3, 0))
            self._tile_lbls[idx] = lbl

            ctk.CTkLabel(overview, text=f"K{idx + 1}",
                         font=("Helvetica", 8), text_color=FG2,
                         fg_color="transparent").grid(row=row * 2 + 1, column=col)

        # Clear button
        self._clear_btn = ctk.CTkButton(
            content, text=self.T("dp_clear_all"),
            font=("Helvetica", 11),
            fg_color=BG3, hover_color=BG2, text_color=FG2,
            height=30, corner_radius=6,
            command=self._clear_all,
        )
        self._clear_btn.pack(padx=16, pady=(4, 4))

        # Dialog buttons row
        dlg_row = ctk.CTkFrame(content, fg_color="transparent")
        dlg_row.pack(padx=16, pady=(0, 4))

        self._assign_btn = ctk.CTkButton(
            dlg_row, text=self.T("dp_assign_images"),
            font=("Helvetica", 11),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            height=30, corner_radius=6, border_width=1, border_color=BORDER,
            command=self._open_dialog,
        )
        self._assign_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._actions_btn = ctk.CTkButton(
            dlg_row, text=self.T("dp_configure_actions"),
            font=("Helvetica", 11),
            fg_color=BG3, hover_color=BG2, text_color=FG,
            height=30, corner_radius=6, border_width=1, border_color=BORDER,
            command=self._open_actions_dialog,
        )
        self._actions_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # GIF speed row
        fps_row = ctk.CTkFrame(content, fg_color="transparent")
        fps_row.pack(padx=16, pady=(0, 4))
        self._min_ms_lbl = ctk.CTkLabel(fps_row, text=self.T("dp_min_ms_frame"),
                     font=("Helvetica", 11), text_color=FG2,
                     fg_color="transparent")
        self._min_ms_lbl.pack(side="left")
        ctk.CTkEntry(fps_row, textvariable=self._min_ms_var,
                     width=60, height=26, font=("Helvetica", 11),
                     fg_color=BG3, border_color=BORDER, text_color=FG,
                     ).pack(side="left", padx=(6, 0))
        self._gif_speed_lbl = ctk.CTkLabel(fps_row, text=self.T("dp_gif_speed"),
                     font=("Helvetica", 10), text_color=FG2,
                     fg_color="transparent")
        self._gif_speed_lbl.pack(side="left", padx=(8, 0))

        # Info label
        self._info_label = ctk.CTkLabel(
            content, text="",
            font=("Helvetica", 11), text_color=FG2, fg_color="transparent")
        self._info_label.pack(pady=(0, 8))

    def _on_rotation_change(self, val):
        deg = int(val.replace("°", ""))
        self._rotation = deg
        _save_displaypad_rotation(deg)
        # Refresh all preview tiles with new rotation
        for idx in range(NUM_KEYS):
            if str(idx) not in self._images:
                self._images[str(idx)] = self._blank_icon
            self._refresh_panel_tile(idx)
        # Re-upload with new rotation
        if not self._uploading and not self._animating:
            self._uploading = True
            self.after(200, self._start_upload)

    def _open_actions_dialog(self):
        if hasattr(self, "_actions_dialog_win") and \
                self._actions_dialog_win is not None and \
                self._actions_dialog_win.winfo_exists():
            self._actions_dialog_win.focus()
            return
        self._actions_dialog_win = DisplayPadActionsDialog(self)

    def _get_action(self, idx):
        """Return (type_str, action_str) for button idx on current page."""
        if self._current_page != 0 and idx == 0:
            return ("back", "")
        actions = self._page_actions.get(self._current_page, _DEFAULT_ACTIONS)
        if idx < len(actions):
            a = actions[idx]
            return (a.get("type", "none"), a.get("action", ""))
        return ("none", "")

    def _save_page_action(self, page, idx, btype, action):
        """Save a single action and persist to config."""
        actions = self._page_actions.setdefault(
            page, [dict(a) for a in _DEFAULT_ACTIONS])
        actions[idx] = {"type": btype, "action": action}
        if page == 0:
            _save_displaypad_actions(actions)
        else:
            self._save_sub_pages()
        # Auto-assign/clear folder icon for "page" type on main page
        if page == 0:
            if btype == "page":
                # Generate folder icon with optional label text
                icon_path = os.path.join(CONFIG_DIR, f"dp_folder_{idx}.png")
                _make_folder_icon(self._folder_icon, action, icon_path)
                self._images[str(idx)] = icon_path
                self._fullscreen_group.discard(idx)
                self._gif_frames.pop(idx, None)
                self._gui_frames_sm.pop(idx, None)
            elif self._images.get(str(idx), "").startswith(
                    os.path.join(CONFIG_DIR, "dp_folder_")):
                # Was a page button, now changed — replace with blank
                self._images[str(idx)] = self._blank_icon
            self._page_images[0] = dict(self._images)
            _save_displaypad_buttons(self._images)
            if self._tile_lbls:
                self._refresh_panel_tile(idx)
            # Push change to device
            if not self._uploading and not self._animating:
                self.after(200, self._start_upload)

    def _save_sub_pages(self):
        """Persist all sub-page data to displaypad_pages.json."""
        out = {}
        for p in range(1, 13):
            if p in self._page_actions or p in self._page_images:
                out[str(p)] = {
                    "buttons": self._page_images.get(p, {}),
                    "actions": self._page_actions.get(p, [dict(a) for a in _DEFAULT_ACTIONS]),
                    "fullscreen": self._page_fullscreen.get(p),
                }
        _save_displaypad_pages(out)

    def _switch_to_page(self, page_num):
        """Switch active page: swap images/actions, refresh GUI, re-upload."""
        if page_num == self._current_page:
            return
        # Stop running animation before switching (check _animating first,
        # because _uploading may also be True during animation)
        if self._animating:
            self._stop_animation()
            self.after(500, lambda: self._switch_to_page(page_num))
            return
        if self._uploading:
            return
        # Save current page state
        old_page = self._current_page
        self._page_images[old_page] = dict(self._images)
        self._page_gif_frames[old_page] = dict(self._gif_frames)
        self._page_gui_frames[old_page] = dict(self._gui_frames_sm)
        if self._fullscreen_group:
            self._page_fullscreen.setdefault(old_page, None)  # keep existing path

        self._current_page = page_num

        # Load new page
        self._images = dict(self._page_images.get(page_num, {}))
        self._gif_frames = {}
        self._gui_frames_sm = {}
        self._gui_fidx = {}
        self._gui_next = {}
        self._fullscreen_group = set()

        # Inject special icons
        if page_num != 0:
            self._images["0"] = self._back_icon
        else:
            # Inject folder icons (with label) for "page" buttons
            for i, act in enumerate(self._page_actions.get(0, _DEFAULT_ACTIONS)):
                if act.get("type") == "page":
                    labeled = os.path.join(CONFIG_DIR, f"dp_folder_{i}.png")
                    if os.path.exists(labeled):
                        self._images[str(i)] = labeled
                    else:
                        self._images[str(i)] = self._folder_icon

        # Fill empty buttons with blank image (device keeps old image otherwise)
        for idx in range(NUM_KEYS):
            if str(idx) not in self._images:
                self._images[str(idx)] = self._blank_icon

        # Load GIF frames for new page
        for k, path in self._images.items():
            if path and os.path.exists(path) and path.lower().endswith('.gif'):
                frames = _load_gif_frames(path)
                if frames:
                    self._gif_frames[int(k)] = frames
                    gui_f = _load_gif_display_frames(path, _PANEL_TILE)
                    if gui_f:
                        self._gui_frames_sm[int(k)] = gui_f
                        self._gui_fidx[int(k)] = 0
                        self._gui_next[int(k)] = time.monotonic()

        # Restore fullscreen GIF if any
        fs = self._page_fullscreen.get(page_num)
        if fs and os.path.exists(fs):
            self._load_fullscreen_gif(fs, save=False)

        # Refresh GUI tiles
        for idx in range(NUM_KEYS):
            self._refresh_panel_tile(idx)
        if self._gui_frames_sm and self._gui_tick_id is None:
            self._gui_tick_id = self.after(50, self._gui_tick)

        # Update page indicator
        if hasattr(self, "_page_lbl"):
            name = self.T("dp_page_main") if page_num == 0 else f"Page {page_num}"
            self._page_lbl.configure(text=f"{self.T('dp_page_label')} {name}")
            self._page_back_btn.pack(
                side="right", padx=(4, 0)) if page_num != 0 else self._page_back_btn.pack_forget()

        self._info_label.configure(
            text=self.T("dp_page_switching", p=page_num if page_num else self.T("dp_page_main")),
            text_color=FG2)
        # Re-upload to device — set flag immediately to block key listener
        if self._images or self._gif_frames:
            self._uploading = True
            self.after(200, self._start_upload)

    def _get_available_pages(self):
        """Return list of page numbers that exist (0 + any with 'page' actions on main)."""
        pages = [0]
        for i, act in enumerate(self._page_actions.get(0, _DEFAULT_ACTIONS)):
            if act.get("type") == "page":
                pages.append(i + 1)
        return sorted(set(pages))

    def _execute_action_k(self, idx):
        btype, action = self._get_action(idx)
        # Page navigation
        if btype == "page":
            self.after(0, lambda p=idx + 1: self._switch_to_page(p))
            return
        if btype == "back":
            self.after(0, lambda: self._switch_to_page(0))
            return
        # OBS action
        if btype == "obs" and action:
            obs_panel = self._app._obs_panel
            if action.startswith("scene:"):
                obs_panel.execute_action("scene", action[6:])
            elif action == "record":
                obs_panel.execute_action("record")
            elif action == "stream":
                obs_panel.execute_action("stream")
            return
        if btype == "none" or not action:
            return
        try:
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                uid     = _pwd.getpwnam(sudo_user).pw_uid
                runtime = f"/run/user/{uid}"
                env = {
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
                    "XDG_RUNTIME_DIR": runtime,
                    "HOME": _pwd.getpwnam(sudo_user).pw_dir,
                    "USER": sudo_user, "LOGNAME": sudo_user,
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                }
                if os.path.exists(os.path.join(runtime, "wayland-0")):
                    env["WAYLAND_DISPLAY"] = "wayland-0"
                    env["XDG_SESSION_TYPE"] = "wayland"
                else:
                    env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
                    env["XAUTHORITY"] = os.path.join(
                        _pwd.getpwnam(sudo_user).pw_dir, ".Xauthority")
                if btype in ("url", "folder"):
                    subprocess.Popen(["sudo", "-u", sudo_user, "-E", "xdg-open", action], env=env)
                else:
                    subprocess.Popen(["sudo", "-u", sudo_user, "-E", "bash", "-c", action], env=env)
            else:
                if btype in ("url", "folder"):
                    subprocess.Popen(["xdg-open", action])
                else:
                    subprocess.Popen(action, shell=True)
        except Exception:
            pass

    def _key_event_loop(self):
        """Persistent key-event listener. Runs for the lifetime of the panel,
        pauses while upload/animation is active, retries on device errors."""
        _DEBOUNCE = 0.8
        last_fire = {}  # key_index -> monotonic time of last action

        while not self._key_stop.is_set():
            # Wait while upload or animation holds the HID device
            if self._uploading or self._animating:
                self._key_stop.wait(timeout=0.5)
                continue

            # Try to open the HID device
            hid_dev = None
            try:
                for d in hid.enumerate(VID, PID):
                    if d['interface_number'] == 3:
                        hid_dev = hid.Device(path=d['path'])
                        break
            except Exception:
                pass

            if hid_dev is None:
                self._key_stop.wait(timeout=1.0)
                continue

            # Read events until stop / upload starts / device error.
            # Only packets with data[0] == 0x01 are key-event packets.
            # All other packets (0x11 init, 0x21 image responses, etc.) are ignored.
            prev = [0] * 64
            try:
                hid_dev.nonblocking = False
                while not self._key_stop.is_set() and not self._uploading and not self._animating:
                    try:
                        data = hid_dev.read(64, timeout=300)
                    except Exception:
                        break
                    if not data or len(data) < 48:
                        continue
                    data = list(data)
                    if data[0] != 0x01:
                        continue
                    now = time.monotonic()
                    for k, (bi, mask) in enumerate(_KEY_MAP):
                        if bi < len(data):
                            if (data[bi] & mask) and not (prev[bi] & mask):
                                if now - last_fire.get(k, 0) >= _DEBOUNCE:
                                    last_fire[k] = now
                                    self.after(0, lambda k=k: self._execute_action_k(k))
                    prev = data
            finally:
                try:
                    hid_dev.close()
                except Exception:
                    pass
            # Brief pause before reconnecting
            self._key_stop.wait(timeout=0.5)

    def apply_lang(self):
        """Called by App when language changes."""
        self._heading_lbl.configure(text=self.T("dp_title"))
        self._assign_btn.configure(text=self.T("dp_assign_images"))
        self._clear_btn.configure(text=self.T("dp_clear_all"))
        self._min_ms_lbl.configure(text=self.T("dp_min_ms_frame"))
        self._gif_speed_lbl.configure(text=self.T("dp_gif_speed"))
        self._actions_btn.configure(text=self.T("dp_configure_actions"))
        self._page_back_btn.configure(text=self.T("dp_page_back"))
        p = self._current_page
        name = self.T("dp_page_main") if p == 0 else f"Page {p}"
        self._page_lbl.configure(text=f"{self.T('dp_page_label')} {name}")

    # ── Dialog ────────────────────────────────────────────────────────────────

    def _open_dialog(self):
        if self._dialog_win is not None and self._dialog_win.winfo_exists():
            self._dialog_win.focus()
            return
        self._dialog_win = DisplayPadImageDialog(self)

    # ── Tile management ───────────────────────────────────────────────────────

    def _set_button_image(self, key_index, path):
        self._images[str(key_index)] = path
        _save_displaypad_buttons(self._images)
        frames = _load_gif_frames(path) if path.lower().endswith('.gif') else None
        if frames:
            self._gif_frames[key_index] = frames
            gui_f = _load_gif_display_frames(path, _PANEL_TILE)
            if gui_f:
                self._gui_frames_sm[key_index] = gui_f
                self._gui_fidx[key_index] = 0
                self._gui_next[key_index] = time.monotonic()
                if self._gui_tick_id is None:
                    self._gui_tick_id = self.after(50, self._gui_tick)
        else:
            self._gif_frames.pop(key_index, None)
            self._gui_frames_sm.pop(key_index, None)
            self._gui_fidx.pop(key_index, None)
            self._gui_next.pop(key_index, None)
        self._refresh_panel_tile(key_index)
        if self._animating:
            bgr = None if frames else _image_to_bgr102(path)
            self._upload_queue.put((key_index, bgr, frames))
        elif not self._uploading:
            self.after(100, self._start_upload)

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
                img = _make_placeholder(_PANEL_TILE)
        self._tile_imgs[idx] = img
        self._tile_lbls[idx].configure(image=img)

    def _get_locked_indices(self):
        """Return set of button indices that must not be overwritten by fullscreen GIF.
        Empty — fullscreen covers everything, actions still work independently."""
        return set()

    def _load_fullscreen_gif(self, path, save=True):
        """Split fullscreen GIF into 12 tile frame lists, populate state."""
        tiles     = _split_gif_to_tiles(path)
        gui_tiles = _split_gif_display_tiles(path, _PANEL_TILE)
        if not tiles:
            return False
        # Exclude locked buttons (back / page) from fullscreen group
        locked = self._get_locked_indices()
        self._fullscreen_group = set(range(NUM_KEYS)) - locked
        for idx in range(NUM_KEYS):
            if idx in locked:
                continue
            self._gif_frames[idx] = tiles[idx]
            if gui_tiles:
                self._gui_frames_sm[idx] = gui_tiles[idx]
                self._gui_fidx[idx]  = 0
                self._gui_next[idx]  = time.monotonic()
            if self._tile_lbls:
                self._refresh_panel_tile(idx)
        # Re-inject special icons for locked buttons
        for idx in locked:
            if self._current_page != 0 and idx == 0:
                self._images[str(idx)] = self._back_icon
            else:
                self._images[str(idx)] = self._folder_icon
            self._gif_frames.pop(idx, None)
            self._gui_frames_sm.pop(idx, None)
            if self._tile_lbls:
                self._refresh_panel_tile(idx)
        if gui_tiles and self._gui_tick_id is None and self._tile_lbls:
            self._gui_tick_id = self.after(50, self._gui_tick)
        if save:
            self._page_fullscreen[self._current_page] = path
            if self._current_page == 0:
                _save_displaypad_fullscreen(path)
            else:
                self._save_sub_pages()
        return True

    def _clear_all(self):
        if self._uploading:
            return
        if self._animating:
            self._stop_animation()
            self.after(500, self._clear_all)
            return
        # Set all buttons to blank, but keep page buttons
        page_btns = {}
        if self._current_page == 0:
            for i, act in enumerate(self._page_actions.get(0, _DEFAULT_ACTIONS)):
                if act.get("type") == "page":
                    labeled = os.path.join(CONFIG_DIR, f"dp_folder_{i}.png")
                    page_btns[str(i)] = labeled if os.path.exists(labeled) else self._folder_icon
        self._images = {str(i): self._blank_icon for i in range(NUM_KEYS)}
        self._images.update(page_btns)
        self._gif_frames = {}
        self._gui_frames_sm = {}
        self._gui_fidx = {}
        self._gui_next = {}
        self._fullscreen_group = set()
        self._page_fullscreen[self._current_page] = None
        if self._current_page == 0:
            save_imgs = dict(page_btns)  # only keep page buttons in config
            _save_displaypad_buttons(save_imgs)
            _clear_displaypad_fullscreen()
        else:
            self._page_images[self._current_page] = {}
            self._save_sub_pages()
        for idx in range(NUM_KEYS):
            self._refresh_panel_tile(idx)
        self._info_label.configure(text=self.T("dp_all_cleared"), text_color=FG2)
        # Upload blank images to device
        self.after(200, self._start_upload)

    # ── GUI preview animation ─────────────────────────────────────────────────

    def _gui_tick(self):
        if not self._gui_frames_sm:
            self._gui_tick_id = None
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

    # ── Upload / Animation ────────────────────────────────────────────────────

    def _start_upload(self):
        assigned = {int(k): v for k, v in self._images.items()
                    if v and os.path.exists(v)}
        # Include gif_frames keys not in _images (fullscreen GIF loaded without individual paths)
        for k in self._gif_frames:
            if k not in assigned:
                assigned[k] = None
        if not assigned:
            self._uploading = False
            self._info_label.configure(text=self.T("dp_no_images"), text_color=YLW)
            return
        try:
            self._min_frame_ms = max(1, int(self._min_ms_var.get()))
        except ValueError:
            self._min_frame_ms = 50

        has_gifs = any(k in self._gif_frames for k in assigned)
        if has_gifs:
            self._animating = True
            self._uploading = False
            self._anim_stop.clear()
        else:
            self._uploading = True

        self._info_label.configure(text=self.T("dp_connecting_pad"), text_color=FG2)
        self._anim_thread = threading.Thread(
            target=self._worker, args=(assigned,), daemon=True)
        self._anim_thread.start()

    def _stop_animation(self):
        self._anim_stop.set()

    def _worker(self, assigned):
        try:
            usb_dev, hid_dev = _open_interfaces()
        except Exception as e:
            self.after(0, lambda e=e: self._finish(False, str(e)))
            return
        try:
            _init_device(hid_dev)

            rot = self._rotation

            static   = {k: _image_to_bgr102(v, rot)
                        for k, v in assigned.items()
                        if k not in self._gif_frames and v is not None}
            animated = {k: self._gif_frames[k]
                        for k in assigned if k in self._gif_frames}

            total = len(static) + len(animated)
            for n, (key_index, bgr) in enumerate(sorted(static.items())):
                self.after(0, lambda n=n, k=key_index: self._info_label.configure(
                    text=self.T("dp_uploading_key", k=k+1, n=n+1, total=total), text_color=FG2))
                _upload_button(usb_dev, hid_dev, key_index, bgr)

            if not animated:
                self.after(0, lambda: self._finish(True, ""))
                return

            for n, (key_index, frames) in enumerate(sorted(animated.items())):
                self.after(0, lambda n=n+len(static), k=key_index:
                           self._info_label.configure(
                               text=self.T("dp_uploading_key", k=k+1, n=n+1, total=total),
                               text_color=FG2))
                fr_bgr = frames[0][0]
                if rot:
                    fr_img = Image.frombytes("RGB", (ICON_SIZE, ICON_SIZE), fr_bgr)
                    fr_img = fr_img.rotate(-rot, expand=False)
                    fr_bgr = fr_img.tobytes()
                _upload_button(usb_dev, hid_dev, key_index, fr_bgr)

            gif_count = len(animated)
            self.after(0, lambda: self._info_label.configure(
                text=self.T("dp_animating", n=gif_count),
                text_color=GRN))

            min_ms = self._min_frame_ms
            group  = sorted(k for k in self._fullscreen_group if k in animated)

            _key_events = []

            if group:
                # ── Synchronized fullscreen GIF loop ──────────────────────────
                n_frames = len(animated[group[0]])
                fidx = 0
                while not self._anim_stop.is_set():
                    t0 = time.monotonic()
                    dur = animated[group[0]][fidx][1]
                    for k in group:
                        bgr, _ = animated[k][fidx % len(animated[k])]
                        if rot:
                            fr = Image.frombytes("RGB", (ICON_SIZE, ICON_SIZE), bgr)
                            bgr = fr.rotate(-rot, expand=False).tobytes()
                        _upload_button(usb_dev, hid_dev, k, bgr, _key_events)
                    fidx = (fidx + 1) % n_frames
                    # Process key events collected during uploads
                    for evt in _key_events:
                        for ki, (bi, mask) in enumerate(_KEY_MAP):
                            if bi < len(evt) and (evt[bi] & mask):
                                self.after(0, lambda ki=ki: self._execute_action_k(ki))
                    _key_events.clear()
                    wait = max(0, max(dur, min_ms) / 1000.0 - (time.monotonic() - t0))
                    if wait > 0:
                        self._anim_stop.wait(timeout=wait)
            else:
                # ── Per-button animation loop ──────────────────────────────────
                frame_idx = {k: 1 % len(f) for k, f in animated.items()}
                next_time = {k: time.monotonic() + max(f[0][1], min_ms) / 1000.0
                             for k, f in animated.items()}

                while not self._anim_stop.is_set():
                    while True:
                        try:
                            qi, bgr, new_frames = self._upload_queue.get_nowait()
                        except queue.Empty:
                            break
                        if new_frames:
                            animated[qi] = new_frames
                            frame_idx[qi] = 0
                            next_time[qi] = time.monotonic()
                        else:
                            animated.pop(qi, None)
                            frame_idx.pop(qi, None)
                            next_time.pop(qi, None)
                            _upload_button(usb_dev, hid_dev, qi, bgr)

                    if not animated:
                        break

                    due_list = sorted((next_time[k], k) for k in animated)
                    t_next, key = due_list[0]
                    wait = t_next - time.monotonic()
                    if wait > 0:
                        self._anim_stop.wait(timeout=wait)
                        if self._anim_stop.is_set():
                            break
                    idx = frame_idx[key]
                    bgr, duration_ms = animated[key][idx]
                    if rot:
                        fr = Image.frombytes("RGB", (ICON_SIZE, ICON_SIZE), bgr)
                        bgr = fr.rotate(-rot, expand=False).tobytes()
                    _upload_button(usb_dev, hid_dev, key, bgr, _key_events)
                    frame_idx[key] = (idx + 1) % len(animated[key])
                    next_time[key] = time.monotonic() + max(duration_ms, min_ms) / 1000.0
                    for evt in _key_events:
                        for ki, (bi, mask) in enumerate(_KEY_MAP):
                            if bi < len(evt) and (evt[bi] & mask):
                                self.after(0, lambda ki=ki: self._execute_action_k(ki))
                    _key_events.clear()

        except Exception as e:
            self.after(0, lambda e=e: self._finish(False, str(e)))
        else:
            self.after(0, lambda: self._finish(True, ""))
        finally:
            _close_interfaces(usb_dev, hid_dev)

    def _finish(self, success, err):
        self._uploading = False
        self._animating = False
        if success:
            self._info_label.configure(text=self.T("dp_done"), text_color=GRN)
        else:
            self._info_label.configure(text=self.T("dp_error", err=err), text_color=RED)

    def _monitor_loop(self):
        """Background thread: detect device connect/disconnect and auto-reupload."""
        while not self._monitor_stop.is_set():
            present = HID_AVAILABLE and any(
                d['interface_number'] == 3
                for d in hid.enumerate(VID, PID)
            )
            if present and not self._device_present:
                # Device just connected / reconnected
                self._device_present = True
                has_content = bool(self._images or self._gif_frames)
                self.after(0, lambda hc=has_content: self._on_device_connected(hc))
            elif not present and self._device_present:
                # Device just disconnected
                self._device_present = False
                self.after(0, self._on_device_disconnected)
            self._monitor_stop.wait(timeout=3)

    def _on_device_connected(self, has_content):
        if self._uploading or self._animating:
            return
        if has_content:
            self._info_label.configure(text=self.T("dp_reconnected"), text_color=FG2)
            self.after(2500, self._start_upload)

    def _on_device_disconnected(self):
        if not self._uploading and not self._animating:
            self._info_label.configure(
                text=self.T("dp_disconnected"), text_color=FG2)
