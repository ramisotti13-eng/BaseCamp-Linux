"""Pairing a phone: a one-time code becomes a device token.

"Pair a device" makes a code of 8 characters from an alphabet without
look-alikes (no 0/O, 1/I), valid for 2 minutes and used once; a new code
replaces the old one, and five wrong codes in a row cancel it. The phone
posts the code and gets a token (32 random bytes, URL-safe), which it keeps
as its cookie.

`remote_devices.json` holds a list of {id, name, token_sha256, paired,
last_seen}: the token's SHA-256, never the token, so the file alone lets
nobody in. Tokens are compared as digests with hmac.compare_digest. The file
is written atomically with mode 0600. `last_seen` is written at most once a
minute, so a phone's requests do not keep rewriting the file.

Thread-safe: the HTTP server calls in from its own threads.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import unicodedata

log = logging.getLogger("basecamp.core.remote")

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_SECONDS = 120
MAX_WRONG = 5
SEEN_EVERY = 60
MAX_NAME = 40


def save_json(path, data):
    """Write `data` as JSON to `path` atomically (temp file in the same
    folder, then os.replace), readable only by the user (0600)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), dir=folder)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log.warning("%s cannot be read (%s), starting empty", path, e)
        return default


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_name(name):
    """One line of at most MAX_NAME characters: whitespace runs become one
    space, control and format characters (escape sequences, bidi
    overrides, zero-width marks) are dropped."""
    text = "".join(" " if ch.isspace() else ch for ch in str(name or "")
                   if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(text.split())[:MAX_NAME]


def _public(dev):
    return {"id": dev["id"], "name": dev["name"], "paired": dev["paired"],
            "last_seen": dev["last_seen"]}


class Pairing:
    def __init__(self, path, now=time.time):
        self.path = path
        self._now = now
        self._lock = threading.Lock()
        self._code = None
        self._expires = 0.0
        self._wrong = 0
        self._devices = self._load()

    def _load(self):
        raw = load_json(self.path, [])
        out = []
        for d in raw if isinstance(raw, list) else []:
            if (isinstance(d, dict) and isinstance(d.get("id"), str)
                    and isinstance(d.get("token_sha256"), str)):
                out.append({"id": d["id"], "name": clean_name(d.get("name")) or "Device",
                            "token_sha256": d["token_sha256"],
                            "paired": _number(d.get("paired")),
                            "last_seen": _number(d.get("last_seen"))})
        return out

    def _save(self):
        save_json(self.path, self._devices)

    # ── Codes ─────────────────────────────────────────────────────────────────

    def start_code(self):
        """A new one-time code (the old one stops working)."""
        with self._lock:
            self._code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            self._expires = self._now() + CODE_SECONDS
            self._wrong = 0
            return {"code": self._code, "expires": self._expires}

    def cancel(self):
        """The current code stops working (remote access was switched off)."""
        with self._lock:
            self._cancel()

    def _cancel(self):
        self._code = None
        self._expires = 0.0
        self._wrong = 0

    def redeem(self, code, name):
        """The new device's token for the current code, else None. Case and
        spaces in `code` do not matter."""
        if not isinstance(code, str):
            return None
        given = "".join(code.split()).upper()
        with self._lock:
            if self._code is None:
                return None
            if self._now() > self._expires:
                self._cancel()
                return None
            if not hmac.compare_digest(given.encode("utf-8"), self._code.encode("utf-8")):
                self._wrong += 1
                if self._wrong >= MAX_WRONG:
                    log.warning("five wrong pairing codes in a row: the code is cancelled")
                    self._cancel()
                return None
            self._cancel()
            token = secrets.token_urlsafe(32)
            now = self._now()
            self._devices.append({
                "id": "d" + secrets.token_hex(6),
                "name": clean_name(name) or "Device",
                "token_sha256": _digest(token),
                "paired": now, "last_seen": now})
            self._save()
            return token

    # ── Devices ───────────────────────────────────────────────────────────────

    def device_for(self, token):
        """The paired device of `token` ({id, name, paired, last_seen}), or
        None."""
        if not isinstance(token, str) or not token:
            return None
        digest = _digest(token)
        with self._lock:
            found = None
            for d in self._devices:
                if hmac.compare_digest(digest, d["token_sha256"]):
                    found = d
            if found is None:
                return None
            now = self._now()
            if now - found["last_seen"] >= SEEN_EVERY:
                found["last_seen"] = now
                try:
                    self._save()
                except OSError as e:
                    log.warning("last_seen not saved: %s", e)
            return _public(found)

    def devices(self):
        with self._lock:
            return [_public(d) for d in self._devices]

    def rename(self, dev_id, name):
        """True when the device exists (and now has `name`)."""
        with self._lock:
            for d in self._devices:
                if d["id"] == dev_id:
                    d["name"] = clean_name(name) or d["name"]
                    self._save()
                    return True
            return False

    def remove(self, dev_id):
        """True when the device existed; its token stops working."""
        with self._lock:
            keep = [d for d in self._devices if d["id"] != dev_id]
            if len(keep) == len(self._devices):
                return False
            self._devices = keep
            self._save()
            return True
