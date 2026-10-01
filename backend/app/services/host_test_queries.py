"""Shared SQL definitions for host testing coverage; no cached state."""
from sqlalchemy import select

from app.db.models_host_tests import ACTIVE_TEST_STATUSES, TESTED_OUTCOMES, HostTest
from app.db.models_proposals import EvidenceRecord


def planned_host_ids(project_id):
    return select(HostTest.host_id).where(HostTest.project_id == project_id, HostTest.status.in_(ACTIVE_TEST_STATUSES))


def tested_host_ids(project_id):
    return select(EvidenceRecord.host_id).where(EvidenceRecord.project_id == project_id, EvidenceRecord.outcome.in_(TESTED_OUTCOMES))
