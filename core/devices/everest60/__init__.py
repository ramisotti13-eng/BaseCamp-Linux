"""Everest 60 / Everest 60 ISO keyboard: protocol, HID access, service.

install() imports the service only when called, so the controller CLI (and
the everest60-controller binary), which imports protocol and device from
here, does not pull in the service machinery.
"""


def install(app):
    from core.devices.everest60.service import Everest60Service
    app.add_service(Everest60Service(app))
