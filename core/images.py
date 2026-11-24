"""The image editor's core: crop, rotate and flip a picture for a device.

Every place a picture goes to a device (the DisplayPad's keys, the Everest's
D-keys and main display) takes the same steps, as Mountain's Base Camp does
in its Cropper.js modal, except that here the core does them, so they hold
for animated GIFs too (Base Camp sent a GIF on as it was):

    prepare(path, target, crop=None, rotate=0, flip=False)

  1. the picture is turned clockwise by `rotate` (0, 90, 180, 270) and then
     mirrored left to right if `flip`;
  2. `crop` = [x, y, w, h] in the pixels of that turned picture (None: the
     biggest centred box of the target's aspect) is cut out;
  3. a still image becomes a master (450x450 for the square targets, 480x408
     for the main display) on black where it is transparent, saved as PNG in
     the DisplayPad library; a GIF (or animated WebP) keeps every frame, its
     durations and its loop, at twice the device's size, and is saved as GIF.

The device gets the picture scaled down again by the service that sends it
(102x102 for a DisplayPad key, 72x72 for an Everest D-key).

Service "images": prepare {path, target, crop, rotate, flip}, info {path},
delete {path}, targets.
"""
import hashlib
import io
import os

from PIL import Image, ImageSequence

from core.commands import CommandError

#: target -> {aspect (w, h), device size, still master size, GIF size}
TARGETS = {
    "displaypad_key": {"aspect": (1, 1), "device": (102, 102),
                       "master": (450, 450), "animated": (204, 204)},
    # The whole DisplayPad panel as one picture: 800 x 240 (the keys are windows in it).
    "displaypad_fullscreen": {"aspect": (10, 3), "device": (800, 240),
                              "master": (1600, 480), "animated": (800, 240)},
    "everest_dkey": {"aspect": (1, 1), "device": (72, 72),
                     "master": (450, 450), "animated": (144, 144)},
    "everest_main": {"aspect": (240, 204), "device": (240, 204),
                     "master": (480, 408), "animated": (240, 204)},
}

EXTS = (".jpg", ".jpeg", ".bmp", ".png", ".gif", ".webp")

#: Limits (error codes: too_large, too_many_frames).
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40 * 1000 * 1000          # one frame of the source
MAX_FRAMES = 300
MAX_ANIM_PIXELS = 400 * 1000 * 1000    # all source frames together

DEFAULT_MS = 100
MIN_MS = 20


