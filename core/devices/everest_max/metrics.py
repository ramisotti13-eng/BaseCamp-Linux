"""The numbers Monitor Mode sends to the keyboard, for an interface to show.

The monitor card shows what the keyboard is being sent, from the same psutil
the controller uses, plus the system volume the controller pushes to the
wheel display. Sampled on request (the interface asks every two seconds while
the card is on screen), so nothing polls for a screen nobody looks at.

RAM, disk and the network rate come from core/metrics.py, which the
DisplayPad's PC-info keys read too.
"""
from core.metrics import NetRate, disk_percent, ram_percent


class MeterSampler:
    def __init__(self):
        self._net = NetRate()

    def sample(self):
        """{cpu, ram, disk, net_mbs, volume}; volume is None when unknown."""
        import psutil
        from shared.volume import system_volume
        cpu = psutil.cpu_percent(interval=None)
        ram = ram_percent(psutil)
        disk = disk_percent("/", psutil)
        mbs = self._net.read(psutil)
        # The watcher started by watch_volume() makes this a variable read;
        # without it this would run a mixer command on every sample.
        vol = system_volume()
        return {"cpu": cpu, "ram": ram, "disk": disk, "net_mbs": mbs,
                "volume": vol}


def watch_volume():
    """Follow the mixer, so sample() reads a variable instead of a command."""
    from shared.volume import start_watch
    start_watch()
