"""Keyboard layouts of the Everest keyboards: the physical shape and the legends.

A layout is a *shape* (ANSI or ISO) and a *legend set*. The keyboards do not
report their layout (the Everest 60's USB product id tells ANSI from ISO, that
is all), so the legend set comes from the system's keyboard layout
(core.platform.keymap), falling back to the interface language, and the user
can override it. The ten layouts are those of Mountain's Base Camp.

Legends are given per key as ``[base, shift, altgr]`` ("" where there is
none), keyed by the *US* name of the key: letters "A".."Z", digits, and
"`-=[]\\;',./"; the extra keys of an ISO board are "ISO#" (right of the
apostrophe key) and "ISO<" (right of the left Shift). Keys that are not listed
keep the US legend. Hebrew and Korean put the Latin letter in `base` and the
native letter in `altgr`, so both show, as on those keyboards.
"""

import json
import os
import time

LAYOUT_IDS = ["us", "uk", "de", "fr", "it", "nordic", "es", "pt", "he", "ko"]
ANSI_LAYOUTS = {"us", "ko"}
DEFAULT_ID = "us"

# English names; an interface translates them itself.
NAMES = {"us": "English (US)", "uk": "English (UK)", "de": "German", "fr": "French",
         "it": "Italian", "nordic": "Nordic", "es": "Spanish", "pt": "Portuguese",
         "he": "Hebrew", "ko": "Korean"}

_SHIFT_US = {"`": "~", "1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^", "7": "&",
             "8": "*", "9": "(", "0": ")", "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
             ";": ":", "'": '"', ",": "<", ".": ">", "/": "?"}
_US_KEYS = list("`1234567890-=[]\\;',./")

US = {k: [k, _SHIFT_US[k], ""] for k in _US_KEYS}
US.update({c: [c, "", ""] for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})
US["ISO#"] = ["#", "~", ""]
US["ISO<"] = ["\\", "|", ""]


def _l(*pairs):
    return {k: list(v) for k, v in pairs}


