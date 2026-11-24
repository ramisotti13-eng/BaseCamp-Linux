"""CoreApp: holds the services, the event bus and the command registry.

One CoreApp runs per process. An interface creates it, adds the services,
subscribes to the events it wants to show and calls start(). The same CoreApp
can run with no interface at all (a tray-only start, a headless test), since
nothing in it waits for a window.
"""
import logging

from core.bus import EventBus
from core.commands import CommandError, CommandRegistry
from core.scheduler import ThreadScheduler

log = logging.getLogger("basecamp.core.app")


class CoreApp:
    def __init__(self, bus=None, registry=None, scheduler=None):
        self.bus = bus or EventBus()
        self.commands = registry or CommandRegistry()
        # Where services run their logic (see core/scheduler.py). An interface
        # with a main loop passes its own; otherwise the core runs one thread
        # of its own, started and stopped with the app.
        self._own_scheduler = scheduler is None
        self.scheduler = scheduler or ThreadScheduler()
        self._services = {}
        self._order = []
        self._started = False

    # ── Services ───────────────────────────────────────────────────────────
    def add_service(self, service):
        """Register a service and its commands. Returns the service."""
        if service.name in self._services:
            raise ValueError("service %r is already added" % service.name)
        names = []
        for local, fn in (service.commands() or {}).items():
            full = "%s.%s" % (service.name, local)
            self.commands.register(full, fn)
            names.append(full)
        # Names without the service's prefix: the control socket's 3.x
        # commands ("ping", "dp_page" ...) stay reachable as they were.
        aliases = getattr(service, "aliases", None)
        for alias, fn in ((aliases() if aliases else None) or {}).items():
            self.commands.register(alias, fn)
            names.append(alias)
        self._services[service.name] = service
        self._order.append(service.name)
        service._command_names = names
        if self._started:
            self._start_one(service)
        return service

    def remove_service(self, name):
        service = self._services.pop(name, None)
        if service is None:
            return
        self._order.remove(name)
        for full in getattr(service, "_command_names", ()):
            self.commands.unregister(full)
        if self._started:
            self._stop_one(service)

    def service(self, name):
        """The service called `name`, or None."""
        return self._services.get(name)

    def services(self):
        return [self._services[n] for n in self._order]

    # ── Lifecycle ──────────────────────────────────────────────────────────
    @property
    def started(self):
        return self._started

    def start(self):
        if self._started:
            return
        self._started = True
        if self._own_scheduler:
            self.scheduler.start()
        for name in list(self._order):
            self._start_one(self._services[name])
        self.bus.publish("core.started")

    def stop(self):
        """Stop every service, last added first. Never raises."""
        if not self._started:
            return
        self._started = False
        for name in reversed(list(self._order)):
            self._stop_one(self._services[name])
        self.bus.publish("core.stopped")
        if self._own_scheduler:
            self.scheduler.stop()

    def _start_one(self, service):
        try:
            service.start()
        except Exception:
            log.exception("service %s failed to start", service.name)
            self.bus.publish("core.service_failed", service=service.name,
                             phase="start")

    def _stop_one(self, service):
        try:
            service.stop()
        except Exception:
            log.exception("service %s failed to stop", service.name)

    # ── Commands ───────────────────────────────────────────────────────────
    def call(self, command, /, **params):
        """Run a registered command; see CommandRegistry.call()."""
        return self.commands.call(command, **params)

    def dispatch(self, obj):
        """Run a command given as a wire object; see CommandRegistry.dispatch()."""
        return self.commands.dispatch(obj)


__all__ = ["CoreApp", "CommandError"]