class ImageError(Exception):
    """`code`: not_found, bad_format, too_large, too_many_frames, bad_target,
    bad_crop, bad_rotate, not_allowed."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _open(path):
    if not isinstance(path, str) or not os.path.isfile(path):
        raise ImageError("not_found", "no such file: %s" % (path,))
    if not path.lower().endswith(EXTS):
        raise ImageError("bad_format", "not a supported picture: %s" % os.path.basename(path))
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            raise ImageError("too_large", "the file is bigger than %d MiB" % (MAX_FILE_BYTES >> 20))
        im = Image.open(path)
    except ImageError:
        raise
    except Exception as e:
        raise ImageError("bad_format", "cannot read the picture: %s" % type(e).__name__) from None
    w, h = im.size
    if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        im.close()
        raise ImageError("too_large", "the picture is %dx%d pixels" % (w, h))
    return im


def _frames_of(im):
    return getattr(im, "n_frames", 1) or 1


def info(path):
    """{width, height, animated, frames} of a picture file."""
    im = _open(path)
    try:
        n = _frames_of(im)
        return {"width": im.size[0], "height": im.size[1], "animated": n > 1, "frames": n}
    finally:
        im.close()


#: The editor's preview: longest side, and the biggest GIF kept as it is.
PREVIEW_SIDE = 900
PREVIEW_RAW_BYTES = 6 * 1024 * 1024


def preview(path, max_side=PREVIEW_SIDE):
    """What the editor shows of a picture: {url, width, height, animated,
    frames}; `url` is a data URL, a PNG of a still image, and for an
    animation a GIF with every frame (the file itself when it is small
    enough, else scaled down). width and height are the picture's own."""
    import base64
    try:
        max_side = max(64, min(int(max_side), 2000))
    except (TypeError, ValueError):
        max_side = PREVIEW_SIDE
    im = _open(path)
    try:
        n = _frames_of(im)
        w, h = im.size
        if n > MAX_FRAMES:
            raise ImageError("too_many_frames", "%d frames (at most %d)" % (n, MAX_FRAMES))
        if n * w * h > MAX_ANIM_PIXELS:
            raise ImageError("too_large", "the animation is too big")
        scale = min(1.0, max_side / max(w, h))
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        try:
            if n > 1 and path.lower().endswith(".gif") and scale == 1.0 \
                    and os.path.getsize(path) <= PREVIEW_RAW_BYTES:
                with open(path, "rb") as f:
                    data, mime = f.read(), "image/gif"
            elif n > 1:
                frames, durs = [], []
                for fr in ImageSequence.Iterator(im):
                    durs.append(max(MIN_MS, int(fr.info.get("duration") or DEFAULT_MS)))
                    frames.append(fr.convert("RGBA").resize(size, Image.LANCZOS))
                save = {"save_all": True, "append_images": frames[1:], "duration": durs,
                        "disposal": 2}
                if "loop" in im.info:
                    save["loop"] = im.info["loop"]
                buf = io.BytesIO()
                frames[0].save(buf, "GIF", **save)
                data, mime = buf.getvalue(), "image/gif"
            else:
                im.seek(0)
                fr = im.convert("RGBA")
                if scale < 1.0:
                    fr = fr.resize(size, Image.LANCZOS)
                buf = io.BytesIO()
                fr.save(buf, "PNG")
                data, mime = buf.getvalue(), "image/png"
        except ImageError:
            raise
        except Image.DecompressionBombError:
            raise ImageError("too_large", "the picture is too big") from None
        except (OSError, ValueError, SyntaxError, EOFError) as e:
            raise ImageError("bad_format", "the picture is damaged: %s" % type(e).__name__) from None
        return {"url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii")),
                "width": w, "height": h, "animated": n > 1, "frames": n}
    finally:
        im.close()


def _orient(frame, rotate, flip):
    if rotate == 90:
        frame = frame.transpose(Image.ROTATE_270)      # clockwise
    elif rotate == 180:
        frame = frame.transpose(Image.ROTATE_180)
    elif rotate == 270:
        frame = frame.transpose(Image.ROTATE_90)
    if flip:
        frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
    return frame


def oriented_size(size, rotate):
    w, h = size
    return (h, w) if rotate in (90, 270) else (w, h)


