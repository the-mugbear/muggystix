"""
Server-side prompt sanitizer.

Audit finding H2: the client-side ``promptSanitizer.ts`` stripped
agent API keys and inlined scanner credentials before the
``InAppAgentPanel`` posted to ``/llm-providers/{id}/complete``, but
the backend endpoint itself did no scrubbing.  A user who bypassed
the frontend (direct curl, scripted client, compromised analyst
session) could deliberately include a real agent API key or
credential in the ``prompt`` body and the backend would forward the
full text to the configured LLM provider, where it would land in the
provider's request log.

This module mirrors ``frontend/src/utils/promptSanitizer.ts`` at the
bullet / pattern level.  Keep them in sync when patterns change —
the frontend version is defense-in-depth (makes the intent visible
to users and prevents accidental leakage via the happy path), and
the server version is the enforcement point (catches anything that
bypasses the frontend).

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
    # 2. Inlined scanner-credential bullets emitted by
    #    ``agent_prompt_service._integration_block``.  The template
    #    shape is ``  - <Label>: `<value>``` (backtick-wrapped
    #    value after a label).  Redact the value, keep the label so
    #    the LLM can still reason about the structure of the prompt.
    (
        re.compile(
            r"^(\s*-\s*(?:Access key|Secret key|Password|Username|API key|PDCP token|Secret)\s*:\s*)`[^`]*`",
            re.IGNORECASE | re.MULTILINE,
        ),
        rf"\1`{_REDACTED}`",
    ),
    # 3. Defense in depth — any bare ``nm_agent_...`` token elsewhere
    #    in the text gets stripped.  The 20+ char minimum avoids
    #    false-matching shorter strings that happen to start with
    #    ``nm_agent_`` (prompt-version strings, etc).
    (
        re.compile(r"nm_agent_[A-Za-z0-9_-]{20,}"),
        _REDACTED,
    ),
    # 4. Well-known third-party secret formats.  These prompts are
    #    forwarded to whatever LLM provider the operator configured, so
    #    a pasted cloud / API credential (not just a BlueStick agent key)
    #    would land in that provider's request log.  Same cheap-redaction
    #    posture: err on the side of over-stripping.  Keep in sync with
    #    frontend/src/utils/promptSanitizer.ts.
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
