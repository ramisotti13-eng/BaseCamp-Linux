"""The control socket: commands from outside the application.

``ControlService`` (service ``control``) hosts the Unix socket that scripts,
the tray helper and the Everest Max action daemon use to drive a running
BaseCamp (docs/CONTROL_INTERFACE.md). shared/ipc.py's ControlServer stays the
transport: one JSON line in, one JSON line out. Every line goes through the
core's command registry, so the socket reaches two kinds of command:

- the 3.x commands, registered as aliases under their old names, with
  exactly the replies they always gave: ``ping``, ``show``, ``list``,
  ``page``, ``rgb``, ``run``, ``image``, ``set_key``, ``dp_page``, plus
  ``quit``, which 3.x did not have;
- every service command under its dotted name (``displaypad.switch_page``,
  ``everest_max.status``, ``macros.toggle`` ...), the same ones an interface
  calls.

What only an interface can do is asked of it with an event, never done here:
``ui.show`` (bring the window up), ``ui.page {page}`` (show a screen),
``ui.quit``. An interface tells the service which screens it has with
attach_ui(); without one, ``list`` has no screens and ``page`` knows none.

The Everest Max's controller commands (``rgb``, ``run``, ``image`` for the
Everest Max) run through EverestMaxService.run_command(): with Monitor Mode
paused around them, so they no longer fail to claim the keyboard while it
runs, and an image upload gets the time it needs (45 to 60 seconds) instead of
being killed at 30. The Everest 60 and the Makalu are run as before, one
controller process each.

The handlers run on the socket's connection thread. They only read state,
publish events and hand work to the scheduler or to a service's own worker.
"""
import logging
import os
import subprocess

from core.service import Service

log = logging.getLogger("basecamp.core.control")

APP_NAME = "BaseCamp Linux"

#: Seconds a controller command may take (3.x: 30 for everything).
COMMAND_TIMEOUT = 30
#: An upload erases and rewrites flash: 45 to 60 seconds on an Everest Max.
UPLOAD_TIMEOUT = 180
#: Verbs of the Everest Max controller that upload a picture.
UPLOAD_VERBS = ("upload", "upload-main")
#: What the controller is given to settle before an upload claims the
#: keyboard: 2.5 s right after Monitor Mode let go of it, else 0.5 s (the
#: manual D-key upload's waits).
UPLOAD_SETTLE = (2.5, 0.5)

#: Devices with a controller of their own; any other id is the Everest Max's.
OWN_CONTROLLERS = ("everest60", "makalu67")


def default_command(device_id, *args):
    from core import runtime
    return runtime.controller_cmd(device_id, *args)


