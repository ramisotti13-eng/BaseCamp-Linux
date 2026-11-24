"""The whole core, wired the way gui.py wires it, without any interface.

gui.py builds its CoreApp step by step between the parts of its window: the
scheduler is the Tk main loop, and the DisplayPad gets its host (transport,
translation, plugin types, widget services) from its screen. An interface
that is not CustomTkinter (the web interface, a tray-only start, a test)
gets the same core from here:

    app = build_core(version="4.0")
    start_core(app)          # app.start(), a first device scan, polling
    ...
    app.stop()

What build_core() does, in gui.py's order:

  i18n, devices (the registry: presence, access, the OBS "device"), settings,
  obs, update, the device services (install_all: macros with the one key
  action dispatcher, everest_max, everest60, makalu, macropad, displaypad),
  profiles (the device profiles, core/profiles.py; not in gui.py),
  device_images, icon_packs, images (the image editor), plugins, control.

and the wiring between them:

  - the key action dispatcher runs OBS actions through the OBS service and
    plugin types through the plugin manager (gui: _obs_executor,
    _plugin_action_handler);
  - the Everest 60 and Makalu services build their controller command lines
    with core.runtime, as gui's _cmd_for_device does;
  - the registry asks the DisplayPad service whether an upload is running
    (gui: _displaypad_busy) and counts OBS as a device;
  - the DisplayPad gets the host its screen hands it under CustomTkinter:
    the transport (core protocol, or a stand-in), the i18n service's T(),
    the plugins' action type ids and their page-bound widget services. The
    action env is the service's default, the shared dispatcher, as under
    the screen;
  - the plugins are discovered and loaded when the core starts, with a
    PluginContext over a small host object instead of the window, and their
    services are started and shut down with the core.

Nothing here imports a toolkit (tools/test_core_isolation.py). gui.py keeps
its own wiring, because its tests patch names inside it; tools/test_bootstrap.py
checks this one.

Stand-ins: `presence(vid, pid)` replaces the USB presence check (with
nothing denied unless `access_denied` says so), and `transports` replaces
what talks to a device, per service:

    {"displaypad": pad with protocol.py's names,
     "everest_max": an everest_max.backend.ControllerBackend,
     "everest60": / "makalu": a core.devices.controller_backend.ControllerBackend,
     "macropad": an opener, () -> a MacroPad-like device}

`net`, `http` and `obs_client` replace the plugin index, the update check and
the OBS WebSocket. `http.open` also stands in for the downloads of Mountain's
assets (device_images.download, icon_packs.install; core/mountain_assets.py),
which otherwise go to mountain.gg, and only when a command asks. The web
interface's demo mode uses all of them.
"""
import logging
import socket

from core.app import CoreApp

log = logging.getLogger("basecamp.core.bootstrap")

#: Services whose `backend` a stand-in replaces.
_BACKEND_SERVICES = ("everest_max", "everest60", "makalu")
#: (service, device id) of the services that run a controller CLI.
_COMMAND_SERVICES = (("everest60", "everest60"), ("makalu", "makalu67"))


class HeadlessHost:
    """What PluginContext reaches on the application when there is no
    window: the core, translation, and empty views of the screens. A
    plugin's panel is not built (nobody calls create_panel), a DisplayPad
    widget gets the DisplayPad service through DisplayPadView."""

    _panel_area = None
    _kb_panel_id = None

    def __init__(self, core, plugin_manager):
        self.core = core
        self._plugin_manager = plugin_manager
        self._panels = {}

    @property
    def _i18n(self):
        return self.core.service("i18n")

    def T(self, key, **kwargs):
        svc = self._i18n
        return svc.T(key, **kwargs) if svc is not None else key

    @property
    def _lang_code(self):
        svc = self._i18n
        return svc.code if svc is not None else "en"

    @property
    def _lang(self):
        svc = self._i18n
        return svc.strings if svc is not None else {}

    @property
    def _dev_present(self):
        reg = self.core.service("devices")
        return dict(reg.present) if reg is not None else {}


def _plugin_host_class():
    from core.plugins import PluginHost

    class LoadingPluginHost(PluginHost):
        """The plugin service, plus what gui.py does around it: discover and
        load the plugins when the core starts (after every device service,
        so a plugin finds them), start their services a moment later, and
        shut them down before the device services stop."""

        def __init__(self, app, manager=None, net=None, load=True, **kw):
            super().__init__(app, manager, net=net, **kw)
            self._load = load
            self._loaded = False
            self._services_timer = None
            self.host = HeadlessHost(app, self.manager)

        def start(self):
            if self._load and not self._loaded:
                self._loaded = True
                from shared.plugin_api import PluginContext
                try:
                    self.manager.discover()
                    self.manager.load_all(PluginContext(self.host, self.manager))
                except Exception:
                    log.exception("loading the plugins failed")
                self._services_timer = self.app.scheduler.call_later(
                    100, self.manager.start_services)
            super().start()

        def stop(self):
            t, self._services_timer = self._services_timer, None
            self.app.scheduler.cancel(t)
            try:
                self.manager.shutdown()
            except Exception:
                log.exception("stopping the plugins failed")
            super().stop()

    return LoadingPluginHost


