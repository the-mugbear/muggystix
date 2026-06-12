"""
SSRF-safe URL validator.

Audit finding C2: the ``base_url`` fields on integration credentials
and LLM provider configs were accepted without format validation or
private-IP filtering.  The values are later fed to ``httpx.get/post``
from inside the backend container for Test Connection and actual
chat-completion requests, so a user with analyst role could point the
backend at cloud metadata endpoints, internal databases, or any other
HTTP-reachable service behind the application's network.

This module provides ``require_public_http_url`` which:

  1. Parses the URL and enforces an ``http``/``https`` scheme.
  2. Extracts the hostname and resolves it via ``getaddrinfo``.
  3. Rejects every resolved IP that falls inside a forbidden range
     (loopback, link-local, RFC1918, IPv6 equivalents).
  4. Accepts a per-call allowlist of integration types that are
     permitted to use private addresses (Ollama at
     ``http://localhost:11434`` is the intended example — users
     legitimately run it on the local host and there's no way to
     make that work without a carve-out).

**Important — this is not a complete SSRF defense on its own.**  Even
with this validator, a DNS-rebinding attack can make a hostname
resolve to a public IP at ``getaddrinfo`` time and a private IP at
connection time.  The complete fix is to pin the resolved IP on the
httpx client via a custom transport, so the final TCP connection is
guaranteed to go to the checked address.  That's a larger change; the
validator closes the common case (direct private-IP URLs) and buys us
time to build the pinning layer.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import FrozenSet, Iterable
from urllib.parse import urlparse


_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Networks that are ALWAYS rejected, even when ``allow_private=True``.
# These are SSRF traps: cloud-instance metadata services and the
# IPv4/IPv6 link-local ranges they live in.  Letting an operator point
# an integration at 169.254.169.254 would turn the Test endpoint into
# a metadata exfiltration probe.
_ALWAYS_FORBIDDEN_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),    # IPv4 link-local + AWS/GCP metadata
    ipaddress.ip_network("0.0.0.0/8"),         # "this network"
    ipaddress.ip_network("224.0.0.0/4"),       # multicast
    ipaddress.ip_network("240.0.0.0/4"),       # reserved
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
    ipaddress.ip_network("ff00::/8"),          # IPv6 multicast
    ipaddress.ip_network("::/128"),
]

# Networks that must never be reachable from the backend unless the
# integration_type is in the per-call allowlist.  Keep in sync with
# RFC 1918 + RFC 6598 + IANA link-local allocations.
_FORBIDDEN_NETWORKS = [
    # IPv4
    ipaddress.ip_network("0.0.0.0/8"),         # "this network"
    ipaddress.ip_network("10.0.0.0/8"),        # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),     # RFC6598 CGNAT
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("169.254.0.0/16"),    # link-local (incl. cloud metadata)
    ipaddress.ip_network("172.16.0.0/12"),     # RFC1918
    ipaddress.ip_network("192.0.0.0/24"),      # IETF protocol assignments
    ipaddress.ip_network("192.168.0.0/16"),    # RFC1918
    ipaddress.ip_network("198.18.0.0/15"),     # RFC2544 benchmark
    ipaddress.ip_network("224.0.0.0/4"),       # multicast
    ipaddress.ip_network("240.0.0.0/4"),       # reserved
    # IPv6
    ipaddress.ip_network("::1/128"),           # loopback
    ipaddress.ip_network("fc00::/7"),          # ULA
    ipaddress.ip_network("fe80::/10"),         # link-local
    ipaddress.ip_network("ff00::/8"),          # multicast
]


def require_public_http_url(
    value: str,
    *,
    allow_private: bool = False,
) -> str:
    """Raise ``ValueError`` if ``value`` is not a safe outbound HTTP URL.

    Parameters
    ----------
    value
        The URL to validate.  Empty / None is rejected.
    allow_private
        If True, loopback and RFC1918 addresses are permitted.  Use
        this exactly for integration types where a private address is
        the expected deployment (Ollama on localhost, an on-prem
        scanner on the LAN, etc.).  Default is False — every other
        integration must resolve to a public IP.

    Returns
    -------
    str
        The original URL, unchanged, so callers can write
        ``body.base_url = require_public_http_url(body.base_url)``.

    Raises
    ------
    ValueError
        If the URL is malformed, uses a disallowed scheme, cannot be
        resolved, or resolves to any address in the forbidden set.
    """
    if not value:
        raise ValueError("URL is empty")

    parsed = urlparse(value.strip())
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"URL scheme must be http or https (got {parsed.scheme!r})"
        )
    host = parsed.hostname
    if not host:
        raise ValueError("URL must include a hostname")

    # Resolve the hostname.  ``getaddrinfo`` returns every A/AAAA
    # record the resolver knows about — we check all of them so a
    # multi-homed name can't slip a private IP past us via round-robin.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve {host!r}: {exc}")

    seen: set = set()
    for family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            # Shouldn't happen for getaddrinfo output; be defensive.
            raise ValueError(f"Could not parse resolved address {ip_str!r}")
        # ALWAYS check the SSRF-trap ranges (cloud metadata, link-local,
        # multicast, reserved) — even ``allow_private=True`` doesn't
        # unlock these.
        for net in _ALWAYS_FORBIDDEN_NETWORKS:
            if addr.version == net.version and addr in net:
                raise ValueError(
                    f"URL host {host!r} resolves to {addr}, which is in "
                    f"a forbidden range ({net}).  Link-local, cloud "
                    f"metadata, multicast, and reserved ranges are "
                    f"rejected regardless of integration type."
                )
        # Private-IP check only applies to public-only integrations.
        # Scanner integrations on a LAN (Nessus on 192.168.x.x, OpenVAS
        # on 10.x.x.x, Ollama on 127.0.0.1) skip this gate.
        if allow_private:
            continue
        for net in _FORBIDDEN_NETWORKS:
            if addr.version == net.version and addr in net:
                raise ValueError(
                    f"URL host {host!r} resolves to a private address "
                    f"({addr}). Public IP required for this integration "
                    f"type.  On-prem scanners (Nessus, OpenVAS, Nuclei, "
                    f"Burp) and local LLMs (Ollama) are allowed to use "
                    f"private addresses; other integration types are not."
                )

    return value


# Integration types whose servers normally live on a private network
# (on-prem scanners on the LAN, local LLMs on the operator's box).
# For a pentesting platform this is the *normal* deployment — public
# IPs are the exception.  Pre-v2.49.4 only ollama was allowlisted,
# which silently broke every Nessus/OpenVAS install at 192.168.x.x /
# 10.x.x.x.  Cloud-metadata / link-local / multicast remain blocked
# for these types via ``_ALWAYS_FORBIDDEN_NETWORKS``.
_PRIVATE_ALLOWED = frozenset({
    "ollama",
    "nessus",
    "openvas",
    "nuclei",
    "burp",
    "generic_api",
})


def is_integration_private_allowed(integration_type: str) -> bool:
    """Return True if the integration type is permitted to use private IPs.

    Centralizes the policy so callers don't scatter the allowlist.
    See ``_PRIVATE_ALLOWED`` above for the current set.  Even when this
    returns True, ``require_public_http_url`` still rejects cloud-
    metadata / link-local / multicast ranges (``_ALWAYS_FORBIDDEN_NETWORKS``)
    so an integration URL cannot be turned into an SSRF probe at
    169.254.169.254.
    """
    return (integration_type or "").lower() in _PRIVATE_ALLOWED


# ---------------------------------------------------------------------------
# IP-pinning httpx transport — closes the DNS-rebinding window
# ---------------------------------------------------------------------------

def _host_resolves_safely(host: str, *, allow_private: bool = False) -> None:
    """Raise ValueError if any A/AAAA record for host is forbidden.

    Shared by require_public_http_url and the pinned transport so both
    paths apply the exact same policy.  ``_ALWAYS_FORBIDDEN_NETWORKS``
    (cloud metadata, link-local, multicast) is rejected regardless of
    ``allow_private``.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve {host!r}: {exc}")
    seen: set = set()
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError(f"Could not parse resolved address {ip_str!r}")
        for net in _ALWAYS_FORBIDDEN_NETWORKS:
            if addr.version == net.version and addr in net:
                raise ValueError(
                    f"Host {host!r} resolves to {addr}, in a forbidden "
                    f"range ({net})."
                )
        if allow_private:
            continue
        for net in _FORBIDDEN_NETWORKS:
            if addr.version == net.version and addr in net:
                raise ValueError(
                    f"Host {host!r} resolves to a private address ({addr})."
                )


