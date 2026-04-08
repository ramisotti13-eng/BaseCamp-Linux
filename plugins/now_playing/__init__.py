"""Now Playing -- shows current browser media (YouTube, Spotify, etc.) via MPRIS/playerctl."""
import subprocess
import threading
import os
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

try:
    from shared.ui_helpers import BG, BG2, BG3, FG, FG2, BLUE, GRN, RED, YLW, BORDER
except ImportError:
    BG, BG2, BG3 = "#0e0e1a", "#16162a", "#222244"
    FG, FG2 = "#e0e0e0", "#707090"
    BLUE, GRN, RED, YLW = "#0ea5e9", "#22c55e", "#dc2626", "#f5c542"
    BORDER = "#2a2a4a"


def _playerctl(*args):
    """Run playerctl command, return stdout or empty string."""
    try:
        r = subprocess.run(
            ["playerctl"] + list(args),
            capture_output=True, timeout=2,
            encoding="utf-8", errors="replace")
        return r.stdout.strip()
    except Exception:
        return ""


def _pactl(*args):
    """Run pactl command, return stdout or empty string."""
    try:
        r = subprocess.run(
            ["pactl"] + list(args),
            capture_output=True, timeout=2,
            encoding="utf-8", errors="replace")
        return r.stdout.strip()
    except Exception:
        return ""


def _find_browser_sink_input():
    """Find the PulseAudio/PipeWire sink-input index for the active browser."""
    try:
        raw = _pactl("list", "sink-inputs")
        if not raw:
            return None
        current_idx = None
        for line in raw.splitlines():
            stripped = line.strip()
            # Match both English and German output
            if stripped.startswith("Sink Input #") or stripped.startswith("Ziel-Eingabe #"):
                current_idx = stripped.split("#")[-1].strip()
            if "application.name" in stripped:
                name = stripped.split("=", 1)[-1].strip().strip('"')
                name_lower = name.lower()
                if any(b in name_lower for b in ("chrom", "firefox", "spotify")):
                    return current_idx
        return None
    except Exception:
        return None


def _get_media_info():
    """Return dict with current media info from MPRIS."""
    fmt = "{{title}}|||{{artist}}|||{{album}}|||{{status}}|||{{playerName}}|||{{mpris:artUrl}}|||{{mpris:length}}"
    raw = _playerctl("metadata", "--format", fmt)
    if not raw or "|||" not in raw:
        return None
    parts = raw.split("|||")
    if len(parts) < 7:
        parts.extend([""] * (7 - len(parts)))
    title, artist, album, status, player, art_url, length = parts[:7]
    # Duration in seconds (mpris:length is in microseconds)
    try:
        duration_s = int(length) // 1_000_000
    except (ValueError, TypeError):
        duration_s = 0
    # Current position
    try:
        pos_raw = _playerctl("position")
        position_s = int(float(pos_raw)) if pos_raw else 0
    except (ValueError, TypeError):
        position_s = 0

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "status": status,      # Playing, Paused, Stopped
        "player": player,      # chromium, firefox, spotify, etc.
        "art_url": art_url,
        "duration": duration_s,
        "position": position_s,
    }


def _fmt_time(seconds):
    """Format seconds as M:SS or H:MM:SS."""
    if seconds <= 0:
        return "0:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


_FONT = "DejaVu Sans"   # Nimbus Sans (Helvetica alias) drops glyphs; DejaVu has full Unicode


def _player_icon(name):
    """Return a display name/emoji for the player."""
    name = (name or "").lower()
    if "chrom" in name:
        return "Chrome"
    if "firefox" in name:
        return "Firefox"
    if "spotify" in name:
        return "Spotify"
    if "vlc" in name:
        return "VLC"
    return name.capitalize() if name else "Browser"


