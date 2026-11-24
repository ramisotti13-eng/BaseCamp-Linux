"""Live DisplayPad keys: a clock and PC info, drawn by the application.

Two action types draw their key themselves and keep it up to date:

  clock     the time. The value is a list of options, e.g. "digital,24h" or
            "analog,12h,seconds": digital or analog, 24h or 12h, with or
            without seconds. Drawn again at each minute, or each second with
            seconds on.
  pc_info   one number of the computer: cpu, ram, disk (the disk holding /),
            net (MB/s sent and received), or gpu where the driver tells it.
            Read every two seconds (core/metrics.py) and sent only when the
            picture changed.

They take the path the widget plugins take (see pages.py and service.py): a
frame is written to the frame directory under a widget's name,
dp_live_p<page>_k<key>.png, put into the live image map (so the editor
shows it and a full page upload sends it), and queued for the device worker,
which sends it while it holds the pad. So the frame stays out of the stored
configuration, a page switch keeps it on its own key and nowhere else, and a
GIF animation on the same page takes it along. A GIF of its own would not do:
its frames are drawn in advance, and a clock is not.

LiveKeys runs on the core scheduler, as everything of the service does: one
timer, armed for the next key that is due, and only for the keys of the page
on the pad. Nothing is drawn for a page that is not shown, and nothing at
all while paused (the pad dark or showing its lock screen) or after stop().

Also here, the pictures the lock screen spreads across the pad (612 x 204,
split into the twelve keys): a large clock, the user's own picture, and the
BaseCamp Linux logo.
"""
import math
import os
import re
import time

from PIL import Image, ImageDraw, ImageOps

from core.devices.displaypad import images as dpi
from core.devices.displaypad.actions import LIVE_TYPES  # noqa: F401 (named here too)
from core.devices.displaypad.protocol import ICON_SIZE, KEYS_PER_ROW, NUM_KEYS

PC_INFO_NAMES = ("cpu", "ram", "disk", "net", "gpu")

SCREEN_W = ICON_SIZE * KEYS_PER_ROW                 # 612
SCREEN_H = ICON_SIZE * (NUM_KEYS // KEYS_PER_ROW)   # 204

_BG = (12, 14, 20)
_FG = (255, 255, 255)
_DIM = (150, 156, 175)
_ACCENT = (0, 56, 226)          # the logo's blue
_TRACK = (44, 48, 62)
_SECOND = (253, 157, 2)         # the penguin's beak


# ── Options ───────────────────────────────────────────────────────────────────

def parse_clock(value):
    """{style: digital|analog, hours: 24|12, seconds: bool} from a clock key's
    value; anything not understood keeps its default (digital, 24h, none)."""
    toks = {t for t in re.split(r"[,;\s]+", str(value or "").lower()) if t}
    return {"style": "analog" if "analog" in toks else "digital",
            "hours": 12 if toks & {"12h", "12"} else 24,
            "seconds": bool(toks & {"seconds", "sec"})}


def clock_value(style="digital", hours=24, seconds=False):
    """The stored value for those options (the inverse of parse_clock)."""
    return ",".join([("analog" if style == "analog" else "digital"),
                     "12h" if int(hours) == 12 else "24h"]
                    + (["seconds"] if seconds else []))


def parse_pc_info(value):
    """Which number a PC-info key shows; cpu when the value says none."""
    v = str(value or "").strip().lower()
    return v if v in PC_INFO_NAMES else "cpu"


def key_spec(action):
    """("clock", options) or ("pc_info", name) for a live key's action dict,
    else None."""
    if not isinstance(action, dict):
        return None
    kind = action.get("type")
    if kind == "clock":
        return ("clock", parse_clock(action.get("action")))
    if kind == "pc_info":
        return ("pc_info", parse_pc_info(action.get("action")))
    return None


def period(spec):
    """Seconds between two drawings of a key."""
    if spec[0] == "clock":
        return 1.0 if spec[1]["seconds"] else 60.0
    return LiveKeys.PC_INFO_SECONDS


def next_due(spec, t):
    """When a key drawn at `t` is to be drawn next. A clock follows the
    clock on the wall: the next full second or minute."""
    if spec[0] == "clock":
        step = period(spec)
        return (math.floor(t / step + 1e-9) + 1) * step
    return t + LiveKeys.PC_INFO_SECONDS


# ── Drawing a key ─────────────────────────────────────────────────────────────

def _fit(draw, text, max_w, sizes):
    """The largest of `sizes` at which `text` is at most `max_w` wide."""
    font = None
    for size in sizes:
        font = dpi._font(size)
        if draw.textlength(text, font=font) <= max_w:
            return font
    return font


def _centered(draw, cy, text, font, fill, width=ICON_SIZE, x0=0):
    """Draw `text` centered on x and on the line y = cy."""
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.text((x0 + (width - w) / 2 - box[0], cy - h / 2 - box[1]), text,
              font=font, fill=fill)


def _clock_parts(lt, hours, seconds):
    """("14:05[:09]", None) or ("2:05[:09]", "PM")."""
    h = lt.tm_hour
    ampm = None
    if hours == 12:
        ampm = "AM" if h < 12 else "PM"
        h = h % 12 or 12
        text = "%d:%02d" % (h, lt.tm_min)
    else:
        text = "%02d:%02d" % (h, lt.tm_min)
    if seconds:
        text += ":%02d" % lt.tm_sec
    return text, ampm


def _date(lt, hours, year=False):
    if hours == 12:
        return "%02d/%02d" % (lt.tm_mon, lt.tm_mday) + ("/%d" % lt.tm_year if year else "")
    return "%02d.%02d." % (lt.tm_mday, lt.tm_mon) + ("%d" % lt.tm_year if year else "")


def render_clock_key(t, opts):
    """A clock key showing the local time at epoch seconds `t`."""
    lt = time.localtime(t)
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), _BG)
    draw = ImageDraw.Draw(img)
    if opts.get("style") == "analog":
        _analog(draw, lt, opts.get("seconds"))
        return img
    text, ampm = _clock_parts(lt, opts.get("hours", 24), opts.get("seconds"))
    font = _fit(draw, text, ICON_SIZE - 8, (38, 34, 30, 27, 24, 21, 18))
    _centered(draw, 42 if ampm else 46, text, font, _FG)
    if ampm:
        _centered(draw, 70, ampm, dpi._font(15), (110, 150, 255))
    _centered(draw, 90, _date(lt, opts.get("hours", 24)), dpi._font(12), _DIM)
    return img


