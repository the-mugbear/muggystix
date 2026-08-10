"""Identity of a vulnerability ISSUE, independent of which scanner found it.

Nessus and GreenBone/OpenVAS report the same underlying problem with their own
plugin ids, their own wording, and often their own severity. Everything that
wants to treat those as one thing — the inspector's grouping, the Finding
spine's dedup, the cross-host fan-out on promote — needs the same answer to
"what issue is this?", or the UI and the database disagree about what has been
deduplicated.

This module is that single answer. The frontend does not recompute it: the key
is serialized onto each vulnerability so the grouping a user sees is provably
the grouping the backend dedups on.

Two tiers, both EXACT matches:

  1. CVE — the one genuinely cross-vendor identifier, populated by both
     parsers and indexed on ``Vulnerability.cve_id``.
  2. Normalised title — for the large share of scanner output with no CVE
     (config checks, weak ciphers, missing headers, default credentials).

There is deliberately no fuzzy or similarity matching. In a security tool a
wrong merge hides a real finding, which is worse than showing a duplicate; the
failure mode of being too strict is simply that an issue stays split, which is
the behaviour that already exists. A row with neither identifier keys to itself
rather than pooling with other unidentifiable rows — absence of information is
not evidence of sameness.
"""
from __future__ import annotations

import re
from typing import Optional

# Vendor prefixes/suffixes both tools bolt onto otherwise-identical titles.
_VENDOR_PREFIX = re.compile(r"^(nessus|openvas|greenbone|qualys)\s*[:\-–]\s*", re.I)
_VENDOR_SUFFIX = re.compile(r"\s*\((?:nessus|openvas|greenbone)\)\s*$", re.I)
# Keep word characters, whitespace and dots: dots carry version numbers
# ("TLS 1.0" vs "TLS 1.1"), which distinguish genuinely different findings.
_PUNCT = re.compile(r"[^\w\s.]")
_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Reduce a scanner title to a comparable form.

    Only strips noise that is reliably meaningless — case, punctuation,
    whitespace, vendor decoration. Anything that could carry meaning is
    preserved, because stripping it would merge different findings.
    """
    s = _VENDOR_PREFIX.sub("", title)
    s = _VENDOR_SUFFIX.sub("", s)
    s = _PUNCT.sub(" ", s.lower())
    return _WS.sub(" ", s).strip()


def issue_key(
    *,
    cve_id: Optional[str],
    title: Optional[str],
    row_id: Optional[int] = None,
) -> Optional[str]:
    """Stable identity for the issue a vulnerability row describes.

    Returns ``None`` when the row carries no identifying information at all
    AND no ``row_id`` was supplied to fall back to — callers that need a
    guaranteed key should pass ``row_id``.
    """
    cve = (cve_id or "").strip()
    if cve:
        return f"cve:{cve.upper()}"
    raw = (title or "").strip()
    if raw:
        norm = normalize_title(raw)
        if norm:
            return f"title:{norm}"
    return f"row:{row_id}" if row_id is not None else None


def issue_key_for(vuln) -> Optional[str]:
    """``issue_key`` for a Vulnerability ORM row."""
    return issue_key(cve_id=vuln.cve_id, title=vuln.title, row_id=vuln.id)
