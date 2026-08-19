"""The deployment's TLS certificate, served so operators can pin it.

Node-based MCP clients ignore the OS trust store and refuse a self-signed
deployment outright (verified against a real client). The fix we point people
at is NODE_EXTRA_CA_CERTS, which needs the certificate as a file — so the API
hands it over rather than making the operator find it on the host.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_serves_the_certificate_as_pem(client, tmp_path):
    pem = tmp_path / "networkmapper.crt"
    pem.write_text("-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n")

    with patch("app.api.v1.endpoints.references._TLS_CERT_PATH", pem):
        resp = client.get("/api/v1/references/tls-certificate")

    assert resp.status_code == 200
    assert resp.text.startswith("-----BEGIN CERTIFICATE-----")
    assert "attachment" in resp.headers["content-disposition"]


def test_explains_itself_when_no_certificate_is_mounted(client):
    """Deployments that terminate TLS elsewhere have nothing to serve — say so
    rather than 500ing or returning an empty file the operator would then try
    to pin."""
    with patch("app.api.v1.endpoints.references._TLS_CERT_PATH", Path("/nonexistent/x.crt")):
        resp = client.get("/api/v1/references/tls-certificate")

    assert resp.status_code == 404
    assert "terminate TLS elsewhere" in resp.json()["detail"]


def test_listed_in_the_references_index(client):
    body = client.get("/api/v1/references/").json()
    assert "tls_certificate" in body
    assert body["tls_certificate"]["url"] == "/api/v1/references/tls-certificate"


# ---------------------------------------------------------------------------
# Serving the trust script (v2.286.0)
#
# The pinning variable differs per client and the operator running the client
# usually has no checkout of this repo, so "run ./scripts/trust-cert.sh" was
# advice only reachable on the deployment host. Serving it turns six
# translate-them-yourself steps into one command.
# ---------------------------------------------------------------------------

def test_trust_script_is_served_as_a_downloadable_script(client):
    resp = client.get("/api/v1/references/trust-cert-script")
    assert resp.status_code == 200, resp.text
    assert "x-shellscript" in resp.headers["content-type"]
    # Downloaded, not piped — an operator should read a script that installs a
    # trust anchor before running it, and the filename makes that natural.
    assert 'filename="trust-cert.sh"' in resp.headers.get("content-disposition", "")

    body = resp.text
    assert body.startswith("#!")
    # It has to carry BOTH mechanisms, since that is the whole reason a script
    # beats instructions: the two clients need different variables.
    assert "NODE_EXTRA_CA_CERTS" in body
    assert "SSL_CERT_DIR" in body
    # And it must be able to run away from the repo, against a remote host.
    assert "--url" in body


def test_catalog_carries_the_trust_script_and_fingerprint(client):
    """The page needs both exactly when it needs the tool list, and a
    fingerprint an operator has to go and find is one nobody checks."""
    body = client.get("/api/v1/references/mcp-tools").json()
    assert body["trust_script_url"].endswith("/references/trust-cert-script")
    assert body["tls_certificate_url"].endswith("/references/tls-certificate")
    # None when the cert isn't mounted (the test container) — the field must
    # exist either way so the page can decide what to render.
    assert "tls_fingerprint_sha256" in body


def test_fingerprint_matches_the_served_certificate(tmp_path, monkeypatch):
    """A fingerprint that doesn't match the PEM would be worse than none: an
    operator comparing them would 'verify' a certificate that isn't this one."""
    import hashlib
    import ssl

    from app.api.v1.endpoints import references

    pem = (tmp_path / "cert.pem")
    # A real self-signed cert, generated here rather than mocked — the point of
    # the check is the DER conversion, which a fake string would skip.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(references, "_TLS_CERT_PATH", pem)

    expected = hashlib.sha256(
        ssl.PEM_cert_to_DER_cert(pem.read_text())
    ).hexdigest().upper()
    got = references.tls_certificate_fingerprint()
    assert got is not None
    assert got.replace(":", "") == expected


def test_missing_certificate_reports_no_fingerprint(tmp_path, monkeypatch):
    """Absent is a real state (the cert isn't mounted in every container), and
    it must read as 'unknown' rather than crashing the catalog the page needs."""
    from app.api.v1.endpoints import references

    monkeypatch.setattr(references, "_TLS_CERT_PATH", tmp_path / "nope.pem")
    assert references.tls_certificate_fingerprint() is None