def _analog(draw, lt, seconds):
    cx = cy = ICON_SIZE / 2
    r = ICON_SIZE / 2 - 5
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(20, 22, 30),
                 outline=(80, 86, 104), width=2)

    def point(deg, length):
        a = math.radians(deg)
        return cx + length * math.sin(a), cy - length * math.cos(a)

    for i in range(12):
        quarter = i % 3 == 0
        draw.line([point(i * 30, r - (12 if quarter else 8)), point(i * 30, r - 3)],
                  fill=_FG if quarter else _DIM, width=3 if quarter else 2)
    sec = lt.tm_sec if seconds else 0
    minute = lt.tm_min + sec / 60.0
    hour = (lt.tm_hour % 12) + minute / 60.0
    draw.line([(cx, cy), point(hour * 30, r * 0.52)], fill=_FG, width=5)
    draw.line([(cx, cy), point(minute * 6, r * 0.80)], fill=_FG, width=3)
    if seconds:
        draw.line([point(lt.tm_sec * 6 + 180, r * 0.18), point(lt.tm_sec * 6, r * 0.86)],
                  fill=_SECOND, width=1)
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=_SECOND if seconds else _FG)


_PC_LABELS = {"cpu": "CPU", "ram": "RAM", "disk": "DISK", "net": "NET", "gpu": "GPU"}


def _rate(mbs):
    """("1.2", "MB/s") for a rate in MB/s."""
    if mbs < 1.0:
        return "%d" % round(mbs * 1000), "KB/s"
    if mbs < 10.0:
        return "%.1f" % mbs, "MB/s"
    return "%d" % round(mbs), "MB/s"


def render_pc_info_key(name, value):
    """A PC-info key: what it shows, the number, and a bar for a percentage.
    `value` is a percentage (MB/s for net), or None when unknown. Numbers
    are rounded as shown, so the picture changes only when they do."""
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), _BG)
    draw = ImageDraw.Draw(img)
    _centered(draw, 15, _PC_LABELS.get(name, name.upper()), dpi._font(15), _DIM)
    if value is None:
        _centered(draw, 52, "–", dpi._font(34), _FG)
        return img
    if name == "net":
        text, unit = _rate(max(0.0, float(value)))
        _centered(draw, 50, text, _fit(draw, text, ICON_SIZE - 10, (36, 32, 28, 24)), _FG)
        _centered(draw, 82, unit, dpi._font(14), _DIM)
        return img
    pct = max(0, min(100, int(round(float(value)))))
    text = "%d%%" % pct
    _centered(draw, 50, text, _fit(draw, text, ICON_SIZE - 10, (36, 32, 28)), _FG)
    x0, x1, y0, y1 = 12, ICON_SIZE - 12, 78, 86
    draw.rectangle([x0, y0, x1, y1], fill=_TRACK)
    if pct:
        draw.rectangle([x0, y0, x0 + round((x1 - x0) * pct / 100), y1], fill=_ACCENT)
    return img


