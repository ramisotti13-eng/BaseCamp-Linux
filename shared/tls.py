"""Finding the system's certificates for HTTPS.

The AppImage carries the OpenSSL of the machine it was built on, and OpenSSL
looks for trusted certificates where that machine's distribution keeps them.
The Fedora build looks under /etc/pki/tls and nowhere else; Arch, Debian,
Ubuntu and openSUSE keep their bundle somewhere else, so on those every HTTPS
request failed with CERTIFICATE_VERIFY_FAILED. The update check swallows its
errors, so the only thing anybody saw was an update that never showed up; the
plugin list could not load either.

ensure_ca_bundle() runs once at start. When the compiled-in locations hold
nothing, it points SSL_CERT_FILE at the first bundle the system has. OpenSSL
reads that variable each time a context is set up, so it holds for every
request made afterwards, plugins included. Programs the app starts do not get
it: clean_child_env() drops it for them.
"""
import os
import ssl

# Where distributions keep the bundle of trusted certificates, in the order Go
# and curl look for them.
CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                 # Debian, Ubuntu, Arch, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",                   # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",                             # openSUSE
    "/etc/pki/tls/cacert.pem",                            # OpenELEC
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora, RHEL
    "/etc/ssl/cert.pem",                                  # Alpine, Arch
    "/var/lib/ca-certificates/ca-bundle.pem",             # openSUSE
)


def _usable_file(p):
    try:
        return bool(p) and os.path.isfile(p) and os.path.getsize(p) > 0
    except OSError:
        return False


def _usable_dir(p):
    try:
        return bool(p) and os.path.isdir(p) and bool(os.listdir(p))
    except OSError:
        return False


def ensure_ca_bundle(environ=None, defaults=None, candidates=CA_BUNDLES):
    """Point SSL_CERT_FILE at the system's bundle when OpenSSL would find none.

    Returns the bundle chosen, or None when nothing had to be done or nothing
    was found. A SSL_CERT_FILE or SSL_CERT_DIR already set is left as it is.
    """
    env = os.environ if environ is None else environ
    if env.get("SSL_CERT_FILE") or env.get("SSL_CERT_DIR"):
        return None
    d = ssl.get_default_verify_paths() if defaults is None else defaults
    if _usable_file(d.openssl_cafile) or _usable_dir(d.openssl_capath):
        return None
    for p in candidates:
        if _usable_file(p):
            env["SSL_CERT_FILE"] = p
            return p
    return None
