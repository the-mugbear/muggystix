"""Greenbone/OpenVAS connection test (v2.336.0).

The Test button was universal but only Nessus and Ollama had a probe, so
Greenbone always answered "no automated connectivity test is implemented for
'openvas' yet" — the credentials in the form were never exercised.

Classic GVM can only verify a username and password over GMP (XML over a TLS
socket, default 9390); the configured Base URL is GSA, the web UI, which
cannot. These tests cover the reply parsing, the address guard, the port
override and the no-credentials path. The wire exchange itself is not covered
— that needs a live gvmd.
"""
from app.services import integration_test_service as svc


def _result(**kwargs):
    return svc.test_integration_config(integration_type="openvas", **kwargs)


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------

def test_successful_authentication_reports_the_role():
    out = svc._parse_gmp_authenticate(
        '<authenticate_response status="200" status_text="OK">'
        "<role>Admin</role></authenticate_response>",
        host="gvm.local", port=9390,
    )
    assert out["ok"] is True
    assert "as role Admin" in out["message"]
    assert out["details"] == {"gmp_port": 9390, "role": "Admin"}


def test_rejected_credentials_are_a_failure_not_an_error():
    out = svc._parse_gmp_authenticate(
        '<authenticate_response status="400" status_text="Authentication failed"/>',
        host="gvm.local", port=9390,
    )
    assert out["ok"] is False
    assert "rejected the credentials" in out["message"]
    assert "Authentication failed" in out["message"]


def test_a_non_gmp_listener_says_so():
    http = svc._parse_gmp_authenticate("HTTP/1.1 200 OK\r\n\r\n<html>", host="h", port=9392)
    assert http["ok"] is False
    assert "not with GMP" in http["message"]

    silent = svc._parse_gmp_authenticate("   ", host="h", port=9392)
    assert silent["ok"] is False
    assert "without replying" in silent["message"]


def test_other_gmp_status_is_surfaced_verbatim():
    out = svc._parse_gmp_authenticate(
        '<authenticate_response status="503" status_text="Service temporarily down"/>',
        host="h", port=9390,
    )
    assert out["ok"] is False
    assert "status 503" in out["message"] and "Service temporarily down" in out["message"]


# ---------------------------------------------------------------------------
# Port selection
# ---------------------------------------------------------------------------

def test_gmp_port_comes_from_extra_config_when_usable():
    assert svc._gmp_port({"gmp_port": 9391}) == 9391
    assert svc._gmp_port({"gmp_port": "9391"}) == 9391
    # Unusable values fall back rather than failing the test run.
    for bad in ({}, None, {"gmp_port": 0}, {"gmp_port": 70000}, {"gmp_port": "nine"}):
        assert svc._gmp_port(bad) == 9390


def test_the_configured_port_is_the_one_dialled(monkeypatch):
    seen = {}

    def _fake(ip, port, sni_host, username, password):
        seen.update(ip=ip, port=port, sni_host=sni_host)
        return '<authenticate_response status="200" status_text="OK"/>'

    monkeypatch.setattr(svc, "_gmp_authenticate", _fake)
    out = _result(
        base_url="https://127.0.0.1:9392", secret="admin", secret2="pw",
        extra_config={"gmp_port": 9391},
    )
    assert out.ok is True
    assert seen == {"ip": "127.0.0.1", "port": 9391, "sni_host": "127.0.0.1"}


# ---------------------------------------------------------------------------
# Guards and the no-credentials path
# ---------------------------------------------------------------------------

def test_forbidden_address_is_refused_before_connecting(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover — must never run
        raise AssertionError("connected to a forbidden address")

    monkeypatch.setattr(svc, "_gmp_authenticate", _boom)
    out = _result(base_url="http://169.254.169.254", secret="admin", secret2="pw")
    assert out.ok is False
    assert "forbidden" in out.message.lower() or "validation failed" in out.message.lower()


def test_unreachable_gvmd_explains_the_unix_socket_case(monkeypatch):
    monkeypatch.setattr(
        svc, "_gmp_authenticate",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    out = _result(base_url="https://127.0.0.1:9392", secret="admin", secret2="pw")
    assert out.ok is False
    assert "unix socket" in out.message
    assert out.integration_type == "openvas"


def test_without_credentials_it_reports_reachability_not_success(monkeypatch):
    class _Resp:
        status_code = 200
        text = "<title>Greenbone Security Assistant</title>"

    monkeypatch.setattr(svc, "safe_request", lambda *a, **k: _Resp())
    out = _result(base_url="https://127.0.0.1:9392", secret=None, secret2=None)
    # Tri-state "unknown", never a pass: nothing authenticated.
    assert out.ok is None
    assert "Greenbone Security Assistant" in out.message
    assert "not verified" in out.message


def test_credentials_never_reach_the_log(monkeypatch, caplog):
    monkeypatch.setattr(
        svc, "_gmp_authenticate",
        lambda *a, **k: '<authenticate_response status="200" status_text="OK"/>',
    )
    with caplog.at_level("INFO"):
        _result(base_url="https://127.0.0.1:9392", secret="admin", secret2="sup3rs3cret")
    assert "sup3rs3cret" not in caplog.text
