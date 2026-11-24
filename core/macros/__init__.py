"""Macros: what they are (model), how they play (engine), mouse recordings,
and the MacroService that edits and runs them.

A macro is a list of steps (keys, text, mouse, delays, launches) saved in
macros.json under an id. Keys on the MacroPad, the DisplayPad and the
Everest Max run it by that id.

install(app) is called first by core.devices.install_all(), so device
services can find the "macros" service, and the shared key action
dispatcher on it (service.actions, see core/actions.py), when they are
added.
"""


def install(app):
    from core.actions import ActionDispatcher
    from core.macros.service import MacroService
    service = MacroService(app)
    service.actions = ActionDispatcher(macros=service)
    app.add_service(service)