# Differences to the US table. Letters are given as their base legend in capitals.
_OVERRIDES = {
    "us": {},
    "uk": _l(("`", ("`", "¬", "¦")), ("2", ("2", '"', "")), ("3", ("3", "£", "")), ("4", ("4", "$", "€")),
             ("'", ("'", "@", "")), ("ISO#", ("#", "~", "")), ("ISO<", ("\\", "|", ""))),
    "de": _l(("`", ("^", "°", "")), ("2", ("2", '"', "²")), ("3", ("3", "§", "³")), ("6", ("6", "&", "")),
             ("7", ("7", "/", "{")), ("8", ("8", "(", "[")), ("9", ("9", ")", "]")), ("0", ("0", "=", "}")),
             ("-", ("ß", "?", "\\")), ("=", ("´", "`", "")), ("Y", ("Z", "", "")), ("Z", ("Y", "", "")),
             ("Q", ("Q", "", "@")), ("E", ("E", "", "€")), ("M", ("M", "", "µ")),
             ("[", ("Ü", "", "")), ("]", ("+", "*", "~")), (";", ("Ö", "", "")), ("'", ("Ä", "", "")),
             (",", (",", ";", "")), (".", (".", ":", "")), ("/", ("-", "_", "")),
             ("ISO#", ("#", "'", "")), ("ISO<", ("<", ">", "|"))),
    "fr": _l(("`", ("²", "", "")), ("1", ("&", "1", "")), ("2", ("é", "2", "~")), ("3", ('"', "3", "#")),
             ("4", ("'", "4", "{")), ("5", ("(", "5", "[")), ("6", ("-", "6", "|")), ("7", ("è", "7", "`")),
             ("8", ("_", "8", "\\")), ("9", ("ç", "9", "^")), ("0", ("à", "0", "@")),
             ("-", (")", "°", "]")), ("=", ("=", "+", "}")), ("A", ("Q", "", "")), ("Q", ("A", "", "")),
             ("Z", ("W", "", "")), ("W", ("Z", "", "")), ("E", ("E", "", "€")), ("M", (",", "?", "")),
             ("[", ("^", "¨", "")), ("]", ("$", "£", "¤")), (";", ("M", "", "")), ("'", ("Ù", "%", "")),
             (",", (";", ".", "")), (".", (":", "/", "")), ("/", ("!", "§", "")),
             ("ISO#", ("*", "µ", "")), ("ISO<", ("<", ">", ""))),
    "it": _l(("`", ("\\", "|", "")), ("2", ("2", '"', "")), ("3", ("3", "£", "")), ("6", ("6", "&", "")),
             ("7", ("7", "/", "")), ("8", ("8", "(", "")), ("9", ("9", ")", "")), ("0", ("0", "=", "")),
             ("-", ("'", "?", "")), ("=", ("Ì", "^", "")), ("E", ("E", "", "€")),
             ("[", ("È", "É", "[")), ("]", ("+", "*", "]")), (";", ("Ò", "Ç", "@")), ("'", ("À", "°", "#")),
             (",", (",", ";", "")), (".", (".", ":", "")), ("/", ("-", "_", "")),
             ("ISO#", ("Ù", "§", "")), ("ISO<", ("<", ">", ""))),
    "nordic": _l(("`", ("§", "½", "")), ("2", ("2", '"', "@")), ("3", ("3", "#", "£")), ("4", ("4", "¤", "$")),
                 ("5", ("5", "%", "€")), ("6", ("6", "&", "")), ("7", ("7", "/", "{")), ("8", ("8", "(", "[")),
                 ("9", ("9", ")", "]")), ("0", ("0", "=", "}")), ("-", ("+", "?", "\\")), ("=", ("´", "`", "")),
                 ("E", ("E", "", "€")), ("[", ("Å", "", "")), ("]", ("¨", "^", "~")), (";", ("Ö", "", "")),
                 ("'", ("Ä", "", "")), (",", (",", ";", "")), (".", (".", ":", "")), ("/", ("-", "_", "")),
                 ("ISO#", ("'", "*", "")), ("ISO<", ("<", ">", "|"))),
    "es": _l(("`", ("º", "ª", "\\")), ("1", ("1", "!", "|")), ("2", ("2", '"', "@")), ("3", ("3", "·", "#")),
             ("4", ("4", "$", "~")), ("5", ("5", "%", "€")), ("6", ("6", "&", "¬")), ("7", ("7", "/", "")),
             ("8", ("8", "(", "")), ("9", ("9", ")", "")), ("0", ("0", "=", "")), ("-", ("'", "?", "")),
             ("=", ("¡", "¿", "")), ("E", ("E", "", "€")), ("[", ("`", "^", "[")), ("]", ("+", "*", "]")),
             (";", ("Ñ", "", "")), ("'", ("´", "¨", "{")), (",", (",", ";", "")), (".", (".", ":", "")),
             ("/", ("-", "_", "")), ("ISO#", ("Ç", "", "}")), ("ISO<", ("<", ">", ""))),
    "pt": _l(("`", ("\\", "|", "")), ("2", ("2", '"', "@")), ("3", ("3", "#", "£")), ("4", ("4", "$", "§")),
             ("5", ("5", "%", "€")), ("6", ("6", "&", "")), ("7", ("7", "/", "{")), ("8", ("8", "(", "[")),
             ("9", ("9", ")", "]")), ("0", ("0", "=", "}")), ("-", ("'", "?", "")), ("=", ("«", "»", "")),
             ("E", ("E", "", "€")), ("[", ("+", "*", "´")), ("]", ("´", "`", "")), (";", ("Ç", "", "")),
             ("'", ("º", "ª", "")), (",", (",", ";", "")), (".", (".", ":", "")), ("/", ("-", "_", "")),
             ("ISO#", ("~", "^", "")), ("ISO<", ("<", ">", ""))),
    "he": {**_l(("Q", ("Q", "", "/")), ("W", ("W", "", "'")), ("E", ("E", "", "ק")), ("R", ("R", "", "ר")),
                ("T", ("T", "", "א")), ("Y", ("Y", "", "ט")), ("U", ("U", "", "ו")), ("I", ("I", "", "ן")),
                ("O", ("O", "", "ם")), ("P", ("P", "", "פ")), ("A", ("A", "", "ש")), ("S", ("S", "", "ד")),
                ("D", ("D", "", "ג")), ("F", ("F", "", "כ")), ("G", ("G", "", "ע")), ("H", ("H", "", "י")),
                ("J", ("J", "", "ח")), ("K", ("K", "", "ל")), ("L", ("L", "", "ך")), ("Z", ("Z", "", "ז")),
                ("X", ("X", "", "ס")), ("C", ("C", "", "ב")), ("V", ("V", "", "ה")), ("B", ("B", "", "נ")),
                ("N", ("N", "", "מ")), ("M", ("M", "", "צ")), (",", (",", "<", "ת")), (".", (".", ">", "ץ")),
                (";", (";", ":", "ף")), ("/", ("/", "?", ".")), ("'", ("'", '"', ","))),
           "ISO#": ["#", "~", ""], "ISO<": ["\\", "|", ""]},
    "ko": _l(("Q", ("Q", "", "ㅂ")), ("W", ("W", "", "ㅈ")), ("E", ("E", "", "ㄷ")), ("R", ("R", "", "ㄱ")),
             ("T", ("T", "", "ㅅ")), ("Y", ("Y", "", "ㅛ")), ("U", ("U", "", "ㅕ")), ("I", ("I", "", "ㅑ")),
             ("O", ("O", "", "ㅐ")), ("P", ("P", "", "ㅔ")), ("A", ("A", "", "ㅁ")), ("S", ("S", "", "ㄴ")),
             ("D", ("D", "", "ㅇ")), ("F", ("F", "", "ㄹ")), ("G", ("G", "", "ㅎ")), ("H", ("H", "", "ㅗ")),
             ("J", ("J", "", "ㅓ")), ("K", ("K", "", "ㅏ")), ("L", ("L", "", "ㅣ")), ("Z", ("Z", "", "ㅋ")),
             ("X", ("X", "", "ㅌ")), ("C", ("C", "", "ㅊ")), ("V", ("V", "", "ㅍ")), ("B", ("B", "", "ㅠ")),
             ("N", ("N", "", "ㅜ")), ("M", ("M", "", "ㅡ"))),
}


