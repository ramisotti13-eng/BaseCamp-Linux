"""Running a device's controller CLI for a service, one call at a time.

The Everest 60 and the Makalu are driven by their controller CLIs
(devices/everest60/controller.py, devices/makalu67/controller.py, or the
everest60-controller / makalu-controller binaries of a frozen build), one
process per operation. Those CLIs are also what the control socket's `rgb`
and `run` commands reach, so they stay the backend: a service hands this
class an argument list and gets the result back on the core's scheduler.

What it adds over the panels' old `subprocess.run` in a daemon thread:

- one call at a time per device, in the order asked for, so two operations
  never open the same hidraw node at once;
- never on the scheduler thread (the Tk main loop under the CustomTkinter
  interface), and always with a timeout;
- the result comes back through `app.scheduler.call_soon`, so the service's
  own logic still runs one callback at a time.

How a command line is built is replaceable: under the CustomTkinter
interface the screen hands over `app._cmd_for_device`, so the application
starts exactly the process it always started; without one,
controller_command() builds the same line.
"""
import logging
import os
import queue
import subprocess
import sys
import threading
from collections import namedtuple

log = logging.getLogger("basecamp.core.controller")

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: device id -> (frozen binary name, script relative to the repository)
CONTROLLERS = {
    "everest60": ("everest60-controller", os.path.join("devices", "everest60", "controller.py")),
    "makalu67": ("makalu-controller", os.path.join("devices", "makalu67", "controller.py")),
}


def controller_command(device_id, *args):
    """The command line that runs `device_id`'s controller with `args`, the
    way gui.App._cmd_for_device builds it."""
    binary, script = CONTROLLERS[device_id]
    if getattr(sys, "frozen", False):
        return [os.path.join(os.path.dirname(sys.executable), binary)] + list(args)
    return [sys.executable, os.path.join(_REPO, script)] + list(args)


class Result(namedtuple("Result", "args returncode stdout stderr error")):
    """What a controller call came back with.

    `error` is set (and returncode None) when the process could not be run
    or did not finish in time; stdout and stderr are text.
    """

    @property
    def said_ok(self):
        """The CLIs' success: exit code 0 and "ok" as the whole output."""
        return self.error is None and self.returncode == 0 and self.stdout.strip() == "ok"

    @property
    def exited_ok(self):
        return self.error is None and self.returncode == 0


_STOP = object()


class ControllerBackend:
    def __init__(self, app, device_id, timeout=8.0, command=None, runner=None):
        self._app = app
        self.device_id = device_id
        self.timeout = timeout
        self._command = command
        # subprocess.run is looked up at call time unless a runner is given,
        # so a test can replace it.
        self._runner = runner
        self._jobs = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None
        self._busy = False
        self._closed = False

    # ── configuration ──────────────────────────────────────────────────────
    def set_command(self, builder):
        """Use `builder(*args)` to make command lines (None: the default)."""
        self._command = builder

    def command_for(self, args):
        if self._command is not None:
            return list(self._command(*args))
        return controller_command(self.device_id, *args)

    # ── calls ──────────────────────────────────────────────────────────────
    @property
    def busy(self):
        """A call is running or waiting."""
        return self._busy or not self._jobs.empty()

    def submit(self, args, on_done=None, timeout=None):
        """Queue one controller call. `on_done(result)` runs on the scheduler.

        Returns False (and drops the call) once the backend is closed.
        """
        args = [str(a) for a in args]
        with self._lock:
            if self._closed:
                return False
            self._jobs.put((args, on_done, timeout))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._work, name="%s-controller" % self.device_id,
                    daemon=True)
                self._thread.start()
        return True

    def run_now(self, args, timeout=None):
        """Run one call on the caller's thread and return its Result. For
        shutdown, when no scheduler is left to hand a result to."""
        return self._run([str(a) for a in args],
                         self.timeout if timeout is None else timeout)

    def close(self):
        """Drop waiting calls and let the worker end after the current one.
        The current call's result is still delivered."""
        with self._lock:
            self._closed = True
            self._drain()
            self._jobs.put(_STOP)

    def reopen(self):
        with self._lock:
            self._closed = False

    # ── worker ─────────────────────────────────────────────────────────────
    def _drain(self):
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                return

    def _work(self):
        while True:
            job = self._jobs.get()
            if job is _STOP:
                with self._lock:
                    if self._closed:
                        self._thread = None
                        return
                continue                # reopened since: carry on
            args, on_done, timeout = job
            self._busy = True
            try:
                result = self._run(args, self.timeout if timeout is None else timeout)
            finally:
                self._busy = False
            if on_done is not None:
                self._app.scheduler.call_soon(on_done, result)

    def _run(self, args, timeout):
        try:
            cmd = self.command_for(args)
            runner = self._runner or subprocess.run
            r = runner(cmd, capture_output=True, text=True, timeout=timeout)
            return Result(args, r.returncode, r.stdout or "", r.stderr or "", None)
        except Exception as e:
            log.info("%s controller %s failed: %s", self.device_id, args[:1], e)
            return Result(args, None, "", "", str(e))
