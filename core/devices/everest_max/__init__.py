"""Everest Max: wire protocol, key layout and EverestMaxService.

protocol.py and layout.py are pure data and functions; emax_controller.py
builds its packets with them, so importing this package must stay cheap: the
service is imported only when install() runs.
"""


def install(app):
    from core.devices.everest_max.service import EverestMaxService
    app.add_service(EverestMaxService(app))