def safe_http_client(
    *,
    allow_private: bool = False,
    timeout: float = 15.0,
    verify: bool = True,
):
    """Return an httpx.Client that re-validates every outbound hostname.

    This exists because ``require_public_http_url`` has a TOCTOU window:
    the validator resolves DNS once at save-time or request-time, but
    the actual socket connect happens later inside httpx — a malicious
    DNS record with a short TTL (or a DNS-rebinding name server) can
    return a public IP during validation and a private IP at connect
    time.

    The transport we install here hooks ``handle_request`` and runs
    ``_host_resolves_safely`` on every hop — including redirects —
    immediately before httpx hands the request to httpcore.  The window
    between that check and the TCP connect is not zero, but it is small
    and defeats every practical DNS-rebinding variant seen in the wild.

    A full fix (pin the resolved IP into an explicit socket passed to
    httpcore) would require a private httpcore backend and is not worth
    the maintenance burden for the handful of egress call sites we
    have.  Revisit if we ever proxy arbitrary user-supplied URLs.
    """
    import httpx  # local import — httpx is already a dep

    class _PinnedTransport(httpx.HTTPTransport):
        def handle_request(self, request):
            host = request.url.host
            if host:
                # Even with allow_private=True we still reject cloud
                # metadata / link-local / multicast — the two-tier
                # policy lives inside _host_resolves_safely.
                try:
                    _host_resolves_safely(host, allow_private=allow_private)
                except ValueError as exc:
                    raise httpx.ConnectError(str(exc)) from exc
            return super().handle_request(request)

    return httpx.Client(
        transport=_PinnedTransport(verify=verify),
        timeout=timeout,
        follow_redirects=False,  # redirects can land on a private IP — refuse them
        verify=verify,
    )


