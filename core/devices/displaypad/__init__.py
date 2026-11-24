"""Mountain DisplayPad: twelve keys with a screen each, in pages.

protocol   the wire: finding the pad, opening it, writing a key, key events
images     pictures: BGR conversion, rotation, GIF frames, generated icons
pages      PageModel: what each key of each page does and shows
actions    the action catalogue, building an action, ActionRunner
service    DisplayPadService: the pad, its workers, its events and commands
"""


def install(app):
    from core.devices.displaypad.service import DisplayPadService
    app.add_service(DisplayPadService(app))
