"""Base class for the core's services (one per device, plus macros, plugins...).

A service owns one area: its state, its worker threads, its hardware access.
It tells the world what happened through events under its own name
(``self.publish("page_switched", page=...)`` becomes the topic
``displaypad.page_switched``) and offers its operations as commands, which
CoreApp registers under the same prefix.
"""


class Service:
    #: Prefix of this service's events and commands, e.g. "displaypad".
    name = ""

    def __init__(self, app):
        if not self.name:
            raise TypeError("%s has no name" % type(self).__name__)
        self.app = app
        self.bus = app.bus

    def start(self):
        """Start worker threads and monitors. Called once by CoreApp.start()."""

    def stop(self):
        """Stop what start() started and release the hardware. Must not raise."""

    def commands(self):
        """Operations this service offers: {local name: callable}.

        Registered as "<service name>.<local name>".
        """
        return {}

    def aliases(self):
        """Commands registered under a name of their own, without the
        service's prefix: {name: callable}. Only for names that exist from
        before the core (the control socket's 3.x commands)."""
        return {}

    def publish(self, event, **payload):
        self.bus.publish("%s.%s" % (self.name, event), **payload)
