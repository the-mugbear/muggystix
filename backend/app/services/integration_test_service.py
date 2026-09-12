"""
Integration connection-test service — verifies a scanner integration's
``base_url + credentials`` actually work before the operator persists
the row.

Why pre-save: pre-v2.49.4 the only way to know an integration worked
was to start an agentic recon session and watch the agent's first
request to Nessus / OpenVAS / etc. fail.  The Test button now wires
through this service so the create modal can confirm the config in
one round trip.

Each test:
    * Validates the URL through ``require_public_http_url`` so the
      address-policy gate (allow_private + always-forbidden cloud
      metadata) is the SAME as on save — no chance the test passes
      and save then rejects, or vice versa.
    * Dispatches to a per-type probe (Nessus → ``/server/properties``,
      Ollama → ``/api/version``, OpenVAS/Greenbone → GMP
      ``<authenticate>`` on the gvmd TLS socket).  Types without a
      concrete probe return ``ok=None`` (not_implemented) so the UI
      button is universal and honest about what's actually verified.
    * Returns a sanitized result — never includes the plaintext
      credential, only the integration type, host, status, and
      human-readable message.
    * Logs every attempt (user, type, host, ok, http status, duration,
      message) via the structured app logger so a failed integration
      is root-cause-reviewable from ``docker compose logs backend``.
      Credentials are NEVER logged (the probe functions don't pass
      them to the log helper).
"""

from __future__ import annotations

import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import httpx

from app.services.url_validator import (
    is_integration_private_allowed,
    require_public_http_url,
    resolve_host_guarded,
    safe_request,
    ResponseTooLarge,
)

logger = logging.getLogger(__name__)

# Classic GVM (Greenbone): gvmd speaks GMP — XML over a TLS socket, default
# 9390. The Base URL operators configure is GSA, the web UI on 9392, which
# cannot verify a username and password (its login endpoint is deprecated), so
# a credential check has to talk GMP. This is the one place in the backend that
# egresses outside the guarded HTTP client, and it is bounded accordingly:
# admin-only endpoint, the address vetted by resolve_host_guarded and connected
# to by vetted IP (no second resolution), one operator-supplied port, a read
# cap, and a timeout. Credentials are never logged.
_GMP_DEFAULT_PORT = 9390
_GMP_TIMEOUT = 10.0
_GMP_READ_LIMIT = 64 * 1024


@dataclass
class IntegrationTestResult:
    """The shape the endpoint returns.

    ``ok`` is tri-state:
        * ``True``  → the probe reached the server and authenticated.
        * ``False`` → the probe reached something / didn't reach something /
                       authentication was rejected — see ``message``.
        * ``None``  → no probe is implemented for this integration type;
                       the URL validator passed but no deeper check ran.
    """
    ok: Optional[bool]
    integration_type: str
    message: str
    details: Optional[Dict[str, Any]] = None
    duration_ms: int = 0
    http_status: Optional[int] = None


