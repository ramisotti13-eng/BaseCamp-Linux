#!/usr/bin/env python3
"""
Checks that HTTPS finds the system's certificates when the bundled OpenSSL
looks for them in the wrong place.

    python3 tools/test_ca_bundle.py

The AppImage carries the OpenSSL of the machine it was built on. Built on
Fedora, it looks for certificates under /etc/pki/tls only; on Arch, Debian or
openSUSE nothing is there, every HTTPS request fails with
CERTIFICATE_VERIFY_FAILED, and the update check, which swallows its errors,
just never says there is an update.

The first part runs against made-up paths. The second hides the compiled-in
certificate locations in a mount namespace of its own (unshare, no root
needed) and checks that a running process picks up the bundle it is pointed
at afterwards. It is skipped where unshare is not allowed.
"""
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import tls                                  # noqa: E402
from shared.macros import clean_child_env               # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + "%-62s %s" % (name, detail), flush=True)
    if not ok:
        failures.append(name)


tmp = tempfile.mkdtemp(prefix="basecamp-ca-test-")


def path(*parts):
    return os.path.join(tmp, *parts)


def write(p, text="-----BEGIN CERTIFICATE-----\n"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    return p


def defaults(cafile, capath):
    return SimpleNamespace(openssl_cafile=cafile, openssl_capath=capath)


fedora_file = path("fedora", "pki", "tls", "cert.pem")
fedora_dir = path("fedora", "pki", "tls", "certs")
arch_bundle = write(path("arch", "ssl", "certs", "ca-certificates.crt"))
os.makedirs(path("empty_dir"))
empty_bundle = write(path("empty", "bundle.crt"), "")

# Fedora: the compiled-in directory is there, nothing to do.
write(os.path.join(fedora_dir, "002c0b4f.0"))
env = {}
got = tls.ensure_ca_bundle(env, defaults(fedora_file, fedora_dir), (arch_bundle,))
check("compiled-in directory with certificates: left alone",
      got is None and "SSL_CERT_FILE" not in env, env)

env = {}
got = tls.ensure_ca_bundle(env, defaults(write(path("f2", "cert.pem")), path("nope")),
                           (arch_bundle,))
check("compiled-in file with certificates: left alone",
      got is None and "SSL_CERT_FILE" not in env, env)

# Arch: neither compiled-in location exists.
env = {}
got = tls.ensure_ca_bundle(env, defaults(path("x", "cert.pem"), path("x", "certs")),
                           (path("missing.crt"), arch_bundle))
check("nothing where OpenSSL looks: the system's bundle is used",
      got == arch_bundle and env.get("SSL_CERT_FILE") == arch_bundle, env)

env = {}
got = tls.ensure_ca_bundle(env, defaults(path("x", "cert.pem"), path("empty_dir")),
                           (arch_bundle,))
check("an empty certificate directory counts as nothing",
      env.get("SSL_CERT_FILE") == arch_bundle, env)

env = {}
got = tls.ensure_ca_bundle(env, defaults(path("x", "cert.pem"), path("x", "certs")),
                           (empty_bundle, arch_bundle))
check("an empty bundle file is passed over", env.get("SSL_CERT_FILE") == arch_bundle, env)

env = {}
got = tls.ensure_ca_bundle(env, defaults(path("x", "cert.pem"), path("x", "certs")),
                           (path("missing.crt"),))
check("no bundle anywhere: nothing is set", got is None and env == {}, env)

# What the user set stays.
env = {"SSL_CERT_FILE": "/my/own.pem"}
tls.ensure_ca_bundle(env, defaults(path("x", "cert.pem"), path("x", "certs")),
                     (arch_bundle,))
check("a SSL_CERT_FILE of the user's own is kept", env == {"SSL_CERT_FILE": "/my/own.pem"},
      env)
env = {"SSL_CERT_DIR": "/my/certs"}
tls.ensure_ca_bundle(env, defaults(path("x", "cert.pem"), path("x", "certs")),
                     (arch_bundle,))
check("and so is a SSL_CERT_DIR", env == {"SSL_CERT_DIR": "/my/certs"}, env)

# The real list covers the families people run the AppImage on.
for where in ("/etc/ssl/certs/ca-certificates.crt",        # Arch, Debian, Ubuntu
              "/etc/pki/tls/certs/ca-bundle.crt",          # Fedora, RHEL
              "/etc/ssl/ca-bundle.pem"):                   # openSUSE
    check("the list knows %s" % where, where in tls.CA_BUNDLES)

# A program the app starts gets the environment it would get from the desktop.
child = clean_child_env({"APPIMAGE": "/usr/bin/basecamp-linux", "PATH": "/usr/bin",
                         "SSL_CERT_FILE": arch_bundle})
check("the bundle set for the app does not reach programs it starts",
      "SSL_CERT_FILE" not in child, child)


# ── In a process, with the compiled-in locations really gone ─────────────────

def namespace_run(script, hide):
    """Run `script` in a python of its own, with the directories in `hide`
    covered by an empty tmpfs and the bundle staged where Arch keeps it."""
    staged = os.path.join(tmp, "ns_bundle.crt")
    mounts = "".join("mount -t tmpfs none %s && " % d for d in hide)
    sh = (mounts + "mkdir -p /etc/ssl/certs && cp %s /etc/ssl/certs/ca-certificates.crt && "
          "exec %s -c %s" % (staged, sys.executable, _quote(script)))
    return subprocess.run(["unshare", "-r", "-m", "sh", "-c", sh],
                          capture_output=True, text=True, timeout=30,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _quote(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def system_bundle():
    paths = ssl.get_default_verify_paths()
    for p in (paths.cafile,) + tls.CA_BUNDLES:
        if p and os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None


src = system_bundle()
if not shutil.which("unshare") or not src:
    print("skipping the namespace part: no unshare or no system bundle found")
else:
    shutil.copyfile(src, os.path.join(tmp, "ns_bundle.crt"))
    d = ssl.get_default_verify_paths()
    hide = sorted({os.path.dirname(d.openssl_cafile), d.openssl_capath, "/etc/ssl"})
    # A directory inside one already covered is gone after the first mount.
    hide = [h for h in hide if os.path.isdir(h)
            and not any(h.startswith(o + "/") for o in hide)]
    probe = ("import ssl, sys\n"
             "def cas(): return ssl.create_default_context().cert_store_stats()['x509_ca']\n"
             "before = cas()\n"
             "sys.path.insert(0, '.')\n"
             "from shared import tls\n"
             "tls.ensure_ca_bundle()\n"
             "print(before, cas())\n")
    r = namespace_run(probe, hide)
    if r.returncode != 0:
        print("skipping the namespace part: %s" % (r.stderr.strip().splitlines() or ["?"])[-1])
    else:
        before, after = (int(x) for x in r.stdout.split())
        if before:
            print("skipping the namespace part: OpenSSL here finds the bundle on its own")
        else:
            check("with the compiled-in locations gone, no certificates load", before == 0,
                  before)
            check("once pointed at the system's bundle, a running process has them",
                  after > 0, after)

shutil.rmtree(tmp, ignore_errors=True)
print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all checks passed")
