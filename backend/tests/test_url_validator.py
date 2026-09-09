"""Contract tests for the SSRF URL validator.

The validator is the primary defense against an analyst (or
compromised session) pointing NetworkMapper's backend at an internal
service via the ``base_url`` field on integration / LLM provider
config.  These tests pin the forbidden-network table and the
Ollama carve-out so a regression to a "permissive" validator gets
caught immediately.
"""

from __future__ import annotations

import pytest

from app.services.url_validator import (
    require_public_http_url,
    is_integration_private_allowed,
)


class TestSchemeEnforcement:
    def test_https_public_allowed(self):
        # Use a stable well-known domain that resolves to a public IP.
        # Cloudflare's 1.1.1.1 DNS is a safe bet.
        out = require_public_http_url("https://one.one.one.one/")
        assert out == "https://one.one.one.one/"

    def test_http_scheme_allowed(self):
        # Tests the scheme check in isolation from resolution —
        # allow_private skips the DNS check entirely.
        out = require_public_http_url("http://localhost:11434", allow_private=True)
        assert out == "http://localhost:11434"

    @pytest.mark.parametrize("url", [
        "ftp://example.com/",
        "gopher://example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ])
    def test_non_http_schemes_rejected(self, url):
        with pytest.raises(ValueError, match="scheme"):
            require_public_http_url(url)

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            require_public_http_url("")

    def test_missing_hostname_rejected(self):
        with pytest.raises(ValueError, match="hostname"):
            require_public_http_url("http://")


class TestForbiddenNetworks:
    """Every forbidden range in the validator should reject URLs that
    resolve there.  We use literal-IP URLs so the test doesn't depend
    on live DNS — ``getaddrinfo`` of a literal IP returns that IP."""

    @pytest.mark.parametrize("url,label", [
        ("http://127.0.0.1/", "loopback IPv4"),
        ("http://127.0.0.5:8080/admin", "loopback IPv4 with port"),
        ("http://10.0.0.1/", "RFC1918 10.x"),
        ("http://10.255.255.254/", "RFC1918 10.x edge"),
        ("http://172.16.0.1/", "RFC1918 172.16.x"),
        ("http://172.31.255.254/", "RFC1918 172.31.x edge"),
        ("http://192.168.0.1/", "RFC1918 192.168.x"),
        ("http://169.254.169.254/", "link-local / AWS metadata"),
        ("http://169.254.170.2/", "ECS metadata"),
        ("http://100.64.0.1/", "CGNAT"),
        ("http://[::1]/", "IPv6 loopback"),
    ])
    def test_forbidden_network_rejected(self, url, label):
        # v2.65.0 — regex widened from `"forbidden"` to
        # `"forbidden|private"` so both rejection paths match:
        #   * `_ALWAYS_FORBIDDEN_NETWORKS` (cloud metadata, link-local,
        #     multicast, reserved) → message contains "forbidden range"
        #   * `_FORBIDDEN_NETWORKS` (RFC1918, loopback, CGNAT) →
        #     message contains "private address ... Public IP required"
        # The validator was rewritten to distinguish the two paths
        # (private-IP failures point operators at the
        # `allow_private` opt-in for scanner integrations; always-
        # forbidden ones don't have that escape hatch).  Pinning
        # only "forbidden" stopped matching the more-common
        # private-IP rejection messages.
        with pytest.raises(ValueError, match="forbidden|private"):
            require_public_http_url(url)

    def test_metadata_endpoint_specifically_rejected(self):
        """The canonical cloud metadata exploit URL.  If this test
        ever passes silently, SSRF-to-IAM is back on the table."""
        with pytest.raises(ValueError):
            require_public_http_url(
                "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            )


class TestPrivateAllowance:
    """allow_private is the Ollama carve-out — users legitimately run
    Ollama at http://localhost:11434.  Nothing else should opt in."""

    def test_localhost_allowed_when_opted_in(self):
        out = require_public_http_url("http://localhost:11434", allow_private=True)
        assert out == "http://localhost:11434"

    def test_loopback_ipv4_allowed_when_opted_in(self):
        out = require_public_http_url("http://127.0.0.1:11434", allow_private=True)
        assert out == "http://127.0.0.1:11434"

    def test_private_ip_allowed_when_opted_in(self):
        out = require_public_http_url("http://10.0.0.5:11434", allow_private=True)
        assert out == "http://10.0.0.5:11434"

    def test_scheme_still_enforced_when_opted_in(self):
        """allow_private only disables the IP check; scheme
        validation still runs.  Otherwise the opt-in would be a
        bypass for the entire validator."""
        with pytest.raises(ValueError, match="scheme"):
            require_public_http_url("ftp://127.0.0.1/", allow_private=True)


class TestIntegrationPolicy:
    # v2.65.0 — the original test pinned a narrow allowlist of just
    # `{"ollama"}`, but the production allowlist was widened to
    # include the on-prem scanner types (Nessus, OpenVAS, Nuclei,
    # Burp, generic_api) because those legitimately run on LAN
    # addresses — pinning them to public-only IPs silently broke
    # every customer with a 192.168.x.x Nessus.  Tests split into
    # explicit positive (scanner types + ollama) and explicit
    # negative (cloud LLM providers + empty string) cases so any
    # future allowlist drift fails loud on the right side.

    @pytest.mark.parametrize("itype", [
        "ollama",
        "OLLAMA",  # case insensitive
        "nessus",
        "openvas",
        "nuclei",
        "burp",
        "generic_api",
    ])
    def test_scanner_and_local_llm_types_allow_private(self, itype):
        """Carve-out for integrations that legitimately use LAN
        addresses.  See `_PRIVATE_ALLOWED` in url_validator.py."""
        assert is_integration_private_allowed(itype) is True

    @pytest.mark.parametrize("itype", [
        "openai",
        "anthropic",
        "",
    ])
    def test_cloud_providers_and_empty_disallowed(self, itype):
        """Cloud LLM providers run at public endpoints; the empty
        string is what callers see when integration_type is unset
        and we want fail-closed semantics."""
        assert is_integration_private_allowed(itype) is False


# ---------------------------------------------------------------------------
# DNS-rebinding pin: the transport must connect to the address it validated,
# not re-resolve the name (which a rebinding resolver can answer differently).
# ---------------------------------------------------------------------------
import socket as _socket

import httpx

from app.services import url_validator as _uv
from app.services.url_validator import safe_http_client


def _addrinfo(ip: str):
    fam = _socket.AF_INET6 if ":" in ip else _socket.AF_INET
    return [(fam, _socket.SOCK_STREAM, 6, "", (ip, 0))]


class TestPinnedTransport:
    def _capture(self, monkeypatch):
        seen = {}

        def fake_handle(self, request):
            seen["request"] = request
            return httpx.Response(200, request=request, content=b"ok")

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fake_handle)
        return seen

    def test_connects_to_validated_ip_even_when_dns_rebinds(self, monkeypatch):
        answers = iter([_addrinfo("93.184.216.34"), _addrinfo("169.254.169.254")])
        calls = []

        def fake_getaddrinfo(host, *args, **kwargs):
            calls.append(host)
            return next(answers)

        monkeypatch.setattr(_uv.socket, "getaddrinfo", fake_getaddrinfo)
        seen = self._capture(monkeypatch)

        with safe_http_client() as client:
            resp = client.get("https://example.test:8443/path?q=1")
        assert resp.status_code == 200

        req = seen["request"]
        # The socket target is the vetted address — the second (rebound) DNS
        # answer was never consulted by the transport.
        assert req.url.host == "93.184.216.34"
        assert req.url.port == 8443
        assert req.url.path == "/path" and req.url.query == b"q=1"
        assert calls == ["example.test"]
        # Name preserved where it matters: virtual host + TLS verification.
        assert req.headers["host"] == "example.test:8443"
        assert req.extensions["sni_hostname"] == "example.test"

    def test_http_default_port_host_header_has_no_port(self, monkeypatch):
        monkeypatch.setattr(_uv.socket, "getaddrinfo", lambda h, *a, **k: _addrinfo("93.184.216.34"))
        seen = self._capture(monkeypatch)
        with safe_http_client() as client:
            client.get("http://example.test/")
        req = seen["request"]
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "example.test"
        assert "sni_hostname" not in req.extensions

    def test_ipv6_answer_is_pinned_bracketed(self, monkeypatch):
        monkeypatch.setattr(_uv.socket, "getaddrinfo", lambda h, *a, **k: _addrinfo("2606:2800:220:1:248:1893:25c8:1946"))
        seen = self._capture(monkeypatch)
        with safe_http_client() as client:
            client.get("https://example.test/")
        req = seen["request"]
        assert req.url.host == "2606:2800:220:1:248:1893:25c8:1946"
        assert str(req.url).startswith("https://[2606:2800:")
        assert req.headers["host"] == "example.test"

    def test_literal_ip_url_left_alone(self, monkeypatch):
        seen = self._capture(monkeypatch)
        with safe_http_client() as client:
            client.get("http://93.184.216.34/x")
        req = seen["request"]
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "93.184.216.34"

    def test_forbidden_answer_still_rejected_before_connect(self, monkeypatch):
        monkeypatch.setattr(_uv.socket, "getaddrinfo", lambda h, *a, **k: _addrinfo("169.254.169.254"))
        seen = self._capture(monkeypatch)
        with safe_http_client() as client:
            with pytest.raises(httpx.ConnectError):
                client.get("http://metadata.test/")
        assert "request" not in seen  # never reached the transport

    def test_mixed_answers_rejected_when_any_is_forbidden(self, monkeypatch):
        monkeypatch.setattr(
            _uv.socket, "getaddrinfo",
            lambda h, *a, **k: _addrinfo("93.184.216.34") + _addrinfo("10.0.0.5"),
        )
        seen = self._capture(monkeypatch)
        with safe_http_client() as client:
            with pytest.raises(httpx.ConnectError):
                client.get("http://split.test/")
        assert "request" not in seen