def test_integration_config(
    *,
    integration_type: str,
    base_url: Optional[str],
    secret: Optional[str],
    secret2: Optional[str],
    extra_config: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> IntegrationTestResult:
    """Dispatch a connection test to the per-type probe.

    Never raises — every failure mode returns an
    ``IntegrationTestResult`` with ``ok=False`` and a human-readable
    ``message`` so the endpoint can stay 200 and the UI can render
    failure inline.  The structured log line is emitted by
    ``_finish`` regardless of outcome.
    """
    started = time.monotonic()
    itype = (integration_type or "").lower()
    host_for_log = _safe_host(base_url)

    if not base_url:
        return _finish(
            started, itype, host_for_log, user_id,
            ok=False, message="base_url is required for connection test.",
        )

    # Validate URL first — same gate as save.  Run this BEFORE the
    # type dispatch so an unsupported type with a bad URL still fails
    # on the URL (the useful diagnostic).
    try:
        require_public_http_url(
            base_url,
            allow_private=is_integration_private_allowed(itype),
        )
    except ValueError as exc:
        return _finish(
            started, itype, host_for_log, user_id,
            ok=False, message=f"URL validation failed: {exc}",
        )

    try:
        if itype == "nessus":
            return _finish(started, itype, host_for_log, user_id,
                           **_test_nessus(base_url, secret, secret2))
        if itype == "ollama":
            return _finish(started, itype, host_for_log, user_id,
                           **_test_ollama(base_url))
        if itype == "openvas":
            return _finish(started, itype, host_for_log, user_id,
                           **_test_openvas(base_url, secret, secret2, extra_config))
        # Graceful default — the URL passed validation, but no
        # type-specific probe exists yet.  Operator can save and
        # verify manually.
        return _finish(
            started, itype, host_for_log, user_id,
            ok=None,
            message=(
                f"URL reachable per policy, but no automated connectivity "
                f"test is implemented for '{integration_type}' yet.  "
                f"Save and verify the configuration manually."
            ),
        )
    except Exception as exc:
        # Defensive last resort — each probe catches its own httpx
        # errors, but if anything slips through, surface as a failure
        # rather than 500.  The exception itself goes to logs.
        logger.exception(
            "integration_test unexpected error type=%s host=%s",
            itype, host_for_log,
        )
        return _finish(
            started, itype, host_for_log, user_id,
            ok=False,
            message=f"Unexpected error during test: {type(exc).__name__}",
        )


# ---------------------------------------------------------------------------
# Per-type probes
# ---------------------------------------------------------------------------

def _test_nessus(
    base_url: str,
    access_key: Optional[str],
    secret_key: Optional[str],
) -> dict:
    """Hit ``/server/properties`` with X-ApiKeys.

    Returns the build / version on success.  ``verify=False`` because
    Nessus serves with a self-signed cert by default — the operator
    explicitly authorized this address by typing it in.
    """
    if not access_key or not secret_key:
        return {
            "ok": False,
            "message": "Nessus requires both an access key and a secret key.",
        }
    headers = {"X-ApiKeys": f"accessKey={access_key}; secretKey={secret_key}"}
    target = f"{base_url.rstrip('/')}/server/properties"
    try:
        resp = safe_request(
            "GET", target, allow_private=True, timeout=10.0, verify=False, headers=headers,
        )
    except ResponseTooLarge:
        return {"ok": False, "message": "Nessus returned an oversized response."}
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"Could not connect to Nessus: {exc}"}
    except httpx.TimeoutException:
        return {
            "ok": False,
            "message": "Connection to Nessus timed out after 10 seconds.",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "message": f"HTTP error talking to Nessus: {type(exc).__name__}",
        }

    if resp.status_code == 401:
        return {
            "ok": False, "http_status": 401,
            "message": "Nessus rejected the credentials (HTTP 401). Check access + secret keys.",
        }
    if resp.status_code == 403:
        return {
            "ok": False, "http_status": 403,
            "message": "Nessus refused (HTTP 403). Keys may lack permission to read /server/properties.",
        }
    if resp.status_code >= 400:
        return {
            "ok": False, "http_status": resp.status_code,
            "message": f"Nessus returned HTTP {resp.status_code}.",
        }
    try:
        body = resp.json()
    except ValueError:
        return {
            "ok": False, "http_status": resp.status_code,
            "message": "Nessus response was not valid JSON — is the URL pointing at the Nessus server?",
        }
    version = body.get("server_version") or "unknown"
    nessus_type = body.get("nessus_type") or "Nessus"
    return {
        "ok": True, "http_status": resp.status_code,
        "message": f"Connected to {nessus_type} {version}. Credentials accepted.",
        "details": {"server_version": version, "nessus_type": nessus_type},
    }


