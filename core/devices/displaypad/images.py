"""Pictures for the DisplayPad: what a key is sent, and what the editor shows.

The pad takes 102x102 pixels in BGR order. Everything here works on files
and PIL images only; an interface wraps the PIL images it gets back in its
own image type (the CustomTkinter panel makes CTkImages of them).

Moved here unchanged from devices/displaypad/panel.py (4.0), which still
offers each function under its old underscore name.
"""
import os
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageOps

from shared.config import CONFIG_DIR, FRAME_DIR
from core.devices.displaypad.protocol import (
    ICON_SIZE, KEYS_PER_ROW, NUM_KEYS, PANEL_H, PANEL_W)

TILES_DIR = os.path.join(CONFIG_DIR, "displaypad_tiles")

# Linux font locations; elsewhere PIL's own bitmap font is used.
_FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf")


# ── What the pad is sent ──────────────────────────────────────────────────────

def _bgr(img):
    r, g, b = img.split()
    return Image.merge("RGB", (b, g, r)).tobytes()


def image_to_bgr(path, rotation=0, text=None):
    """A key's picture file as the pad wants it: 102x102, rotated, BGR.
    `text` (a key's text overlay, see draw_text_overlay) is drawn on first."""
    img = Image.open(path).convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    if text:
        draw_text_overlay(img, text)
    if rotation:
        img = img.rotate(-rotation, expand=False)  # PIL rotates CCW, we want CW
    return _bgr(img)


def pil_to_bgr(pil_image, rotation=0):
    """A PIL image (a plugin's frame, any size) as the pad wants it."""
    img = pil_image.convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    if rotation:
        img = img.rotate(-rotation, expand=False)
    return _bgr(img)


def rotate_bgr(bgr, rotation):
    """Rotate one key's BGR pixels clockwise by `rotation` degrees. The bytes
    go through PIL as if they were RGB, which keeps the channel order."""
    if not rotation:
        return bgr
    fr = Image.frombytes("RGB", (ICON_SIZE, ICON_SIZE), bgr)
    return fr.rotate(-rotation, expand=False).tobytes()


