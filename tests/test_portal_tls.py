"""Public Contracts Scotland sends no intermediate certificate, so requests failed verification and almost no Scottish awards were stored.
The shipped intermediate + certifi roots form the bundle requests verifies against.
Run: python -m pytest tests/test_portal_tls.py
"""
import os
import ssl

from etenders_scraper import tls


def test_bundle_holds_the_roots_and_the_shipped_intermediate():
    path = tls.ca_bundle_path()
    text = open(path, encoding="utf8").read()
    assert text.count("BEGIN CERTIFICATE") > 100, "certifi's roots are kept"
    shipped = (tls.CERTS_DIR / "sectigo_dv_r36_intermediate.pem").read_text(encoding="ascii").strip()
    assert shipped in text
    ctx = ssl.create_default_context(cafile=path)  # every certificate in it parses
    assert ctx.cert_store_stats()["x509_ca"] > 100


def test_install_respects_an_existing_setting(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/somewhere/else.pem")
    tls.install()
    assert os.environ["REQUESTS_CA_BUNDLE"] == "/somewhere/else.pem"
    monkeypatch.delenv("REQUESTS_CA_BUNDLE")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    tls.install()
    assert os.environ["REQUESTS_CA_BUNDLE"] == tls.ca_bundle_path()
