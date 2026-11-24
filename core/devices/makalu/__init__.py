"""Makalu 67 / Makalu Max mouse: protocol, HID access, service.

install() imports the service only when called, so the controller CLI (and
the makalu-controller binary), which imports protocol and device from here,
does not pull in the service machinery.
"""


def install(app):
    from core.devices.makalu.service import MakaluService
    app.add_service(MakaluService(app))
