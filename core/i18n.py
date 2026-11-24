"""Language files, the current language and plugin translations.

``available()``, ``default_for_env()`` and ``load()`` are the functions that
used to sit at the top of gui.py (which keeps ``available_langs``,
``default_lang`` and ``load_lang`` as delegating names). ``I18nService`` holds
the current language: ``T()`` translates, ``set_language()`` persists the
choice and publishes ``i18n.changed``.

Plugins add strings through ``register_plugin_translations()``. The service
remembers every registration and merges them again after each language switch;
before, a switch replaced the whole dictionary and the plugins' own strings
fell back to their keys until the next restart.

The service publishes codes and strings, never widgets. Translation itself
stays a lookup in a dict, so the interface can keep reading ``strings`` (the
panels' ``app._lang``). An interface in another process or language (the web
interface) asks for the whole dict with ``i18n.strings`` and translates on
its own side.

Commands: ``languages``, ``set_language {code}``, ``strings {code?}``.
"""
import json
import logging
import os

from core.service import Service

log = logging.getLogger("basecamp.core.i18n")


def available(lang_dir):
    """{code: display name} of every language file in `lang_dir`."""
    result = {}
    try:
        for fname in os.listdir(lang_dir):
            if fname.endswith(".json"):
                code = fname[:-5]
                try:
                    with open(os.path.join(lang_dir, fname), encoding="utf-8") as f:
                        data = json.load(f)
                    result[code] = data.get("name", code)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return result


def load(lang_dir, code):
    """The strings of language `code`.

    A missing or broken file falls back to de.json, as it always has (the
    start-up choice in default_for_env() falls back to English instead; the
    two were never aligned and behaviour must not change here).
    """
    path = os.path.join(lang_dir, "%s.json" % code)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        try:
            with open(os.path.join(lang_dir, "de.json"), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def default_for_env(avail, environ=None):
    """The language to start in when nobody has picked one yet.

    This used to be German whatever the machine was set to, so every new user
    outside Germany got a German interface and then had to find the setting to
    get out of it, in German (#92). The system locale decides now, and English
    is the fallback rather than German.

    The variables are read in gettext's order. LANGUAGE is a colon separated
    list of preferences and comes first, except under the C locale where it is
    ignored, which is also what gettext does.
    """
    env = os.environ if environ is None else environ
    candidates = []
    if (env.get("LC_ALL") or env.get("LC_MESSAGES")
            or env.get("LANG") or "") not in ("C", "POSIX"):
        candidates.extend((env.get("LANGUAGE") or "").split(":"))
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        candidates.append(env.get(var) or "")
    for value in candidates:
        code = value.split(".")[0].split("@")[0].split("_")[0].strip().lower()
        if code and code in avail:
            return code
    if "en" in avail:
        return "en"
    return next(iter(avail), "en")


class I18nService(Service):
    name = "i18n"

    def __init__(self, app, lang_dir, config_dir):
        super().__init__(app)
        self.lang_dir = lang_dir
        self.config_dir = config_dir
        self.avail = available(lang_dir)
        self._plugin_strings = []        # [{code: {key: text}}, ...]
        self.strings = {}
        self.code = ""
        self.set_language(self._stored_or_default(), persist=False, announce=False)

    # ── The choice ─────────────────────────────────────────────────────────
    def _language_file(self):
        return os.path.join(self.config_dir, "language")

    def _stored_or_default(self):
        try:
            with open(self._language_file()) as f:
                code = f.read().strip()
        except OSError:
            code = ""
        if code not in self.avail:
            code = default_for_env(self.avail)
        return code

    def set_language(self, code, persist=True, announce=True):
        """Switch to `code`: reload the strings, merge the plugins' again,
        write CONFIG_DIR/language and publish ``i18n.changed``."""
        if persist:
            with open(self._language_file(), "w") as f:
                f.write(code)
        self.load_code(code)
        if announce:
            self.publish("changed", code=code)

    def load_code(self, code):
        """Load `code` without persisting it or publishing anything."""
        self.strings = load(self.lang_dir, code)
        self.code = code
        for all_langs in self._plugin_strings:
            self._merge(all_langs)

    def code_for_name(self, display_name):
        for code, name in self.avail.items():
            if name == display_name:
                return code
        return None

    # ── Translation ────────────────────────────────────────────────────────
    def T(self, key, **kwargs):
        val = self.strings.get(key, key)
        if kwargs:
            try:
                val = val.format(**kwargs)
            except (KeyError, IndexError):
                pass
        return val

    # ── Plugins ────────────────────────────────────────────────────────────
    def _merge(self, all_langs):
        mine = all_langs.get(self.code)
        if mine:
            self.strings.update(mine)

    def register_plugin_translations(self, all_langs):
        """Merge a plugin's {"en": {...}, "de": {...}} now and after every
        language switch."""
        self._plugin_strings.append(all_langs)
        self._merge(all_langs)

    def strings_for(self, code):
        """The strings of `code` as the interface would have them after a
        switch to it (the file plus the plugins' strings), without switching."""
        if code == self.code:
            return dict(self.strings)
        strings = load(self.lang_dir, code)
        for all_langs in self._plugin_strings:
            mine = all_langs.get(code)
            if mine:
                strings.update(mine)
        return strings

    def commands(self):
        return {"set_language": self._cmd_set_language,
                "languages": self._cmd_languages,
                "strings": self._cmd_strings}

    def _cmd_strings(self, code=None):
        """The current language's strings, for an interface that translates
        on its own side (the web interface). `code` asks for another
        language's, e.g. English as the fallback for a missing key."""
        from core.commands import CommandError
        if code is None or code == "":
            code = self.code
        if code not in self.avail:
            raise CommandError("unknown language: %s" % code)
        return {"code": code, "strings": self.strings_for(code)}

    def _cmd_set_language(self, code):
        from core.commands import CommandError
        if code not in self.avail:
            raise CommandError("unknown language: %s" % code)
        self.set_language(code)
        return {"code": code}

    def _cmd_languages(self):
        return {"languages": dict(self.avail), "current": self.code}