def build_core(scheduler=None, *, presence=None, transports=None, version="",
               lang_dir=None, home=None, access_denied=None, describe=None,
               net=None, http=None, obs_client=None, plugins=True,
               control=True, control_path=None):
    """A CoreApp with every service and gui.py's wiring, not started.

    scheduler      the core's scheduler (default: a ThreadScheduler of its own)
    presence       (vid, pid) -> bool instead of the USB bus
    transports     stand-ins for what talks to a device (see the module doc)
    version        what `ping` and the update check say
    lang_dir       the language files (default: the shipped lang/)
    home           the user's home for the update service
    net, http      the plugin index / downloads, the update check's HTTP
                   (and, through http.open, Mountain's asset downloads)
    obs_client     the OBS WebSocket client factory
    plugins        False: no plugins are loaded (the service is still there)
    control        False: no control socket (the service is still there)
    control_path   where the control socket goes (default: shared.ipc's)
    """
    # Imported here, not at the top: shared.config creates the configuration
    # directory when it is imported, and a caller (the demo mode) has to be
    # able to point it elsewhere first.
    from shared.config import CONFIG_DIR, shipped_path
    from core import firstrun, runtime
    # Before any service reads or migrates a 3.x configuration: save it once.
    firstrun.backup_before_first_start(CONFIG_DIR)
    from core.control import ControlService
    from core.devices import install_all
    from core.devices.registry import DeviceRegistry
    from core.device_images import make_service as device_images_service
    from core.images import make_service as images_service
    from core.mountain_assets import Downloader, make_icon_pack_service
    from core.i18n import I18nService
    from core.obs import ObsService
    from core.settings import SettingsService
    from core.updates import UpdateService

    transports = dict(transports or {})
    app = CoreApp(scheduler=scheduler)

    i18n = app.add_service(I18nService(app, lang_dir or shipped_path("lang"), CONFIG_DIR))

    if presence is not None:
        # A stand-in bus has no /dev nodes to be refused.
        access_denied = access_denied or (lambda vid, pid: [])
        describe = describe or str
    registry = app.add_service(DeviceRegistry(
        app, presence=presence, access_denied=access_denied, describe=describe))

    app.add_service(SettingsService(app))
    obs = app.add_service(ObsService(app, client_factory=obs_client))
    registry.add_virtual("obs", obs.is_connected)
    app.add_service(UpdateService(app, version, home=home, http=http))

    install_all(app)
    # After the device services: the profiles find their adapters there,
    # and stop before them (quit saves each active profile's files).
    from core.profiles import ProfileService
    app.add_service(ProfileService(app, version=version))

    # The one key action dispatcher: OBS and the plugins' types are the
    # application's (gui: _obs_executor, _plugin_action_handler).
    host_cls = _plugin_host_class()
    # Without plugins there is nothing to look for updates of either.
    plugin_host = host_cls(app, net=net, load=plugins,
                           **({} if plugins else {"first_check_ms": None}))
    pm = plugin_host.manager
    actions = getattr(app.service("macros"), "actions", None)
    if actions is not None:
        actions.set_obs(lambda: obs.execute_action)
        actions.set_plugin_lookup(lambda type_id: pm.get_action_handler(type_id))

    # Stand-ins for the hardware, and the controller command lines.
    for name in _BACKEND_SERVICES:
        svc = app.service(name)
        if svc is not None and transports.get(name) is not None:
            svc.backend = transports[name]
    for name, dev_id in _COMMAND_SERVICES:
        svc = app.service(name)
        if svc is not None and transports.get(name) is None and hasattr(svc, "use_command"):
            svc.use_command(lambda *a, d=dev_id: runtime.controller_cmd(d, *a))
    macropad = app.service("macropad")
    if macropad is not None and transports.get("macropad") is not None:
        macropad.use_opener(transports["macropad"])

    # The DisplayPad's host, as its screen hands it in under CustomTkinter.
    pad = app.service("displaypad")
    if pad is not None:
        pad.attach_host(
            transport=transports.get("displaypad"),
            translate=i18n.T,
            plugin_types=lambda: pm.get_action_type_ids(),
            sync_services=lambda page: pm.sync_services_for_page(page),
            start=False)
        registry.set_busy_probe("displaypad", lambda: pad.busy)

    # Mountain's assets: the network only on a command, and never past a
    # stand-in when one was given.
    downloader = Downloader(opener=http.open) if http is not None else None
    app.add_service(device_images_service(app, downloader=downloader))
    app.add_service(make_icon_pack_service(app, downloader=downloader))
    app.add_service(images_service(app))
    app.add_service(plugin_host)
    app.add_service(ControlService(
        app, version=version, path=control_path,
        command=lambda dev, *a: runtime.controller_cmd(dev, *a),
        # No AF_UNIX (Windows): the service is there, the socket is not.
        listen=bool(control) and hasattr(socket, "AF_UNIX")))
    return app


def start_core(app, poll_ms=5000):
    """Start the core, scan the devices once at once and then every
    `poll_ms` (gui: _check_devices, start_polling)."""
    app.start()
    registry = app.service("devices")
    if registry is not None:
        app.scheduler.call_soon(registry.scan)
        registry.start_polling(poll_ms)
    return app
