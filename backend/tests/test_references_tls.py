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


def _self_signed_pem(tmp_path, cn="127.0.0.1"):
    """A real certificate, generated rather than mocked — the behaviour under
    test is PEM parsing, which a fake string would skip."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
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
    pem = tmp_path / f"{cn}.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return pem, cert


def test_fingerprint_matches_the_served_certificate(tmp_path, monkeypatch):
    """A fingerprint that doesn't match the PEM would be worse than none: an
    operator comparing them would 'verify' a certificate that isn't this one."""
    from cryptography.hazmat.primitives import hashes

    from app.api.v1.endpoints import references

    pem, cert = _self_signed_pem(tmp_path)
    monkeypatch.setattr(references, "_TLS_CERT_PATH", pem)

    info = references.tls_certificate_info()
    expected = cert.fingerprint(hashes.SHA256()).hex().upper()
    assert info.fingerprint_sha256 is not None
    assert info.fingerprint_sha256.replace(":", "") == expected
    assert info.self_signed is True


def test_a_certificate_chain_still_fingerprints_the_leaf(tmp_path, monkeypatch):
    """A CA-issued deployment mounts leaf-plus-intermediate in one PEM. The
    first implementation passed the whole file to ssl.PEM_cert_to_DER_cert,
    which raises on a chain — swallowed into a silent None, so the operators
    most likely to hold a *correct* certificate saw no fingerprint at all."""
    from cryptography.hazmat.primitives import hashes

    from app.api.v1.endpoints import references

    leaf_pem, leaf = _self_signed_pem(tmp_path, cn="leaf.example")
    issuer_pem, _ = _self_signed_pem(tmp_path, cn="intermediate.example")
    chained = tmp_path / "chain.pem"
    chained.write_bytes(leaf_pem.read_bytes() + issuer_pem.read_bytes())
    monkeypatch.setattr(references, "_TLS_CERT_PATH", chained)

    info = references.tls_certificate_info()
    # The LEAF is what a client validates and what an operator would pin.
    assert info.fingerprint_sha256 is not None, "a chain must not read as 'no certificate'"
    assert (
        info.fingerprint_sha256.replace(":", "")
        == leaf.fingerprint(hashes.SHA256()).hex().upper()
    )


def test_a_ca_issued_certificate_is_reported_as_not_self_signed(tmp_path, monkeypatch):
    """Self-signed is this project's DEFAULT, not an invariant — an operator can
    mount an internal-CA or DNS-validated certificate. Telling them to pin one
    their clients already trust is busywork, so the page needs to know."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from app.api.v1.endpoints import references

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Internal CA")])
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bluestick.internal")])
    now = datetime.datetime.now(datetime.timezone.utc)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)          # issued BY the CA, not by itself
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(ca_key, hashes.SHA256())
    )
    pem = tmp_path / "ca-issued.pem"
    pem.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(references, "_TLS_CERT_PATH", pem)

    info = references.tls_certificate_info()
    assert info.self_signed is False
    assert info.subject and "bluestick.internal" in info.subject
    assert info.expires_at is not None


def test_missing_certificate_reports_nothing_rather_than_crashing(tmp_path, monkeypatch):
    """Absent is a real state (the cert isn't mounted in every container), and
    it must read as 'unknown' rather than breaking the catalog the page needs."""
    from app.api.v1.endpoints import references

    monkeypatch.setattr(references, "_TLS_CERT_PATH", tmp_path / "nope.pem")
    info = references.tls_certificate_info()
    assert info.fingerprint_sha256 is None
    assert info.self_signed is None


def test_reference_page_recipes_come_from_the_session_builder(client):
    """The page used to carry its own TypeScript copy of the connect recipes,
    and the pair drifted twice: on the config wrapper key (the bug the shared
    builder was created to fix) and on the Codex TLS note, which had to be
    corrected in both languages by hand. Both failures were silent — a config
    the client ignores, and advice that cannot work.

    So the catalog serves what a live session would emit, with a placeholder
    where the key goes. This asserts they are the same code, not merely similar.
    """
    from app.api.v1.endpoints.references import SAMPLE_KEY_PLACEHOLDER
    from app.services.mcp_client_setup_service import build_mcp_clients

    body = client.get("/api/v1/references/mcp-tools").json()
    served = body["sample_clients"]
    assert body["sample_key_placeholder"] == SAMPLE_KEY_PLACEHOLDER

    expected = build_mcp_clients(
        body["endpoint"], SAMPLE_KEY_PLACEHOLDER, workflow="assist"
    )
    assert served == expected

    # No live key ever reaches a page anyone with app access can read.
    for entry in served:
        assert "nm_agent_" not in entry["payload"], entry["id"]

    by_id = {e["id"]: e for e in served}
    # The two recipes that embed a credential show the placeholder in its place.
    assert SAMPLE_KEY_PLACEHOLDER in by_id["vscode"]["payload"]
    assert SAMPLE_KEY_PLACEHOLDER in by_id["claude_code"]["payload"]
    # Codex is the exception BY DESIGN: it reads the key from an env var at run
    # time, so the recipe has no slot for one — which is why it is the only
    # client where the credential never touches a config file.
    assert SAMPLE_KEY_PLACEHOLDER not in by_id["codex"]["payload"]
    assert "--bearer-token-env-var" in by_id["codex"]["payload"]