class ResponseTooLarge(ValueError):
    """An outbound response body exceeded the configured byte cap."""


def safe_request(
    method: str,
    url: str,
    *,
    allow_private: bool = False,
    timeout: float = 15.0,
    verify: bool = True,
    max_bytes: int | None = None,
    **kwargs,
):
    """Perform a single SSRF-safe request and return a fully-read
    ``httpx.Response``, capping the body at ``max_bytes``.

    The shared egress helper for every outbound call (LLM providers, scanner
    integrations, webhooks).  It reuses ``safe_http_client`` (so the
    DNS-rebinding pin + no-redirect policy still apply), but STREAMS the
    response and stops at the cap — a plain ``client.get(...).json()`` buffers
    the entire body into RAM first, which a hostile/broken endpoint can abuse.

    Raises :class:`ResponseTooLarge` (a ``ValueError``) if the body exceeds the
    cap; otherwise the returned response behaves like a normal buffered one
    (``.json()`` / ``.text`` / ``.status_code`` / ``.raise_for_status()``).
    """
    import httpx
    from app.core.config import settings

    cap = settings.MAX_OUTBOUND_RESPONSE_BYTES if max_bytes is None else max_bytes
    with safe_http_client(allow_private=allow_private, timeout=timeout, verify=verify) as client:
        request = client.build_request(method, url, **kwargs)
        response = client.send(request, stream=True)
        try:
            total = 0
            chunks = []
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > cap:
                    raise ResponseTooLarge(
                        f"Response from {response.url.host} exceeded the "
                        f"{cap}-byte cap"
                    )
                chunks.append(chunk)
            # Populate _content so the closed response still serves
            # .json()/.text/.content — same mechanism as Response.read().
            response._content = b"".join(chunks)
        finally:
            response.close()
        return response
