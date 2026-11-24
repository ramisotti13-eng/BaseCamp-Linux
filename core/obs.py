"""OBS Studio over its WebSocket: connection, scenes, actions.

``ObsService`` (service ``obs``) is what devices/obs/panel.py did besides
drawing its form. The panel is a client now: it passes the connection settings
it shows, and draws what ``obs.changed`` says. Other panels keep calling
``app._obs_panel.is_connected()``, ``get_scenes()`` and ``execute_action()``,
which delegate here.

Event ``obs.changed {status, connected, scenes, message}``. ``status`` is one
of ``connecting``, ``connected``, ``disconnected``, ``unreachable`` (connection
refused) or ``error`` (``message`` holds the reason). Connecting happens on a
thread and its result is published on the scheduler; ``connect(wait=True)`` runs
inline (tests).

The WebSocket client (``obsws_python``, not always installed) is created by an
injectable ``client_factory(host, port, password, timeout)``.
"""
import logging
import threading

from core.commands import CommandError
from core.service import Service

log = logging.getLogger("basecamp.core.obs")


def _default_client(host, port, password, timeout):
    import obsws_python as obs
    return obs.ReqClient(host=host, port=port, password=password, timeout=timeout)


class ObsService(Service):
    name = "obs"

    def __init__(self, app, config=None, client_factory=None):
        super().__init__(app)
        self._config = config
        self._factory = client_factory or _default_client
        self.connected = False
        self.scenes = []

    @property
    def config(self):
        if self._config is None:
            import shared.config as cfg
            self._config = cfg
        return self._config

    # ── Settings ───────────────────────────────────────────────────────────
    def get_config(self):
        return self.config.load_obs_config()

    @staticmethod
    def build_config(host, port, password):
        """The settings dict that is saved (and used for actions)."""
        return {
            "host": (host or "").strip(),
            "port": int(str(port).strip() or "4455"),
            "password": password or "",
            "buttons": [{"type": "none", "scene": ""} for _ in range(4)],
        }

    def save_config(self, cfg):
        self.config.save_obs_config(cfg)

    # ── Connection ─────────────────────────────────────────────────────────
    def state(self):
        return {"connected": self.connected, "scenes": list(self.scenes)}

    def _publish_changed(self, status, message=""):
        self.publish("changed", status=status, connected=self.connected,
                     scenes=list(self.scenes), message=message)

    def connect(self, cfg=None, wait=False):
        """Connect with `cfg` (default: the saved settings), save it, read the
        scene list. The result arrives as obs.changed."""
        cfg = cfg or self.get_config()
        self.save_config(cfg)
        self._publish_changed("connecting")

        def run():
            import socket
            old_timeout = socket.getdefaulttimeout()
            try:
                socket.setdefaulttimeout(4)
                cl = self._factory(cfg["host"], cfg["port"],
                                   cfg.get("password", ""), 4)
                resp = cl.get_scene_list()
                scenes = [s["sceneName"] for s in resp.scenes]
                cl.disconnect()
                self._deliver(self._on_connected, cfg, scenes, wait=wait)
            except ConnectionRefusedError:
                self._deliver(self._publish_changed, "unreachable", wait=wait)
            except Exception as e:
                msg = str(e) or type(e).__name__
                self._deliver(self._publish_changed, "error", msg, wait=wait)
            finally:
                socket.setdefaulttimeout(old_timeout)

        if wait:
            run()
        else:
            threading.Thread(target=run, name="obs-connect", daemon=True).start()

    def _deliver(self, fn, *args, wait=False):
        if wait:
            fn(*args)
        else:
            self.app.scheduler.call_soon(fn, *args)

    def _on_connected(self, cfg, scenes):
        self.connected = True
        self.scenes = list(scenes)
        self.save_config(cfg)
        self._publish_changed("connected")

    def disconnect(self, cfg=None):
        """Forget the connection (the sockets are short-lived anyway) and save
        the settings."""
        self.connected = False
        self.scenes = []
        self.save_config(cfg or self.get_config())
        self._publish_changed("disconnected")

    def get_scenes(self):
        return list(self.scenes)

    def is_connected(self):
        return self.connected

    # ── Actions ────────────────────────────────────────────────────────────
    def execute_action(self, action_type, scene="", cfg=None, wait=False):
        """Run an OBS action ('scene', 'record', 'stream') on a thread.
        Best effort: failures are swallowed, as they always were."""
        cfg = cfg or self.get_config()
        btn = {"type": action_type, "scene": scene}
        if wait:
            self._run_action(btn, cfg)
        else:
            threading.Thread(target=self._run_action, args=(btn, cfg),
                             name="obs-action", daemon=True).start()

    def _run_action(self, btn_cfg, obs_cfg):
        try:
            cl = self._factory(obs_cfg["host"], obs_cfg["port"],
                               obs_cfg.get("password", ""), 4)
            t = btn_cfg.get("type", "none")
            if t == "scene":
                cl.set_current_program_scene(btn_cfg["scene"])
            elif t == "record":
                cl.toggle_record()
            elif t == "stream":
                cl.toggle_stream()
            cl.disconnect()
        except Exception:
            pass

    # ── Commands ───────────────────────────────────────────────────────────
    def commands(self):
        return {"connect": self._cmd_connect, "disconnect": self._cmd_disconnect,
                "scenes": self._cmd_scenes, "execute": self._cmd_execute,
                "state": self.state, "config": self._cmd_config}

    def _cmd_config(self):
        """The saved connection settings, without the password (an interface
        shows whether one is set; it sends one only when the user types it)."""
        cfg = self.get_config()
        return {"host": cfg.get("host", ""), "port": cfg.get("port", 4455),
                "password_set": bool(cfg.get("password"))}

    def _cmd_connect(self, host=None, port=None, password=None):
        cfg = None
        if host is not None or port is not None or password is not None:
            cur = self.get_config()
            cfg = self.build_config(
                host if host is not None else cur["host"],
                port if port is not None else cur["port"],
                password if password is not None else cur.get("password", ""))
        self.connect(cfg)
        return {"connecting": True}

    def _cmd_disconnect(self):
        self.disconnect()
        return self.state()

    def _cmd_scenes(self):
        return {"scenes": self.get_scenes(), "connected": self.connected}

    def _cmd_execute(self, type, scene=""):
        if type not in ("scene", "record", "stream"):
            raise CommandError("unknown OBS action: %s" % type)
        if type == "scene" and not scene:
            raise CommandError("scene is required")
        self.execute_action(type, scene)
        return None
