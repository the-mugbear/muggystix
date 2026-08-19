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