def default_crop(size, aspect):
    """The biggest centred box of `aspect` in a picture of `size`."""
    w, h = size
    aw, ah = aspect
    if w * ah >= h * aw:
        cw, ch = max(1, round(h * aw / ah)), h
    else:
        cw, ch = w, max(1, round(w * ah / aw))
    return [(w - cw) // 2, (h - ch) // 2, cw, ch]


def _check_crop(crop, size):
    try:
        x, y, w, h = [float(v) for v in crop]
    except (TypeError, ValueError):
        raise ImageError("bad_crop", "crop must be [x, y, w, h]") from None
    if not all(v == v and abs(v) < 1e9 for v in (x, y, w, h)) or w < 1 or h < 1:
        raise ImageError("bad_crop", "the crop box is empty")
    W, H = size
    x0, y0 = max(0, round(x)), max(0, round(y))
    x1, y1 = min(W, round(x + w)), min(H, round(y + h))
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise ImageError("bad_crop", "the crop box is outside the picture")
    return (x0, y0, x1, y1)


def _flat(frame):
    """A frame as RGB, on black where it is transparent."""
    if frame.mode == "RGB":
        return frame
    rgba = frame.convert("RGBA")
    bg = Image.new("RGB", rgba.size, (0, 0, 0))
    bg.paste(rgba, mask=rgba.getchannel("A"))
    return bg


def _process(frame, rotate, flip, box, out_size):
    frame = _orient(_flat(frame), rotate, flip).crop(box)
    return frame.resize(out_size, Image.LANCZOS)


def output_dir():
    from core.devices.displaypad import library as dplib
    return dplib.default_dir()


def prepare(path, target, crop=None, rotate=0, flip=False, out_dir=None):
    """Make the device-ready picture (module doc). Returns {path, animated,
    frames, size}. Raises ImageError."""
    spec = TARGETS.get(target)
    if spec is None:
        raise ImageError("bad_target", "unknown target %r (%s)" % (target, ", ".join(TARGETS)))
    try:
        rotate = int(rotate or 0) % 360
    except (TypeError, ValueError):
        raise ImageError("bad_rotate", "rotate must be a multiple of 90") from None
    if rotate % 90:
        raise ImageError("bad_rotate", "rotate must be a multiple of 90")
    flip = bool(flip)
    im = _open(path)
    try:
        n = _frames_of(im)
        if n > MAX_FRAMES:
            raise ImageError("too_many_frames", "%d frames (at most %d)" % (n, MAX_FRAMES))
        if n * im.size[0] * im.size[1] > MAX_ANIM_PIXELS:
            raise ImageError("too_large", "the animation is too big")
        oriented = oriented_size(im.size, rotate)
        box = _check_crop(crop if crop is not None else default_crop(oriented, spec["aspect"]),
                          oriented)
        out_dir = out_dir or output_dir()
        os.makedirs(out_dir, exist_ok=True)
        try:
            if n > 1:
                return _animated(im, rotate, flip, box, spec["animated"], out_dir)
            im.seek(0)
            frame = _process(im.convert("RGBA"), rotate, flip, box, spec["master"])
            buf = io.BytesIO()
            frame.save(buf, "PNG", optimize=True)
            return _store(buf.getvalue(), ".png", out_dir, False, 1, frame.size)
        except ImageError:
            raise
        except Image.DecompressionBombError:
            raise ImageError("too_large", "the picture is too big") from None
        except (OSError, ValueError, SyntaxError, EOFError) as e:
            raise ImageError("bad_format", "the picture is damaged: %s" % type(e).__name__) from None
    finally:
        im.close()


def _animated(im, rotate, flip, box, out_size, out_dir):
    frames, durations = [], []
    for frame in ImageSequence.Iterator(im):
        durations.append(max(MIN_MS, int(frame.info.get("duration") or DEFAULT_MS)))
        frames.append(_process(frame.convert("RGBA"), rotate, flip, box, out_size))
    save = {"save_all": True, "append_images": frames[1:], "duration": durations,
            "disposal": 1}
    # A GIF without a loop count plays once, as in Base Camp.
    if "loop" in im.info:
        save["loop"] = im.info["loop"]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", **save)
    return _store(buf.getvalue(), ".gif", out_dir, True, len(frames), out_size)


def _store(data, ext, out_dir, animated, frames, size):
    name = "edit-" + hashlib.sha1(data).hexdigest()[:16] + ext
    dest = os.path.join(out_dir, name)
    if not os.path.isfile(dest):
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    return {"path": dest, "animated": animated, "frames": frames, "size": list(size)}


def delete(path, out_dir=None):
    """Delete one of the user's own pictures: a picture directly in the
    library folder (not in a pack). True if it was removed."""
    out_dir = os.path.realpath(out_dir or output_dir())
    if not isinstance(path, str):
        raise ImageError("not_allowed", "only your own pictures can be deleted")
    real = os.path.realpath(path)
    if os.path.dirname(real) != out_dir or not real.lower().endswith(EXTS) or not os.path.isfile(real):
        raise ImageError("not_allowed", "only your own pictures can be deleted")
    os.remove(real)
    return True


def make_service(app, out_dir=None):
    from core.service import Service

    class ImagesService(Service):
        name = "images"

        def commands(self):
            return {"prepare": self.prepare, "info": self.info, "preview": self.preview,
                    "delete": self.delete, "targets": self.targets}

        @staticmethod
        def _wrap(fn, *a, **kw):
            try:
                return fn(*a, **kw)
            except ImageError as e:
                raise CommandError("%s: %s" % (e.code, e)) from None

        def prepare(self, path, target, crop=None, rotate=0, flip=False):
            return self._wrap(prepare, path, target, crop, rotate, flip, out_dir=out_dir)

        def info(self, path):
            return self._wrap(info, path)

        def preview(self, path, max_side=PREVIEW_SIDE):
            return self._wrap(preview, path, max_side)

        def delete(self, path):
            return {"removed": self._wrap(delete, path, out_dir=out_dir)}

        def targets(self):
            return {k: {"aspect": list(v["aspect"]), "device": list(v["device"])}
                    for k, v in TARGETS.items()}

    return ImagesService(app)
