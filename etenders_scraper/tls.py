"""CA bundle for portals that do not send their full certificate chain.

Public Contracts Scotland sends only its own certificate, without the Sectigo intermediate that signed it. Browsers fetch the
missing intermediate on their own; Python's requests does not, so every request to the portal failed with
CERTIFICATE_VERIFY_FAILED and only a handful of Scottish awards were ever stored. The intermediate is a public certificate, so
it is shipped in certs/ and added to the usual trusted roots (certifi). Verification stays on, with a bundle that can finish the chain.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

CERTS_DIR = Path(__file__).resolve().parent / "certs"


def ca_bundle_path() -> str:
    """Path of certifi's roots plus the shipped intermediates, written once per process to the temp folder."""
    import certifi

    extra = [p.read_text(encoding="ascii") for p in sorted(CERTS_DIR.glob("*.pem"))]
    out = Path(tempfile.gettempdir()) / "tenderflow_ca_bundle.pem"
    wanted = Path(certifi.where()).read_text(encoding="utf8") + "\n" + "\n".join(extra)
    if not out.exists() or out.read_text(encoding="utf8") != wanted:
        out.write_text(wanted, encoding="utf8")
    return str(out)


def install() -> None:
    """Make requests use that bundle unless the deployment already set its own."""
    if not os.environ.get("REQUESTS_CA_BUNDLE") and not os.environ.get("SSL_CERT_FILE"):
        try:
            os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle_path()
        except OSError:  # a read-only temp folder: the default roots are used, as before
            pass