def render_key(spec, t, values):
    if spec[0] == "clock":
        return render_clock_key(t, spec[1])
    return render_pc_info_key(spec[1], values.get(spec[1]))


# ── The lock screen: one picture across the pad ───────────────────────────────

def render_clock_screen(t, hours=24):
    """The time across the upper row of keys, the date across the lower."""
    lt = time.localtime(t)
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    text, ampm = _clock_parts(lt, hours, False)
    if ampm:
        text += " " + ampm
    font = _fit(draw, text, SCREEN_W - 40, (84, 76, 68, 60, 52, 44))
    _centered(draw, ICON_SIZE / 2, text, font, _FG, width=SCREEN_W)
    _centered(draw, ICON_SIZE * 1.5, _date(lt, hours, year=True), dpi._font(40), _DIM,
              width=SCREEN_W)
    return img


_LOGO_CACHE = {}


def _logo_mark(path):
    """The wedge and penguin of resources/logo.png, without the word under
    them: the rows down to the first fully clear row after the drawing
    starts. The whole picture if there is no such gap."""
    key = (path, os.path.getmtime(path))
    if key in _LOGO_CACHE:
        return _LOGO_CACHE[key]
    logo = Image.open(path).convert("RGBA")
    alpha = logo.getchannel("A")
    box = alpha.getbbox() or (0, 0, logo.width, logo.height)
    bottom = box[3]
    seen = False
    for y in range(box[1], box[3]):
        filled = alpha.crop((0, y, logo.width, y + 1)).getbbox() is not None
        if filled:
            seen = True
        elif seen:
            bottom = y
            break
    mark = logo.crop((box[0], box[1], box[2], bottom))
    mark = mark.crop(mark.getchannel("A").getbbox() or (0, 0, mark.width, mark.height))
    _LOGO_CACHE.clear()
    _LOGO_CACHE[key] = mark
    return mark


def logo_path(res_path):
    return os.path.join(res_path, "resources", "logo.png")


