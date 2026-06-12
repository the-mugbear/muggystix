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
      Ollama → ``/api/version``).  Types without a concrete probe
      return ``ok=None`` (not_implemented) so the UI button is
      universal and honest about what's actually verified.
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
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from app.services.url_validator import (
    is_integration_private_allowed,
    require_public_http_url,
    safe_request,
    ResponseTooLarge,
)

logger = logging.getLogger(__name__)


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
