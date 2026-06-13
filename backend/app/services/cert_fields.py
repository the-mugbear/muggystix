"""Shared certificate-field derivation (column-vs-blob promotion, v2.205.0).

The raw TLS block a web scanner emits (``web_interfaces.tls_info``) varies by
tool, so cert *expiry* and *self-signed* used to be re-parsed from the JSON blob
on every insight read — five datetime formats and issuer/subject key probing,
per row, per request.  Those two predicates are now promoted to typed columns
(``cert_not_after``, ``cert_self_signed``) derived ONCE here at ingest.

This module is the single implementation the parsers (write) and the insight
services (read) share, so the parse logic can't drift between them.
"""
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
