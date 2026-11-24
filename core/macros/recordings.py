"""Mouse recordings: the paths a "Mouse Path" macro step plays back.

Each recording is a JSON file in the recordings directory
(shared.config.MOUSE_RECORDINGS_DIR, read when it is needed so a test or a
profile switch can point it elsewhere):

    {"name": "recording_1", "positions": [[x, y, t], ...], "click_at_end": true}

t is a monotonic time in seconds; only the differences matter.
"""
import json
import os


def recordings_dir():
    from shared import config
    return config.MOUSE_RECORDINGS_DIR


def recording_path(filename):
    """Where `filename` lives. Only the base name counts, so a value stored in
    a macro cannot point outside the directory."""
    return os.path.join(recordings_dir(), os.path.basename(filename))


def save_mouse_recording(name, positions, click_at_end=False):
    """Save mouse positions to a recording file. Returns filename."""
    rec_dir = recordings_dir()
    os.makedirs(rec_dir, exist_ok=True)
    data = {
        "name": name,
        "positions": positions,
        "click_at_end": click_at_end,
    }
    safe_name = os.path.basename(name.replace(" ", "_").lower())
    filename = safe_name + ".json"
    path = os.path.join(rec_dir, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return filename


def list_recordings():
    """[{"file", "name", "points"}] for every recording, by file name.

    A file that cannot be read is listed under its file name with no points,
    so it can still be picked or deleted.
    """
    result = []
    rec_dir = recordings_dir()
    try:
        names = sorted(os.listdir(rec_dir))
    except FileNotFoundError:
        return result
    for f in names:
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(rec_dir, f)) as fh:
                data = json.load(fh)
            result.append({"file": f, "name": data.get("name", f),
                           "points": len(data.get("positions", []))})
        except Exception:
            result.append({"file": f, "name": f, "points": None})
    return result


def list_mouse_recordings():
    """Return list of (filename, display_name) for saved recordings."""
    return [(r["file"], r["name"] if r["points"] is None
             else f"{r['name']} ({r['points']} pts)")
            for r in list_recordings()]


def next_recording_name(existing_files):
    """"recording_N" with the first N whose file is not taken."""
    n = 1
    while f"recording_{n}.json" in existing_files:
        n += 1
    return f"recording_{n}"


def delete_recording(filename):
    """Remove a recording. False when there was nothing to remove."""
    try:
        os.remove(recording_path(filename))
        return True
    except OSError:
        return False


def load_recording(filename):
    """(positions, click_at_end) of a recording, or None if it is missing or
    unreadable."""
    path = recording_path(filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("positions", []), data.get("click_at_end", False)
    except Exception:
        return None
