"""What counts as WORK on a host: the one list.

A host can be deleted with the scan that introduced it — by the cleanup of an
import that did not finish, and by a scan delete by hand.  Both must know
whether a person or an agent has put something on the host first:

* the cleanup KEEPS such a host (``ingestion_service._host_is_only_this_attempts``
  takes ``no_work_conditions`` as part of its guards);
* the hand delete names those hosts in its preview and refuses until the
  request confirms (``work_on_hosts``).

Both read ``work_kinds``, so a new table that holds work on a host is added
in exactly one place — an entry there — and is then kept by the cleanup and
counted by the warning.
"""
from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Sequence

from sqlalchemy import Integer, any_, bindparam, exists, func, literal, select, union_all
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, aliased


class WorkKind(NamedTuple):
    """One kind of work.  ``host_id`` is the column that names the host on
    the rows that are the work; ``conditions`` narrow (and join) those rows.
    Each row counted is one piece of work."""

    kind: str
    host_id: Any
    conditions: tuple = ()


def work_kinds() -> List[WorkKind]:
    """Every kind of work a host can carry, in a fixed order.  ``kind`` is
    the name the deletion preview sends to the client.

    Only what a person or an agent DID belongs here.  What another scan or
    source attached to the host (history, ports, scanner rows, web rows) is
    provenance (``ingestion_service.host_provenance_conditions``): both the
    cleanup and a hand delete keep a host another scan or source has."""
    from app.db import models
    from app.db.models_findings import FindingHost
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import AgentProposal, EvidenceRecord
    from app.db.models_remediation import RemediationEvent
    from app.db.models_vulnerability import Vulnerability

    proposed_vuln = aliased(Vulnerability)
    named_host = aliased(models.Host)

    return [
        WorkKind("notes", models.Annotation.host_id),
        WorkKind("reviews", models.HostFollow.host_id),
        WorkKind("tags", models.HostTagAssignment.host_id),
        WorkKind("findings", FindingHost.host_id),
        WorkKind("tests", HostTest.host_id),
        WorkKind("evidence", EvidenceRecord.host_id),
        # A remediation note needs only a host, not a finding on it.
        WorkKind("remediation_entries", RemediationEvent.host_id),
        # A proposal about one of the host's scanner observations.
        WorkKind("proposals", proposed_vuln.host_id, (AgentProposal.vulnerability_id == proposed_vuln.id,)),
        # A host name someone typed in place of the scanner's.
        WorkKind("corrections", named_host.id, (named_host.hostname_source == "operator",)),
    ]


def no_work_conditions(host_id) -> List[Any]:
    """One ``NOT EXISTS`` per kind, correlated to ``host_id``: the host
    carries no work.  The cleanup's guard."""
    return [
        ~exists().where(entry.host_id == host_id, *entry.conditions)
        for entry in work_kinds()
    ]


def work_on_hosts(db: Session, host_ids: Sequence[int]) -> Dict[int, Dict[str, int]]:
    """``{host id: {kind: how many}}`` for the hosts among ``host_ids`` that
    carry work; a host with none is absent and a kind with none is absent
    from its host.  ONE statement whatever the number of hosts: a grouped
    count per kind, unioned, with the ids as one array parameter."""
    if not host_ids:
        return {}
    ids = bindparam("host_ids", list(host_ids), type_=ARRAY(Integer))
    counts = union_all(*[
        select(literal(entry.kind).label("kind"), entry.host_id.label("host_id"), func.count().label("n"))
        .where(entry.host_id == any_(ids), *entry.conditions)
        .group_by(entry.host_id)
        for entry in work_kinds()
    ])
    found: Dict[int, Dict[str, int]] = {}
    for kind, host_id, n in db.execute(counts):
        if n:
            found.setdefault(host_id, {})[kind] = int(n)
    return found
