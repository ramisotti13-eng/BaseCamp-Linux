"""Device services of the core, one package per device family.

Each service module may define ``install(app)``, which creates its service(s)
and adds them to the CoreApp. ``install_all()`` calls every module that has
one, in the order below (shared services first, so devices can use them),
so the interface wires everything up with one call and a new device needs no
change to the interface's start-up code.
"""
import importlib
import logging

log = logging.getLogger("basecamp.core.devices")

SERVICE_MODULES = (
    "core.macros",                  # macros and the shared action dispatcher
    "core.devices.everest_max",
    "core.devices.everest60",
    "core.devices.makalu",
    "core.devices.macropad",
    "core.devices.displaypad",
)


def install_all(app, modules=SERVICE_MODULES):
    """Install every service module that exists. Returns the installed names."""
    installed = []
    for modname in modules:
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError as e:
            if e.name == modname:
                continue            # not extracted yet
            raise
        install = getattr(mod, "install", None)
        if install is None:
            continue
        try:
            install(app)
            installed.append(modname)
        except Exception:
            log.exception("installing service module %s failed", modname)
    return installed
