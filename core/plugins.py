"""Plugins: loading them, installing them, keeping them up to date.

``PluginManager`` is what used to live in shared/plugins.py (discover, load,
enable/disable/reload, action types, page-bound services); that module now
re-exports it. ``PluginHost`` (service ``plugins``) wraps one manager and adds
what the Plugins screen and gui.py did on their own:

    fetch_index()            the published plugins (network, blocking)
    updates_for(...)         published entries newer than the installed ones
    note_index(plugins, announce=False)
                             remember an index; publishes plugins.updates
                             {count, announce}. ``announce`` is true when the
                             caller asked for it and there are updates not
                             told about yet; the interface decides whether
                             anybody can see it right now.
    check_updates()          the 12 h check: fetch off-thread, then note_index
    install_from_folder(src) put a plugin folder in place (PluginInstallError)
    install_from_github(url) download, install, clean up
    download_updates(), install_prepared(), update_all()
    uninstall(pid), enable/disable/reload
    status(pid)              off | bad | warn | info | ok
    plugin_list()            everything an interface lists, JSON-serialisable

Commands: list, status, fetch_index, updates, check, enable, disable, reload,
uninstall, install, update_all. Events: plugins.index {count},
plugins.updates {count, announce}, plugins.changed {id, change}.

Nothing here translates: install problems carry a code (``PluginInstallError``)
and parameters, the interface builds the sentence. The network goes through
``net`` (``fetch_index(timeout)``, ``download_repo(url)``), so tests supply
their own and nothing is downloaded.
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile
import importlib.util

from core.commands import CommandError
from core.service import Service
from shared.config import CONFIG_DIR, PLUGINS_DISABLED_FILE

log = logging.getLogger("basecamp.core.plugins")

# Set BASECAMP_PAGE_DEBUG=1 in the environment to trace page-scoped
# start/stop decisions (also toggles matching trace lines in
# devices/displaypad/panel.py and page-bound widget plugins). Off by default.
_DEBUG = os.environ.get("BASECAMP_PAGE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _dbg(msg):
    if _DEBUG:
        print(msg, flush=True)

PLUGINS_DIR = os.path.join(CONFIG_DIR, "plugins")
PLUGINS_INDEX_URL = "https://raw.githubusercontent.com/ramisotti13-eng/basecamp-plugins/main/plugins.json"
CHECK_INTERVAL_MS = 12 * 3600 * 1000
FIRST_CHECK_MS = 15000

# A manifest names its dependencies the way pip does, which is not always the
# way import does: "Pillow" imports as PIL, "opencv-python" as cv2. Feeding the
# manifest string straight to __import__ therefore reported Pillow as missing
# everywhere, including in the AppImage that ships it, and the plugin screen
# put a warning dot on plugins that were working fine (#76).
_REQUIREMENT_MODULES = {
    "pillow": "PIL",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "opencv-contrib-python": "cv2",
    "pyusb": "usb",
    "hidapi": "hid",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "beautifulsoup4": "bs4",
    "pyserial": "serial",
    "pillow-simd": "PIL",
}


def requirement_module(name):
    """The module name to import for a requirement as spelled in a manifest.

    Strips any version marker ("Pillow>=10") and maps the handful of packages
    whose import name differs from their pip name; everything else keeps its
    own name with dashes turned into underscores, which is the pip convention.
    """
    base = str(name or "").strip()
    for sep in ("<", ">", "=", "!", "~", "[", ";", " "):
        base = base.split(sep)[0]
    base = base.strip()
    if not base:
        return ""
    return _REQUIREMENT_MODULES.get(base.lower(), base.replace("-", "_"))


def has_requirement(name):
    """Is a manifest requirement importable in the interpreter we run in?"""
    mod = requirement_module(name)
    if not mod:
        return True
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        # find_spec raises for a parent package that is itself missing.
        return False


class PluginManager:
    """Scan, load, and manage lifecycle of user plugins."""

    def __init__(self):
        self._manifests = {}     # id -> manifest dict
        self._instances = {}     # id -> Plugin instance
        self._action_types = {}  # type_id -> {"label", "handler"}
        self._disabled = set()   # set of disabled plugin IDs
        self._errors = {}        # id -> error string
        self._running = {}       # id -> bool, service plugins currently start()-ed
        self._loading_pid = None  # set while instantiating a plugin, so
                                   # register_action_type() can tag ownership
        self._load_disabled()

    def _load_disabled(self):
        """Load set of disabled plugin IDs from disk."""
        try:
            with open(PLUGINS_DISABLED_FILE) as f:
                data = json.load(f)
            self._disabled = set(data) if isinstance(data, list) else set()
        except Exception:
            self._disabled = set()
        self._has_disabled_file = os.path.exists(PLUGINS_DISABLED_FILE)

    def _save_disabled(self):
        """Persist disabled plugin IDs to disk."""
        with open(PLUGINS_DISABLED_FILE, "w") as f:
            json.dump(sorted(self._disabled), f)

    def discover(self):
        """Scan plugins directory for valid plugin.json manifests."""
        if not os.path.isdir(PLUGINS_DIR):
            return
        for name in sorted(os.listdir(PLUGINS_DIR)):
            pdir = os.path.join(PLUGINS_DIR, name)
            manifest_path = os.path.join(pdir, "plugin.json")
            if not os.path.isdir(pdir) or not os.path.isfile(manifest_path):
                continue
            try:
                with open(manifest_path) as f:
                    info = json.load(f)
                info["_path"] = pdir
                pid = info.get("id", name)
                self._manifests[pid] = info
            except Exception as e:
                print(f"[Plugin] Failed to read {manifest_path}: {e}")

    def load_all(self, context):
        """Import and instantiate all discovered plugins (skip disabled)."""
        self._context = context
        for pid, info in self._manifests.items():
            # Plugins with default_disabled: true start disabled unless user
            # has explicitly toggled them (i.e. a disabled file exists).
            if info.get("default_disabled") and pid not in self._disabled and not self._has_disabled_file:
                self._disabled.add(pid)
                self._save_disabled()
            if pid in self._disabled:
                print(f"[Plugin] Skipped (disabled): {info.get('name', pid)}")
                continue
            self._load_one(pid, info, context)

    def _load_one(self, pid, info, context):
        """Import and instantiate a single plugin."""
        try:
            self._check_requires(info)
            entry = info.get("entry", "__init__")
            mod_path = os.path.join(info["_path"], entry.replace(".", "/") + ".py")
            spec = importlib.util.spec_from_file_location(f"plugins.{pid}", mod_path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"plugins.{pid}"] = mod
            self._loading_pid = pid  # so ctx.register_action_type can tag ownership
            try:
                spec.loader.exec_module(mod)
                instance = mod.Plugin(context)
            finally:
                self._loading_pid = None
            self._instances[pid] = instance
            self._errors.pop(pid, None)
            print(f"[Plugin] Loaded: {info.get('name', pid)} v{info.get('version', '?')}")
            return True
        except Exception as e:
            self._errors[pid] = str(e)
            print(f"[Plugin] Failed to load {pid}: {e}")
            return False

    def _check_requires(self, info):
        """Print warnings for missing dependencies (informational only)."""
        for pkg in info.get("requires", []):
            if not has_requirement(pkg):
                print(f"[Plugin] Warning: '{info.get('id')}' requires '{pkg}' which is not installed")

    # ── Enable / Disable ──────────────────────────────────────────────────────

    def is_disabled(self, pid):
        return pid in self._disabled

    def is_loaded(self, pid):
        return pid in self._instances

    def get_error(self, pid):
        return self._errors.get(pid)

    def disable_plugin(self, pid):
        """Disable a plugin. Calls stop() if running. Takes effect on next restart."""
        self._disabled.add(pid)
        self._save_disabled()
        # Stop the instance if it's running
        inst = self._instances.pop(pid, None)
        if inst:
            if hasattr(inst, "stop"):
                try:
                    inst.stop()
                except Exception:
                    pass
            self._running.pop(pid, None)
            # Remove any action types this plugin registered (by owner: a lambda
            # handler has no __self__ and used to survive a disable)
            to_remove = [tid for tid, d in self._action_types.items()
                         if d.get("owner") == pid]
            for tid in to_remove:
                del self._action_types[tid]

    def reload_plugin(self, pid):
        """Stop, reimport, and restart a plugin without full app restart."""
        info = self._manifests.get(pid)
        if not info:
            return False
        # Stop the running instance
        inst = self._instances.pop(pid, None)
        if inst:
            if hasattr(inst, "stop"):
                try:
                    inst.stop()
                except Exception:
                    pass
            self._running.pop(pid, None)
            # Remove action types registered by old instance
            to_remove = [tid for tid, d in self._action_types.items()
                         if d.get("owner") == pid]
            for tid in to_remove:
                del self._action_types[tid]
        # Clear cached module so importlib re-reads the source
        mod_key = f"plugins.{pid}"
        sys.modules.pop(mod_key, None)
        # Clear __pycache__ in plugin dir
        cache_dir = os.path.join(info["_path"], "__pycache__")
        if os.path.isdir(cache_dir):
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)
        # Re-load
        if not self._load_one(pid, info, self._context):
            return False
        # Re-start service if applicable (only if not bound to a button on a
        # page that isn't currently showing)
        new_inst = self._instances.get(pid)
        if new_inst:
            ptypes = info.get("type", "")
            if isinstance(ptypes, str):
                ptypes = [ptypes]
            if "service" in ptypes:
                if self._owns_a_button(pid) and pid not in self._bound_plugins_for_page(self._current_page()):
                    pass  # its button is on a different page -- stays off until shown
                else:
                    self._start_pid(pid, new_inst, info)
        return True

    def _current_page(self):
        """Best-effort lookup of the DisplayPad's active page via the stored
        plugin context (defaults to 0 if unavailable)."""
        ctx = getattr(self, "_context", None)
        get_page = getattr(ctx, "get_displaypad_current_page", None)
        return get_page() if callable(get_page) else 0

    def enable_plugin(self, pid):
        """Enable a plugin. Loads it immediately if possible."""
        self._disabled.discard(pid)
        self._save_disabled()
        info = self._manifests.get(pid)
        if info and pid not in self._instances and hasattr(self, "_context"):
            if self._load_one(pid, info, self._context):
                # Start service if applicable
                inst = self._instances.get(pid)
                if inst:
                    ptypes = info.get("type", "")
                    if isinstance(ptypes, str):
                        ptypes = [ptypes]
                    if "service" in ptypes:
                        if self._owns_a_button(pid) and pid not in self._bound_plugins_for_page(self._current_page()):
                            pass
                        else:
                            self._start_pid(pid, inst, info)

    # ── Panel plugins ─────────────────────────────────────────────────────────

    def get_panel_plugins(self):
        """Yield (id, info, instance) for plugins that provide a panel."""
        for pid, inst in self._instances.items():
            info = self._manifests[pid]
            ptypes = info.get("type", "")
            if isinstance(ptypes, str):
                ptypes = [ptypes]
            if "panel" in ptypes and hasattr(inst, "create_panel"):
                yield pid, info, inst

    # ── Action plugins ────────────────────────────────────────────────────────

    def get_action_type_ids(self):
        """Return list of registered plugin action type IDs."""
        return list(self._action_types.keys())

    def get_action_type_labels(self):
        """Return list of (type_id, label) tuples."""
        return [(tid, d["label"]) for tid, d in self._action_types.items()]

    def get_action_handler(self, type_id):
        """Return handler callable for a plugin action type, or None."""
        entry = self._action_types.get(type_id)
        return entry["handler"] if entry else None

    def get_action_value_options(self, type_id):
        """Return (display_label, value) tuples for a plugin action type, or None.

        Plugin callbacks may raise — we swallow exceptions and treat them as
        "no suggestions" so the editor falls back to a plain text entry.
        Returned tuples have both fields as strings so the UI can show them
        directly; bare-string entries from plugins are normalised here.
        """
        entry = self._action_types.get(type_id)
        if not entry:
            return None
        cb = entry.get("value_options")
        if not callable(cb):
            return None
        try:
            opts = cb() or []
        except Exception:
            return None
        result = []
        for o in opts:
            if isinstance(o, (list, tuple)) and len(o) >= 2:
                result.append((str(o[0]), str(o[1])))
            else:
                s = str(o)
                result.append((s, s))
        return result

    # ── DisplayPad page-scoped services ───────────────────────────────────────
    #
    # A service plugin that also registers an action type (e.g. Now Playing,
    # via register_action_type) is "page-bound": it only makes sense to run
    # while a button on the currently visible DisplayPad page has that action
    # type assigned. Such plugins are started/stopped with their normal
    # start()/stop() lifecycle as the user switches pages — no extra hook is
    # required in the plugin interface. A service plugin that registers no
    # action type at all (e.g. a background socket server) is treated as
    # global and simply runs for the whole app lifetime, as before.

    def _owns_a_button(self, pid):
        """True if this plugin registered at least one action type."""
        return any(d.get("owner") == pid for d in self._action_types.values())

    def _bound_plugins_for_page(self, page):
        """Return the set of plugin ids whose registered action type is
        assigned to some button on the given DisplayPad page."""
        from shared.config import _load_displaypad_actions
        actions = _load_displaypad_actions(page)
        assigned = {a.get("type") for a in actions if a.get("type") not in (None, "none")}
        bound = set()
        for tid in assigned:
            entry = self._action_types.get(tid)
            if entry and entry.get("owner"):
                bound.add(entry["owner"])
        return bound

    def _start_pid(self, pid, inst, info, reason=""):
        if not hasattr(inst, "start"):
            return
        try:
            inst.start()
            self._running[pid] = True
            print(f"[Plugin] Started service{reason}: {info.get('name', pid)}")
        except Exception as e:
            print(f"[Plugin] Failed to start {pid}: {e}")

    def _stop_pid(self, pid, inst, info, reason=""):
        if not hasattr(inst, "stop"):
            return
        try:
            inst.stop()
        except Exception:
            pass
        self._running[pid] = False
        print(f"[Plugin] Stopped service{reason}: {info.get('name', pid)}")

    def sync_services_for_page(self, page):
        """Call this whenever the DisplayPad's active page changes. Stops
        page-bound service plugins whose button isn't on the new page and
        starts the ones whose button is — using their own start()/stop()."""
        bound = self._bound_plugins_for_page(page)
        _dbg(f"[DBG PluginManager] sync_services_for_page({page}): bound={bound} "
             f"running={dict(self._running)}")
        for pid, inst in list(self._instances.items()):
            info = self._manifests.get(pid, {})
            ptypes = info.get("type", "")
            if isinstance(ptypes, str):
                ptypes = [ptypes]
            if "service" not in ptypes or not self._owns_a_button(pid):
                continue  # not a service, or a global service -- leave alone
            should_run = pid in bound
            is_running = self._running.get(pid, False)
            _dbg(f"[DBG PluginManager]   {pid}: should_run={should_run} is_running={is_running}")
            if should_run and not is_running:
                self._start_pid(pid, inst, info, f" (page {page})")
            elif not should_run and is_running:
                self._stop_pid(pid, inst, info, f" (button not on page {page})")


    # ── Service lifecycle ─────────────────────────────────────────────────────

    def start_services(self):
        """Call start() on all service-type plugins. A plugin that binds to a
        specific DisplayPad button only starts if that button is on the
        page shown at launch (page 0); it will start later via
        sync_services_for_page() once its page becomes active. A plugin with
        no button binding is global and always starts with the app."""
        for pid, inst in self._instances.items():
            info = self._manifests[pid]
            ptypes = info.get("type", "")
            if isinstance(ptypes, str):
                ptypes = [ptypes]
            if "service" not in ptypes:
                continue
            if self._owns_a_button(pid) and pid not in self._bound_plugins_for_page(0):
                continue
            self._start_pid(pid, inst, info)

    def shutdown(self):
        """Call stop() on all plugins that have it."""
        for pid, inst in self._instances.items():
            if hasattr(inst, "stop"):
                try:
                    inst.stop()
                except Exception:
                    pass
                self._running[pid] = False


# ── Index, download, versions ────────────────────────────────────────────────

def version_tuple(s):
    """Parse a dotted version like '1.2.3' into a sortable tuple.
    Non-numeric parts become 0 so 'v1.2' parses the same as '1.2'."""
    parts = []
    for p in str(s or "").lstrip("v").split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def fetch_plugin_index(timeout=8):
    """The published plugins, from the index in the plugins repository."""
    req = urllib.request.Request(PLUGINS_INDEX_URL)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("plugins", []) if isinstance(data, dict) else []


def updates_for(plugins, manifests):
    """The published entries newer than what is installed, by id."""
    out = {}
    for pinfo in plugins or []:
        pid = pinfo.get("id")
        if pid in manifests and version_tuple(pinfo.get("version", "0")) > \
                version_tuple(manifests[pid].get("version", "0")):
            out[pid] = pinfo
    return out


def home_short(path):
    """A path as it is shown: the home directory as ~."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def download_repo(url):
    """Fetch the repository a plugin URL points into, as one zip.

    Returns (temporary directory, extracted root, path inside the repo). The
    caller removes the directory. All published plugins live in one
    repository, which is what lets an update of several share one download.
    """
    url = url.rstrip("/")
    parts = url.split("github.com/", 1)[1].split("/")
    owner = parts[0]
    repo = parts[1] if len(parts) > 1 else ""
    branch = "main"
    subpath = ""
    if len(parts) > 3 and parts[2] == "tree":
        branch = parts[3]
        subpath = "/".join(parts[4:]) if len(parts) > 4 else ""
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    tmp = tempfile.mkdtemp()
    zip_path = os.path.join(tmp, "repo.zip")
    req = urllib.request.Request(zip_url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        with open(zip_path, "wb") as f:
            f.write(resp.read())
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    return tmp, os.path.join(tmp, f"{repo}-{branch}"), subpath


def repo_branch_path(url):
    """(repository URL, branch, path inside it) of a plugin URL."""
    url = url.rstrip("/")
    if "/tree/" not in url:
        return url, "main", ""
    repo, rest = url.split("/tree/", 1)
    branch, _, sub = rest.partition("/")
    return repo, branch, sub


def plugin_dir_in(extracted_root, subpath):
    """Where in an extracted repository the plugin is."""
    if subpath:
        return os.path.join(extracted_root, subpath)
    if os.path.isfile(os.path.join(extracted_root, "plugin.json")):
        return extracted_root
    for d in os.listdir(extracted_root):
        candidate = os.path.join(extracted_root, d)
        if os.path.isdir(candidate) and os.path.isfile(
                os.path.join(candidate, "plugin.json")):
            return candidate
    raise FileNotFoundError("No plugin.json found in repository")


class UrllibNet:
    """The real network: the plugin index and the repository download."""

    def fetch_index(self, timeout=8):
        return fetch_plugin_index(timeout)

    def download_repo(self, url):
        return download_repo(url)


class PluginInstallError(Exception):
    """An install that did not happen.

    ``code`` is one of no_manifest, no_id, is_link, other_copy, failed;
    ``label`` names the plugin (its name, else its id, else the folder);
    ``params`` carries what a sentence for is_link (path, target) and
    other_copy (path) needs. ``str()`` is "label: message" in English, which
    is what an interface without a translation shows.
    """

    def __init__(self, code, label, message, **params):
        super().__init__(message)
        self.code = code
        self.label = label
        self.message = message
        self.params = params

    def __str__(self):
        return "%s: %s" % (self.label, self.message)


class PreparedUpdates:
    """What download_updates() fetched: plugin folders to install, what could
    not be fetched (strings), and the temporary directories to remove."""

    def __init__(self):
        self.folders = []
        self.failed = []
        self.temps = []


class PluginHost(Service):
    name = "plugins"

    def __init__(self, app, manager=None, net=None, plugins_dir=None,
                 check_interval_ms=CHECK_INTERVAL_MS,
                 first_check_ms=FIRST_CHECK_MS):
        super().__init__(app)
        self.manager = manager if manager is not None else PluginManager()
        self.net = net or UrllibNet()
        self.plugins_dir = plugins_dir or PLUGINS_DIR
        self.check_interval_ms = check_interval_ms
        self.first_check_ms = first_check_ms
        self.available = []         # the last index noted
        self.update_count = 0
        self.told = 0               # the count last announced
        self._timer = None
        self._checking = False

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def start(self):
        """Look for plugin updates a little after start and then twice a day:
        the Plugins screen is only built when someone opens it, so leaving it
        to that screen meant nobody heard of an update they did not go looking
        for (#103)."""
        if self.first_check_ms is not None:
            self._timer = self.app.scheduler.call_later(
                self.first_check_ms, self._tick)

    def stop(self):
        t, self._timer = self._timer, None
        try:
            self.app.scheduler.cancel(t)
        except Exception:
            pass

    def commands(self):
        return {
            "list": self.cmd_list, "status": self.cmd_status,
            "fetch_index": self.cmd_fetch_index, "updates": self.cmd_updates,
            "check": self.cmd_check, "enable": self.cmd_enable,
            "disable": self.cmd_disable, "reload": self.cmd_reload,
            "uninstall": self.cmd_uninstall, "install": self.cmd_install,
            "update_all": self.cmd_update_all,
        }

    # ── Index and updates ──────────────────────────────────────────────────
    def fetch_index(self, timeout=8):
        """The published plugins. Blocking network call, raises when offline."""
        return self.net.fetch_index(timeout)

    def updates_for(self, plugins=None):
        """Published entries newer than the installed ones, by id."""
        return updates_for(self.available if plugins is None else plugins,
                           self.manager._manifests)

    def note_index(self, plugins, announce=False):
        """A fetched index: remember it, count the installed plugins with a
        newer version and publish plugins.updates {count, announce}.

        ``announce`` in the event is true only when asked for here and the
        count is higher than the last announced one, so the same update is
        said once. Runs on the scheduler thread (other threads are handed
        over). Returns the published payload.
        """
        if not self.app.scheduler.in_scheduler_thread():
            self.app.scheduler.call_soon(self.note_index, plugins, announce)
            return None
        self.available = list(plugins or [])
        count = len(updates_for(self.available, self.manager._manifests))
        self.update_count = count
        say = bool(announce and count > self.told)
        self.told = count if announce else min(self.told, count)
        self.publish("index", count=len(self.available))
        payload = {"count": count, "announce": say}
        self.publish("updates", **payload)
        return payload

    def check_updates(self):
        """Ask the index off the scheduler thread; the answer is noted (with
        announce) on it. Silent when there is no network: the next round asks
        again."""
        if self._checking:
            return
        self._checking = True

        def run():
            try:
                plugins = self.fetch_index()
            except Exception:
                return
            finally:
                self._checking = False
            try:
                self.app.scheduler.call_soon(self.note_index, plugins, True)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True, name="plugin-index").start()

    def _tick(self):
        self._timer = self.app.scheduler.call_later(
            self.check_interval_ms, self._tick)
        self.check_updates()

    # ── Status ─────────────────────────────────────────────────────────────
    def newer_published(self, pid):
        """The published entry when it is newer than the installed copy."""
        return self.updates_for().get(pid)

    def status(self, pid):
        """off (disabled), bad (did not load), warn (a requirement is
        missing), info (an update is published), ok."""
        pm = self.manager
        info = pm._manifests.get(pid, {})
        if pm.is_disabled(pid):
            return "off"
        if not pm.is_loaded(pid):
            return "bad"
        if any(not has_requirement(m) for m in info.get("requires", []) or []):
            return "warn"
        if self.newer_published(pid):
            return "info"
        return "ok"

    def plugin_list(self):
        """Everything an interface lists for the installed plugins."""
        pm = self.manager
        out = []
        for pid in sorted(pm._manifests):
            info = pm._manifests[pid]
            ptypes = info.get("type", "")
            if isinstance(ptypes, str):
                ptypes = [ptypes]
            newer = self.newer_published(pid)
            path = info.get("_path", "")
            requires = [{"name": str(r), "ok": has_requirement(r)}
                        for r in info.get("requires", []) or []]
            out.append({
                "id": pid, "name": info.get("name", pid),
                "version": str(info.get("version", "")),
                "types": list(ptypes), "status": self.status(pid),
                "error": pm.get_error(pid), "path": path,
                "update": newer.get("version") if newer else None,
                "description": str(info.get("description", "")),
                "author": str(info.get("author", "")),
                "requires": requires,
                # a panel plugin draws a CustomTkinter panel, which a web
                # interface cannot show
                "panel": "panel" in ptypes,
                # only plugins in the user's folder can be uninstalled
                "removable": bool(path) and os.path.abspath(path).startswith(
                    os.path.abspath(self.plugins_dir) + os.sep),
            })
        return out

    # ── Enable / disable / reload ──────────────────────────────────────────
    def enable(self, pid):
        self.manager.enable_plugin(pid)
        self.publish("changed", id=pid, change="enabled")

    def disable(self, pid):
        self.manager.disable_plugin(pid)
        self.publish("changed", id=pid, change="disabled")

    def reload(self, pid):
        ok = self.manager.reload_plugin(pid)
        self.publish("changed", id=pid, change="reloaded" if ok else "failed")
        return ok

    def uninstall(self, pid):
        """Disable a plugin, delete its folder and forget it."""
        pm = self.manager
        pm.disable_plugin(pid)
        plugin_path = os.path.join(self.plugins_dir, pid)
        if os.path.isdir(plugin_path):
            shutil.rmtree(plugin_path)
        pm._manifests.pop(pid, None)
        pm._instances.pop(pid, None)
        pm._errors.pop(pid, None)
        self.publish("changed", id=pid, change="uninstalled")

    # ── Installing ─────────────────────────────────────────────────────────
    def install_from_folder(self, src):
        """Put the plugin in folder `src` in place of the installed one.

        Raises PluginInstallError when it could not become the copy that runs:
        no manifest, no id, the folder is a link (most likely someone's own
        checkout, which Python refuses to remove through), another folder with
        the same id later in the alphabet is the one the manager keeps.
        Returns the plugin id.
        """
        label = os.path.basename(os.path.normpath(src))
        try:
            manifest_path = os.path.join(src, "plugin.json")
            if not os.path.isfile(manifest_path):
                raise PluginInstallError("no_manifest", label,
                                         "No plugin.json found")
            with open(manifest_path) as f:
                manifest = json.load(f)
            pid = manifest.get("id", "")
            label = manifest.get("name") or pid or label
            if not pid:
                raise PluginInstallError("no_id", label, "No id in plugin.json")

            dest = os.path.join(self.plugins_dir, pid)
            if os.path.islink(dest):
                target = os.path.realpath(dest)
                raise PluginInstallError(
                    "is_link", label,
                    "%s is a link to %s" % (home_short(dest), home_short(target)),
                    path=home_short(dest), target=home_short(target))
            if os.path.lexists(dest):
                shutil.rmtree(dest)

            shutil.copytree(src, dest)
            cache = os.path.join(dest, "__pycache__")
            if os.path.isdir(cache):
                shutil.rmtree(cache)

            # Another folder with the same id, later in the alphabet, is the
            # one the manager keeps, and the update beside it changes nothing.
            pm = self.manager
            pm.discover()
            runs = pm._manifests.get(pid, {}).get("_path", "")
            if runs and os.path.abspath(runs) != os.path.abspath(dest):
                raise PluginInstallError(
                    "other_copy", label,
                    "another copy at %s runs instead" % home_short(runs),
                    path=home_short(runs))
        except PluginInstallError:
            raise
        except Exception as e:
            raise PluginInstallError("failed", label, str(e)) from e
        self.publish("changed", id=pid, change="installed")
        return pid

    def fetch_plugin(self, url):
        """Download the repository `url` points into and find the plugin in
        it. Returns (temporary directory, plugin folder); the caller installs
        the folder and removes the directory. Blocking network call."""
        tmp, extracted_root, subpath = self.net.download_repo(url)
        try:
            return tmp, plugin_dir_in(extracted_root, subpath)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def install_from_github(self, url):
        """Download, install and clean up. Returns the plugin id."""
        tmp, folder = self.fetch_plugin(url)
        try:
            return self.install_from_folder(folder)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def download_updates(self, pending=None):
        """Download what Update all installs: every installed plugin with a
        newer published version, one download per repository. Blocking
        network; safe off the scheduler thread. Returns PreparedUpdates."""
        if pending is None:
            pending = self.updates_for()
        by_source = {}      # (repository, branch) -> entries, one download each
        for pinfo in pending.values():
            url = pinfo.get("url", "")
            if url:
                repo, branch, _sub = repo_branch_path(url)
                by_source.setdefault((repo, branch), []).append(pinfo)
        prepared = PreparedUpdates()
        for plugins in by_source.values():
            try:
                tmp, root, _sub = self.net.download_repo(plugins[0]["url"])
            except Exception as e:
                prepared.failed.append(str(e))
                continue
            prepared.temps.append(tmp)
            for pinfo in plugins:
                try:
                    prepared.folders.append(plugin_dir_in(
                        root, repo_branch_path(pinfo["url"])[2]))
                except Exception as e:
                    prepared.failed.append(
                        f"{pinfo.get('name') or pinfo.get('id')}: {e}")
        return prepared

    def install_prepared(self, prepared):
        """Put downloaded plugins in place one after another. Returns
        (installed count, failed): failed holds PluginInstallError objects and
        strings, each beginning with the plugin's name when str()ed."""
        failed = list(prepared.failed)
        installed = 0
        for folder in prepared.folders:
            try:
                self.install_from_folder(folder)
                installed += 1
            except PluginInstallError as e:
                failed.append(e)
        for tmp in prepared.temps:
            shutil.rmtree(tmp, ignore_errors=True)
        return installed, failed

    def update_all(self):
        """download_updates() and install_prepared() in one go."""
        return self.install_prepared(self.download_updates())

    # ── Commands ───────────────────────────────────────────────────────────
    def _need(self, pid):
        if pid not in self.manager._manifests:
            raise CommandError("no such plugin: %s" % pid)

    def cmd_list(self):
        return {"plugins": self.plugin_list()}

    def cmd_status(self, id):
        self._need(id)
        return {"id": id, "status": self.status(id)}

    def cmd_fetch_index(self):
        try:
            plugins = self.fetch_index()
        except Exception as e:
            raise CommandError("could not fetch the plugin index: %s" % e)
        self.note_index(plugins)
        return {"plugins": plugins}

    def cmd_updates(self):
        return {"count": self.update_count,
                "updates": {p: i.get("version") for p, i in
                            self.updates_for().items()}}

    def cmd_check(self):
        self.check_updates()
        return None

    def cmd_enable(self, id):
        self._need(id)
        self.enable(id)

    def cmd_disable(self, id):
        self._need(id)
        self.disable(id)

    def cmd_reload(self, id):
        self._need(id)
        if not self.reload(id):
            raise CommandError("could not reload %s" % id)

    def cmd_uninstall(self, id):
        self._need(id)
        self.uninstall(id)

    @staticmethod
    def _install_error(e):
        """A CommandError that carries the install problem's code, the
        plugin's label and the sentence's parameters."""
        return CommandError(str(e), code=e.code, label=e.label, **e.params)

    def cmd_install(self, source):
        try:
            if os.path.isdir(source):
                pid = self.install_from_folder(source)
            elif "github.com" in source:
                pid = self.install_from_github(source)
            else:
                raise CommandError("not a folder or GitHub URL", code="bad_source")
        except PluginInstallError as e:
            raise self._install_error(e)
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(str(e), code="failed")
        return {"id": pid}

    def cmd_update_all(self):
        installed, failed = self.update_all()
        return {"installed": installed, "failed": [str(f) for f in failed],
                "failures": [self._failure(f) for f in failed]}

    @staticmethod
    def _failure(f):
        """One failed update as {code, label, message, params}; a download
        problem (a plain string) has the code "failed"."""
        if isinstance(f, PluginInstallError):
            return {"code": f.code, "label": f.label, "message": f.message,
                    "params": f.params}
        return {"code": "failed", "label": "", "message": str(f), "params": {}}


def install(app, manager=None, net=None):
    """Add the plugin service to a CoreApp."""
    return app.add_service(PluginHost(app, manager, net))