# Characters that are dead keys in the desktop's usual layout of that name: the
# key only arms an accent, so a keyboard macro types them as the key and a
# space (everest_max/keymacro.py).
DEAD_KEYS = {"de": "^´`", "fr": "^¨`~", "nordic": "¨^~´`", "es": "`^´¨", "pt": "´`~^¨"}

def shape_of(layout_id):
    """"ansi" for the US and Korean legend sets, "iso" for the others."""
    return "ansi" if layout_id in ANSI_LAYOUTS else "iso"


def legends(layout_id):
    """{us key name: [base, shift, altgr]} for every key whose legend is a character."""
    out = {k: list(v) for k, v in US.items()}
    out.update({k: list(v) for k, v in _OVERRIDES.get(layout_id, {}).items()})
    return out


# ── from the system's layout name to one of ours ──────────────────────────────────
_XKB = {"us": "us", "gb": "uk", "uk": "uk", "de": "de", "at": "de", "ch": "de", "fr": "fr", "be": "fr",
        "it": "it", "no": "nordic", "se": "nordic", "dk": "nordic", "fi": "nordic", "is": "nordic",
        "es": "es", "latam": "es", "pt": "pt", "br": "pt", "il": "he", "kr": "ko"}
_LANG = {"en": "us", "de": "de", "fr": "fr", "it": "it", "nb": "nordic", "no": "nordic", "sv": "nordic",
         "da": "nordic", "fi": "nordic", "es": "es", "pt": "pt", "he": "he", "iw": "he", "ko": "ko"}