def _test_ollama(base_url: str) -> dict:
    """Hit ``/api/version`` — no auth, fastest possible round-trip."""
    target = f"{base_url.rstrip('/')}/api/version"
    try:
        resp = safe_request("GET", target, allow_private=True, timeout=5.0, verify=False)
    except ResponseTooLarge:
        return {"ok": False, "message": "Ollama returned an oversized response."}
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"Could not connect to Ollama: {exc}"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection to Ollama timed out after 5 seconds."}
    if resp.status_code >= 400:
        return {
            "ok": False, "http_status": resp.status_code,
            "message": f"Ollama returned HTTP {resp.status_code}.",
        }
    try:
        body = resp.json()
    except ValueError:
        return {
            "ok": True, "http_status": resp.status_code,
            "message": "Connected to Ollama, but the response was not JSON.",
        }
    version = body.get("version") or "unknown"
    return {
        "ok": True, "http_status": resp.status_code,
        "message": f"Connected to Ollama {version}.",
        "details": {"version": version},
    }


def _test_openvas(
    base_url: str,
    username: Optional[str],
    password: Optional[str],
    extra_config: Optional[Dict[str, Any]],
) -> dict:
    """Authenticate to gvmd over GMP; without credentials, report reachability.

    The honest split: a username and password can only be checked by GMP, so
    that is what runs when both are present. With either missing we probe the
    configured URL over HTTP and say outright that the credentials were not
    verified, rather than implying a passing test.
    """
    host = urlparse(base_url).hostname
    if not host:
        return {"ok": False, "message": "Could not read a host from the base URL."}
    if not username or not password:
        return _gsa_reachability(base_url)

    port = _gmp_port(extra_config)
    try:
        addresses = resolve_host_guarded(host, allow_private=True)
    except ValueError as exc:
        return {"ok": False, "message": f"URL validation failed: {exc}"}

    try:
        reply = _gmp_authenticate(addresses[0], port, host, username, password)
    except ConnectionRefusedError:
        return {
            "ok": False,
            "message": (
                f"Nothing accepted a GMP connection on {host}:{port}. gvmd often listens only "
                f"on a unix socket — enable its TLS listener, or set the GMP port this "
                f"deployment uses."
            ),
        }
    except (socket.timeout, TimeoutError):
        return {
            "ok": False,
            "message": f"GMP connection to {host}:{port} timed out after {int(_GMP_TIMEOUT)}s.",
        }
    except ssl.SSLError as exc:
        return {
            "ok": False,
            "message": (
                f"TLS handshake with {host}:{port} failed "
                f"({getattr(exc, 'reason', None) or type(exc).__name__}) — is that the GMP port?"
            ),
        }
    except OSError as exc:
        return {
            "ok": False,
            "message": (
                f"Could not reach GMP at {host}:{port}: "
                f"{getattr(exc, 'strerror', None) or type(exc).__name__}"
            ),
        }
    return _parse_gmp_authenticate(reply, host=host, port=port)


def _gmp_port(extra_config: Optional[Dict[str, Any]]) -> int:
    """``extra_config.gmp_port`` when it is a usable port, else 9390."""
    raw = (extra_config or {}).get("gmp_port")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return _GMP_DEFAULT_PORT
    return port if 1 <= port <= 65535 else _GMP_DEFAULT_PORT


def _gmp_authenticate(
    ip: str, port: int, sni_host: str, username: str, password: str,
) -> str:
    """One GMP ``<authenticate>`` exchange; returns the raw reply.

    Connects to ``ip`` (already vetted) while presenting ``sni_host``. GVM
    ships a self-signed certificate and the operator authorized this exact
    address by typing it in, so verification is off — the same stance as the
    Nessus probe's ``verify=False``. Values are XML-escaped: a password
    containing ``&`` or ``<`` would otherwise corrupt the request.
    """
    payload = (
        "<authenticate><credentials>"
        f"<username>{xml_escape(username)}</username>"
        f"<password>{xml_escape(password)}</password>"
        "</credentials></authenticate>"
    ).encode()

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    chunks: list = []
    total = 0
    with socket.create_connection((ip, port), timeout=_GMP_TIMEOUT) as raw_sock:
        with context.wrap_socket(raw_sock, server_hostname=sni_host) as tls:
            tls.settimeout(_GMP_TIMEOUT)
            tls.sendall(payload)
            while True:
                chunk = tls.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if b"</authenticate_response>" in chunk or total >= _GMP_READ_LIMIT:
                    break
    return b"".join(chunks).decode("utf-8", "replace")


