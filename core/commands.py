"""Command registry: the one way to ask the core for a change.

Every service registers its operations under dotted names
(``displaypad.switch_page``, ``everest_max.set_rgb``). The CustomTkinter
interface, a web interface, the control socket and plugins all go through the
same names, so an operation exists once and behaves the same whoever asks.

``dispatch()`` speaks the control socket's existing wire format, so the socket
and a web socket can hand their JSON straight to it:

    {"cmd": "displaypad.switch_page", "page": "Editor"}
        -> {"ok": true, ...whatever the command returned...}
        -> {"ok": false, "error": "..."}
"""
import inspect
import logging
import threading

log = logging.getLogger("basecamp.core.commands")


class CommandError(Exception):
    """A command was refused: unknown name, bad arguments, device absent.

    The message is meant for the user (or the script) that asked, so it
    should say what was wrong in plain words.

    An interface that translates can also be given ``code`` (a short machine
    name such as "is_link") and ``params`` (what a sentence for it needs);
    dispatch() adds both to the reply when they are set.
    """

    def __init__(self, message="", code=None, **params):
        super().__init__(message)
        self.code = code
        self.params = params


class CommandRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._commands = {}

    def register(self, name, fn, replace=False):
        """Make `fn(**params)` callable as `name`."""
        if not callable(fn):
            raise TypeError("command %r is not callable" % name)
        with self._lock:
            if name in self._commands and not replace:
                raise ValueError("command %r is already registered" % name)
            self._commands[name] = fn

    def unregister(self, name):
        with self._lock:
            self._commands.pop(name, None)

    def has(self, name):
        with self._lock:
            return name in self._commands

    def names(self, prefix=""):
        with self._lock:
            return sorted(n for n in self._commands if n.startswith(prefix))

    def call(self, command, /, **params):
        """Run a command and return what it returned.

        Raises CommandError for an unknown name or arguments the command does
        not take; anything the command raises itself passes through. The
        command name is positional-only, so a command may itself take a
        parameter called `name` or `command`.
        """
        with self._lock:
            fn = self._commands.get(command)
        if fn is None:
            raise CommandError("unknown command: %s" % command)
        try:
            inspect.signature(fn).bind(**params)
        except TypeError as e:
            raise CommandError("%s: %s" % (command, e)) from None
        except ValueError:
            pass            # builtins without a signature: let the call decide
        return fn(**params)

    def dispatch(self, obj):
        """Run a command given as a wire object, return the wire reply.

        Never raises: every failure becomes ``{"ok": False, "error": ...}``, so
        a socket server can send the result back without a try of its own.
        """
        if not isinstance(obj, dict):
            return {"ok": False, "error": "a command must be a JSON object"}
        params = dict(obj)
        name = params.pop("cmd", None)
        if not isinstance(name, str) or not name:
            return {"ok": False, "error": "missing \"cmd\""}
        try:
            result = self.call(name, **params)
        except CommandError as e:
            reply = {"ok": False, "error": str(e)}
            if getattr(e, "code", None):
                reply["code"] = e.code
                if e.params:
                    reply["params"] = e.params
            return reply
        except Exception as e:
            log.exception("command %s failed", name)
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if result is None:
            return {"ok": True}
        if isinstance(result, dict):
            reply = {"ok": True}
            reply.update(result)
            return reply
        return {"ok": True, "result": result}
