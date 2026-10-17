"""One reading of an instant a person, an agent or a registry typed
(v2.513.3; comprehensive review 2026-10-16, A6).

The Hosts query language's time windows, the Query page's `where` on a time
column, the agents' `created_after` and RDAP's event dates each parsed ISO
8601 inline, and a value with no offset meant UTC in three of them and the
server's local zone in the fourth: one literal, two instants.  This is the
rule for all of them.  (A scanner's own timestamps are not this rule's:
`parser_utils.parse_rfc3339` refuses a value with no offset there, because a
tool's zone-less clock must not be guessed.)
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_instant(text: object) -> datetime:
    """ISO 8601 → an AWARE datetime: a date (its midnight, UTC), a time with
    ``Z`` or an offset, or a time without one (UTC).  Raises ``ValueError``
    for anything else; the caller words the refusal for its door."""
    s = str(text).strip()
    if not s:
        raise ValueError("empty")
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    moment = datetime.fromisoformat(s)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