def split_image_to_tiles(path, tiles_dir=None):
    """Split a full image into 12 tiles, save as PNGs, return list of 12 paths."""
    tiles_dir = tiles_dir or TILES_DIR
    os.makedirs(tiles_dir, exist_ok=True)
    grid_w = ICON_SIZE * KEYS_PER_ROW
    grid_h = ICON_SIZE * (NUM_KEYS // KEYS_PER_ROW)
    img = Image.open(path).convert("RGB").resize((grid_w, grid_h), Image.LANCZOS)
    paths = []
    for idx in range(NUM_KEYS):
        row = idx // KEYS_PER_ROW
        col = idx % KEYS_PER_ROW
        x, y = col * ICON_SIZE, row * ICON_SIZE
        tile = img.crop((x, y, x + ICON_SIZE, y + ICON_SIZE))
        tile_path = os.path.join(tiles_dir, f"tile_{idx}.png")
        tile.save(tile_path)
        paths.append(tile_path)
    return paths


class PanelSource:
    """A picture or GIF for the whole 800 x 240 panel (fullscreen). Frames are
    cut to the panel's shape (cover) and turned to BGR one at a time, so a long
    GIF is never all in memory at once.

        src = PanelSource(path)
        src.animated, src.count, src.duration(i)
        src.frame(i, rotation)   # BGR bytes, PANEL_W * PANEL_H * 3
        src.close()
    """

    def __init__(self, path):
        self._img = Image.open(path)
        n = getattr(self._img, "n_frames", 1)
        self._durations = []
        for i in range(n):
            self._img.seek(i)
            self._durations.append(max(int(self._img.info.get("duration") or 100), 20))
        self._img.seek(0)
        self.count = n
        self.animated = n > 1
        self._still = None

    def duration(self, i):
        return self._durations[i % self.count]

    def frame(self, i=0, rotation=0):
        if not self.animated and self._still is not None and not rotation:
            return self._still
        self._img.seek(i % self.count)
        im = ImageOps.fit(self._img.convert("RGB"), (PANEL_W, PANEL_H), Image.LANCZOS)
        if rotation % 360 == 180:       # the panel is wide: only a half turn makes sense
            im = im.rotate(180)
        out = _bgr(im)
        if not self.animated and not rotation:
            self._still = out
        return out

    def close(self):
        try:
            self._img.close()
        except Exception:
            pass


def _open_animated(path):
    """The opened image if it is an animation, else None."""
    try:
        img = Image.open(path)
        if not getattr(img, 'is_animated', False) and getattr(img, 'n_frames', 1) <= 1:
            return None
    except Exception:
        return None
    return img


def _grid_tiles(path, tile_size, convert):
    """{key: [(convert(tile), duration_ms), ...]} for an animation split
    across all twelve keys, or None if it is not animated."""
    img = _open_animated(path)
    if img is None:
        return None
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
                result[idx].append((convert(tile), duration))
    except EOFError:
        pass
    return result if result[0] and len(result[0]) > 1 else None


def split_gif_to_tiles(path):
    """Split animated GIF into 12 synchronized tile frame lists.
    Returns {key_idx: [(bgr_bytes, duration_ms), ...]} or None if not animated.
    """
    return _grid_tiles(path, ICON_SIZE, _bgr)


def _frames(path, size, convert):
    img = _open_animated(path)
    if img is None:
        return None
    frames = []
    try:
        for i in range(img.n_frames):
            img.seek(i)
            duration = max(img.info.get('duration', 100), 20)
            frame = img.convert("RGB").resize((size, size), Image.LANCZOS)
            frames.append((convert(frame), duration))
    except EOFError:
        pass
    return frames if len(frames) > 1 else None


def load_gif_frames(path, text=None):
    """Extract all frames from a GIF. Returns [(bgr_bytes, duration_ms), ...] or None.
    `text` (a text overlay) is drawn on every frame."""
    if not text:
        return _frames(path, ICON_SIZE, _bgr)

    def convert(frame):
        draw_text_overlay(frame, text)
        return _bgr(frame)
    return _frames(path, ICON_SIZE, convert)


# ── What an editor shows (PIL images; the interface wraps them) ───────────────

def gif_display_tiles(path, size):
    """An animated GIF split across the twelve keys for a preview:
    {key_idx: [(PIL image, duration_ms), ...]} or None."""
    return _grid_tiles(path, size, lambda tile: tile)


def gif_display_frames(path, size):
    """All frames of an animated GIF for a preview: [(PIL image, ms), ...] or None."""
    return _frames(path, size, lambda frame: frame)


def thumb_image(path, size, rotation=0):
    """A key's picture at preview size; a grey square if it cannot be read."""
    try:
        img = Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)
    except Exception:
        img = Image.new("RGB", (size, size), (40, 40, 50))
    if rotation:
        img = img.rotate(-rotation, expand=False)
    return img


def gif_thumb_image(path, size, rotation=0):
    """thumb_image() with a GIF badge in the corner."""
    img = thumb_image(path, size, rotation)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, size - 14, 28, size], fill=(20, 20, 20))
    draw.text((3, size - 13), "GIF", fill=(80, 220, 80))
    return img


