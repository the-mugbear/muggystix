"""The project's notes, newest activity first: the ONE read behind the
Collaboration feed (``GET /projects/{id}/hosts/notes/activity``) and the
agents' ``GET /agent/assist/notes`` (MCP ``assist_list_recent_notes``).

Review 2026-10-23, R-AG2: the agents' read was a second implementation that
ordered by ``created_at`` where the page ordered by the last edit, and took no
search.  Both doors now call :func:`project_notes_page`; what differs is a
parameter — the page lists host notes, the agents every target — not a copy.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional, Tuple

from sqlalchemy import desc, func, null, or_
from sqlalchemy.orm import Session, aliased

from app.db import models
from app.services.host_query_common import escape_like


class NoteFeedRow(NamedTuple):
    """One note with what names its target.  Fields of targets the note is
    not on are None."""
    note: models.Annotation
    host_ip: Optional[str]
    host_name: Optional[str]
    finding_title: Optional[str]
    scan_filename: Optional[str]
    port_number: Optional[int]
    port_host_ip: Optional[str]


def activity_order():
    """Newest activity first — an edit counts as activity — and the id
    breaks a tie (notes written together share a timestamp, and a page
    boundary between them would repeat or skip one)."""
    return (
        desc(func.coalesce(models.Annotation.updated_at, models.Annotation.created_at)),
        desc(models.Annotation.id),
    )


def project_notes_page(
    db: Session,
    project_id: int,
    *,
    every_target: bool,
    author_id: Optional[int] = None,
    search: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
    with_total: bool = True,
    load_options=(),
) -> Tuple[Optional[int], List[NoteFeedRow]]:
    """``(total, rows)`` of the project's notes matching the filters.

    ``every_target=False`` — host notes only (the Collaboration feed);
    ``True`` — a note on any target: host, port (via its host), finding, scan,
    the scope or the project itself.  A note has exactly one target
    (``ck_annotations_exactly_one_target``), so every join is many-to-one and
    a row is a note: the count is the list's.  ``search`` matches the note's
    body or its host's address or name (a port note's host's too).
    ``total`` is None when ``with_total`` is False.
    """
    from app.db.models_findings import Finding

    note = models.Annotation
    host = models.Host
    port_host = aliased(models.Host)
    if every_target:
        q = (
            db.query(
                note, host.ip_address, host.hostname,
                Finding.title, models.Scan.filename,
                models.Port.port_number, port_host.ip_address,
            )
            .outerjoin(host, note.host_id == host.id)
            .outerjoin(Finding, note.finding_id == Finding.id)
            .outerjoin(models.Scan, note.scan_id == models.Scan.id)
            .outerjoin(models.Scope, note.scope_id == models.Scope.id)
            .outerjoin(models.Port, note.port_id == models.Port.id)
            .outerjoin(port_host, models.Port.host_id == port_host.id)
            .filter(or_(
                note.project_id == project_id,
                host.project_id == project_id,
                Finding.project_id == project_id,
                models.Scan.project_id == project_id,
                models.Scope.project_id == project_id,
                port_host.project_id == project_id,
            ))
        )
    else:
        q = (
            db.query(
                note, host.ip_address, host.hostname,
                null(), null(), null(), null(),
            )
            .join(host, note.host_id == host.id)
            .filter(host.project_id == project_id)
        )

    if author_id is not None:
        q = q.filter(note.user_id == author_id)
    if search:
        pattern = f"%{escape_like(search)}%"
        matches = [
            host.ip_address.ilike(pattern, escape="\\"),
            host.hostname.ilike(pattern, escape="\\"),
            note.body.ilike(pattern, escape="\\"),
        ]
        if every_target:
            matches.append(port_host.ip_address.ilike(pattern, escape="\\"))
        q = q.filter(or_(*matches))

    total = (q.with_entities(func.count(note.id)).scalar() or 0) if with_total else None
    rows = (
        q.options(*load_options)
        .order_by(*activity_order())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, [NoteFeedRow(*r) for r in rows]