def run_controller(cmdline, timeout=COMMAND_TIMEOUT):
    """Run a controller command line and say how it went, as the socket
    replies: {ok, code, stdout, stderr}, or {ok: false, error}."""
    from core.platform.launcher import clean_child_env
    try:
        r = subprocess.run(cmdline, capture_output=True, timeout=timeout,
                           env=clean_child_env())
        return {"ok": r.returncode == 0, "code": r.returncode,
                "stdout": r.stdout.decode("utf-8", "replace").strip(),
                "stderr": r.stderr.decode("utf-8", "replace").strip()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class ControlService(Service):
    name = "control"

    def __init__(self, app, version="", path=None, command=None, listen=True,
                 screens=None, active=None):
        """
        version   what `ping` says
        path      the socket (default: shared.ipc.socket_path())
        command   (device_id, *args) -> argv of a device's controller, for the
                  devices not run through a service (Everest 60, Makalu)
        listen    open the socket on start()
        screens   () -> the ids of the interface's screens (see attach_ui)
        active    () -> the id of the screen on show, or None
        """
        super().__init__(app)
        self.version = version
        self._path = path
        self._command = command or default_command
        self._listen = listen
        self._screens = screens or (lambda: ())
        self._active = active or (lambda: None)
        self.server = None

    # ── Wiring ─────────────────────────────────────────────────────────────
    def attach_ui(self, screens=None, active=None):
        """An interface says which screens it has (`page` and `list` name
        them) and which one is on show."""
        if screens is not None:
            self._screens = screens
        if active is not None:
            self._active = active

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def start(self):
        if self._listen:
            self.start_server()

    def stop(self):
        self.stop_server()

    def start_server(self):
        """Open the socket. False when it cannot be (another instance holds
        it, no AF_UNIX here); the application runs on without it."""
        if self.server is not None:
            return True
        try:
            from shared.ipc import ControlServer
            server = ControlServer(self.handle, path=self._path)
        except Exception as e:
            print(f"[Control] unavailable: {e}")
            return False
        if not server.start():
            return False
        self.server = server
        return True

    def stop_server(self):
        server, self.server = self.server, None
        if server is not None:
            server.stop()

    @property
    def path(self):
        return self.server._path if self.server is not None else self._path

    # ── The socket's entry point ───────────────────────────────────────────
    def handle(self, obj):
        """One command object from the socket -> its reply object."""
        if not isinstance(obj, dict):
            return {"ok": False, "error": "bad request: a command must be a JSON object"}
        params = dict(obj)
        cmd = params.pop("cmd", None)
        name = (cmd if isinstance(cmd, str) else str(cmd or "")).lower()
        if not self.app.commands.has(name):
            return {"ok": False, "error": f"unknown cmd '{name}'"}
        params["cmd"] = name
        return self.app.commands.dispatch(params)

    def commands(self):
        # `ping` under the service's name too: the application and its
        # version, for an interface's about line (control.ping).
        return {"ping": self.cmd_ping}

    # ── The 3.x commands ───────────────────────────────────────────────────
    def aliases(self):
        return {"ping": self.cmd_ping, "show": self.cmd_show, "list": self.cmd_list,
                "page": self.cmd_page, "rgb": self.cmd_rgb, "run": self.cmd_run,
                "image": self.cmd_image, "set_key": self.cmd_set_key,
                "modules": self.cmd_modules,
                "dp_page": self.cmd_dp_page, "quit": self.cmd_quit}

    # Each takes the command object's fields as keywords and ignores the ones
    # it does not use, as the 3.x handler did.

    def cmd_ping(self, **_obj):
        return {"ok": True, "app": APP_NAME, "version": self.version}

    def cmd_show(self, **_obj):
        """Bring the window to the front, also out of the tray. The second
        start of the application sends it to the first."""
        self.bus.publish("ui.show")
        return {"ok": True, "shown": True}

    def cmd_quit(self, **_obj):
        """Quit the application, as its Quit entry does."""
        self.bus.publish("ui.quit")
        return {"ok": True}

    def _screen_ids(self):
        try:
            return set(self._screens() or ())
        except Exception:
            log.exception("listing the interface's screens failed")
            return set()

    def cmd_list(self, **_obj):
        # Every screen that exists, built or not: a script asking what is
        # there should not get a different answer depending on what the
        # person happened to click.
        reg = self.app.service("devices")
        return {"ok": True, "pages": sorted(self._screen_ids()),
                "active": self._active(),
                "present": dict(reg.present) if reg is not None else {},
                "displaypad": self._dp_state()}

    def cmd_page(self, **obj):
        page = obj.get("page", "")
        if page not in self._screen_ids():
            return {"ok": False, "error": f"unknown page '{page}'"}
        self.bus.publish("ui.page", page=page)
        return {"ok": True}

    def _keyboard_id(self):
        reg = self.app.service("devices")
        return getattr(reg, "keyboard_id", None) or "everest_max"

    def cmd_rgb(self, **obj):
        device = obj.get("device") or self._keyboard_id()
        args = [str(a) for a in obj.get("args") or []]
        if not args:
            return {"ok": False, "error": "rgb: 'args' required"}
        return self.run_device(device, ["rgb"] + args)

    def cmd_run(self, **obj):
        """Any verb of a device's controller."""
        device = obj.get("device") or self._keyboard_id()
        args = [str(a) for a in obj.get("args") or []]
        if not args:
            return {"ok": False, "error": "run: 'args' required"}
        return self.run_device(device, args)

    def cmd_image(self, **obj):
        device = obj.get("device", "everest_max")
        button = int(obj.get("button", 0))
        path = obj.get("path", "")
        if not os.path.isfile(path):
            return {"ok": False, "error": f"no such file: {path}"}
        return self.run_device(device, ["upload", str(button), path])

    def run_device(self, device, args):
        """Run a controller verb for `device` and return the socket's reply.

        The Everest Max's go through its service, which pauses Monitor Mode
        for them (it holds the keyboard) and runs them on its worker, one
        owner of the keyboard at a time. Uploads get the time they need.
        """
        upload = bool(args) and args[0] in UPLOAD_VERBS
        timeout = UPLOAD_TIMEOUT if upload else COMMAND_TIMEOUT
        svc = self.app.service("everest_max")
        if device in OWN_CONTROLLERS or svc is None:
            return run_controller(self._command(device, *args), timeout=timeout)
        from core.platform.launcher import clean_child_env
        return svc.run_command(list(args), timeout=timeout, env=clean_child_env(),
                               settle=UPLOAD_SETTLE if upload else None)

    def cmd_set_key(self, **obj):
        """Redefine an Everest Max D-key (#18). Monitor Mode reads the saved
        actions every two seconds, so saving is enough to take effect; the
        service tells any screen showing them."""
        try:
            button = int(obj.get("button", -1))
        except (TypeError, ValueError):
            button = -1
        if not 0 <= button <= 3:
            return {"ok": False, "error": "set_key: 'button' must be 0..3 (D1..D4)"}
        btype = obj.get("type", "shell")
        action = obj.get("action", "")
        svc = self.app.service("everest_max")
        if svc is not None:
            svc.set_button(button, type=btype, action=action, strip=False)
        else:
            from shared.config import load_buttons, save_buttons
            buttons = load_buttons()
            while len(buttons) <= button:
                buttons.append({"icon": 7, "action": "", "type": "shell"})
            buttons[button]["type"] = btype
            buttons[button]["action"] = action
            save_buttons(buttons)
        return {"ok": True}

    def cmd_modules(self, **obj):
        """What the Everest Max's keyboard says is attached (numpad, media dock):
        Monitor Mode reads it from the answer to its keepalive and reports it
        here, over the same socket as `page` and `set_key`."""
        svc = self.app.service("everest_max")
        if svc is None:
            return {"ok": False, "error": "modules: the Everest Max service is not running"}
        svc.set_modules(bool(obj.get("numpad")), bool(obj.get("dock")))
        return {"ok": True}

    def _dp_state(self):
        """DisplayPad key pages for the `list` reply: {id: name} plus the page
        the pad is on right now, so a script can discover the names before it
        sends `dp_page`. Read-only and best effort, `list` has to keep working
        without a DisplayPad and must not create or rename anything."""
        svc = self.app.service("displaypad")
        if svc is None:
            return {}
        try:
            from shared.config import _load_displaypad_page_names
            return {"pages": {str(pid): name for pid, name
                              in _load_displaypad_page_names().items()},
                    "current": svc.model.current_page}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def cmd_dp_page(self, **obj):
        """Switch the DisplayPad's key page from outside the application.

        `page` only switches the interface's screen; this puts the pad itself
        on a page (e.g. an editor wrapper that flips to a page of snippets on
        launch). Targets are given the way a 'page' key gives them: by name,
        with a page id and "prev" accepted as well.
        """
        svc = self.app.service("displaypad")
        if svc is None:
            return {"ok": False, "error": "dp_page: no DisplayPad panel"}
        model = svc.model
        want = obj.get("page")
        if isinstance(want, str):
            want = want.strip()
        if want is None or want == "":
            return {"ok": False, "error": "dp_page: 'page' required"}
        if isinstance(want, bool):  # JSON true/false: int(True) would mean page 1
            return {"ok": False, "error": f"dp_page: bad page '{want}'"}

        try:
            from shared.config import _load_displaypad_page_names
            known = _load_displaypad_page_names()
        except Exception as e:
            return {"ok": False, "error": f"dp_page: {type(e).__name__}: {e}"}

        if isinstance(want, str):
            # A real page name always wins, over the "prev" keyword as well as
            # over an id, so a page the user called "prev" or "3" stays
            # reachable by the name shown in the picker. Resolved the way a
            # 'page' key action resolves it, so both agree on what a name means.
            target = model.page_id_by_name(want)
            if target is None and want.lower() == "prev":
                target = model.prev_page
            elif target is None:
                try:
                    target = int(want)
                except ValueError:
                    return {"ok": False,
                            "error": f"dp_page: no page named '{want}'",
                            "pages": sorted(known.values())}
        else:
            try:
                target = int(want)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"dp_page: bad page '{want}'"}

        if target not in known:
            return {"ok": False, "error": f"dp_page: no page with id {target}",
                    "pages": sorted(known.values())}
        if target == model.current_page:
            # Not an error: a script that flips to a page on every window focus
            # would otherwise report a failure for every repeat activation.
            return {"ok": True, "page": target, "name": known[target],
                    "changed": False}
        # The switch runs on the scheduler, like every DisplayPad change, and
        # may wait there for a running upload or animation: "accepted", not
        # "done". A script or the tray switching pages is somebody switching
        # pages, so the page's box comes up (#93); "hints": false leaves it down.
        hints = obj.get("hints", True) is not False
        self.app.scheduler.call_soon(svc.switch_page, target, hints)
        return {"ok": True, "page": target, "name": known[target], "changed": True}