def render_logo_screen(res_path):
    """The BaseCamp Linux logo (the penguin on the wedge), centered on black."""
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (0, 0, 0))
    try:
        mark = _logo_mark(logo_path(res_path))
    except Exception:
        return img
    mark = mark.copy()
    mark.thumbnail((SCREEN_W - 40, SCREEN_H - 24), Image.LANCZOS)
    img.paste(mark, ((SCREEN_W - mark.width) // 2, (SCREEN_H - mark.height) // 2), mark)
    return img


def render_startup_logo(res_path, size=(800, 240), margin=20):
    """resources/logo.png (the whole logo) centred on black, scaled to fit
    inside `size` with `margin` pixels all round: the start-up picture."""
    img = Image.new("RGB", size, (0, 0, 0))
    logo = Image.open(logo_path(res_path)).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").getbbox() or (0, 0, logo.width, logo.height))
    logo.thumbnail((size[0] - 2 * margin, size[1] - 2 * margin), Image.LANCZOS)
    img.paste(logo, ((size[0] - logo.width) // 2, (size[1] - logo.height) // 2), logo)
    return img


def render_picture_screen(path):
    """The user's picture filling the pad, cut to its shape (not stretched);
    None if it cannot be read."""
    try:
        with Image.open(path) as im:
            im.seek(0)
            pic = im.convert("RGB")
    except Exception:
        return None
    return ImageOps.fit(pic, (SCREEN_W, SCREEN_H), Image.LANCZOS)


def split_screen(img):
    """The twelve key pictures of a 612 x 204 picture, key 0 top left."""
    tiles = []
    for idx in range(NUM_KEYS):
        x = (idx % KEYS_PER_ROW) * ICON_SIZE
        y = (idx // KEYS_PER_ROW) * ICON_SIZE
        tiles.append(img.crop((x, y, x + ICON_SIZE, y + ICON_SIZE)))
    return tiles


def save_png(img, path):
    """Write a picture so that a reader never finds it half written."""
    tmp = path + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, path)


# ── Keeping the keys up to date ───────────────────────────────────────────────

class LiveKeys:
    """The clock and PC-info keys of the page on the pad.

    `model` is the PageModel, `scheduler` the core scheduler (every method
    runs there), `push(idx, image, send)` the service's part: announce the
    frame and, if `send`, queue it for the pad. `now()` is the wall clock in
    epoch seconds and `sampler` a core.metrics.SystemSampler (made when the
    first PC-info key needs one); both are seams for the tests.
    """

    PC_INFO_SECONDS = 2.0
    #: A timer this early still draws the time it was armed for.
    EARLY = 0.05
    #: Fired this long after a key is due, so a clock lands past the boundary.
    SLACK_MS = 15

    def __init__(self, model, scheduler, push, now=time.time, sampler=None,
                 frame_dir=None):
        self.model = model
        self.scheduler = scheduler
        self.push = push
        self.now = now
        self._sampler = sampler
        self.frame_dir = frame_dir
        self.page = None
        self.specs = {}          # key -> spec, of the page on the pad
        self.due = {}            # key -> wall time it is to be drawn again
        self.last = {}           # key -> bytes of the picture last filed
        self.handle = None
        self.paused = False
        self.stopped = False
        self.drawn = 0           # pictures drawn (for the tests)
        self.sent = 0            # pictures that changed and were filed

    # ── What there is ─────────────────────────────────────────────────────────

    @property
    def sampler(self):
        if self._sampler is None:
            from core.metrics import SystemSampler
            self._sampler = SystemSampler()
        return self._sampler

    def pc_info_names(self):
        """The numbers this computer can show (gpu only where it is read)."""
        try:
            return [n for n in PC_INFO_NAMES if n in self.sampler.available()]
        except Exception:
            return list(PC_INFO_NAMES[:4])

    def _page_specs(self):
        acts = self.model.page_actions.get(self.model.current_page) or []
        out = {}
        for idx, act in enumerate(acts[:NUM_KEYS]):
            if idx in self.model.fullscreen_group:
                continue         # a fullscreen GIF covers the key
            spec = key_spec(act)
            if spec is not None:
                out[idx] = spec
        return out

    def frame_path(self, page, idx):
        if self.frame_dir is None:
            from shared.config import FRAME_DIR
            self.frame_dir = FRAME_DIR
        return os.path.join(self.frame_dir, "dp_live_p%d_k%d.png" % (page, idx))

    # ── Following the page ────────────────────────────────────────────────────

    def refresh(self, push=True):
        """Read the page's live keys again and draw them now. `push` False
        when the whole page is about to be sent anyway."""
        if self.stopped:
            return
        if self.model.current_page != self.page:
            self.page = self.model.current_page
            self.last.clear()    # the frames on file are another page's
        self.specs = self._page_specs()
        for idx in list(self.last):
            if idx not in self.specs:
                del self.last[idx]
        self.due = {}
        self._cancel()
        if not self.specs or self.paused:
            return
        self._draw(sorted(self.specs), self.now(), push)
        self._arm()

    def pause(self):
        """Draw nothing until resume(): the pad is dark or locked."""
        self.paused = True
        self._cancel()

    def resume(self, push=True):
        if self.stopped or not self.paused:
            return
        self.paused = False
        self.refresh(push=push)

    def stop(self):
        self.stopped = True
        self._cancel()
        if self._sampler is not None:
            try:
                self._sampler.stop()
            except Exception:
                pass

    # ── The timer ─────────────────────────────────────────────────────────────

    def _cancel(self):
        if self.handle is not None:
            self.scheduler.cancel(self.handle)
            self.handle = None

    def _arm(self):
        self._cancel()
        if self.paused or self.stopped or not self.due:
            return
        now = self.now()
        for idx, due in list(self.due.items()):
            # The wall clock was set back: start that key over from now.
            if due - now > period(self.specs[idx]) + 1.0:
                self.due[idx] = now
        delay = min(self.due.values()) - now
        self.handle = self.scheduler.call_later(
            max(0, int(round(delay * 1000))) + self.SLACK_MS, self._tick)

    def _tick(self):
        self.handle = None
        if self.paused or self.stopped:
            return
        now = self.now()
        due = sorted(i for i, d in self.due.items() if d <= now + self.EARLY)
        if due:
            self._draw(due, now, True)
        self._arm()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self, keys, now, push):
        names = sorted({self.specs[i][1] for i in keys if self.specs[i][0] == "pc_info"})
        values = {}
        if names:
            try:
                values = self.sampler.sample(names)
            except Exception:
                values = {}
        page = self.model.current_page
        for idx in keys:
            spec = self.specs[idx]
            prev = self.due.get(idx)
            t = now if prev is None else max(now, prev)   # a hair early: its time
            if spec[0] == "pc_info" and prev is not None and now - prev < period(spec):
                self.due[idx] = prev + period(spec)       # keep the beat
            else:
                self.due[idx] = next_due(spec, t)
            try:
                img = render_key(spec, t, values)
            except Exception as e:
                print(f"[DisplayPad] live key {idx + 1}: {e}", flush=True)
                continue
            self.drawn += 1
            data = img.tobytes()
            path = self.frame_path(page, idx)
            if (self.last.get(idx) == data and self.model.images.get(str(idx)) == path
                    and os.path.exists(path)):
                continue         # the same picture is already up
            try:
                save_png(img, path)
            except OSError as e:
                print(f"[DisplayPad] live key {idx + 1}: {e}", flush=True)
                continue
            self.last[idx] = data
            self.model.images[str(idx)] = path
            self.sent += 1
            self.push(idx, img, push)
