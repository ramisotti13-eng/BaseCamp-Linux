"""The version history the application shows: CHANGELOG.md, one entry per
version.

The file ships with the application (the AppImage bundle and the source
overlay). Its sections start with `## [<version>] - <date>`; everything up to
the next such heading is that version's text, in Markdown. Nothing here
talks to the network; the notes of a release not installed yet come with the
update check (core.updates).
"""
import os
import re

from shared.config import shipped_path

_HEAD = re.compile(r"^## \[([^\]]+)\](?:\s*-\s*(.*?))?\s*$")
#: A version's text is cut here: CHANGELOG.md has very long entries, and an
#: interface shows the newest ones.
MAX_BODY = 40000


def parse(text):
    """[{version, date, body}] in the order of the file (newest first)."""
    out = []
    cur = None
    for line in (text or "").splitlines():
        m = _HEAD.match(line)
        if m:
            cur = {"version": m.group(1).strip(), "date": (m.group(2) or "").strip(), "lines": []}
            out.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    entries = []
    for e in out:
        lines = e.pop("lines")
        while lines and lines[-1].strip() in ("", "---"):
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        e["body"] = "\n".join(lines)[:MAX_BODY]
        entries.append(e)
    return entries


def load(path=None):
    """The entries of the shipped CHANGELOG.md, [] when there is none."""
    path = path or shipped_path("CHANGELOG.md")
    try:
        with open(path, encoding="utf-8") as f:
            return parse(f.read())
    except (OSError, UnicodeDecodeError):
        return []


def base_version(version):
    """"4.0.0-beta.1" -> "4.0.0": the changelog has one entry per release,
    its pre-releases included."""
    return str(version or "").strip().lstrip("vV").split("-", 1)[0]


def find(entries, version):
    """The entry of `version` (or of its release), None if there is none."""
    want = base_version(version)
    return next((e for e in entries if base_version(e["version"]) == want), None)


def seen_path(config_dir):
    return os.path.join(config_dir, "last_seen_version")
