"""Shared certificate-field derivation (column-vs-blob promotion, v2.205.0).

The raw TLS block a web scanner emits (``web_interfaces.tls_info``) varies by
tool, so cert *expiry* and *self-signed* used to be re-parsed from the JSON blob
on every insight read — five datetime formats and issuer/subject key probing,
per row, per request.  Those two predicates are now promoted to typed columns
(``cert_not_after``, ``cert_self_signed``) derived ONCE here at ingest.

This module is the single implementation the parsers (write) and the insight
services (read) share, so the parse logic can't drift between them.
"""
import re
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

# Tool-specific not_after serializations, tried after ISO-8601.
_CERT_DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%b %d %H:%M:%S %Y %Z",
    "%Y-%m-%d",
)


def parse_cert_not_after(value: Any) -> Optional[datetime]:
    """Best-effort parse of a certificate ``not_after`` across tool formats.

    Always returns a tz-aware UTC datetime, or ``None`` when unparseable."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = None
        for fmt in _CERT_DT_FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def derive_cert_fields(tls_info: Any) -> Tuple[Optional[datetime], Optional[bool]]:
    """From a raw ``tls_info`` blob, derive ``(cert_not_after, cert_self_signed)``
    for the promoted columns.

    Returns ``(None, None)`` when there's no usable cert info.  ``cert_self_signed``
    stays ``None`` (unknown) unless both issuer and subject are present — only
    then can self-signedness be decided."""
    if not isinstance(tls_info, dict):
        return None, None
    not_after = parse_cert_not_after(tls_info.get("not_after"))
    issuer = tls_info.get("issuer_dn") or tls_info.get("issuer")
    subject = (
        tls_info.get("subject_dn")
        or tls_info.get("subject")
        or tls_info.get("subject_cn")
    )
    self_signed: Optional[bool] = None
    if issuer and subject:
        self_signed = str(issuer) == str(subject)
    return not_after, self_signed


# --- Certificate organisation (v2.238.0) -----------------------------------
# A certificate's subject Organization is attribution evidence a *third party*
# validated: a public CA checked that the requester controls the name before
# issuing. That makes it stronger evidence of who runs a host than a registry
# record, which is self-declared — and it costs no external lookup, because
# the value is already sitting in the tls_info blob every web scan writes.
#
# Only OV/EV certificates carry an Organization at all; DV certs (Let's
# Encrypt and most of the modern web) legitimately have none. An absent O= is
# therefore "no claim made", never "unattributed" — see the None handling in
# the callers.

_DN_ORG_KEYS = ("organization", "org", "o", "subject_org", "organization_name")


def _dn_field(value: Any, key: str = "O") -> Optional[str]:
    """Pull one RDN out of a DN, whether the tool serialised it as a string
    or a dict.

    Tools disagree: httpx emits ``subject_dn`` as an X.509 string
    (``CN=x, O=Acme Corp, C=US``), others hand back a parsed mapping. Both
    shapes appear in the wild, so both are handled in the one place parsers
    and readers share.
    """
    if not value:
        return None
    if isinstance(value, dict):
        for k in _DN_ORG_KEYS:
            found = value.get(k)
            if isinstance(found, str) and found.strip():
                return found.strip()
        return None
    text = str(value)
    # Split on commas that separate RDNs, tolerating escaped commas inside a
    # value (``O=Acme\, Inc``) which would otherwise truncate the org.
    parts = re.split(r"(?<!\\),", text)
    for part in parts:
        if "=" not in part:
            continue
        name, _, val = part.partition("=")
        if name.strip().upper() == key.upper():
            cleaned = val.strip().replace("\\,", ",").strip('"')
            return cleaned or None
    return None


def derive_cert_orgs(tls_info: Any) -> Tuple[Optional[str], Optional[str]]:
    """``(subject_org, issuer_org)`` from a raw tls_info blob.

    ``subject_org`` is who the certificate says runs this host; ``issuer_org``
    is which CA vouched for it. Returns ``(None, None)`` when there's no cert
    or no Organization — which is the normal case for DV certificates and must
    not be read as a negative finding.
    """
    if not isinstance(tls_info, dict):
        return None, None
    subject_org = _dn_field(
        tls_info.get("subject_dn") or tls_info.get("subject")
    ) or _dn_field(tls_info.get("subject_org"))
    issuer_org = _dn_field(tls_info.get("issuer_dn") or tls_info.get("issuer"))
    trim = lambda v: v[:255] if v else None  # noqa: E731 — column width
    return trim(subject_org), trim(issuer_org)


def cert_issue_from_columns(
    not_after: Optional[datetime], self_signed: Optional[bool], now: datetime,
) -> Optional[str]:
    """Hygiene verdict from the promoted columns: ``'expired'`` (priority),
    ``'self-signed'``, or ``None``.  Mirrors the old blob-based ``_cert_issue``
    so the insight surfaces read columns instead of re-parsing JSON."""
    if not_after is not None and not_after < now:
        return "expired"
    if self_signed:
        return "self-signed"
    return None
