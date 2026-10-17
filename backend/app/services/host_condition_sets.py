"""Per-condition host-id sets — the single source of truth for "which hosts in
this project exhibit weakness X".

Two surfaces read these and must never disagree:

* **Systemic insights** (``systemic_insight_service``) intersects each set with
  its in-scope host set and measures how widely the weakness spreads.
* **The /hosts filter DSL** (``has:eol`` / ``has:smb_unsigned`` /
  ``has:weak_auth`` / ``has:cert_issue`` / ``has:cleartext``) turns the same
  sets into a drill-down so an analyst can jump from "SMB signing is an estate
  blind spot — 40 hosts" straight to those 40 hosts.

Keeping the judgments here once means the manager's headline count and the
analyst's drill-down are literally the same computation.  The non-trivial bits
— the EOL regex catalog, latest-observation-wins for TLS certs and NetExec auth
— live here so they can't drift between the two views.

Each ``*_host_ids`` function returns the set of ``Host.id`` for EVERY host in
the project that exhibits the condition; systemic intersects it with the
in-scope set.  The DSL takes the ``*_condition`` the set is built from (a
condition on ``Host``, never an id list) and ``AND``\\s it with the user's
other predicates.  The simple column
conditions (SMB signing, cleartext ports) are *also* expressed directly in SQL
by ``host_query_predicates`` for index-friendly filtering at scale — those are
trivially equivalent to the queries here (same column / same port set), so there
is no judgment to drift; this module owns the shared ``CLEARTEXT_PORTS`` const.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Collection, Optional, Set

from sqlalchemy import exists, false, func, literal, or_, tuple_
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.db import models
from app.db.models import WebInterface
from app.db.models_confidence import NetexecResult
from app.services.os_eol import match_eol_os
from app.services.subnet_insight_service import _EPOCH, _WEAK_USERNAMES

# Cleartext-credential ports (credentials observable on the wire) —
# Telnet / FTP / POP3 / IMAP.  Shared so the DSL predicate and the systemic
# view filter the identical port set.
CLEARTEXT_PORTS = {21, 23, 110, 143}


def _only(query, column, host_ids: Optional[Collection[int]]):
    """``host_ids`` narrows a set function to those hosts (a page being
    labelled); None is the whole project.  The judgment is per host, so the
    answer for a host is the same either way."""
    return query if host_ids is None else query.filter(column.in_(list(host_ids)))


def _hosts_where(db: Session, project_id: int, condition: ColumnElement,
                 host_ids: Optional[Collection[int]] = None) -> Set[int]:
    """The ids of the project's hosts the condition holds for — the set form
    of a condition the Hosts filter uses as it is (one judgment, two uses)."""
    return {
        hid for (hid,) in _only(db.query(models.Host.id), models.Host.id, host_ids)
        .filter(models.Host.project_id == project_id, condition)
    }


def eol_os_condition(db: Session, project_id: int, host_ids: Optional[Collection[int]] = None) -> ColumnElement:
    """Host whose ``os_name`` matches the end-of-life OS catalog.

    The EOL regex catalog runs over the project's DISTINCT ``os_name`` values
    (far fewer than hosts); the answer is a condition on the column, never a
    list of host ids."""
    distinct_os = (
        _only(db.query(models.Host.os_name), models.Host.id, host_ids)
        .filter(
            models.Host.project_id == project_id,
            models.Host.os_name.isnot(None),
            models.Host.os_name != "",
        )
        .distinct()
        .all()
    )
    eol_names = [name for (name,) in distinct_os if match_eol_os(name) is not None]
    return models.Host.os_name.in_(eol_names) if eol_names else false()


def eol_os_host_ids(
    db: Session, project_id: int, host_ids: Optional[Collection[int]] = None,
) -> Set[int]:
    """Hosts whose ``os_name`` matches the end-of-life OS catalog."""
    return _hosts_where(db, project_id, eol_os_condition(db, project_id, host_ids), host_ids)


# ---------------------------------------------------------------------------
# Latest observation wins (comprehensive review 2026-10-16, A1)
#
# A web interface is a row per scan, a NetExec result a row per run: a
# weakness is judged on the LATEST observation of each thing (a host's URL;
# a host's protocol and port), so a re-scan that shows it fixed retires it.
# This was a loop over every row of the project in Python, and its answer
# reached the Hosts filter as a literal list of host ids — on every filtered
# request.  It is a correlated EXISTS now: a row of the host, observed, for
# which the weakness holds, with no LATER observed row of the same thing.
#
# "Later": the newer timestamp, a missing one being the oldest; on equal
# timestamps the higher row id (the row written last) — the tie rule, stated
# (the loop kept whichever row the database happened to return last).
# ---------------------------------------------------------------------------

def _later(newer, row, stamp: str) -> ColumnElement:
    epoch = literal(_EPOCH, type_=getattr(row, stamp).type)
    return tuple_(func.coalesce(getattr(newer, stamp), epoch), newer.id) > tuple_(
        func.coalesce(getattr(row, stamp), epoch), row.id,
    )


def _cert_observed(w) -> ColumnElement:
    return or_(w.cert_not_after.isnot(None), w.cert_self_signed.isnot(None))


def cert_issue_condition(now: Optional[datetime] = None) -> ColumnElement:
    """Host whose LATEST cert observation per URL is expired or self-signed
    (``cert_fields.cert_issue_from_columns``: expired first, then
    self-signed).  Correlated on ``Host.id``."""
    now = now or datetime.now(timezone.utc)
    row, newer = aliased(WebInterface), aliased(WebInterface)
    return exists().where(
        row.host_id == models.Host.id,
        _cert_observed(row),
        or_(row.cert_not_after < now, row.cert_self_signed.is_(True)),
        ~exists().where(
            newer.host_id == row.host_id, newer.url == row.url, _cert_observed(newer),
            _later(newer, row, "last_seen"),
        ),
    )


def weak_tls_condition() -> ColumnElement:
    """Host whose LATEST TLS observation per URL offers a weak protocol
    (SSLv2 / SSLv3 / TLS 1.0 / 1.1); a URL whose protocols were not
    enumerated (NULL) is no observation.  Correlated on ``Host.id``."""
    row, newer = aliased(WebInterface), aliased(WebInterface)
    return exists().where(
        row.host_id == models.Host.id,
        row.tls_weak_protocol.is_(True),
        ~exists().where(
            newer.host_id == row.host_id, newer.url == row.url, newer.tls_weak_protocol.isnot(None),
            _later(newer, row, "last_seen"),
        ),
    )


def weak_auth_condition() -> ColumnElement:
    """Host where the LATEST NetExec login per (protocol, port) succeeded as
    an EXPLICIT guest / anonymous / null-session identity
    (``subnet_insight_service._is_weak_user``: an unknown username never
    counts).  A banner or listing row (no ``auth_success``) is no login.
    Correlated on ``Host.id``."""
    row, newer = aliased(NetexecResult), aliased(NetexecResult)
    return exists().where(
        row.host_id == models.Host.id,
        row.auth_success.is_(True),
        func.lower(func.btrim(row.username)).in_(sorted(_WEAK_USERNAMES)),
        ~exists().where(
            newer.host_id == row.host_id,
            newer.protocol == row.protocol,
            newer.port.isnot_distinct_from(row.port),
            newer.auth_success.isnot(None),
            _later(newer, row, "discovered_at"),
        ),
    )


def smb_unsigned_host_ids(db: Session, project_id: int) -> Set[int]:
    """Hosts that do not require SMB signing (``not_required`` or
    ``disabled`` — relayable; v2.387.0, was ``disabled`` only)."""
    from app.services import smb_signing as smb_signing_states

    return {
        hid
        for (hid,) in db.query(models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            models.Host.smb_signing.in_(smb_signing_states.RELAYABLE),
        )
        .all()
    }


def cleartext_host_ids(db: Session, project_id: int) -> Set[int]:
    """Hosts with at least one OPEN cleartext-credential port."""
    return {
        hid
        for (hid,) in db.query(models.Port.host_id)
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            models.Port.state == "open",
            models.Port.port_number.in_(CLEARTEXT_PORTS),
        )
        .distinct()
        .all()
    }


def cert_issue_host_ids(
    db: Session, project_id: int, now: Optional[datetime] = None,
    *, host_ids: Optional[Collection[int]] = None,
) -> Set[int]:
    """Hosts whose LATEST cert observation per URL is expired or self-signed
    (``cert_issue_condition``)."""
    return _hosts_where(db, project_id, cert_issue_condition(now), host_ids)


def weak_tls_host_ids(
    db: Session, project_id: int, host_ids: Optional[Collection[int]] = None,
) -> Set[int]:
    """Hosts whose LATEST TLS observation per URL offers a weak protocol
    (``weak_tls_condition``)."""
    return _hosts_where(db, project_id, weak_tls_condition(), host_ids)


def weak_auth_host_ids(
    db: Session, project_id: int, host_ids: Optional[Collection[int]] = None,
) -> Set[int]:
    """Hosts where the LATEST NetExec login per (protocol, port) was a weak
    identity's success (``weak_auth_condition``)."""
    return _hosts_where(db, project_id, weak_auth_condition(), host_ids)