def _parse_gmp_authenticate(reply: str, *, host: str, port: int) -> dict:
    """Read a GMP authenticate reply. Status 200 is success; 400 / 401 is a
    credential rejection; anything unparseable means the port is not GMP."""
    if not reply.strip():
        return {
            "ok": False,
            "message": (
                f"{host}:{port} closed the connection without replying — "
                f"probably not a GMP listener."
            ),
        }
    status_match = re.search(r'status="(\d+)"', reply)
    if "<authenticate_response" not in reply or status_match is None:
        return {
            "ok": False,
            "message": (
                f"{host}:{port} answered, but not with GMP "
                f"(no authenticate_response) — check the port."
            ),
        }
    status = int(status_match.group(1))
    text_match = re.search(r'status_text="([^"]*)"', reply)
    status_text = text_match.group(1) if text_match else ""
    if status == 200:
        role_match = re.search(r"<role>([^<]*)</role>", reply)
        role = role_match.group(1) if role_match else None
        return {
            "ok": True,
            "message": (
                f"Authenticated to GVM over GMP at {host}:{port}"
                + (f" as role {role}." if role else ". Credentials accepted.")
            ),
            "details": {"gmp_port": port, **({"role": role} if role else {})},
        }
    if status in (400, 401):
        return {
            "ok": False,
            "message": (
                f"GVM rejected the credentials (GMP status {status}"
                f"{': ' + status_text if status_text else ''})."
            ),
        }
    return {
        "ok": False,
        "message": (
            f"GMP at {host}:{port} returned status {status}"
            f"{': ' + status_text if status_text else ''}."
        ),
    }


def _gsa_reachability(base_url: str) -> dict:
    """No credentials to check: confirm something answers, and say so plainly
    rather than letting a reachability pass read as a verified login."""
    try:
        resp = safe_request("GET", base_url, allow_private=True, timeout=10.0, verify=False)
    except ResponseTooLarge:
        return {
            "ok": None,
            "message": "The server answered with an oversized response; credentials not verified.",
        }
    except httpx.ConnectError as exc:
        return {"ok": False, "message": f"Could not connect: {exc}"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out after 10 seconds."}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"HTTP error talking to the server: {type(exc).__name__}"}

    body = ""
    try:
        body = (resp.text or "")[:4096].lower()
    except Exception:  # pragma: no cover — non-text body
        body = ""
    who = "Greenbone Security Assistant" if "greenbone" in body else "A server"
    return {
        "ok": None,
        "http_status": resp.status_code,
        "message": (
            f"{who} answered at this URL, but the credentials were not verified — "
            f"enter the GVM username and password to authenticate over GMP."
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_host(url: Optional[str]) -> str:
    if not url:
        return "(no url)"
    try:
        return urlparse(url).hostname or "(no host)"
    except Exception:
        return "(invalid url)"


def _finish(
    started: float,
    itype: str,
    host: str,
    user_id: Optional[int],
    *,
    ok: Optional[bool],
    message: str,
    details: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
) -> IntegrationTestResult:
    """Stamp duration, emit the structured log line, return the result.

    The log line uses positional %-args (not f-strings) so log
    aggregators can pivot on the field names.  Credentials are not
    accepted by this helper — the per-type probes never pass them
    here.  Failures log at WARNING so they surface in error-filtered
    log views; success / not-implemented log at INFO.
    """
    duration_ms = int((time.monotonic() - started) * 1000)
    result = IntegrationTestResult(
        ok=ok,
        integration_type=itype,
        message=message,
        details=details,
        duration_ms=duration_ms,
        http_status=http_status,
    )
    log_method = logger.warning if ok is False else logger.info
    log_method(
        "integration_test type=%s host=%s user_id=%s ok=%s status=%s "
        "duration_ms=%s message=%r",
        itype, host, user_id, ok, http_status, duration_ms, message,
    )
    return result
