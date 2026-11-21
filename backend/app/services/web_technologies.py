"""A web interface's technologies — one definition of "current" for every reader.

A fingerprinting tool (httpx, WhatWeb) reports the technologies it saw at a
URL.  They are kept as rows (``web_interface_technologies``), one per name —
written by the model itself whenever a row's ``technologies`` is set
(``app.db.models``: the listeners under ``WebInterfaceTechnology``), so no
writer has a second step — and four readers ask about them: the ``tech:`` and
``techis:`` words of the host query language, the Hosts filter's Technologies
facet, the query suggestions and Patterns' technology monoculture.  Each used
to read the JSON array on the web-interface row with its own idea of which
rows count (every historical row, as a substring over the array's text; or
the latest row per URL, in Python), so the monoculture's count and the filter
beside it could not agree (review 2026-11-20, T1).

**Current** is the one rule, the same as the certificate and weak-TLS
conditions (``host_condition_sets``): a technology counts when the LATEST
observation of technologies at that host's URL reports it.  A later scan that
no longer reports nginx 1.14 retires it.

Names are stored trimmed and cut at ``NAME_MAX`` — what the monoculture
always counted by — so ``techis:"<name>"`` is exactly the hosts it counts.
"""
from __future__ import annotations

from sqlalchemy import exists
from sqlalchemy.orm import Query, Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.db import models
from app.db.models import TECHNOLOGY_NAME_MAX, WebInterface, WebInterfaceTechnology

NAME_MAX = TECHNOLOGY_NAME_MAX


def _is_current(row) -> ColumnElement:
    """``row`` is the latest observation of technologies at its host's URL."""
    from app.services.host_condition_sets import _later

    newer = aliased(WebInterface)
    return ~exists().where(
        newer.host_id == row.host_id, newer.url == row.url, newer.technologies_stated.is_(True),
        _later(newer, row, "last_seen"),
    )


def host_has_technology(match) -> ColumnElement:
    """The host currently shows a technology whose name ``match(name_column)``
    accepts.  Correlated on ``Host.id``."""
    tech, row = aliased(WebInterfaceTechnology), aliased(WebInterface)
    return exists().where(
        tech.host_id == models.Host.id, match(tech.name),
        row.id == tech.web_interface_id, _is_current(row),
    )


def current_technologies(db: Session, *columns) -> "tuple[Query, type]":
    """``(query, tech)``: a query over the CURRENT technology rows joined to
    their hosts, selecting each of ``columns`` (a column, or a function of the
    technology alias) — for a reader that groups them (the facet, the
    suggestions, the monoculture).  The caller filters by project or host
    scope and groups."""
    tech, row = aliased(WebInterfaceTechnology), aliased(WebInterface)
    query = (
        db.query(*[column(tech) if callable(column) else column for column in columns])
        .select_from(tech)
        .join(row, row.id == tech.web_interface_id)
        .join(models.Host, models.Host.id == tech.host_id)
        .filter(_is_current(row))
    )
    return query, tech