class Plugin:
    panel_id = "now_playing"
    panel_label = "Now Playing"

    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()
        self._last_info = None
        self._dp_key = None        # auto-detect from DisplayPad action config
        self._dp_last_key = None   # track what's on the button to avoid re-uploads
        self._dp_font = None
        self._dp_font_sm = None

        # Register as action type so users can assign play-pause to any button
        ctx.register_action_type(
            type_id="now_playing",
            label="Now Playing (Play/Pause)",
            handler=lambda val: _playerctl("play-pause")
        )

        ctx.register_translations({
            "en": {
                "np_title": "NOW PLAYING",
                "np_nothing": "Nothing playing",
                "np_hint": "Play something in your browser (YouTube, Spotify, ...)",
                "np_no_playerctl": "playerctl not found -- install: sudo dnf/apt install playerctl",
                "np_play": "Play",
                "np_pause": "Pause",
                "np_mute": "Mute",
                "np_unmute": "Unmute",
                "np_volume": "Vol:",
            },
            "de": {
                "np_title": "AKTUELLE WIEDERGABE",
                "np_nothing": "Nichts wird abgespielt",
                "np_hint": "Spiel etwas im Browser ab (YouTube, Spotify, ...)",
                "np_no_playerctl": "playerctl nicht gefunden -- installieren: sudo dnf/apt install playerctl",
                "np_play": "Play",
                "np_pause": "Pause",
                "np_mute": "Stumm",
                "np_unmute": "Laut",
                "np_volume": "Vol:",
            },
        })

    # ── Panel ─────────────────────────────────────────────────────────────────

    def create_panel(self, parent):
        self._frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)

        # Header
        hdr = ctk.CTkFrame(self._frame, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(hdr, text=self.ctx.T("np_title"),
                     font=(_FONT, 14, "bold"),
                     text_color=BLUE).pack(side="left")

        self._player_lbl = ctk.CTkLabel(
            hdr, text="", font=(_FONT, 10), text_color=FG2)
        self._player_lbl.pack(side="right")

        # Thumbnail / info card
        self._thumb_frame = ctk.CTkFrame(self._frame, fg_color=BG3,
                                          corner_radius=8, height=196)
        self._thumb_frame.pack(fill="x", padx=16, pady=(8, 4))
        self._thumb_frame.pack_propagate(False)

        # 1x1 transparent placeholder image (CTkLabel crashes with image=None)
        self._empty_img = ctk.CTkImage(
            light_image=Image.new("RGBA", (1, 1), (0, 0, 0, 0)),
            dark_image=Image.new("RGBA", (1, 1), (0, 0, 0, 0)), size=(1, 1))
        self._thumb_lbl = ctk.CTkLabel(self._thumb_frame, text="",
                                        image=self._empty_img,
                                        fg_color="transparent")
        self._thumb_lbl.pack(expand=True)
        self._thumb_photo = None  # keep reference

        # Hint shown when nothing is playing (replaces the card)
        self._hint_lbl = ctk.CTkLabel(
            self._frame, text=self.ctx.T("np_hint"),
            font=(_FONT, 11), text_color=FG2,
            wraplength=440, justify="left")
        self._hint_lbl.pack(fill="x", padx=16, pady=(2, 4))

        # Progress bar + time
        prog_row = ctk.CTkFrame(self._frame, fg_color="transparent")
        prog_row.pack(fill="x", padx=16, pady=(4, 2))

        self._pos_lbl = ctk.CTkLabel(
            prog_row, text="0:00", font=(_FONT, 10), text_color=FG2, width=50)
        self._pos_lbl.pack(side="left")

        self._progress = ctk.CTkProgressBar(
            prog_row, fg_color=BG3, progress_color=BLUE, height=6,
            corner_radius=3)
        self._progress.set(0)
        self._progress.pack(side="left", fill="x", expand=True, padx=8)

        self._dur_lbl = ctk.CTkLabel(
            prog_row, text="0:00", font=(_FONT, 10), text_color=FG2, width=50)
        self._dur_lbl.pack(side="right")

        # Control buttons
        ctrl = ctk.CTkFrame(self._frame, fg_color="transparent")
        ctrl.pack(pady=(12, 8))

        btn_style = dict(
            font=(_FONT, 12, "bold"), height=36, corner_radius=6,
            text_color=FG, hover_color="#0884be")

        self._play_btn = ctk.CTkButton(
            ctrl, text=self.ctx.T("np_pause"), fg_color=BLUE, width=140,
            command=lambda: _playerctl("play-pause"), **btn_style)
        self._play_btn.pack(side="left", padx=6)

        self._mute_btn = ctk.CTkButton(
            ctrl, text=self.ctx.T("np_mute"), fg_color=BG3, width=100,
            command=self._toggle_mute, **btn_style)
        self._mute_btn.pack(side="left", padx=6)
        self._muted = False

        # Volume slider
        vol_row = ctk.CTkFrame(self._frame, fg_color="transparent")
        vol_row.pack(fill="x", padx=16, pady=(4, 2))

        ctk.CTkLabel(vol_row, text=self.ctx.T("np_volume"),
                     font=(_FONT, 10), text_color=FG2, width=50
                     ).pack(side="left")

        self._vol_slider = ctk.CTkSlider(
            vol_row, from_=0, to=100, number_of_steps=20,
            fg_color=BG3, progress_color=BLUE, button_color=FG,
            height=14, width=260,
            command=self._on_volume_change)
        cur_vol = 100
        self._vol_slider.set(cur_vol)
        self._vol_slider.pack(side="left", padx=8)

        self._vol_lbl = ctk.CTkLabel(
            vol_row, text=f"{int(cur_vol)}%",
            font=(_FONT, 10), text_color=FG2, width=40)
        self._vol_lbl.pack(side="left")

        # Status line
        self._status_lbl = ctk.CTkLabel(
            self._frame, text="", font=(_FONT, 10), text_color=FG2)
        self._status_lbl.pack(fill="x", padx=16, pady=(4, 8))

        # Check if playerctl is available
        if not _playerctl("--version"):
            self._status_lbl.configure(
                text=self.ctx.T("np_no_playerctl"), text_color=RED)

        return self._frame

    # ── Service ───────────────────────────────────────────────────────────────

    def start(self):
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                info = _get_media_info()
                self.ctx.schedule(0, lambda i=info: self._update_ui(i))
                self._update_displaypad(info)
            except Exception:
                pass
            self._stop.wait(2)

    def _update_ui(self, info):
        if info is None or not info.get("title"):
            self._hint_lbl.configure(text=self.ctx.T("np_hint"))
            self._player_lbl.configure(text="")
            self._pos_lbl.configure(text="0:00")
            self._dur_lbl.configure(text="0:00")
            self._progress.set(0)
            self._play_btn.configure(text=self.ctx.T("np_play"))
            self._status_lbl.configure(text="", text_color=FG2)
            self._update_thumb(None)
            return

        self._hint_lbl.configure(text="")
        self._update_thumb(info)
        self._player_lbl.configure(
            text=_player_icon(info["player"]))

        # Progress
        pos = info["position"]
        dur = info["duration"]
        self._pos_lbl.configure(text=_fmt_time(pos))
        self._dur_lbl.configure(text=_fmt_time(dur))
        self._progress.set(pos / dur if dur > 0 else 0)

        # Play/Pause button
        if info["status"] == "Playing":
            self._play_btn.configure(text=self.ctx.T("np_pause"))
            self._status_lbl.configure(text="", text_color=FG2)
        elif info["status"] == "Paused":
            self._play_btn.configure(text=self.ctx.T("np_play"))
            self._status_lbl.configure(text="Paused", text_color=YLW)
        else:
            self._play_btn.configure(text=self.ctx.T("np_play"))

        # Thumbnail is updated at the top of _update_ui

    def _toggle_mute(self):
        sink = _find_browser_sink_input()
        if not sink:
            return
        if self._muted:
            _pactl("set-sink-input-mute", sink, "0")
            self._vol_slider.set(self._saved_vol)
            self._vol_lbl.configure(text=f"{int(self._saved_vol)}%")
            self._mute_btn.configure(text=self.ctx.T("np_mute"), fg_color=BG3)
            self._muted = False
        else:
            self._saved_vol = self._vol_slider.get()
            _pactl("set-sink-input-mute", sink, "1")
            self._mute_btn.configure(text=self.ctx.T("np_unmute"), fg_color=RED)
            self._muted = True

    def _on_volume_change(self, val):
        vol = int(val)
        sink = _find_browser_sink_input()
        if sink:
            _pactl("set-sink-input-volume", sink, f"{vol}%")
        self._vol_lbl.configure(text=f"{vol}%")
        if vol > 0 and self._muted:
            if sink:
                _pactl("set-sink-input-mute", sink, "0")
            self._mute_btn.configure(text=self.ctx.T("np_mute"), fg_color=BG3)
            self._muted = False

    def _update_thumb(self, info):
        """Generate a visual thumbnail card with title, artist, and play status."""
        if not info or not info.get("title"):
            self._thumb_lbl.configure(image=self._empty_img, text="")
            self._thumb_photo = None
            self._last_thumb_key = None
            return

        title = info["title"]
        artist = info.get("artist", "")
        status = info.get("status", "")
        player = _player_icon(info.get("player", ""))

        # Only regenerate if content changed (not position)
        thumb_key = f"{title}|{artist}|{status}"
        if getattr(self, "_last_thumb_key", None) == thumb_key:
            return
        self._last_thumb_key = thumb_key

        try:
            W, H = 440, 190
            img = Image.new("RGB", (W, H), (22, 22, 46))
            draw = ImageDraw.Draw(img)

            # Try to find a good font
            font_title = None
            font_artist = None
            for fpath in [
                "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            ]:
                if os.path.exists(fpath):
                    font_title = ImageFont.truetype(fpath, 18)
                    font_artist = ImageFont.truetype(
                        fpath.replace("-Bold", ""), 14)
                    break
            if font_title is None:
                font_title = ImageFont.load_default()
                font_artist = font_title

            # Status indicator bar (left edge)
            bar_color = (14, 165, 233) if status == "Playing" else (245, 197, 66)
            draw.rectangle([0, 0, 4, H], fill=bar_color)

            # Title (wrapped)
            y = 16
            max_text_w = W - 40
            words = title.split()
            lines = []
            line = ""
            for w in words:
                test = f"{line} {w}".strip()
                bbox = draw.textbbox((0, 0), test, font=font_title)
                if bbox[2] - bbox[0] > max_text_w and line:
                    lines.append(line)
                    line = w
                else:
                    line = test
            if line:
                lines.append(line)

            for i, ln in enumerate(lines[:3]):  # max 3 lines
                if i == 2 and len(lines) > 3:
                    ln = ln[:40] + "..."
                draw.text((16, y), ln, fill=(224, 224, 224), font=font_title)
                y += 26

            # Artist
            if artist:
                y = max(y + 4, 100)
                draw.text((16, y), artist, fill=(112, 112, 144),
                          font=font_artist)

            # Status text (bottom right)
            if status == "Playing":
                sym = ">> Playing"
                sym_color = (14, 165, 233)
            elif status == "Paused":
                sym = "|| Paused"
                sym_color = (245, 197, 66)
            else:
                sym = ""
                sym_color = (112, 112, 144)
            if sym:
                bbox = draw.textbbox((0, 0), sym, font=font_artist)
                sw = bbox[2] - bbox[0]
                draw.text((W - sw - 16, H - 28), sym, fill=sym_color,
                          font=font_artist)

            self._thumb_photo = ctk.CTkImage(
                light_image=img, dark_image=img, size=(W, H))
            self._thumb_lbl.configure(image=self._thumb_photo, text="")
        except Exception:
            self._thumb_lbl.configure(image=self._empty_img, text="")
            self._thumb_photo = None

    # ── DisplayPad ────────────────────────────────────────────────────────────

    def _get_dp_font(self, size):
        """Get a Pillow font for DisplayPad rendering."""
        for fpath in [
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]:
            if os.path.exists(fpath):
                return ImageFont.truetype(fpath, size)
        return ImageFont.load_default()

    def _find_dp_key(self):
        """Find which DisplayPad button has 'now_playing' action type assigned."""
        try:
            from shared.config import _load_displaypad_actions
            actions = _load_displaypad_actions()
            for i, act in enumerate(actions):
                if act.get("type") == "now_playing":
                    return i
        except Exception:
            pass
        return None

    def _update_displaypad(self, info):
        """Render now-playing info onto a DisplayPad button (runs in poll thread)."""
        # Find which button the user assigned to now_playing
        self._dp_key = self._find_dp_key()
        if self._dp_key is None:
            return

        if not info or not info.get("title"):
            # Nothing playing -- show idle icon
            dp_key = "idle"
            if self._dp_last_key == dp_key:
                return
            self._dp_last_key = dp_key
            img = Image.new("RGB", (102, 102), (22, 22, 46))
            draw = ImageDraw.Draw(img)
            font = self._get_dp_font(10)
            draw.text((20, 40), "No Media", fill=(80, 80, 120), font=font)
            self.ctx.push_displaypad_image(self._dp_key, img)
            return

        title = info["title"]
        status = info.get("status", "")
        artist = info.get("artist", "")
        pos = info.get("position", 0)
        dur = info.get("duration", 0)

        # Only re-upload when content or status changes (not every 2s for position)
        dp_key = f"{title}|{status}"
        if self._dp_last_key == dp_key:
            return
        self._dp_last_key = dp_key

        try:
            S = 102
            img = Image.new("RGB", (S, S), (16, 16, 36))
            draw = ImageDraw.Draw(img)
            font = self._get_dp_font(11)
            font_sm = self._get_dp_font(9)

            # Status bar top
            if status == "Playing":
                draw.rectangle([0, 0, S, 3], fill=(14, 165, 233))
            elif status == "Paused":
                draw.rectangle([0, 0, S, 3], fill=(245, 197, 66))

            # Title (word-wrapped, max 4 lines)
            y = 8
            words = title.split()
            lines = []
            line = ""
            for w in words:
                test = f"{line} {w}".strip()
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] > S - 12 and line:
                    lines.append(line)
                    line = w
                else:
                    line = test
            if line:
                lines.append(line)

            for ln in lines[:4]:
                draw.text((6, y), ln, fill=(224, 224, 224), font=font)
                y += 15

            # Artist (1 line, truncated)
            if artist:
                y = max(y + 2, 68)
                # Truncate if too long
                while draw.textbbox((0, 0), artist, font=font_sm)[2] > S - 12 and len(artist) > 5:
                    artist = artist[:-2]
                draw.text((6, y), artist, fill=(100, 100, 140), font=font_sm)

            # Play/Pause icon bottom
            if status == "Playing":
                # Two vertical bars (pause icon)
                draw.rectangle([40, 88, 45, 98], fill=(14, 165, 233))
                draw.rectangle([50, 88, 55, 98], fill=(14, 165, 233))
            elif status == "Paused":
                # Triangle (play icon)
                draw.polygon([(42, 88), (42, 98), (56, 93)], fill=(245, 197, 66))

            self.ctx.push_displaypad_image(self._dp_key, img)
        except Exception as e:
            print(f"[NowPlaying] DisplayPad render error: {e}", flush=True)
