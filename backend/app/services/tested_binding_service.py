"""TESTED evidence: keep ``dns_records`` in step with one execution result
(v2.325.0 — extracted so the online endpoint and the offline bundle import
share ONE rule and neither owns the transaction).

A TESTED observation says "an EXECUTED command against this named endpoint
reached THIS address".  It is recorded only when the result row establishes
both facts — ``status == executed`` AND the agent reported ``observed_ip`` —
and never inferred from the inventory address (rotating DNS is exactly why
that assumption is unsafe).  It is keyed to the result
(``dns_records.exec_result_id``), so re-recording replaces and a downgrade to
skipped withdraws the evidence.

``sync_tested_binding`` flushes but does not commit; the caller owns the
transaction (the agent endpoint commits per request, the bundle importer
commits once per file).
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db import models
from app.db.models import DNS_OBS_TESTED
from app.db.models_agent import TestExecutionStatus
from app.services.dns_name_service import record_observation

logger = logging.getLogger(__name__)


def sync_tested_binding(db: Session, entry, result) -> bool:
    """Make the TESTED observation for ``result`` match the result row.
    Returns True when an observation is present afterwards.  Requires
    ``result.id`` (flush first)."""
    if result is None or result.id is None:
        raise ValueError("sync_tested_binding needs a flushed result row")
    db.query(models.DNSRecord).filter(models.DNSRecord.exec_result_id == result.id).delete(
        synchronize_session=False,
    )
    if not (
        result.status == TestExecutionStatus.EXECUTED.value
        and result.observed_ip
        and entry is not None
        and entry.name_id is not None
        and entry.target_name is not None
        and entry.host is not None
    ):
        db.flush()
        return False
    record_observation(
        db, project_id=entry.host.project_id, name=entry.target_name.fqdn,
        record_type=DNS_OBS_TESTED, value=result.observed_ip,
        exec_result_id=result.id, observed_at=result.executed_at,
    )
    db.flush()
    return True