def placeholder_image(size):
    """An empty key: a grey square with a plus."""
    img = Image.new("RGB", (size, size), (40, 40, 50))
    draw = ImageDraw.Draw(img)
    draw.text((size // 2 - 6, size // 2 - 6), "+", fill=(100, 100, 120))
    return img


def plugin_tile_image(label, size):
    """A small tile with a frame and a plugin action's label on it."""
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
    return img


# ── Icons the application draws itself ───────────────────────────────────────

# Every picture the application draws for a key is named the same way: what
# it is, which page, which key. The widget plugins already write
# dp_<plugin>_p<page>_k<key>.png, and the application did not (#95): labels
# were dp_label_<page>_<key>.png, and folder icons dropped the page entirely
# on page 0, which is a legacy form from before sub-pages existed.
ICON_KINDS = ("label", "folder")


def generated_icon_name(kind, page, idx):
    """Config path of an icon the application draws itself."""
    return os.path.join(CONFIG_DIR, f"dp_{kind}_p{page}_k{idx}.png")


def legacy_icon_names(kind, page, idx):
    """What that same icon was called before the scheme was settled."""
    names = [f"dp_{kind}_{page}_{idx}.png"]
    if kind == "folder" and page == 0:
        names.append(f"dp_{kind}_{idx}.png")   # older still, page 0 only
    return [os.path.join(CONFIG_DIR, n) for n in names]


_DRAWN_NAME = re.compile(r"^dp_([a-z_]+?)_p(\d+)_k(\d+)\.png$")
_FRAME_HOMES = {os.path.abspath(FRAME_DIR), os.path.abspath(CONFIG_DIR)}


def widget_frame_of(path):
    """(page, key) a widget plugin drew this frame for, or None.

    Read from the name, which every bundled and published widget follows. A
    picture of the application's own (a label or a folder icon) is not a
    frame, and nor is anything outside the two places widgets write to, so a
    user's own image is never mistaken for one. Frames live in FRAME_DIR, in
    memory (#105); a widget not updated since still writes them to the config
    directory."""
    if not path or not isinstance(path, str):
        return None
    if os.path.dirname(os.path.abspath(path)) not in _FRAME_HOMES:
        return None
    m = _DRAWN_NAME.match(os.path.basename(path))
    if not m or m.group(1) in ICON_KINDS:
        return None
    return int(m.group(2)), int(m.group(3))


def _font(size):
    from PIL import ImageFont
    for p in _FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_folder_icon(base_path, label, out_path):
    """Render label text on top of the DPFolder.png icon and save to out_path."""
    img = Image.open(base_path).convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    if label:
        draw = ImageDraw.Draw(img)
        font = _font(16)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        x = max(2, (ICON_SIZE - tw) // 2)
        # Draw with shadow for readability
        draw.text((x + 1, 5), label, fill=(0, 0, 0), font=font)
        draw.text((x, 4), label, fill=(255, 255, 255), font=font)
    img.save(out_path, "PNG")
    return out_path


def double_caption(double):
    """What a key's auto label shows under its own caption for its
    double-click action, or "" when there is nothing short to show (#111).
    A keystroke or a text is shown as itself and a page switch as where it
    goes; a command or an address would not fit and is left out."""
    if not isinstance(double, dict):
        return ""
    kind = double.get("type", "none")
    if kind in ("keypress", "text"):
        return str(double.get("action", "")).strip()
    if kind == "page":
        where = str(double.get("target") or double.get("action") or "").strip()
        return "→ " + where if where else ""
    return ""


def make_label_icon(text, out_path, sub="", only_if_changed=False):
    """Render a short label centered on a dark tile — the auto-generated icon
    for keypress/text actions so those keys aren't blank (issue #31).

    `sub` goes underneath in a smaller, dimmer face: the key's double-click
    action, so F1 with F13 on a double-click says both (#111). With
    `only_if_changed` the file is left alone when it already holds this
    picture, which keeps a redraw of every label from rewriting them all."""
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (28, 28, 36))
    draw = ImageDraw.Draw(img)

    # The second line first: what it takes is what the caption has left.
    sub = (sub or "").strip()
    sub_h = 0
    if sub:
        for size in (15, 13, 11):
            sub_font = _font(size)
            if draw.textlength(sub, font=sub_font) <= ICON_SIZE - 8:
                break
        while len(sub) > 1 and draw.textlength(sub, font=sub_font) > ICON_SIZE - 8:
            sub = sub[:-2] + "…"
        sub_h = draw.textbbox((0, 0), "Ag", font=sub_font)[3] + 8
    room = ICON_SIZE - 6 - sub_h

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
        if line_h * len(lines) <= room and all(
                draw.textlength(ln, font=font) <= ICON_SIZE - 6 for ln in lines):
            break
    total_h = line_h * len(lines)
    y = max(2, (room + 6 - total_h) // 2)
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        x = max(2, (ICON_SIZE - tw) // 2)
        draw.text((x + 1, y + 1), ln, fill=(0, 0, 0), font=font)
        draw.text((x, y), ln, fill=(255, 255, 255), font=font)
        y += line_h
    if sub:
        sy = ICON_SIZE - sub_h + 2
        draw.line([(16, sy - 3), (ICON_SIZE - 17, sy - 3)], fill=(60, 60, 78))
        tw = draw.textlength(sub, font=sub_font)
        draw.text((max(2, (ICON_SIZE - tw) // 2), sy), sub,
                  fill=(160, 160, 185), font=sub_font)
    if only_if_changed and os.path.exists(out_path):
        try:
            with Image.open(out_path) as old:
                if old.convert("RGB").tobytes() == img.tobytes():
                    return out_path
        except Exception:
            pass
    img.save(out_path, "PNG")
    return out_path


# ── Text over a key's picture ────────────────────────────────────────────────
#
# A key may carry `text` next to its picture: {text, pos, font, size_pt, bold,
# italic, underline, color}. It is drawn on the picture just before it is
# sent (every frame of a GIF, and a black tile when the key has no picture),
# with a 1 px shadow in the opposite colour. Generated label tiles are not
# touched by this.

TEXT_MAX_CHARS = 50
TEXT_POSITIONS = ("top", "bottom", "center")
TEXT_COLORS = ("white", "black")
TEXT_SIZE_MIN, TEXT_SIZE_MAX = 6, 18
TEXT_DEFAULT_FONT = "DejaVu Sans"

# The fonts offered when fc-list is not there, and where their files are
# (regular, bold, italic, bold italic).
_FALLBACK_FONTS = {
    "DejaVu Sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
                    "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
    "DejaVu Serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf",
                     "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf"),
    "DejaVu Sans Mono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
                         "DejaVuSansMono-Oblique.ttf", "DejaVuSansMono-BoldOblique.ttf"),
    "Liberation Sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf",
                        "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    "Liberation Serif": ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf",
                         "LiberationSerif-Italic.ttf", "LiberationSerif-BoldItalic.ttf"),
    "Liberation Mono": ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf",
                        "LiberationMono-Italic.ttf", "LiberationMono-BoldItalic.ttf"),
}
_STYLE_KEYS = ((False, False), (True, False), (False, True), (True, True))
_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts",
              os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts"))

_font_table = None     # family -> {(bold, italic): file}


def _scan_fc_list():
    """{family: {(bold, italic): file}} from fc-list, or {} when it is not
    there or says nothing."""
    exe = shutil.which("fc-list")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "--format", "%{family[0]}\\t%{style[0]}\\t%{file}\\n"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        family, style, path = (x.strip() for x in parts)
        if not family or not path.lower().endswith((".ttf", ".otf")):
            continue
        low = style.lower()
        bold = "bold" in low and "semi" not in low and "demi" not in low
        italic = "italic" in low or "oblique" in low
        table.setdefault(family, {}).setdefault((bold, italic), path)
    return table


def _scan_fallback():
    """The fallback fonts that are installed, found by file name."""
    wanted = {f for files in _FALLBACK_FONTS.values() for f in files}
    found = {}
    for base in _FONT_DIRS:
        for root, _dirs, files in os.walk(base):
            for name in files:
                if name in wanted:
                    found.setdefault(name, os.path.join(root, name))
    table = {}
    for family, files in _FALLBACK_FONTS.items():
        styles = {key: found[f] for key, f in zip(_STYLE_KEYS, files) if f in found}
        if styles:
            table[family] = styles
    return table


def font_table(refresh=False):
    """{family: {(bold, italic): file}} for the fonts a key's text can use."""
    global _font_table
    if _font_table is None or refresh:
        table = _scan_fc_list()
        for family, styles in _scan_fallback().items():
            table.setdefault(family, styles)
        _font_table = table
    return _font_table


def list_fonts():
    """The font families to offer, sorted; the six fallback names when the
    system has none of them (the drawing then uses PIL's own font)."""
    names = sorted(font_table(), key=str.lower)
    return names or list(_FALLBACK_FONTS)


def _font_file(family, bold, italic):
    """(file, bold_ok): the file of `family` that best matches the style, and
    whether it is a bold face when bold was asked for. (None, False) when no
    font file can be found."""
    table = font_table()
    styles = table.get(family) or table.get(TEXT_DEFAULT_FONT)
    if not styles:
        for p in _FONT_PATHS:
            if os.path.exists(p):
                return p, True
        return None, False
    for key in ((bold, italic), (bold, False), (False, italic), (False, False)):
        if key in styles:
            return styles[key], key[0] == bold
    return next(iter(styles.values())), False


def text_px(size_pt):
    """Pixels on the 102 px tile for a point size: 12 pt is 16 px, the
    caption size the folder icons use."""
    return max(6, round(size_pt * 4 / 3))


def clean_text_overlay(cfg):
    """A key's text overlay as stored: the dict with every field in range, or
    None when there is nothing to draw. Raises ValueError for something that
    is not a dict."""
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise ValueError("text must be an object or null")
    text = str(cfg.get("text") or "").replace("\r", "").strip()[:TEXT_MAX_CHARS]
    if not text:
        return None
    font = cfg.get("font")
    try:
        size = int(cfg.get("size_pt", 12))
    except (TypeError, ValueError):
        size = 12
    return {
        "text": text,
        "pos": cfg.get("pos") if cfg.get("pos") in TEXT_POSITIONS else "bottom",
        "font": font.strip() if isinstance(font, str) and font.strip() else TEXT_DEFAULT_FONT,
        "size_pt": max(TEXT_SIZE_MIN, min(TEXT_SIZE_MAX, size)),
        "bold": bool(cfg.get("bold")),
        "italic": bool(cfg.get("italic")),
        "underline": bool(cfg.get("underline")),
        "color": cfg.get("color") if cfg.get("color") in TEXT_COLORS else "white",
    }


def stored_text_overlay(cfg):
    """clean_text_overlay for what a config file holds: junk gives None."""
    try:
        return clean_text_overlay(cfg)
    except ValueError:
        return None


def _wrap(draw, text, font, width):
    """Lines of `text` no wider than `width`: at spaces, and inside a word
    that is too long on its own."""
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while draw.textlength(word, font=font) > width and len(word) > 1:
            n = len(word) - 1
            while n > 1 and draw.textlength(word[:n], font=font) > width:
                n -= 1
            lines.append(word[:n])
            word = word[n:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_text_overlay(img, cfg):
    """Draw a text overlay (see clean_text_overlay) on the RGB image `img`,
    in place, and return it."""
    cfg = stored_text_overlay(cfg)
    if not cfg:
        return img
    from PIL import ImageFont
    w, h = img.size
    scale = w / ICON_SIZE
    px = max(6, round(text_px(cfg["size_pt"]) * scale))
    path, bold_ok = _font_file(cfg["font"], cfg["bold"], cfg["italic"])
    try:
        font = ImageFont.truetype(path, px) if path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    fake_bold = cfg["bold"] and not bold_ok
    draw = ImageDraw.Draw(img)
    margin = max(2, round(4 * scale))
    lines = _wrap(draw, cfg["text"], font, w - 2 * margin - 1)
    try:
        ascent, descent = font.getmetrics()
    except Exception:
        ascent, descent = px, 0
    line_h = ascent + descent + max(1, round(scale))
    total = line_h * len(lines)
    if cfg["pos"] == "top":
        y = margin
    elif cfg["pos"] == "center":
        y = (h - total) // 2
    else:
        y = h - margin - total
    y = max(0, y)
    main = (255, 255, 255) if cfg["color"] == "white" else (0, 0, 0)
    shade = (0, 0, 0) if cfg["color"] == "white" else (255, 255, 255)
    one = max(1, round(scale))
    offsets = (0, one) if fake_bold else (0,)
    for n, line in enumerate(lines):
        tw = draw.textlength(line, font=font) + (one if fake_bold else 0)
        x = max(0, round((w - tw) / 2))
        ly = y + n * line_h
        for fill, d in ((shade, one), (main, 0)):
            for ox in offsets:
                draw.text((x + d + ox, ly + d), line, fill=fill, font=font)
            if cfg["underline"]:
                uy = ly + ascent + 1
                draw.line([(x + d, uy + d), (x + d + tw - 1, uy + d)],
                          fill=fill, width=one)
    return img


def overlay_bgr(bgr, cfg):
    """A key's BGR pixels with the text overlay drawn on. White and black
    read the same in either channel order, so the bytes are drawn on as they
    are."""
    if not stored_text_overlay(cfg):
        return bgr
    img = Image.frombytes("RGB", (ICON_SIZE, ICON_SIZE), bgr)
    return draw_text_overlay(img, cfg).tobytes()


def blank_bgr_with_text(cfg):
    """A black key tile with the text on it, as BGR."""
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (0, 0, 0))
    return draw_text_overlay(img, cfg).tobytes()
