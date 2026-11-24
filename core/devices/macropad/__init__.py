"""The Mountain MacroPad in the core: MacroPadService (see service.py).

The protocol itself (packet builders, key report decoding, the HID wrapper)
is devices/macropad/controller.py, which has no interface code in it.
"""


def install(app):
    from core.devices.macropad.service import MacroPadService
    app.add_service(MacroPadService(app))
