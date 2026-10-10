"""
Server-side prompt sanitizer.

Text sent to a configured LLM provider (``POST /llm-providers/{id}/complete``,
the report drafter) lands in that provider's request log, so secret-shaped
strings are replaced first: a BlueStick agent key (as an ``X-API-Key`` line
or bare) and the well-known third-party token formats.

There is no rule for scanner-integration credentials any more.  The one that
existed redacted the labelled bullets the session prompt used to print
(``- Access key: `…```); nothing prints an integration's credentials now —
they leave the server only through the agents' recorded request — so the rule
had nothing to match and went with the block (2.482.0).

The sanitizer is intentionally aggressive: false positives (redacting
non-secret text that happens to match a pattern) are cheap — the LLM
sees ``[REDACTED]`` instead of a benign string.  False negatives (a
real secret slipping through) are expensive, so we err on the side
of over-stripping.
"""

from __future__ import annotations

import re
from typing import Optional


_REDACTED = "[REDACTED — out of band]"

# Patterns are applied in order.  Each entry is (compiled regex,
# replacement string).  The replacement uses Python's ``re.sub``
# semantics so ``\1`` / ``\g<name>`` work for capturing groups.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 1. X-API-Key header line with an nm_agent_* token.  Matches
    #    the whole line so the markdown/code-fence shape stays
    #    intact.
    (
        re.compile(
            r"X-API-Key:\s*nm_agent_[A-Za-z0-9_-]+",
            re.IGNORECASE,
        ),
        f"X-API-Key: {_REDACTED}",
    ),
    # 2. Defense in depth — any bare ``nm_agent_...`` token elsewhere
    #    in the text gets stripped.  The 20+ char minimum avoids
    #    false-matching shorter strings that happen to start with
    #    ``nm_agent_`` (prompt-version strings, etc).
    (
        re.compile(r"nm_agent_[A-Za-z0-9_-]{20,}"),
        _REDACTED,
    ),
    # 3. Well-known third-party secret formats.  These prompts are
    #    forwarded to whatever LLM provider the operator configured, so
    #    a pasted cloud / API credential (not just a BlueStick agent key)
    #    would land in that provider's request log.  Same cheap-redaction
    #    posture: err on the side of over-stripping.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACTED),            # AWS access key id
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), _REDACTED),       # OpenAI-style secret key
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _REDACTED),  # GitHub tokens
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), _REDACTED),  # Slack tokens
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), _REDACTED),       # Google API key
    (  # JSON Web Token (three base64url segments)
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        _REDACTED,
    ),
    (  # Bearer auth tokens — keep the scheme, redact the credential
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}"),
        f"Bearer {_REDACTED}",
    ),
]


def sanitize_for_llm(text: Optional[str]) -> Optional[str]:
    """Return ``text`` with sensitive patterns replaced.

    ``None`` and empty strings pass through unchanged so callers can
    uniformly call ``sanitize_for_llm(body.system)`` even when
    ``system`` is optional.  Every other input gets the full pattern
    sweep.
    """
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out
