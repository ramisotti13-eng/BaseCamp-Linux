"""The controller command line as the Everest Max service's backend.

The service does not open the keyboard itself in this step: every operation
runs emax_controller.py (the frozen `basecamp-controller` in a packaged
build), exactly as the panel did. That keeps the frozen binaries, the source
overlay and the control socket's `run` verb working, and keeps the pyusb
claim, which is what makes "Monitor Mode or an upload, never both" fail fast
at the OS level, until an in-process transport has been checked on hardware.

The backend only runs processes. Who may run one when is the service's job.
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def default_argv(*args):
    """The controller's command line, as gui._cmd() builds it."""
    if getattr(sys, "frozen", False):
        return [os.path.join(os.path.dirname(sys.executable),
                             "basecamp-controller")] + list(args)
    return [sys.executable, os.path.join(_ROOT, "emax_controller.py")] + list(args)


def _last_line(text):
    return ((text or "").strip().splitlines() or [""])[-1]


class Result:
    """What a controller run came back with."""

    def __init__(self, code, stdout="", stderr="", error=""):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self._error = error

    @property
    def ok(self):
        return self.code == 0

    @property
    def error(self):
        """The line a user is shown: the last line on stderr, or what failed."""
        return self._error or _last_line(self.stderr)

    def as_dict(self):
        """The control socket's reply for a controller run."""
        if self._error:             # it did not run, or did not finish
            return {"ok": False, "error": self._error}
        return {"ok": self.ok, "code": self.code, "stdout": self.stdout.strip(),
                "stderr": self.stderr.strip()}


class ControllerBackend:
    """Runs one controller command at a time for the service."""

    def __init__(self, argv=None):
        self.argv = argv or default_argv

    def run(self, *args, timeout=None, env=None):
        """Run a command to the end. Never raises; a failure is a Result."""
        try:
            r = subprocess.run(self.argv(*args), capture_output=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return Result(None, error="timed out after %ss" % timeout)
        except Exception as e:
            return Result(None, error=str(e))
        return Result(r.returncode,
                      r.stdout.decode("utf-8", errors="replace"),
                      r.stderr.decode("utf-8", errors="replace"))

    def stream(self, *args, on_progress=None, echo=False):
        """Run an upload, calling on_progress(pct) for each PROGRESS:n line.

        stderr is merged into stdout so a single pipe is drained (a full
        stderr pipe would block the controller while we wait on stdout). The
        error line is the last line that was not progress.
        """
        try:
            proc = subprocess.Popen(self.argv(*args), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            return Result(None, error=str(e))
        last = ""
        for line in proc.stdout:
            if line.startswith("PROGRESS:"):
                try:
                    pct = int(line.strip()[9:])
                except ValueError:
                    continue
                if on_progress:
                    on_progress(pct)
            else:
                stripped = line.rstrip()
                if stripped:
                    last = stripped
                if echo:
                    print(line, end="", flush=True)
        proc.wait()
        return Result(proc.returncode, error="" if proc.returncode == 0 else last)

    def spawn_monitor(self, style, log_path):
        """Start Monitor Mode (`cpu <style>`); stderr goes to `log_path`."""
        log = open(log_path, "w")
        try:
            return subprocess.Popen(self.argv("cpu", style),
                                    stdout=subprocess.DEVNULL, stderr=log)
        finally:
            log.close()

    def kill_strays(self):
        """End Monitor Mode processes this application no longer knows about
        (left by a crash or a second instance): they hold the keyboard."""
        pattern = ("basecamp-controller.*cpu" if getattr(sys, "frozen", False)
                   else r"emax_controller\.py.*cpu")
        try:
            subprocess.run(["pkill", "-f", pattern], capture_output=True)
        except Exception:
            pass            # no pkill (Windows): nothing to clean up that way


def wait_for_exit(proc, timeout=8):
    """Wait for a stopped controller, but never for ever.

    The controller releases the keyboard on its way out and ends itself after
    five seconds if that cleanup does not come back; this is the backstop for
    that backstop. Returns the exit code, or None.
    """
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("[Everest] controller did not stop in %ds, killing it" % timeout,
              file=sys.stderr, flush=True)
        try:
            proc.kill()
            return proc.wait(timeout=2)
        except Exception:
            return None
    except Exception:
        return None