def from_xkb(code, variant=""):
    """Our layout id for an XKB layout name ("de", "gb", "us"...), or None."""
    code = (code or "").strip().lower().split("(")[0]
    if not code:
        return None
    if code == "us" and (variant or "").lower() in ("intl", "altgr-intl"):
        return "us"
    return _XKB.get(code)


def from_language(lang):
    """Our layout id for a UI language code ("de", "en_GB", "fr"...), or None."""
    code = (lang or "").replace("-", "_").split(".")[0]
    if code.lower() in ("en_gb", "en_au", "en_nz", "en_ie"):
        return "uk"
    return _LANG.get(code.split("_")[0].lower())


def resolve(configured=None, system=None, language=None, pid_shape=None):
    """What to draw: {id, shape, auto, detected}.

    configured  the user's choice ("auto"/None or a layout id)
    system      the system's layout (core.platform.keymap) as our id, or None
    language    the UI language code, the fallback of the fallback
    pid_shape   "ansi"/"iso" when the keyboard's USB id says so (Everest 60)

    The legend set follows the choice, else the system, else the language,
    else US. The shape follows the keyboard when it knows (pid_shape), else the
    legend set.
    """
    detected = system if system in LAYOUT_IDS else None
    if detected is None:
        detected = from_language(language)
    auto = configured not in LAYOUT_IDS
    lid = detected if auto else configured
    lid = lid if lid in LAYOUT_IDS else DEFAULT_ID
    shape = pid_shape if pid_shape in ("ansi", "iso") else shape_of(lid)
    return {"id": lid, "shape": shape, "auto": auto, "detected": detected or DEFAULT_ID}


# ── the user's choice and the detected layout ────────────────────────────────────
SETTINGS_FILE = "keyboard_layout.json"
_system_cache = [0.0, None]


def system_layout_id(force=False):
    """The system's keyboard layout as one of our ids, or None. Asking the
    system runs a few small programs, so the answer is kept for a minute."""
    now = time.monotonic()
    if force or not _system_cache[0] or now - _system_cache[0] > 60:
        from core.platform import keymap
        _system_cache[0] = now
        _system_cache[1] = from_xkb(keymap.system_layout())
    return _system_cache[1]


def _path(config_dir):
    return os.path.join(config_dir, SETTINGS_FILE)


def get_choice(device, config_dir):
    """The layout id the user picked for `device`, or "auto"."""
    try:
        with open(_path(config_dir), encoding="utf-8") as f:
            value = json.load(f).get(device)
    except (OSError, ValueError, AttributeError):
        return "auto"
    return value if value in LAYOUT_IDS else "auto"


def set_choice(device, value, config_dir):
    """Store the choice ("auto" or a layout id); ValueError for anything else."""
    if value != "auto" and value not in LAYOUT_IDS:
        raise ValueError("unknown keyboard layout: %r" % (value,))
    try:
        with open(_path(config_dir), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[device] = value
    os.makedirs(config_dir, exist_ok=True)
    with open(_path(config_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return value


def info(device, config_dir, language=None, pid_shape=None, system=None, with_legends=False):
    """The keyboard layout of `device` as an interface needs it."""
    choice = get_choice(device, config_dir)
    if system is None:
        system = system_layout_id()
    r = resolve(choice, system, language or os.environ.get("LANG", ""), pid_shape)
    out = {"id": r["id"], "shape": r["shape"], "auto": r["auto"], "detected": r["detected"],
           "choice": choice,
           "available": [{"id": i, "name": NAMES[i], "shape": shape_of(i)} for i in LAYOUT_IDS]}
    if with_legends:
        out["legends"] = legends(r["id"])
    return out
