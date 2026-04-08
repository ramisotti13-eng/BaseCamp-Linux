"""PluginManager -- discovers and loads plugins from ~/.config/mountain-time-sync/plugins/."""
import os
import sys
import json
import importlib.util

from shared.config import CONFIG_DIR, PLUGINS_DISABLED_FILE

PLUGINS_DIR = os.path.join(CONFIG_DIR, "plugins")


class PluginManager:
    """Scan, load, and manage lifecycle of user plugins."""

    def __init__(self):
        self._manifests = {}     # id -> manifest dict
        self._instances = {}     # id -> Plugin instance
        self._action_types = {}  # type_id -> {"label", "handler"}
        self._disabled = set()   # set of disabled plugin IDs
        self._errors = {}        # id -> error string
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
            spec.loader.exec_module(mod)
            instance = mod.Plugin(context)
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
            try:
                __import__(pkg)
            except ImportError:
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
            # Remove any action types this plugin registered
            to_remove = [tid for tid, d in self._action_types.items()
                         if getattr(d.get("handler"), "__self__", None) is inst]
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
            # Remove action types registered by old instance
            to_remove = [tid for tid, d in self._action_types.items()
                         if getattr(d.get("handler"), "__self__", None) is inst]
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
        # Re-start service if applicable
        new_inst = self._instances.get(pid)
        if new_inst:
            ptypes = info.get("type", "")
            if isinstance(ptypes, str):
                ptypes = [ptypes]
            if "service" in ptypes and hasattr(new_inst, "start"):
                try:
                    new_inst.start()
                except Exception as e:
                    print(f"[Plugin] Failed to start {pid}: {e}")
        return True

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
                    if "service" in ptypes and hasattr(inst, "start"):
                        try:
                            inst.start()
                        except Exception as e:
                            print(f"[Plugin] Failed to start {pid}: {e}")

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

    # ── Service lifecycle ─────────────────────────────────────────────────────

    def start_services(self):
        """Call start() on all service-type plugins."""
        for pid, inst in self._instances.items():
            info = self._manifests[pid]
            ptypes = info.get("type", "")
            if isinstance(ptypes, str):
                ptypes = [ptypes]
            if "service" in ptypes and hasattr(inst, "start"):
                try:
                    inst.start()
                    print(f"[Plugin] Started service: {info.get('name', pid)}")
                except Exception as e:
                    print(f"[Plugin] Failed to start {pid}: {e}")

    def shutdown(self):
        """Call stop() on all plugins that have it."""
        for pid, inst in self._instances.items():
            if hasattr(inst, "stop"):
                try:
                    inst.stop()
                except Exception:
                    pass
