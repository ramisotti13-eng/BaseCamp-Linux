"""Remote access: phones in the LAN press the DisplayPad and the virtual pads.

- pairing.py: one-time codes, device tokens (stored only as their SHA-256);
- policy.py: what a paired phone may call, shell commands and presses while
  the session is locked, redaction of shell commands in what it is sent;
- service.py: the `remote` service, its settings and the methods the HTTP
  server (ui_web/remote_server.py) asks for every decision.
"""
