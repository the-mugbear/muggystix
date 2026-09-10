"""How a Scan's run window leaves the API (v2.333.0).

``Scan.start_time`` / ``end_time`` are naive columns and ``Scan.time_source``
says what a naive value means (see ``models.SCAN_TIME_SOURCES``).  Serialised
as-is, a naive value reaches the browser without an offset and JavaScript
reads it as the VIEWER's local time — a UTC scanner timestamp rendered hours
off, next to an upload time (``created_at``, tz-aware) rendered correctly.

This is the one rule for every surface that emits a scan's run time:

* absolute sources (``tool_run`` / ``tool_records``, and legacy rows with no
  recorded source — the long-standing "naive = UTC" convention) come out
  tz-aware UTC, so clients convert them to the viewer's zone;
* ``tool_clock`` (the scanner's zone-less wall clock) stays naive, so it is
  shown exactly as the tool printed it and never converted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.db.models import SCAN_TIME_TOOL_CLOCK


def scan_time_for_api(value: Optional[datetime], time_source: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    if time_source == SCAN_TIME_TOOL_CLOCK:
        return value.replace(tzinfo=None)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
