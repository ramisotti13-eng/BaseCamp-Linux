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

import shared.ui as UI
from shared.ui_helpers import (
    BG, BG2, BG3, FG, FG2, BLUE, YLW, GRN, RED, BORDER, cap_scroll_speed,
    native_open_image, native_open_folder, parse_desktop_apps,
    pick_dp_library_image, pick_dp_fullscreen_image,
    attach_clipboard_menu,
)
from shared.config import (
    CONFIG_DIR,
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
    _create_displaypad_page, _rename_displaypad_page, _delete_displaypad_page,
    _load_displaypad_actions_dialog_size, _save_displaypad_actions_dialog_size,
    macro_names,
)

# Set BASECAMP_PAGE_DEBUG=1 in the environment to trace page-switch/upload
# decisions (also toggles matching trace lines in shared/plugins.py and
# page-bound widget plugins). Off by default.
_PAGE_DEBUG = os.environ.get("BASECAMP_PAGE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(msg):
    if _PAGE_DEBUG:
        print(msg, flush=True)


from shared import hid_compat
HID_AVAILABLE = hid_compat.HID_AVAILABLE

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

_ACTION_TYPES = ["none", "shell", "url", "folder", "app", "page", "obs", "macro", "keypress", "text", "set_key"]
# Secondary "also on press" action types (issue #16). A key that otherwise only
# renders a widget (System Monitor, plugin keys) can additionally fire one of
# these on press, so it is no longer a dead key. Kept to the simple free-text
# actions that need just one entry field.
_SECONDARY_TYPES = ["none", "keypress", "text", "shell", "url", "page"]

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

def _set_brightness(hid_dev, percent):
    """Set DisplayPad backlight brightness. percent: 0, 25, 50, 75, or 100."""
    percent = max(0, min(100, int(percent)))
    buf = bytearray(64)
    buf[0] = 0x12
    buf[1] = 0x03
    buf[4] = percent
    hid_dev.write(bytes(buf))

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
    for d in hid_compat.enumerate(VID, PID):
        if d['interface_number'] == 3:
            device_path = d['path']
            break
    if device_path is None:
        raise RuntimeError("DisplayPad Interface 3 not found")
    # Opening the hidraw node or claiming interface 1 can transiently fail with
    # [Errno 16] Resource busy if a previous owner (the key listener or a prior
    # upload session) hasn't fully let go yet. Retry a couple of times with a
    # short backoff before giving up (issue #26).
    last_err = None
    for attempt in range(3):
        hid_dev = None
        try:
            hid_dev = hid_compat.open_path(device_path)
            hid_dev.nonblocking = False
            usb_dev = usb.core.find(idVendor=VID, idProduct=PID)
            if usb_dev is None:
                hid_dev.close()
                raise RuntimeError("DisplayPad not found via PyUSB")
            usb.util.claim_interface(usb_dev, 1)
            _init_handshake_ctrl(usb_dev)
            return usb_dev, hid_dev
        except Exception as e:
            last_err = e
            if hid_dev is not None:
                try:
                    hid_dev.close()
                except Exception:
                    pass
            time.sleep(0.2)
    raise last_err if last_err else RuntimeError("DisplayPad open failed")


def _init_handshake_ctrl(usb_dev):
    """
    Step 1: temporarily claim IF0 (the keyboard interface). IF0 is only
    needed for the two control transfers below, so detach its kernel driver
    first and reattach it afterwards — the keyboard must keep working.

    Step 2: SET_IDLE (bRequest 0x0A, wValue 0) on IF0, IF1 (pixels) and IF3
    (cmd) to suppress repeated reports when nothing is changing. EPIPE /
    any failure here just means the interface doesn't support it and is
    ignored (best-effort, matching warnLibusb's non-fatal handling).

    Step 3: SET_REPORT (output report 3, payload {0x03, 0x01}) on IF0 to
    enable the device's event reporting mode (verified by USB capture on
    Windows).

    Step 4: release IF0 and reattach its kernel driver. IF1 stays claimed
    by us (already claimed by the caller) for the rest of the session.
    """
    if0_was_active = False
    try:
        if0_was_active = bool(usb_dev.is_kernel_driver_active(0))
    except Exception:
        pass
    if if0_was_active:
        try:
            usb_dev.detach_kernel_driver(0)
        except Exception:
            pass

    if0_claimed = False
    try:
        usb.util.claim_interface(usb_dev, 0)
        if0_claimed = True
    except Exception:
        pass

    def _set_idle(iface):
        try:
            usb_dev.ctrl_transfer(0x21, 0x0A, 0x0000, iface, None, timeout=500)
        except Exception:
            pass

    if if0_claimed:
        _set_idle(0)
    _set_idle(1)  # IF_PIXELS
    _set_idle(3)  # IF_CMD

    if if0_claimed:
        try:
            usb_dev.ctrl_transfer(0x21, 0x09, 0x0203, 0x0000,
                                   bytes([0x03, 0x01]), timeout=500)
        except Exception:
            pass
        try:
            usb.util.release_interface(usb_dev, 0)
        except Exception:
            pass
        if if0_was_active:
            try:
                usb_dev.attach_kernel_driver(0)
            except Exception:
                pass


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
    """Bring the DisplayPad up: send INIT_MSG on IF3 and wait for a matching echo

    Send INIT_MSG, then wait up to 500 ms for a reply. The reply is accepted
    once its first 5 bytes echo what we sent. Retry up to 60 times with a
    10 ms gap after a failed write or read — the device can be slow to come
    up after a fresh plug-in (issue #40)."""
    # INIT_MSG here carries a leading HID report-ID byte (0x00) that
    # hid_dev.write() needs; the device's raw 64-byte packet — and the echo
    # it sends back via hid_dev.read() — does not include that byte, so the
    # comparison is offset by one to line up with init.cpp's INIT_MSG[0:5]
    # (0x11 0x80 0x00 0x00 0x01).
    pkt = INIT_MSG
    echo = pkt[1:6]
    ack_received = False
    for _attempt in range(60):
        try:
            hid_dev.write(pkt)
        except Exception:
            time.sleep(0.01)
            continue

        try:
            resp = hid_dev.read(64, timeout=500)
        except Exception:
            resp = None
        if not resp:
            time.sleep(0.01)
            continue

        # Accept if the first 5 bytes echo what we sent.
        if len(resp) >= 5 and bytes(resp[:5]) == echo:
            # The pad acknowledges INIT before its display engine is ready to
            # accept pixel data; streaming immediately after a fresh
            # enumeration timed out the first image write ("Connection timed
            # out", issue #43). A short settle lets the firmware come up.
            time.sleep(0.25)
            ack_received = True
            break

    if not ack_received:
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


# Every picture the application draws for a key is named the same way: what
# it is, which page, which key. The widget plugins already write
# dp_<plugin>_p<page>_k<key>.png, and the application did not (#95): labels
# were dp_label_<page>_<key>.png, and folder icons dropped the page entirely
# on page 0, which is a legacy form from before sub-pages existed.
_ICON_KINDS = ("label", "folder")


def _generated_icon_name(kind, page, idx):
    """Config path of an icon the application draws itself."""
    return os.path.join(CONFIG_DIR, f"dp_{kind}_p{page}_k{idx}.png")


def _legacy_icon_names(kind, page, idx):
    """What that same icon was called before the scheme was settled."""
    names = [f"dp_{kind}_{page}_{idx}.png"]
    if kind == "folder" and page == 0:
        names.append(f"dp_{kind}_{idx}.png")   # older still, page 0 only
    return [os.path.join(CONFIG_DIR, n) for n in names]


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


def _make_label_icon(text, out_path):
    """Render a short label centered on a dark tile — the auto-generated icon
    for keypress/text actions so those keys aren't blank (issue #31)."""
    from PIL import ImageFont
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (28, 28, 36))
    draw = ImageDraw.Draw(img)

    def _font(sz):
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(p, sz)
            except Exception:
                continue
        return ImageFont.load_default()

    # Wrap into up to 3 lines, shrinking the font until it fits the tile.
    label = (text or "").strip()
    for size in (30, 26, 22, 18, 15, 12):
        font = _font(size)
        words = label.split()
        lines, cur = [], ""
        for w in words or [label]:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=font) <= ICON_SIZE - 8 or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        lines = lines[:3]
        line_h = (draw.textbbox((0, 0), "Ag", font=font)[3]) + 2
        if line_h * len(lines) <= ICON_SIZE - 6 and all(
                draw.textlength(ln, font=font) <= ICON_SIZE - 6 for ln in lines):
            break
    total_h = line_h * len(lines)
    y = max(2, (ICON_SIZE - total_h) // 2)
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        x = max(2, (ICON_SIZE - tw) // 2)
        draw.text((x + 1, y + 1), ln, fill=(0, 0, 0), font=font)
        draw.text((x, y), ln, fill=(255, 255, 255), font=font)
        y += line_h
    img.save(out_path, "PNG")
    return out_path


def _make_placeholder(size):
    img = Image.new("RGB", (size, size), (40, 40, 50))
    draw = ImageDraw.Draw(img)
    draw.text((size // 2 - 6, size // 2 - 6), "+", fill=(100, 100, 120))
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


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

def action_type_ids(app, include_page=True):
    """Internal action type ids in menu order, plugin types appended.

    Lives at module level because the actions dialog and the key inspector in
    the panel show the same menu, and two copies of this list would drift the
    first time a plugin type is added.
    """
    base = list(_ACTION_TYPES) if include_page else [t for t in _ACTION_TYPES
                                                     if t != "page"]
    pm = getattr(app, "_plugin_manager", None)
    if pm:
        plugin_ids = pm.get_action_type_ids()
        if plugin_ids:
            base.append("_separator")
            base.extend(plugin_ids)
    return base


def action_type_labels(app, include_page=True):
    """Translated labels, index-aligned with action_type_ids()."""
    labels = [app.T("action_type_none"), app.T("action_type_shell"),
              app.T("action_type_url"), app.T("action_type_folder"),
              app.T("action_type_app")]
    if include_page:
        labels.append(app.T("action_type_page"))
    labels.append("OBS")
    labels.append(app.T("action_type_macro"))
    labels.append(app.T("action_type_keypress"))
    labels.append(app.T("action_type_text"))
    labels.append(app.T("action_type_set_key"))
    pm = getattr(app, "_plugin_manager", None)
    if pm:
        plugin_labels = pm.get_action_type_labels()
        if plugin_labels:
            labels.append("-- Plugins --")
            for _tid, lbl in plugin_labels:
                labels.append(lbl)
    return labels


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
        if mode == "off":
            self._panel._page_timeout.pop(page, None)
        else:
            self._panel._page_timeout[page] = {
                "mode": mode, "seconds": secs, "target": tgt}
        _save_displaypad_page_timeouts(self._panel._page_timeout)
        # Re-arm live if the page being edited is the one on the device now.
        if page == self._panel._current_page:
            self._panel._arm_page_timeout(page)

    def apply_lang(self):
        self._label.configure(text=self._app.T("dp_panel_timeout"))
        self._secs_label.configure(text=self._app.T("dp_timeout_secs"))
        self._mode_menu.configure(values=self._mode_labels())
        self.load()


# ── Actions dialog ────────────────────────────────────────────────────────────

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
        self._hue_values_map = []
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
                self._populate_hue_combo(self._hue_combos[i], btype, cmd, btn_idx=i)
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
                self._populate_hue_combo(self._hue_combos[i], btype, target, btn_idx=None)
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
        if label == "── Plugins ──":
            cur = self._act_type[idx].get()
            labels = self._type_labels(include_page=True)
            types = self._get_action_types(include_page=True)
            if cur in types:
                self._type_menus[idx].set(labels[types.index(cur)])
            return
        old_type = self._act_type[idx].get()
        types = self._get_action_types(include_page=True)
        labels = self._type_labels(include_page=True)
        try:
            internal = types[labels.index(label)]
        except (ValueError, IndexError):
            internal = "none"
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
            self._populate_hue_combo(self._hue_combos[idx], internal, self._act_cmd[idx].get(), btn_idx=idx)
            self._hue_combos[idx].pack(side="left", padx=4, expand=True, fill="x")
        elif internal == "hue_bri":
            # Split stored value "group:1:50" into target "group:1" + pct "50"
            cur = self._act_cmd[idx].get()
            parts = cur.rsplit(":", 1) if cur.count(":") >= 2 else (cur, "50")
            target, pct = parts[0], parts[1] if len(parts) > 1 else "50"
            self._populate_hue_combo(self._hue_combos[idx], internal, target, btn_idx=None)
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

    def _populate_hue_combo(self, combo, hue_type, current_val="", btn_idx=None):
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

        self._hue_values_map = values_map
        if not items:
            items = ["(no Hue lights found)"]
            self._hue_values_map = []
        combo.configure(values=items)
        # Restore current selection
        if current_val and current_val in values_map:
            combo.set(items[values_map.index(current_val)])
        elif items and self._hue_values_map:
            combo.set(items[0])
            if btn_idx is not None:
                self._act_cmd[btn_idx].set(values_map[0])

    def _take_hue_value(self, val, idx):
        items = self._hue_combos[idx].cget("values")
        try:
            pos = list(items).index(val) if isinstance(items, (list, tuple)) else 0
            if pos < len(self._hue_values_map):
                target = self._hue_values_map[pos]
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
        action = self._act_cmd[idx].get().strip()
        # For hue_bri, assemble "target:pct" from combo + entry
        if btype == "hue_bri":
            target = self._hue_bri_target.get(idx, "")
            pct = action.replace("%", "").strip()
            try:
                pct = str(max(0, min(100, int(pct))))
            except ValueError:
                pct = "50"
            action = f"{target}:{pct}" if target else ""
        # Secondary "also on press" action (issue #16/#17)
        sectype = self._sec_type[idx].get() if idx < len(self._sec_type) else "none"
        secval  = self._sec_cmd[idx].get().strip() if idx < len(self._sec_cmd) else ""
        extra = []
        if sectype and sectype != "none":
            step = {"type": sectype, "action": secval}
            if sectype == "page":
                # 'also on press → go to page' (#17): the entry holds a page
                # name, stored directly (#52) -- _page_target() resolves it
                # back to an id when the chain actually runs.
                if self._panel._page_id_by_name(secval) is not None:
                    step["target"] = secval
                    extra = [step]
                # unresolved / 'New page' name → skip (nothing to jump to)
            elif secval:
                extra = [step]
        # Double-click action (issue #47): authoritative dict from this key's
        # double-click row ({"type":"none"} clears it).
        dbltype = self._dbl_type[idx].get() if idx < len(self._dbl_type) else "none"
        dblval  = self._dbl_cmd[idx].get().strip() if idx < len(self._dbl_cmd) else ""
        double = {"type": "none", "action": ""}
        if dbltype and dbltype != "none":
            step = {"type": dbltype, "action": dblval}
            if dbltype == "page":
                if self._panel._page_id_by_name(dblval) is not None:
                    step["target"] = dblval
                    double = step
            elif dblval:
                double = step
        # Primary 'page' action carries the target picked in the combo (#30).
        target = self._page_targets[idx] if btype == "page" else None
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

class DisplayPadPanel(ctk.CTkFrame):
    """Panel for Mountain DisplayPad (12 display buttons, GIF animation support)."""

    VID = VID
    PID = PID

    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG, corner_radius=0)
        self._app         = app
        self._images      = {}
        self._gif_frames  = {}
        self._tile_imgs   = {}   # key grid: key_index -> CTkImage
        self._tile_lbls   = {}   # key grid: key_index -> CTkLabel
        self._tile_cells  = {}   # key grid: key_index -> frame (selection border)
        self._selected_key = 0   # key shown in the inspector
        self._insp_loading = False
        self._uploading        = False
        self._animating        = False
        self._page_switch_waiting = False  # set by _switch_to_page while it
                                            # waits for the plugin worker to
                                            # yield the device (see there)
        self._deleted_page_ids = set()     # ids _delete_page() has removed —
                                            # _switch_to_page() must never
                                            # write outgoing-page state back
                                            # for one of these (see there)
        self._anim_stop        = threading.Event()
        self._anim_thread      = None
        self._min_frame_ms     = 50
        self._dialog_win       = None
        self._upload_queue     = queue.Queue()
        self._plugin_frame_keys = {}
        # Per key, the image path the grid was last redrawn for, and when.
        # A widget that keeps writing the same file name still changes what
        # the key shows, so the path alone cannot decide this (#96).
        self._tile_shown = {}     # key idx -> path last drawn
        self._tile_drawn = {}     # key idx -> time.monotonic() of that draw
        self._svc_sync_id = None  # pending widget service sync (#97)
        # Set the moment the application starts going away, so a worker
        # about to open the device does not do it into an interpreter that
        # is already tearing down: libusb aborts the process on that
        # ("libusb_ref_device: Assertion `refcnt >= 2' failed").
        self._closing = threading.Event()
        self._fullscreen_group = set()   # key indices that form a synced fullscreen GIF
        self._rotation         = _load_displaypad_rotation()
        self._brightness       = _load_displaypad_brightness()
        self._debounce         = _load_displaypad_debounce()
        # Double-click support (issue #47): a key with a 'double' action fires
        # that on a quick second press; a lone press fires the primary only after
        # the window elapses. Keys without a double action stay instant.
        self._dc_timers        = {}     # key idx -> pending single-press after() id
        self._dc_window        = 0.6    # seconds to wait for a second press
        self._dc_antibounce    = 0.12   # min gap between the two taps (filters bounce)
        # Per-page auto-timeout (issue #45): jump to a target page after N seconds
        # (mode 'after') or N seconds of no keypress (mode 'idle').
        self._page_timeout     = _load_displaypad_page_timeouts()
        self._timeout_after_id = None
        self._prev_page        = 0      # page we switched here from ('prev' target)
        # GUI preview animation
        self._gui_frames_sm  = {}
        self._gui_fidx       = {}
        self._gui_next       = {}
        self._gui_tick_id    = None
        # Device presence monitor
        self._monitor_stop    = threading.Event()
        self._device_present  = False
        # hidraw path of interface 3 for the currently-present pad. A quick
        # unplug/replug re-enumerates the pad under a new path without the
        # presence flag ever toggling within one poll window; comparing paths
        # catches that so we still re-init (issue #44).
        self._dp_path         = None
        # True only after a successful INIT on the current connection. Plugin
        # image uploads wait for this so they don't stream pixels to a pad that
        # has enumerated but not finished booting (the "mountain logo not shown
        # yet" state that timed out the first plugin upload, issue #43).
        self._pad_ready       = False
        # One-time hint when the DisplayPad is on the USB bus but its command
        # interface (3) never enumerates — the usbhid interface-order quirk
        # FransM hit on Ubuntu/Mint (issue #36).
        self._warned_quirk    = False
        # Set whenever the device worker is NOT holding interface 3, so a
        # manual upload/animation can wait for it to free up before opening.
        self._key_released    = threading.Event()
        self._key_released.set()
        # Plugin live-image uploads (System Monitor, Clock, ...) AND key-event
        # listening — both handled by one persistent worker thread (started
        # once, see __init__ below) that owns interface 1/3 for as long as
        # nothing else needs them, instead of a separate key-event listener
        # and per-burst upload sessions constantly trading the device back
        # and forth.
        self._plugin_worker_stop = threading.Event()
        self._last_plugin_error  = None
        # Serialises every USB session (manual upload, animation, plugin worker)
        # so two of them can never hold interface 1/3 at once. Without this the
        # check-and-set on the _uploading/_animating bools races between the GUI
        # and worker threads and a second _open_interfaces() hits the kernel
        # while the first still owns the device -> [Errno 16] Resource busy (#26).
        self._usb_lock = threading.Lock()
        # Multi-page state
        self._current_page    = 0
        self._page_actions    = {0: _load_displaypad_actions()}
        self._page_images     = {0: _load_displaypad_buttons()}
        self._page_fullscreen = {0: _load_displaypad_fullscreen()}
        self._page_gif_frames = {}   # page -> {idx: frames}
        self._page_gui_frames = {}   # page -> {idx: gui_frames}
        # Load sub-pages from config. `v` marks the page-model version: v<2 is a
        # pre-#30 page (still using the hard-locked back button) — remember those
        # so migration only touches them and never re-derives a back button the
        # user has since removed.
        _legacy_pages = []
        for ps, pdata in _load_displaypad_pages().items():
            p = int(ps)
            self._page_actions[p]    = pdata.get("actions", [dict(a) for a in _DEFAULT_ACTIONS])
            self._page_images[p]     = pdata.get("buttons", {})
            self._page_fullscreen[p] = pdata.get("fullscreen")
            if pdata.get("v", 1) < 2:
                _legacy_pages.append(p)
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

        # ── Page-model migration (#30) ────────────────────────────────────────
        def _back_act():
            return {"type": "page", "action": self.T("dp_page_back"),
                    "target": 0, "icon": self._back_icon}
        # Convert the old dedicated 'back' action into a normal page→main action
        # everywhere, so it's editable and shows correctly in the dropdown
        # (idempotent — nothing left to convert on the next launch).
        for acts in self._page_actions.values():
            for i, a in enumerate(acts):
                if a.get("type") == "back":
                    acts[i] = _back_act()
        # Only for pre-#30 pages: give an untouched K1 (still the default 'none')
        # the implicit back button the old model provided. v>=2 pages are left
        # as-is so a user CAN remove the back button and have it stay removed.
        for p in _legacy_pages:
            acts = self._page_actions.get(p)
            if p != 0 and acts and acts[0].get("type", "none") == "none":
                acts[0] = _back_act()

        self._migrate_generated_icon_names()

        self._images = dict(self._page_images.get(0, {}))
        # A value that is not a path is dropped rather than carried into the
        # first os.path call, where it would stop the app from starting at all.
        for k, v in list(self._images.items()):
            if v is not None and not isinstance(v, str):
                print(f"[DisplayPad] ignoring non-path image for key {k}: {v!r}")
                self._images[k] = self._blank_icon
        for k, path in self._images.items():
            if path and os.path.exists(path) and path.lower().endswith('.gif'):
                frames = _load_gif_frames(path)
                if frames:
                    self._gif_frames[int(k)] = frames
                    gui_f = _load_gif_display_frames(path, _PANEL_TILE)
                    if gui_f:
                        self._gui_frames_sm[int(k)] = gui_f

        # Inject navigation icons for 'page'/'back' buttons on the start page.
        self._inject_page_icons(0)

        # Restore fullscreen GIF if saved
        fs_path = self._page_fullscreen.get(0)
        if fs_path and os.path.exists(fs_path):
            self._load_fullscreen_gif(fs_path, save=False)

        self._min_ms_var = ctk.StringVar(value=str(_load_displaypad_min_ms()))
        self._min_ms_var.trace_add("write", self._on_min_ms_change)
        self._build_ui()

        # Refresh tiles immediately for any pre-loaded GIF frames
        for idx in self._gui_frames_sm:
            self._refresh_panel_tile(idx)

        if self._gui_frames_sm:
            self.after(200, self._gui_tick)
        if self._images or self._gif_frames:
            self.after(200, self._start_upload)

        # Start device presence monitor and the persistent device worker
        # (owns interface 1/3: plugin uploads AND key-event listening).
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        threading.Thread(target=self._plugin_upload_worker, daemon=True).start()
        self.bind("<Destroy>", lambda e: (self._monitor_stop.set(),
                                           self._plugin_worker_stop.set()))
        # Arm the auto-timeout for the start page, if it has one (#45).
        self.after(800, lambda: self._arm_page_timeout(self._current_page))
        _bind_dropdown_autoclose(self.winfo_toplevel())

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
            name = self._insp_page.get()
            if name == self.T("dp_new_page"):
                target, value = "new", self.T("dp_page_main")
            else:
                pid = self._page_id_by_name(name)
                target = pid if pid is not None else "new"
                value = name
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
        pid = self._mint_page_id()
        self._ensure_page(pid, back_to=self._current_page)
        names = _load_displaypad_page_names()
        names[pid] = self._unique_page_name(name, exclude_id=pid)
        _save_displaypad_page_names(names)
        self._rebuild_page_tabs()
        self._switch_to_page(pid)

    def apply_fullscreen_image(self, path, say=None, on_tile=None):
        """Split one image or GIF across all twelve keys of the current page.

        Shared by the button under the keys and by the assign-images window, so
        the two cannot end up splitting an image differently. `say` reports
        progress, `on_tile` is called per key for anything that draws its own
        preview. Returns True if something was applied.
        """
        def _say(key, colour=FG2, **kw):
            if say:
                say(self.T(key, **kw), colour)

        from shared.config import _save_to_dp_fs_library
        _save_to_dp_fs_library(path)

        if path.lower().endswith(".gif"):
            _say("dp_gif_splitting")
            self.update_idletasks()
            if self._load_fullscreen_gif(path, save=True):
                for idx in range(NUM_KEYS):
                    if on_tile:
                        on_tile(idx)
                _say("dp_fullscreen_gif", GRN, name=os.path.basename(path))
                return True
            _say("dp_gif_not_animated", YLW)   # a still GIF: split it as one
        try:
            tile_paths = _split_image_to_tiles(path)
        except Exception as e:
            _say("dp_error", RED, err=str(e))
            return False
        self._fullscreen_group = set()
        for idx, tile_path in enumerate(tile_paths):
            self._set_button_image(idx, tile_path)
            if on_tile:
                on_tile(idx)
        _say("dp_fullscreen_static", GRN, name=os.path.basename(path))
        return True

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
        pct = int(val.replace("%", ""))
        self._brightness = pct
        _save_displaypad_brightness(pct)
        def _apply():
            try:
                dev_path = next(
                    d['path'] for d in hid_compat.enumerate(VID, PID)
                    if d['interface_number'] == 3)
                h = hid_compat.open_path(dev_path)
                try:
                    _set_brightness(h, pct)
                finally:
                    h.close()
            except Exception:
                pass
        threading.Thread(target=_apply, daemon=True).start()

    def _on_debounce_change(self, val):
        sec = float(val.replace("s", ""))
        self._debounce = sec
        _save_displaypad_debounce(sec)

    def _after_safe(self, delay, func):
        """self.after() from a worker thread, tolerating a closed window.

        The upload worker keeps running for a moment after the app is told to
        quit. Scheduling onto a Tk interpreter that is already gone raised out
        of the thread and printed a traceback on every exit taken with no pad
        attached; there is nothing left to schedule for, so drop it.
        """
        try:
            self.after(delay, func)
        except (tk.TclError, RuntimeError):
            pass

    def _on_min_ms_change(self, *_):
        """Keep the GIF frame floor across restarts (#73). Silently ignores
        half-typed values -- this fires on every keystroke."""
        _save_displaypad_min_ms(self._min_ms_var.get())

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
        """Return (type_str, action_str) for button idx on current page.

        Since the page-model redesign (#30/#17) every key — including K1 on a
        sub-page — is a normal editable action; the old hard-locked 'back' on
        sub-page K1 is gone. Minted pages get a back action on K1 by default,
        but the user can change or remove it."""
        actions = self._page_actions.get(self._current_page, _DEFAULT_ACTIONS)
        if idx < len(actions):
            a = actions[idx]
            return (a.get("type", "none"), a.get("action", ""))
        return ("none", "")

    def _page_target(self, act, idx):
        """Resolve a 'page' action's destination page id. Targets are stored
        by NAME (a string, resolved against the page-name registry) so they
        keep working even as page ids get minted/reordered elsewhere and so
        they match what's shown in the page picker (#52). Legacy configs
        stored a raw integer id, which is still accepted here; very old
        main-page-only actions had no target at all and mapped to the fixed
        sub-page idx+1, so that fallback is kept too."""
        t = act.get("target")
        if isinstance(t, str):
            pid = self._page_id_by_name(t)
            if pid is not None:
                return pid
            try:
                return int(t)
            except ValueError:
                return idx + 1
        try:
            return int(t)
        except (TypeError, ValueError):
            return idx + 1

    def _all_page_ids(self):
        """Every page id that exists or is referenced: 0, any page with stored
        actions/images, every 'page' action target anywhere, and every page
        registered by name even if nothing points at it yet (#52)."""
        ids = {0}
        ids.update(self._page_actions.keys())
        ids.update(self._page_images.keys())
        ids.update(_load_displaypad_page_names().keys())
        for page, acts in self._page_actions.items():
            for i, act in enumerate(acts):
                if act.get("type") == "page":
                    ids.add(self._page_target(act, i))
                # A page targeted only by an 'also on press' chain step or a
                # double-click action was already treated as "referenced" by
                # _gc_orphan_pages (so it never gets deleted) — but it was
                # missing here, so it silently fell out of the page picker's
                # option list and got clobbered on reload. Resolve those
                # targets too so such a page always counts as existing.
                for step in (act.get("actions") or []):
                    if isinstance(step, dict) and step.get("type") == "page":
                        pid = self._page_id_by_name(step.get("target") or "")
                        if pid is None:
                            try:
                                pid = int(step.get("target"))
                            except (TypeError, ValueError):
                                pid = None
                        if pid is not None:
                            ids.add(pid)
                dbl = act.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page":
                    pid = self._page_id_by_name(dbl.get("target") or "")
                    if pid is None:
                        try:
                            pid = int(dbl.get("target"))
                        except (TypeError, ValueError):
                            pid = None
                    if pid is not None:
                        ids.add(pid)
        return {p for p in ids if isinstance(p, int) and p >= 0}

    def _mint_page_id(self):
        """Allocate a fresh page id above every existing/referenced one (#30)."""
        return max(self._all_page_ids()) + 1

    def _folder_icon_name(self, page, idx):
        """Config path of the auto folder-label icon for a nav button."""
        return _generated_icon_name("folder", page, idx)

    def _migrate_generated_icon_names(self):
        """Rename the icons this application drew to the one scheme (#95).

        The page and the key are known from where the entry sits, so the old
        name does not have to be parsed, only recognised. A file that cannot
        be renamed keeps its old name and its stored path, which still works;
        the point of this is a config directory that reads consistently, not
        something anything depends on. Idempotent, so it costs one directory
        walk on every later start and does nothing.
        """
        changed = False
        for page, imgs in self._page_images.items():
            if not isinstance(imgs, dict):
                continue
            for key, path in list(imgs.items()):
                if not isinstance(path, str):
                    continue
                try:
                    idx = int(key)
                except (TypeError, ValueError):
                    continue
                for kind in _ICON_KINDS:
                    if path not in _legacy_icon_names(kind, page, idx):
                        continue
                    new = _generated_icon_name(kind, page, idx)
                    try:
                        if os.path.exists(path) and not os.path.exists(new):
                            os.replace(path, new)
                    except OSError as e:
                        print(f"[DisplayPad] keeping {path}: {e}")
                        break
                    if os.path.exists(new):
                        imgs[key] = new
                        changed = True
                    break
        if changed:
            _save_displaypad_buttons(
                self._persistable_images(0, self._page_images.get(0, {})))
            self._save_sub_pages()

    def _inject_page_icons(self, page):
        """Give every navigation button on `page` its icon: a custom icon set on
        the action, else the auto folder-label icon, else the shared folder icon
        — or the back icon for a legacy 'back' action. Applies to all pages now
        that any key can navigate (#30)."""
        stored = self._page_images.get(page, {})
        for i, act in enumerate(self._page_actions.get(page, _DEFAULT_ACTIONS)):
            t = act.get("type")
            if t in ("page", "back"):
                # This key navigates, so it is ours to draw, not a plugin's.
                self._take_key_back(page, i)
            if t == "page":
                custom = act.get("icon")
                user_img = stored.get(str(i), "")
                if custom and os.path.exists(custom):
                    self._images[str(i)] = custom
                elif (user_img and os.path.exists(user_img)
                      and not self._is_nav_icon(user_img)):
                    # A custom image assigned to this switch button via the image
                    # editor wins over the default folder icon (#30 custom icon).
                    self._images[str(i)] = user_img
                else:
                    labeled = self._folder_icon_name(page, i)
                    self._images[str(i)] = (labeled if os.path.exists(labeled)
                                            else self._folder_icon)
            elif t == "back":
                self._images[str(i)] = self._back_icon

    def _ensure_page(self, page_id, back_to=0):
        """Create page `page_id` if it doesn't exist, seeding K1 with a back
        button that returns to `back_to`. The back button is a normal 'page'
        action the user can re-target, re-icon or remove (#30)."""
        page_id = int(page_id)
        if page_id == 0 or page_id in self._page_actions:
            return
        acts = [dict(a) for a in _DEFAULT_ACTIONS]
        acts[0] = {"type": "page", "action": self.T("dp_page_back"),
                   "target": self._get_page_name(int(back_to)), "icon": self._back_icon}
        self._page_actions[page_id] = acts
        self._page_images.setdefault(page_id, {})
        self._save_sub_pages()

    def _gc_orphan_pages(self):
        """Drop pages that nothing navigates to and that hold no content — e.g.
        a page minted when the user picked 'New page' but then retargeted the
        button to an existing page (#30). Conservative: never touches page 0, the
        current page, or any page registered by name (#52 -- a person may have
        pre-created it on purpose and not gotten around to using it yet). Also
        keeps any page with an image/fullscreen or a non-default action (the
        auto back button on K1 doesn't count as content)."""
        registered = set(_load_displaypad_page_names().keys())
        referenced = {0, self._current_page} | registered

        def _resolve(tgt, idx=0):
            if isinstance(tgt, str):
                pid = self._page_id_by_name(tgt)
                if pid is not None:
                    return pid
                try:
                    return int(tgt)
                except ValueError:
                    return None
            if isinstance(tgt, int):
                return tgt
            return None

        for page, acts in self._page_actions.items():
            for i, a in enumerate(acts):
                if a.get("type") == "page":
                    referenced.add(self._page_target(a, i))
                # A page targeted only by an 'also on press' chain step (#17) or a
                # double-click action (#47) is still referenced — don't GC it.
                for step in (a.get("actions") or []):
                    if step.get("type") == "page":
                        pid = _resolve(step.get("target"))
                        if pid is not None:
                            referenced.add(pid)
                dbl = a.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page":
                    pid = _resolve(dbl.get("target"))
                    if pid is not None:
                        referenced.add(pid)
        # A page reachable only as an auto-timeout destination is referenced too (#45).
        for _p, to in (self._page_timeout or {}).items():
            pid = _resolve((to or {}).get("target"))
            if pid is not None:
                referenced.add(pid)
        removed = False
        for p in list(self._page_actions.keys()):
            if p in referenced or p == 0:
                continue
            imgs = {k: v for k, v in (self._page_images.get(p, {}) or {}).items()
                    if v and v != self._blank_icon}
            if imgs or self._page_fullscreen.get(p):
                continue
            empty = True
            for i, a in enumerate(self._page_actions.get(p, [])):
                t = a.get("type", "none")
                if t == "none" or (i == 0 and t in ("page", "back")):
                    continue  # default slot or the auto back button
                empty = False
                break
            if empty:
                self._page_actions.pop(p, None)
                self._page_images.pop(p, None)
                self._page_fullscreen.pop(p, None)
                removed = True
        if removed:
            self._save_sub_pages()

    def _is_nav_icon(self, path):
        """True if `path` is an auto-assigned navigation icon (folder/back) — as
        opposed to a user-chosen image we must not clobber."""
        if not path:
            return True
        return (path == self._blank_icon or path == self._back_icon
                or path == self._folder_icon
                or path.startswith(os.path.join(CONFIG_DIR, "dp_folder_")))

    def _save_page_action(self, page, idx, btype, action, extra=None,
                          target=None, icon=None, double=None):
        """Save one button's action and persist it.

        `extra`  — optional secondary "also on press" chain (issue #16/#17);
                   when omitted, any existing chain on the button is preserved.
        `target` — for a 'page' action, the destination page id. None/"new"
                   mints a fresh page (or reuses the button's current target).
        `icon`   — optional custom image path for a 'page' button (#30).
        `double` — optional double-click action dict (issue #47). None preserves
                   any existing one (so runtime set_key edits don't wipe it); a
                   dict sets it; a dict with type 'none' clears it.

        The editor always switches the panel to the page being edited first, so
        `page == self._current_page` and `self._images` is this page's live map."""
        actions = self._page_actions.setdefault(
            page, [dict(a) for a in _DEFAULT_ACTIONS])
        old = actions[idx] if idx < len(actions) else {}
        if not isinstance(old, dict):
            old = {}
        entry = {"type": btype, "action": action}
        if extra is None:
            if isinstance(old.get("actions"), list) and old["actions"]:
                entry["actions"] = old["actions"]
        elif extra:
            entry["actions"] = list(extra)
        # Double-click action (#47): None = preserve, dict = authoritative.
        if double is None:
            od = old.get("double")
            if isinstance(od, dict) and od.get("type", "none") not in ("none", ""):
                entry["double"] = od
        elif isinstance(double, dict) and double.get("type", "none") not in ("none", ""):
            entry["double"] = double

        # ── 'page' action: resolve/allocate target + custom icon ──────────────
        if btype == "page":
            if target in (None, "", "new"):
                target = (self._page_target(old, idx)
                          if old.get("type") == "page" and old.get("target") not in (None, "")
                          else self._mint_page_id())
            target = int(target)
            self._ensure_page(target, back_to=page)
            entry["target"] = self._get_page_name(target)
            keep_icon = icon or (old.get("icon") if old.get("type") == "page" else None)
            if keep_icon:
                entry["icon"] = keep_icon

        actions[idx] = entry
        self._page_images.setdefault(page, {})

        # Auto-generate a label icon for keypress/text keys with no custom image
        # yet, so they show what they do instead of staying blank (issue #31).
        self._maybe_auto_label_icon(page, idx, btype, action)

        # ── Button image ──────────────────────────────────────────────────────
        # Only the current page owns the live `self._images` map; a 'set_key'
        # action (#18) can redefine a key on a page the user isn't viewing, so
        # for any other page we edit that page's stored image dict directly and
        # leave the visible page (and its gif/fullscreen state) untouched.
        is_current = (page == self._current_page)
        imgs = self._images if is_current else self._page_images[page]
        if btype == "page":
            icon_path = entry.get("icon")
            existing = imgs.get(str(idx), "")
            if icon_path and os.path.exists(icon_path):
                imgs[str(idx)] = icon_path
            elif existing and os.path.exists(existing) and not self._is_nav_icon(existing):
                pass  # keep an image the user assigned via the editor (#30 custom icon)
            else:
                icon_path = self._folder_icon_name(page, idx)
                _make_folder_icon(self._folder_icon, action, icon_path)
                imgs[str(idx)] = icon_path
            if is_current:
                self._fullscreen_group.discard(idx)
                self._gif_frames.pop(idx, None)
                self._gui_frames_sm.pop(idx, None)
        elif btype == "back":
            imgs[str(idx)] = self._back_icon
        elif btype == "none":
            # Blank the slot, but never wipe a user-assigned decorative image.
            if self._is_nav_icon(imgs.get(str(idx), "")):
                imgs[str(idx)] = self._blank_icon
        elif btype not in ("keypress", "text") and self._is_nav_icon(
                imgs.get(str(idx), "")) and imgs.get(str(idx)) != self._blank_icon:
            # Was a nav button, now a non-visual action — drop the nav icon.
            imgs[str(idx)] = self._blank_icon

        # ── Persist ───────────────────────────────────────────────────────────
        if is_current:
            self._sync_live_images(page)
        if page == 0:
            _save_displaypad_actions(actions)
            _save_displaypad_buttons(
                self._persistable_images(0, self._page_images.get(0, {})))
        else:
            self._save_sub_pages()

        # Only the visible page drives the panel preview / device upload.
        if is_current:
            if self._tile_lbls:
                self._refresh_panel_tile(idx)
            if not self._uploading and not self._animating:
                self.after(200, self._start_upload)
            self._schedule_service_sync()
        self._sync_editors(page)

    def _schedule_service_sync(self):
        """Ask for the page's widget services to be brought in line, shortly.

        Assigning a widget to a key has to start that widget's service, and
        clearing the last key that used it has to stop it. Both were only ever
        done on a page switch, so a widget assigned to a key on the page you
        were already looking at simply never started: nothing appeared on the
        pad or in the editor, though the key was stored and worked after a
        restart or a switch away and back (#97). Clearing one had the mirror
        problem, leaving the thread polling and painting a key nobody had
        assigned it to any more.

        Deferred and coalesced because applying the actions dialog saves all
        twelve rows in a loop, and a sync per row would stop a service on one
        row and start it again on the next.
        """
        if getattr(self, "_svc_sync_id", None) is not None:
            try:
                self.after_cancel(self._svc_sync_id)
            except Exception:
                pass
        self._svc_sync_id = self.after(250, self._sync_page_services)

    def _sync_page_services(self):
        self._svc_sync_id = None
        pm = getattr(self._app, "_plugin_manager", None)
        if not pm:
            return
        try:
            pm.sync_services_for_page(self._current_page)
        except Exception as e:
            print(f"[Plugin] sync_services_for_page failed: {e}")

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

    def _maybe_auto_label_icon(self, page, idx, btype, action):
        """Render and assign a text label icon for keypress/text keys (issue
        #31). Only fills an empty slot or replaces a previous auto-icon — a
        user-assigned image is never overwritten."""
        if btype not in ("keypress", "text") or not (action or "").strip():
            return
        if page == self._current_page:
            cur = self._images.get(str(idx), "")
        else:
            cur = self._page_images.get(page, {}).get(str(idx), "")
        auto = (cur.startswith(os.path.join(CONFIG_DIR, "dp_label_"))
                or cur.startswith(os.path.join(CONFIG_DIR, "dp_folder_")))
        is_blank = (not cur) or cur == self._blank_icon or not os.path.exists(cur)
        if not (is_blank or auto):
            return  # user-assigned image — keep it
        label = action.strip()
        icon_path = _generated_icon_name("label", page, idx)
        try:
            _make_label_icon(label, icon_path)
        except Exception:
            return
        self._page_images.setdefault(page, {})[str(idx)] = icon_path
        self._take_key_back(page, idx)
        if page == self._current_page:
            self._images[str(idx)] = icon_path
            # The main-page branch of _save_page_action already refreshes and
            # re-uploads; do it here for sub-pages so the icon shows right away.
            if page != 0:
                if self._tile_lbls:
                    self._refresh_panel_tile(idx)
                if not self._uploading and not self._animating:
                    self.after(200, self._start_upload)

    # ── Plugin frames are not key icons ───────────────────────────────────────
    # A plugin paints a key by writing its frame straight into the live image
    # map (that is how the panel tile and a full page upload pick it up) and
    # into page 0's map, then calling push_plugin_image(). Those two maps are
    # also what we persist, so a frame written a moment after a page switch
    # became the stored icon of whatever key sat there on the new page, and a
    # plugin running on a sub-page overwrote Main's icon for its key index.
    # Both survived the plugin being stopped, because by then they were in
    # displaypad_pages/*.json (#69/#70). A frame is a picture on the device,
    # not a key's icon: it is kept out of everything we store, and the key
    # keeps whatever icon it was given.

    def _mark_plugin_frame(self, idx):
        """Note that key `idx` now shows a plugin frame rather than its icon.
        Both maps a plugin writes are marked: the live one, which belongs to
        the page that is up right now, and Main's, which they write directly."""
        for page in (self._current_page, 0):
            self._plugin_frame_keys.setdefault(page, set()).add(int(idx))

    def _take_key_back(self, page, idx):
        """The panel itself assigned this key's image, so it is an icon again
        and gets stored like any other."""
        keys = self._plugin_frame_keys.get(page)
        if keys:
            keys.discard(int(idx))

    def _icons_restored(self, page, live, keep=()):
        """`live` with every plugin frame replaced by the icon that key
        actually carries, except on the keys in `keep`.

        The stored config is where that icon comes from: it is the one record
        of it a frame cannot have overwritten, since this is the only place
        that writes it and every panel-side assignment takes the key back
        first."""
        imgs = dict(live)
        marked = self._plugin_frame_keys.get(page, set()) - set(keep)
        if not marked:
            return imgs
        if page == 0:
            stored = _load_displaypad_buttons()
        else:
            stored = _load_displaypad_pages().get(str(page), {}).get("buttons") or {}
        for idx in marked:
            k = str(idx)
            if k in stored:
                imgs[k] = stored[k]
            else:
                imgs.pop(k, None)
        return imgs

    def _persistable_images(self, page, live=None):
        """The map to store for `page` (the current page's live map by
        default). Nothing a plugin painted goes in: the config records what a
        key carries, and a widget frame is a picture on the device."""
        return self._icons_restored(page, self._images if live is None else live)

    def _images_for_page(self, page):
        """The map to put on screen for `page`, from what we hold for it.

        A frame is kept on the keys this page gives to a plugin, where the
        widget belongs and where dropping it would flash the static icon
        between the switch and the plugin's next frame. On every other key it
        is somebody else's picture and the key gets its icon back."""
        live = self._page_images.get(page, {})
        owned = self._plugin_key_slots()
        if owned is None:
            return dict(live)   # cannot tell whose key is whose, leave it alone
        return self._icons_restored(page, live, keep=owned)

    def _sync_live_images(self, page=None):
        """Copy the live map into the page's stored map, frames excluded."""
        page = self._current_page if page is None else page
        self._page_images[page] = self._persistable_images(page)

    def _save_sub_pages(self):
        """Persist every non-main page to displaypad_pages.json. Page ids are no
        longer bounded to 1..12 (a carousel can mint arbitrary ids — #30), so we
        iterate the actual page keys instead of a fixed range."""
        out = {}
        for p in set(self._page_actions) | set(self._page_images):
            if p == 0:
                continue
            out[str(p)] = {
                "v": 2,   # page-model version — >=2 means don't re-derive back (#30)
                "buttons": self._persistable_images(p, self._page_images.get(p, {})),
                "actions": self._page_actions.get(p, [dict(a) for a in _DEFAULT_ACTIONS]),
                "fullscreen": self._page_fullscreen.get(p),
            }
        _save_displaypad_pages(out)

    def _switch_to_page(self, page_num, _retry=0):
        """Switch active page: swap images/actions, refresh GUI, re-upload."""
        if page_num == self._current_page:
            return
        # Stop running animation before switching (check _animating first,
        # because _uploading may also be True during animation)
        if self._animating:
            self._stop_animation()
            self.after(500, lambda: self._switch_to_page(page_num))
            return
        # A plugin image upload (System Monitor / Clock push ~1/s) may hold the
        # device. Don't silently drop the switch — that left the panel/device on
        # the old page while the editor already showed the new one, so a fresh
        # page looked "pre-filled" with the previous page's images (issue #28).
        # Defer and retry until the short upload finishes (bounded so a stuck
        # flag can't loop forever).
        #
        # _page_switch_waiting tells _plugin_upload_worker to yield the device
        # at its next loop iteration instead of only releasing it after a full
        # LINGER (2s) idle gap between queued frames — without this, a plugin
        # that keeps pushing more often than every 2s (e.g. a ~1/s System
        # Monitor push) held the device indefinitely and every retry here just
        # failed until the bound below silently gave up (this was the actual
        # bug: page switching appeared to stop working whenever such a plugin
        # was active on the current page).
        self._page_switch_waiting = True
        if self._uploading:
            if _retry < 40:  # ~6s worst case at 150ms
                self.after(150, lambda: self._switch_to_page(page_num, _retry + 1))
            return
        # NOTE: _page_switch_waiting stays True here — it is NOT cleared yet.
        # Clearing it as soon as this guard passes left a window (until the
        # scheduled _start_upload -> _worker actually acquires _usb_lock,
        # ~200ms+ below) where the plugin worker saw no reason to keep
        # yielding and could grab the device again for its next push,
        # blocking the page's own static-icon upload on the same lock —
        # static icons then simply never got refreshed after a switch. It's
        # cleared instead in _finish() (reached by every _worker() upload,
        # including the "nothing assigned" blank-out case) or immediately
        # below if this switch has nothing to upload at all.
        # Cancel any pending double-click timers: they belong to the page we're
        # leaving. Left armed, a stale timer would fire the primary against the
        # wrong page, or a later single press on the new page would be mistaken
        # for its second click (issue #47, surfaced by the #30/#45 page switches).
        for _tid in self._dc_timers.values():
            try:
                self.after_cancel(_tid)
            except Exception:
                pass
        self._dc_timers.clear()

        # Save current page state -- unless old_page was just deleted via
        # _delete_page(). That function tries to clean up the entries this
        # writes right after calling us, but _switch_to_page() isn't always
        # synchronous: if _uploading/_animating was true at the time,
        # this whole call was just a scheduled retry (see the guards above)
        # and the real switch — and this save — happens later, *after*
        # _delete_page() already returned and did its cleanup. That silently
        # resurrected the deleted page in memory, invisible until the next
        # unrelated _save_sub_pages() call (e.g. deleting another page
        # afterward) wrote its file back to disk. Guarding it here, at the
        # point the write actually happens, is correct regardless of timing.
        old_page = self._current_page
        if old_page in self._deleted_page_ids:
            _dbg(f"[DBG switch] {old_page} -> {page_num} | old_page was deleted, not saving its state")
        else:
            self._prev_page = old_page   # 'previous page' timeout target (#45)
            self._sync_live_images(old_page)
            self._page_gif_frames[old_page] = dict(self._gif_frames)
            self._page_gui_frames[old_page] = dict(self._gui_frames_sm)
            if self._fullscreen_group:
                self._page_fullscreen.setdefault(old_page, None)  # keep existing path
            _dbg(f"[DBG switch] {old_page} -> {page_num} | saved page_images[{old_page}]={self._page_images[old_page]}")

        self._current_page = page_num

        # Start/stop page-bound service plugins using their normal start()/
        # stop() lifecycle: a plugin whose button was on the page we're
        # leaving gets stop()'d (it was otherwise still polling and painting
        # over that key index on the new page, since keys are shared
        # hardware slots across pages), and a plugin whose button is on the
        # page we're entering gets start()'d right away.
        pm = getattr(self._app, "_plugin_manager", None)
        if pm:
            try:
                pm.sync_services_for_page(page_num)
            except Exception as e:
                print(f"[Plugin] sync_services_for_page failed: {e}")

        _dbg(f"[DBG switch] after sync_services_for_page({page_num}): "
              f"page_images[{page_num}]={self._page_images.get(page_num)}")

        # Load new page
        self._images = self._images_for_page(page_num)
        _dbg(f"[DBG switch] loaded self._images for page {page_num} = {self._images}")
        self._gif_frames = {}
        self._gui_frames_sm = {}
        self._gui_fidx = {}
        self._gui_next = {}
        self._fullscreen_group = set()

        # Inject navigation icons for every 'page'/'back' button on this page
        # (any key can navigate now — #30).
        self._inject_page_icons(page_num)

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
            name = self._get_page_name(page_num)
            self._page_lbl.configure(text=f"{self.T('dp_page_label')} {name}")
        # The tabs are the page indicator now, and the inspector has to follow
        # the new page: a switch can come from a key press or from dp_page,
        # not just from clicking a tab.
        self._rebuild_page_tabs()
        self._select_key(self._selected_key)

        self._info_label.configure(
            text=self.T("dp_page_switching", p=page_num if page_num else self.T("dp_page_main")),
            text_color=FG2)
        # Re-upload to device — set flag immediately to block key listener
        if self._images or self._gif_frames:
            self._uploading = True
            self.after(200, self._start_upload)
        else:
            # Nothing to upload for this page at all -- _start_upload/_finish
            # (which normally clear _page_switch_waiting) will never run, so
            # clear it here or the plugin worker would keep yielding the
            # device forever for a switch that has already fully completed.
            self._page_switch_waiting = False

        # Start this page's auto-timeout countdown, if any (#45).
        self._arm_page_timeout(page_num)

    def _get_available_pages(self):
        """Return the sorted list of page ids that exist. Pages are no longer
        tied to a main-page button slot — any 'page' action on any page can
        target any page id (#30 carousel)."""
        return sorted(self._all_page_ids())

    def _get_page_name(self, p):
        """Return the persisted name for a page. Page 0 is just another
        entry in the same registry -- it only happens to be the page the
        app opens on -- defaulting to a translated 'Main' the first time
        (after which it's stored data like any other page and can be
        renamed). A page with no registry entry yet but that's targeted by
        an existing 'page' action (pre-#52 configs) has its button-derived
        label adopted into the registry once, so the name becomes durable
        instead of being re-derived (and able to disappear) every time."""
        names = _load_displaypad_page_names()
        if p in names:
            return names[p]
        if p == 0:
            default = self._unique_page_name(self.T("dp_page_main"), exclude_id=0)
            names[0] = default
            _save_displaypad_page_names(names)
            return default
        for page, acts in self._page_actions.items():
            for i, act in enumerate(acts):
                if act.get("type") == "page" and self._page_target(act, i) == p:
                    name = (act.get("action") or "").strip()
                    if name:
                        name = self._unique_page_name(name, exclude_id=p)
                        names[p] = name
                        _save_displaypad_page_names(names)
                        return name
        fallback = self._unique_page_name(f"Page {p}", exclude_id=p)
        names[p] = fallback
        _save_displaypad_page_names(names)
        return fallback

    def _page_id_by_name(self, name):
        """Resolve a page name back to its id, or None if no page has that
        name right now (e.g. it was renamed or deleted elsewhere)."""
        for pid, nm in _load_displaypad_page_names().items():
            if nm == name:
                return pid
        return None

    def _unique_page_name(self, name, exclude_id=None):
        """Disambiguate `name` against every other page's name (#55) by
        appending " (2)", " (3)", etc. Without this, two pages could end up
        with the identical name and the page dropdown would show that name
        twice -- looking like a duplicate entry, even though they're two
        distinct, valid pages underneath."""
        existing = {n for pid, n in _load_displaypad_page_names().items()
                    if pid != exclude_id}
        if name not in existing:
            return name
        i = 2
        while f"{name} ({i})" in existing:
            i += 1
        return f"{name} ({i})"

    def _create_named_page(self, name):
        """Register a brand-new page that isn't targeted by any button yet
        (#52) -- it exists purely because it's in the name registry, and
        _gc_orphan_pages() knows to leave it alone."""
        name = (name or "").strip() or f"Page {self._mint_page_id()}"
        name = self._unique_page_name(name)
        pid = _create_displaypad_page(name, existing_ids=self._all_page_ids())
        self._page_actions.setdefault(pid, [dict(a) for a in _DEFAULT_ACTIONS])
        self._page_images.setdefault(pid, {})
        self._save_sub_pages()
        return pid

    def _rename_page(self, page_id, name):
        """Rename a page and update every stored reference to it (#52).
        References are stored by name, so without this cascade a button
        that pointed at the old name would silently stop resolving the
        moment the page got renamed -- that would make name-based
        references *less* durable than plain ids, not more."""
        page_id = int(page_id)
        name = (name or "").strip()
        if not name:
            return
        name = self._unique_page_name(name, exclude_id=page_id)
        old_name = self._get_page_name(page_id)
        _rename_displaypad_page(page_id, name)
        if old_name == name:
            return

        def _retarget(step):
            if isinstance(step, dict) and step.get("target") == old_name:
                step["target"] = name
                return True
            return False

        changed_actions = False
        for page, acts in self._page_actions.items():
            for act in acts:
                if not isinstance(act, dict):
                    continue
                if act.get("type") == "page" and _retarget(act):
                    changed_actions = True
                for step in (act.get("actions") or []):
                    if step.get("type") == "page" and _retarget(step):
                        changed_actions = True
                dbl = act.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page" and _retarget(dbl):
                    changed_actions = True
        if changed_actions:
            if 0 in self._page_actions:
                _save_displaypad_actions(self._page_actions[0])
            self._save_sub_pages()

        changed_timeout = False
        for _p, to in (self._page_timeout or {}).items():
            if isinstance(to, dict) and to.get("target") == old_name:
                to["target"] = name
                changed_timeout = True
        if changed_timeout:
            _save_displaypad_page_timeouts(self._page_timeout)

    def _find_page_references(self, page_id):
        """Return every place that targets this page by name: a primary
        'page' action, an 'also on press' step, a double-click action, or a
        page-timeout target -- on ANY page, including this one. Used to
        warn before deleting a page that's still pointed at."""
        name = self._get_page_name(page_id)
        refs = []
        for from_page, acts in self._page_actions.items():
            for i, act in enumerate(acts or []):
                if not isinstance(act, dict):
                    continue
                if act.get("type") == "page" and act.get("target") == name:
                    refs.append({"from_page": from_page, "key": i, "kind": "action"})
                for step in (act.get("actions") or []):
                    if step.get("type") == "page" and step.get("target") == name:
                        refs.append({"from_page": from_page, "key": i, "kind": "also_on_press"})
                dbl = act.get("double")
                if isinstance(dbl, dict) and dbl.get("type") == "page" and dbl.get("target") == name:
                    refs.append({"from_page": from_page, "key": i, "kind": "double_click"})
        for from_page, to in (self._page_timeout or {}).items():
            if isinstance(to, dict) and to.get("target") == name:
                refs.append({"from_page": from_page, "key": None, "kind": "timeout"})
        return refs

    def _delete_page(self, page_id):
        """Remove a page's data, its stored file, and switch away from it
        first if it was the one currently on screen. Does NOT clean up
        references to it elsewhere (#54) -- those buttons are left as-is
        and will simply fail to resolve a target next time they're used;
        the delete-page UI is expected to have already warned about that."""
        page_id = int(page_id)
        if page_id == 0:
            return False
        if not _delete_displaypad_page(page_id):
            return False
        # Marks this id so _switch_to_page() will never write outgoing-page
        # state back for it — needed because _switch_to_page() isn't always
        # synchronous (it can defer itself via a scheduled retry while
        # _uploading/_animating is true), so cleanup done only *after*
        # calling it below isn't reliable; see the guard inside
        # _switch_to_page() itself for the actual fix.
        self._deleted_page_ids.add(page_id)
        self._page_actions.pop(page_id, None)
        self._page_images.pop(page_id, None)
        self._page_fullscreen.pop(page_id, None)
        self._page_timeout.pop(page_id, None)
        self._page_gif_frames.pop(page_id, None)
        self._page_gui_frames.pop(page_id, None)
        if self._current_page == page_id:
            self._switch_to_page(0)
            # Belt-and-suspenders: _switch_to_page() itself now refuses to
            # write outgoing-page state for anything in _deleted_page_ids
            # (see the guard there), which is what actually prevents the
            # resurrection regardless of whether the switch above ran
            # synchronously or was deferred. These pops just keep the
            # in-memory dicts tidy for the (now-harmless) synchronous case.
            self._page_actions.pop(page_id, None)
            self._page_images.pop(page_id, None)
            self._page_fullscreen.pop(page_id, None)
            self._page_gif_frames.pop(page_id, None)
            self._page_gui_frames.pop(page_id, None)
            if self._prev_page == page_id:
                self._prev_page = 0
        self._save_sub_pages()
        _save_displaypad_page_timeouts(self._page_timeout)
        return True

    def _get_extra_actions(self, idx):
        """Extra action steps for button idx (issue #17). Stored as an `actions`
        list on the button's action dict; empty for the common single-action case.
        K1 on a sub-page is a normal key now (#30), so it too may carry a chain."""
        actions = self._page_actions.get(self._current_page, _DEFAULT_ACTIONS)
        if idx < len(actions):
            extra = actions[idx].get("actions")
            if isinstance(extra, list):
                return extra
        return []

    def _execute_action_k(self, idx):
        # A keypress resets the idle auto-timeout for the current page (#45).
        self._note_keypress()
        # Double-click (issue #47): if this key has a 'double' action, delay the
        # primary until the window elapses so a quick second press can trigger the
        # double action instead. Keys with no double action fire instantly.
        dbl = self._get_double_action(idx)
        if dbl is None:
            self._fire_action_chain(idx)
            return
        tid = self._dc_timers.pop(idx, None)
        if tid is not None:
            # Second press inside the window → double-click action, cancel single.
            try:
                self.after_cancel(tid)
            except Exception:
                pass
            self._run_one_action(dbl.get("type", "none"),
                                 dbl.get("action", ""), idx, step=dbl)
        else:
            self._dc_timers[idx] = self.after(
                int(self._dc_window * 1000), lambda i=idx: self._dc_fire_single(i))

    def _dc_fire_single(self, idx):
        """Window elapsed with no second press → treat as a single click."""
        self._dc_timers.pop(idx, None)
        self._fire_action_chain(idx)

    def _fire_action_chain(self, idx):
        # Run the primary action, then any chained extras in order (issue #17).
        # Pass the full action dict so a step can carry a page 'target' — this
        # is what lets 'also on press' jump to a page (e.g. press the CPU key →
        # also switch to a per-core page, issue #17).
        primary = self._get_action_dict(idx)
        self._run_one_action(primary.get("type", "none"),
                             primary.get("action", ""), idx, step=primary)
        for step in self._get_extra_actions(idx):
            st = step.get("type", "none")
            sa = step.get("action", "")
            if st and st != "none":
                self._run_one_action(st, sa, idx, step=step)

    def _get_double_action(self, idx):
        """The 'double' action dict for button idx on the current page, or None
        when the key has no double-click action configured (issue #47)."""
        d = self._get_action_dict(idx).get("double")
        if isinstance(d, dict) and d.get("type", "none") not in ("none", ""):
            return d
        return None

    def _is_double_key(self, k):
        """Cheap check used by the key listener to pick a per-key debounce: a
        double-click key needs a short anti-bounce so a deliberate second tap
        isn't swallowed by the normal debounce (issue #47)."""
        acts = self._page_actions.get(self._current_page)
        if acts and k < len(acts) and isinstance(acts[k], dict):
            d = acts[k].get("double")
            return isinstance(d, dict) and d.get("type", "none") not in ("none", "")
        return False

    # ── Per-page auto-timeout (issue #45) ─────────────────────────────────────
    def _arm_page_timeout(self, page):
        """(Re)start the auto-timeout timer for `page`, cancelling any pending
        one. No-op unless the page has an 'after'/'idle' timeout configured."""
        if self._timeout_after_id is not None:
            try:
                self.after_cancel(self._timeout_after_id)
            except Exception:
                pass
            self._timeout_after_id = None
        to = self._page_timeout.get(page)
        if not to:
            return
        secs = int(to.get("seconds", 0) or 0)
        if to.get("mode", "off") in ("after", "idle") and secs > 0:
            self._timeout_after_id = self.after(
                secs * 1000, lambda p=page: self._fire_page_timeout(p))

    def _fire_page_timeout(self, page):
        self._timeout_after_id = None
        if self._current_page != page:
            return
        to = self._page_timeout.get(page) or {}
        tgt = to.get("target", 0)
        if tgt == "prev":
            tgt = self._prev_page
        elif isinstance(tgt, str):
            pid = self._page_id_by_name(tgt)
            tgt = pid if pid is not None else 0
        try:
            tgt = int(tgt)
        except (TypeError, ValueError):
            tgt = 0
        if tgt != page:
            self._switch_to_page(tgt)

    def _note_keypress(self):
        """Idle-mode timeout restarts its countdown on every keypress (#45)."""
        to = self._page_timeout.get(self._current_page)
        if to and to.get("mode") == "idle" and int(to.get("seconds", 0) or 0) > 0:
            self._arm_page_timeout(self._current_page)

    def _get_action_dict(self, idx, page=None):
        """Full action dict for button idx on `page` (current page if None)."""
        page = self._current_page if page is None else page
        actions = self._page_actions.get(page, _DEFAULT_ACTIONS)
        return actions[idx] if idx < len(actions) else {"type": "none", "action": ""}

    def _run_one_action(self, btype, action, idx=0, step=None):
        # Redefine another key on demand (issue #18). action is JSON:
        #   {"page":P,"key":K,"type":T,"action":A}  (page/key optional → current/idx)
        if btype == "set_key" and action:
            self.after(0, lambda a=action, i=idx: self._apply_set_key_action(a, i))
            return
        # Page navigation — target any page id (#30 carousel). Legacy actions
        # with no explicit target fall back to the old idx+1 sub-page.
        if btype == "page":
            target = self._page_target(step if step is not None else {}, idx)
            self.after(0, lambda p=target: self._switch_to_page(p))
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
        # Macro action
        if btype == "macro" and action:
            from shared.macros import execute_macro
            from shared.config import load_macros
            if not hasattr(self, "_macro_toggle_events"):
                self._macro_toggle_events = {}
            macro = load_macros().get("macros", {}).get(action)
            if macro:
                if action in self._macro_toggle_events:
                    self._macro_toggle_events[action].set()
                    del self._macro_toggle_events[action]
                else:
                    stop_ev = None
                    if macro.get("repeat_mode") == "toggle":
                        stop_ev = threading.Event()
                        self._macro_toggle_events[action] = stop_ev
                    threading.Thread(
                        target=execute_macro, args=(macro, stop_ev),
                        daemon=True).start()
            return
        # Keypress / Text action – uses xdotool (X11) or ydotool (Wayland) automatically
        if btype == "keypress" and action:
            from shared.macros import simulate_keypress
            threading.Thread(target=simulate_keypress, args=(action,), daemon=True).start()
            return
        if btype == "text" and action:
            from shared.macros import simulate_text
            threading.Thread(target=simulate_text, args=(action,), daemon=True).start()
            return
        # Plugin action types
        pm = getattr(self._app, "_plugin_manager", None)
        if pm:
            handler = pm.get_action_handler(btype)
            if handler:
                threading.Thread(target=handler, args=(action,), daemon=True).start()
                return
        if btype == "none" or not action:
            return
        # Built-in shell/url/folder/app — dispatch via shared helpers that
        # handle the SUDO_USER / Wayland / X11 environment juggling.
        from shared.macros import _run_shell, _run_xdg_open
        try:
            if btype in ("url", "folder"):
                _run_xdg_open(action)
            else:
                _run_shell(action)
        except Exception:
            pass

    def _apply_set_key_action(self, action_json, host_idx):
        """Redefine a key in response to a 'set_key' action (issue #18).
        action_json: {"page":P,"key":K,"type":T,"action":A} — page/key default
        to the current page and the pressed button."""
        import json as _j
        try:
            d = _j.loads(action_json)
        except (ValueError, TypeError):
            return
        try:
            page = int(d.get("page", self._current_page))
            key  = int(d.get("key", host_idx))
        except (TypeError, ValueError):
            return
        self._save_page_action(page, key, d.get("type", "none"), d.get("action", ""))
        if page == self._current_page and hasattr(self, "_refresh_panel_tile"):
            try:
                self._refresh_panel_tile(key)
            except Exception:
                pass

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
        self._images[str(key_index)] = path
        self._take_key_back(self._current_page, key_index)
        self._persist_images()
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
            try:
                bgr = None if frames else _image_to_bgr102(path)
            except Exception as e:
                print(f"[DisplayPad] key {key_index + 1}: cannot read {path!r} ({e})")
            else:
                self._upload_queue.put((key_index, bgr, frames,
                                        self._current_page))
        elif not self._uploading:
            self.after(100, self._start_upload)

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
        path = self._images.get(str(idx))
        if not path:
            return
        now = time.monotonic()
        if self._tile_shown.get(idx) == path and \
                now - self._tile_drawn.get(idx, 0.0) < _TILE_REDRAW_MIN:
            return
        self._tile_shown[idx] = path
        self._tile_drawn[idx] = now
        try:
            self.after(0, lambda i=idx: self._refresh_panel_tile(i))
        except Exception:
            # The window is going away; a tile nobody will see is no loss.
            pass

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
        size = _PANEL_TILE
        img = Image.new("RGB", (size, size), (20, 30, 50))
        draw = ImageDraw.Draw(img)
        # Plugin icon (small gear/puzzle symbol)
        draw.rectangle([2, 2, size - 3, size - 3], outline=(14, 165, 233), width=1)
        # Wrap label into short lines
        words = label.split()
        y = 8
        for w in words[:3]:
            if len(w) > 8:
                w = w[:7] + "."
            draw.text((6, y), w, fill=(200, 200, 220))
            y += 12
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))

    def _load_fullscreen_gif(self, path, save=True):
        """Split fullscreen GIF into 12 tile frame lists, populate state.
        Fullscreen covers every button — page/back actions still fire on
        key press but visually the whole panel is occupied by the GIF."""
        tiles     = _split_gif_to_tiles(path)
        gui_tiles = _split_gif_display_tiles(path, _PANEL_TILE)
        if not tiles:
            return False
        self._fullscreen_group = set(range(NUM_KEYS))
        for idx in range(NUM_KEYS):
            self._gif_frames[idx] = tiles[idx]
            if gui_tiles:
                self._gui_frames_sm[idx] = gui_tiles[idx]
                self._gui_fidx[idx]  = 0
                self._gui_next[idx]  = time.monotonic()
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

    def _persist_images(self):
        """Write the current page's images where that page keeps them.

        self._images is the live map of whichever page is showing, not
        necessarily Main's, so which file it goes to depends on the page."""
        if self._current_page == 0:
            _save_displaypad_buttons(self._persistable_images(0))
        else:
            self._sync_live_images()
            self._save_sub_pages()

    def _clear_slot(self, idx):
        """Blank one key: image back to the blank icon, action to none."""
        if idx is None:
            return
        self._images[str(idx)] = self._blank_icon
        self._take_key_back(self._current_page, idx)
        self._gif_frames.pop(idx, None)
        self._fullscreen_group.discard(idx)
        self._persist_images()
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
        # Set all buttons to blank, but keep page buttons + their actions so
        # navigation between pages still works after a clear.
        page_btns = {}
        kept_page_actions = {}  # idx -> action dict for "page" buttons on main
        cur_actions = self._page_actions.get(self._current_page, _DEFAULT_ACTIONS)
        if self._current_page == 0:
            for i, act in enumerate(cur_actions):
                if act.get("type") == "page":
                    labeled = self._folder_icon_name(self._current_page, i)
                    page_btns[str(i)] = labeled if os.path.exists(labeled) else self._folder_icon
                    kept_page_actions[i] = dict(act)
        self._images = {str(i): self._blank_icon for i in range(NUM_KEYS)}
        self._images.update(page_btns)
        # Every key on this page is ours again, whatever a plugin was painting.
        self._plugin_frame_keys.pop(self._current_page, None)
        self._gif_frames = {}
        self._gui_frames_sm = {}
        self._gui_fidx = {}
        self._gui_next = {}
        self._fullscreen_group = set()
        self._page_fullscreen[self._current_page] = None

        # Reset button actions to defaults, but preserve "page" buttons on main
        # and the "back" action on K1 of sub-pages.
        new_actions = [dict(a) for a in _DEFAULT_ACTIONS]
        for idx, act in kept_page_actions.items():
            if 0 <= idx < len(new_actions):
                new_actions[idx] = act
        self._page_actions[self._current_page] = new_actions

        if self._current_page == 0:
            save_imgs = dict(page_btns)  # only keep page buttons in config
            _save_displaypad_buttons(save_imgs)
            _clear_displaypad_fullscreen()
            _save_displaypad_actions(new_actions)
        else:
            self._page_images[self._current_page] = {}
            self._save_sub_pages()
        for idx in range(NUM_KEYS):
            self._refresh_panel_tile(idx)
        self._info_label.configure(text=self.T("dp_all_cleared"), text_color=FG2)
        # Upload blank images to device — set flag immediately to block key listener
        # before the scheduled callback fires.
        self._uploading = True
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
        _dbg(f"[DBG upload] page={self._current_page} uploading assigned={assigned}")
        # Include gif_frames keys not in _images (fullscreen GIF loaded without individual paths)
        for k in self._gif_frames:
            if k not in assigned:
                assigned[k] = None
        if not assigned:
            if self._device_present:
                # No images to show — but the pad may still be holding stale
                # frames in its own memory (e.g. a plugin icon whose action was
                # later removed kept rendering, even across reboots — issue #41).
                # An empty-assigned worker run blanks every key and finishes.
                self._uploading = True
                self._info_label.configure(text=self.T("dp_connecting_pad"), text_color=FG2)
                self._anim_thread = threading.Thread(
                    target=self._worker, args=({},), daemon=True)
                self._anim_thread.start()
            else:
                self._uploading = False
                # This path never reaches _worker()/_finish() (no device to
                # talk to), so clear it here too -- otherwise a page switch
                # that landed here would leave the plugin worker yielding
                # the device forever for a switch that's already done.
                self._page_switch_waiting = False
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

    def _worker(self, assigned, _retry=0):
        # Wait for the key-event listener to release interface 3 before grabbing
        # it. The _uploading/_animating flag is already set by _start_upload, so
        # the listener is on its way out; opening hidraw while it still holds the
        # device returns [Errno 16] Resource busy (issue #26).
        self._key_released.wait(timeout=1.5)
        self._usb_lock.acquire()
        if self._closing.is_set():
            self._usb_lock.release()
            return
        try:
            usb_dev, hid_dev = _open_interfaces()
        except Exception as e:
            self._usb_lock.release()
            if _retry < 5:
                # Device busy at boot — retry after a short delay.
                # Release flags so the key listener can resume while we wait,
                # and re-run _start_upload to pick up the latest page state
                # (user may have switched pages during the retry window).
                delay = (2 + _retry * 2) * 1000  # 2s, 4s, 6s, 8s, 10s
                self._uploading = False
                self._animating = False
                self._after_safe(delay, self._start_upload)
                return
            self._after_safe(0, lambda e=e: self._finish(False, str(e)))
            return
        try:
            _init_device(hid_dev)
            # The pad is now booted and addressable: let queued plugin uploads
            # proceed (they wait on this to avoid streaming to a not-yet-ready
            # pad, issue #43).
            self._pad_ready = True

            rot = self._rotation

            # Clear unassigned buttons so stale images from previous
            # sessions don't linger on the device.
            _blank_bgr = b'\x00' * (ICON_SIZE * ICON_SIZE * 3)
            for k in range(NUM_KEYS):
                if k not in assigned:
                    _upload_button(usb_dev, hid_dev, k, _blank_bgr)

            # One unreadable file used to take the whole upload down, and the
            # pad kept whatever it was showing while a red toast repeated the
            # library's error message. A key whose image cannot be read is
            # skipped and named on the console instead; the others still go up,
            # and the key is left blank by the loop above.
            static = {}
            for k, v in assigned.items():
                if k in self._gif_frames or v is None:
                    continue
                try:
                    static[k] = _image_to_bgr102(v, rot)
                except Exception as e:
                    print(f"[DisplayPad] key {k + 1}: cannot read {v!r} ({e})")
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
            # Track previous packet state for rising-edge detection so a held
            # button doesn't fire its action every GIF frame.
            _prev_evt = bytearray(64)

            def _dispatch_key_events(evts):
                nonlocal _prev_evt
                for evt in evts:
                    for ki, (bi, mask) in enumerate(_KEY_MAP):
                        if bi >= len(evt):
                            continue
                        if (evt[bi] & mask) and not (_prev_evt[bi] & mask):
                            self.after(0, lambda ki=ki: self._execute_action_k(ki))
                    if len(evt) >= len(_prev_evt):
                        _prev_evt = bytearray(evt[:len(_prev_evt)])

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
                    _dispatch_key_events(_key_events)
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
                            qi, bgr, new_frames, qpage = self._upload_queue.get_nowait()
                        except queue.Empty:
                            break
                        if qpage is not None and qpage != self._current_page:
                            continue   # drawn for a page we already left (#70)
                        _allowed = self._plugin_key_slots()
                        if (not new_frames and _allowed is not None
                                and qi not in _allowed):
                            continue   # not a key any plugin owns here (#69)
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
                    _dispatch_key_events(_key_events)
                    _key_events.clear()

        except Exception as e:
            self.after(0, lambda e=e: self._finish(False, str(e)))
        else:
            self.after(0, lambda: self._finish(True, ""))
        finally:
            _close_interfaces(usb_dev, hid_dev)
            self._usb_lock.release()

    def _finish(self, success, err):
        self._uploading = False
        self._animating = False
        # A page switch waiting on this upload (see _switch_to_page) can stop
        # yielding the device to the plugin worker now that it's done.
        self._page_switch_waiting = False
        # A result is news, not state: it goes as a toast and leaves the
        # layout alone instead of parking the last message under the grid for
        # the rest of the session.
        self._info_label.configure(text="")
        if success:
            self._app.toast(self.T("dp_done"), kind="ok", ms=2000)
        else:
            self._app.toast(self.T("dp_error", err=err), kind="bad", ms=6000)
        # Any plugin images that queued up while this upload held the device
        # get picked up by the persistent plugin worker on its next poll
        # (at most 0.2s later) — nothing to kick off explicitly here anymore.

    def _monitor_loop(self):
        """Background thread: detect device connect/disconnect and auto-reupload."""
        while not self._monitor_stop.is_set():
            path = None
            if HID_AVAILABLE:
                try:
                    for d in hid_compat.enumerate(VID, PID):
                        if d['interface_number'] == 3:
                            path = d['path']
                            break
                except Exception:
                    path = None
            present = path is not None

            if present and not self._device_present:
                # Device just connected / reconnected
                self._device_present = True
                self._dp_path = path
                self._pad_ready = False
                has_content = bool(self._images or self._gif_frames)
                self.after(0, lambda hc=has_content: self._on_device_connected(hc))
            elif present and self._device_present and path != self._dp_path:
                # Re-enumerated under a new path without us seeing the gap: the
                # user unplugged and replugged within a single poll window, so
                # the pad booted fresh and needs INIT again even though presence
                # never toggled (issue #44).
                self._dp_path = path
                self._pad_ready = False
                has_content = bool(self._images or self._gif_frames)
                self.after(0, lambda hc=has_content: self._on_device_connected(hc))
            elif not present and self._device_present:
                # Device just disconnected
                self._device_present = False
                self._dp_path = None
                self._pad_ready = False
                self.after(0, self._on_device_disconnected)
            elif not present and not self._device_present:
                # Plugged in but interface 3 missing? Likely the usbhid quirk.
                self._maybe_warn_usb_quirk()
            self._monitor_stop.wait(timeout=2)

    def _maybe_warn_usb_quirk(self):
        """If the DisplayPad is on the USB bus but its command interface (3)
        never shows up, the kernel is dropping it due to the interface-order
        quirk. Surface a clear, actionable hint once instead of silently
        showing the pad as 'not connected' (issue #36)."""
        if self._warned_quirk or not PYUSB_AVAILABLE:
            return
        try:
            dev = usb.core.find(idVendor=VID, idProduct=PID)
        except Exception:
            return
        if dev is None:
            return  # genuinely not plugged in — nothing to warn about
        # On the bus but hidapi never listed interface 3 → quirk.
        self._warned_quirk = True
        print(
            "[DisplayPad] Detected on USB but its command interface (3) is "
            "missing — the kernel is likely dropping it (usbhid interface-order "
            "quirk). Fix: create /etc/modprobe.d/mountain-displaypad.conf with\n"
            "  options usbhid quirks=0x3282:0x0009:0x4000\n"
            "then rebuild the initramfs and reboot. See README → Known Issues.",
            flush=True)
        try:
            self.after(0, lambda: self._info_label.configure(
                text=self.T("dp_quirk_hint"), text_color=YLW))
        except Exception:
            pass

    def _on_device_connected(self, has_content, _tries=0):
        if self._uploading or self._animating:
            # A previous upload/animation session still owns the device. Don't
            # drop the reconnect (that left the pad un-initialised after a
            # replug, issue #44) — retry until the session finishes, then init.
            if _tries < 30:
                self.after(500, lambda: self._on_device_connected(has_content, _tries + 1))
            return
        self._on_brightness_change(f"{self._brightness}%")
        if has_content:
            self._info_label.configure(text=self.T("dp_reconnected"), text_color=FG2)
        # Always re-run the upload on connect: with images it repaints them,
        # without any it still clears stale frames the pad kept in its own
        # memory (issue #41 — a removed plugin icon lingering across reboots).
        self.after(2500, self._start_upload)

    def _on_device_disconnected(self):
        if not self._uploading and not self._animating:
            self._info_label.configure(
                text=self.T("dp_disconnected"), text_color=FG2)

    # ── Plugin API ────────────────────────────────────────────────────────────

    def push_plugin_image(self, key_index, pil_image):
        """Upload a 102x102 PIL Image to a DisplayPad button. Thread-safe.
        Called by plugins to display live widgets on buttons.
        pil_image: PIL Image (any size, will be resized to 102x102).
        key_index: 0-11 (K1-K12).

        Images are always queued. A persistent worker thread (started once
        for the panel's lifetime, see _plugin_upload_worker) drains the
        queue — no new thread is spawned per push, and interface 1/3 stay
        claimed across a run of pushes instead of being released and
        reclaimed for every single image.
        """
        if not (0 <= key_index <= 11):
            return
        # Plugins put their frame into the image maps themselves so the panel
        # tile and a full page upload show it. Note the key, so the frame stays
        # out of the config and does not become that key's icon (#69).
        self._mark_plugin_frame(key_index)
        self._note_plugin_tile(key_index)
        _dbg(f"[DBG push_plugin_image] key={key_index} current_page={self._current_page}")
        img = pil_image.convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
        rot = self._rotation
        if rot:
            img = img.rotate(-rot, expand=False)
        r, g, b = img.split()
        bgr_bytes = Image.merge("RGB", (b, g, r)).tobytes()

        # Don't accumulate frames for a device that nobody will drain (absent
        # and no session running) — that would grow the queue without bound.
        busy = self._animating or self._uploading
        if not (self._device_present or busy):
            return
        # Stamped with the page it was drawn for (#69/#70). A plugin renders in
        # its own thread and the queue is drained later, so without this a frame
        # composed for the page we just left gets uploaded on top of the page we
        # just switched to, and that key keeps the old widget's picture.
        self._upload_queue.put((key_index, bgr_bytes, None, self._current_page))

    def _plugin_upload_worker(self):
        """Persistent thread (started once at panel init, alive for the
        whole app session) that owns interface 1 (pixels) + interface 3
        (commands/keys) for as long as nothing else needs them: plugin image
        uploads AND key-event listening both happen from this one thread now.

        Previously these were two separate concerns that kept trading the
        device back and forth — a standalone key-event listener that closed
        and reopened interface 3 every time a plugin (or a manual upload)
        wanted it, and a plugin worker that itself claimed/released interface
        1+3 around every burst of pushes. Since both ultimately want the same
        session, and reading key events is cheap to do "while idle" between
        pushes, they're now one thread: it opens the device once and simply
        never closes it again on its own — listening for a key IS this
        thread's idle-time job, not a reason to give the device back. It only
        yields (closes + releases the lock) the moment a manual upload,
        GIF animation, or a page switch that's waiting on one of those
        (_page_switch_waiting) explicitly needs exclusive access — that code
        (_worker, below) still runs its own separate, more involved session
        exactly as before; this thread just resumes once it's done.
        """
        usb_dev = hid_dev = None
        holding = False  # True while we hold _usb_lock + interface 1/3
        last_evt = [0] * 64
        last_fire = {}  # key_index -> monotonic time of last action
        not_ready_since = None  # when we first saw a present but un-INIT'd pad

        def _wants_device():
            # Anything that isn't us, wanting exclusive access right now.
            return self._uploading or self._animating or self._page_switch_waiting

        def _release():
            nonlocal usb_dev, hid_dev, holding
            if usb_dev is not None:
                try:
                    _close_interfaces(usb_dev, hid_dev)
                except Exception:
                    pass
                usb_dev = hid_dev = None
            if holding:
                self._key_released.set()
                try:
                    self._usb_lock.release()
                except RuntimeError:
                    pass
                holding = False

        while not (self._plugin_worker_stop.is_set() or self._closing.is_set()):
            # Yield the device immediately the moment anything else wants
            # it — don't linger. A plugin pushing frequently, or a steady
            # stream of key reads, must never starve a manual upload or a
            # pending page switch of the device (see _switch_to_page's
            # _page_switch_waiting comment for the bug this avoids).
            if holding and _wants_device():
                _release()

            # --- Is there a plugin image waiting? Grab at most one without
            # blocking long, so this loop stays responsive to key events
            # and to _wants_device() in between. ---
            try:
                item = self._upload_queue.get(timeout=0.05)
            except queue.Empty:
                item = None

            if _wants_device():
                if item is not None:
                    self._upload_queue.put(item)
                time.sleep(0.05)
                continue

            if not self._device_present:
                if item is not None:
                    self._upload_queue.put(item)  # don't lose it, drain later
                if holding:
                    _release()
                not_ready_since = None  # the grace period below is per connection
                time.sleep(0.2)
                continue

            if not self._pad_ready:
                # Normally the connect upload INITs the pad and flips this
                # flag within a couple of seconds. If that upload never
                # happens or fails, this thread must not wait forever: key
                # events live in here now, so a permanently unready flag
                # would mean a silently dead pad until the next replug. After
                # a grace period we open and INIT it ourselves, which is what
                # sets _pad_ready anyway, so nothing streams pixels to a pad
                # that hasn't booted (issue #43).
                if not_ready_since is None:
                    not_ready_since = time.monotonic()
                if time.monotonic() - not_ready_since < 8.0:
                    if item is not None:
                        self._upload_queue.put(item)
                    time.sleep(0.1)
                    continue
            else:
                not_ready_since = None

            if not holding:
                # Wait for any previous session to actually let go before we
                # try to open (mirrors the old key-event listener's own wait).
                self._key_released.wait(timeout=1.5)
                # Serialise with the manual upload/animation worker so the
                # two can never open the device at the same time (#26).
                self._usb_lock.acquire()
                holding = True
                self._key_released.clear()
                try:
                    usb_dev, hid_dev = _open_interfaces()
                    _init_device(hid_dev)
                    self._pad_ready = True
                    self._last_plugin_error = None
                    last_evt = [0] * 64  # fresh session -- no stale key state
                except Exception as e:
                    self._log_plugin_error(e)
                    if item is not None:
                        self._upload_queue.put(item)  # don't lose this frame
                    _release()
                    time.sleep(0.5)
                    continue

            if item is not None:
                try:
                    self._drain_plugin_queue(usb_dev, hid_dev, first=item)
                except Exception as e:
                    self._log_plugin_error(e)
                    _release()
                continue

            # --- Nothing to push right now: this is what used to be the
            # separate key-event listener's job. Only packets with
            # data[0] == 0x01 are key-event packets; everything else
            # (0x11 init, 0x21 image responses, etc.) is ignored. ---
            try:
                data = hid_dev.read(64, timeout=150)
            except Exception as e:
                self._log_plugin_error(e)
                _release()
                continue
            if not data or len(data) < 48 or data[0] != 0x01:
                continue
            data = list(data)
            now = time.monotonic()
            for k, (bi, mask) in enumerate(_KEY_MAP):
                if bi < len(data) and (data[bi] & mask) and not (last_evt[bi] & mask):
                    # A double-click key uses a short anti-bounce so a
                    # deliberate second tap gets through; every other key
                    # keeps the user's debounce (issue #47).
                    deb = (self._dc_antibounce if self._is_double_key(k) else self._debounce)
                    if now - last_fire.get(k, 0) >= deb:
                        last_fire[k] = now
                        self.after(0, lambda k=k: self._execute_action_k(k))
            last_evt = data

        _release()

    def _plugin_key_slots(self):
        """Key indexes on the live page that a plugin action type owns.

        The page stamp on a queued frame closes the common case, but not the
        instant between the page changing and a plugin's stop() taking effect:
        a frame composed for the old page but pushed a moment later carries the
        new page's number and would be written (#69/#70). A plugin only ever
        paints a key its own action type sits on, so a key the live page has
        given to a static image or to another action is never a plugin's to
        write, whatever the stamp says.

        Returns None when that cannot be established, and then nothing is
        filtered: refusing to paint is worse than painting once too often.
        """
        pm = getattr(self._app, "_plugin_manager", None)
        if pm is None:
            return None
        try:
            types = set(pm.get_action_type_ids())
        except Exception:
            return None
        if not types:
            return None
        if self._current_page not in self._page_actions:
            return None      # nothing known about this page, so do not filter
        actions = self._page_actions[self._current_page]
        # An empty action list is knowledge, not ignorance: a page where
        # nothing is assigned is exactly the page on which no plugin frame
        # belongs. Treating it like "cannot be established" turned the guard
        # off on the page that needed it most (#70, #82).
        return {i for i, a in enumerate(actions)
                if isinstance(a, dict) and a.get("type") in types}

    def _drain_plugin_queue(self, usb_dev, hid_dev, first=None):
        """Upload every queued plugin image in this session — plus `first`,
        if given (an item the caller already popped off the queue) — keeping
        only the latest frame per key. Key presses seen during the upload
        are dispatched so the keys stay responsive while the listener is
        paused."""
        latest = {}
        allowed = self._plugin_key_slots()
        pending = [first] if first is not None else []
        while True:
            if pending:
                ki, bgr, frames, page = pending.pop()
            else:
                try:
                    ki, bgr, frames, page = self._upload_queue.get_nowait()
                except queue.Empty:
                    break
            if frames:
                # A GIF arrived -- hand it back and let _start_upload handle it.
                self._upload_queue.put((ki, bgr, frames, page))
                self.after(0, self._restart_for_gif)
                break
            if page is not None and page != self._current_page:
                # Drawn for a page that is no longer on the device (#69/#70).
                _dbg(f"[DBG drain] dropping stale frame for key {ki} "
                     f"(page {page}, now {self._current_page})")
                continue
            if allowed is not None and ki not in allowed:
                _dbg(f"[DBG drain] dropping frame for key {ki}: no plugin "
                     f"action on it on page {self._current_page}")
                continue
            latest[ki] = bgr
        if not latest:
            return
        key_events = []
        for ki, bgr in sorted(latest.items()):
            _upload_button(usb_dev, hid_dev, ki, bgr, key_events)
        self._dispatch_plugin_key_events(key_events)

    def _dispatch_plugin_key_events(self, events):
        """Fire actions for key-event packets captured during a plugin upload.
        Rising-edge against a fresh baseline -- the session is brief, so a held
        key fires at most once."""
        prev = [0] * 64
        for evt in events:
            for ki, (bi, mask) in enumerate(_KEY_MAP):
                if bi < len(evt) and (evt[bi] & mask) and not (prev[bi] & mask):
                    self.after(0, lambda ki=ki: self._execute_action_k(ki))
            if len(evt) >= len(prev):
                prev = list(evt[:len(prev)])

    def _restart_for_gif(self):
        if not self._animating and not self._uploading:
            self.after(100, self._start_upload)

    def _log_plugin_error(self, e):
        """Print a plugin upload error only when it changes, so a persistent
        device problem doesn't flood the log with the same line."""
        msg = str(e)
        if msg != self._last_plugin_error:
            self._last_plugin_error = msg
            print(f"[Plugin] DisplayPad upload failed: {msg}", flush=True)
