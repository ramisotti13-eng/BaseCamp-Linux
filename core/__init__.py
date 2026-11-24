"""The UI-free core of BaseCamp.

Everything that talks to a device, keeps its state, runs actions or stores
configuration lives here; a user interface only shows that state and asks the
core to change it. The rule that makes this work is checked by
tools/test_core_isolation.py: nothing under core/ imports a toolkit (tkinter,
customtkinter, tkinterdnd2, pystray) or one of the interface modules (gui,
devices/*/panel, shared/ui, shared/ui_helpers), not even indirectly.

How an interface uses the core:

    app = CoreApp()
    app.add_service(SomeService(app))
    app.bus.subscribe("displaypad.*", on_event)   # what happened
    app.call("displaypad.switch_page", page="Editor")   # ask for a change
    app.start()

Events arrive on whatever thread published them. A Tk interface hands them to
its main loop with after(), a web interface queues them for its socket.
"""

from core.app import CoreApp            # noqa: F401
from core.bus import EventBus           # noqa: F401
from core.commands import CommandError, CommandRegistry   # noqa: F401
from core.service import Service        # noqa: F401
