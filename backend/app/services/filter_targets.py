"""A list filter that names a record must name one of this project's.

A list filtered by a host, finding, host test or agent session that is not in
the project answered 200 with no rows — "no evidence on that host" said about a
host that does not exist here, while the record's own read answered 404 for the
same id (agent feedback #30, 2026-10-02).  The check lived on one route of one
door; it is here so that the SERVICE a list is built by refuses for every door
(review 2026-10-23b, AGT-3).  Another project's record and a nonexistent one
are the same answer: a 404 naming the filter.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_findings import Finding
from app.db.models_host_tests import HostTest

_TARGETS = (
    ("host_id", models.Host, "Host not found in this project"),
    ("finding_id", Finding, "Finding not found in this project"),
    ("host_test_id", HostTest, "Host test not found in this project"),
    ("agent_session_id", AgentSession, "Agent session not found in this project"),
)


def require_in_project(
    db: Session, project_id: int, *,
    host_id: Optional[int] = None, finding_id: Optional[int] = None,
    host_test_id: Optional[int] = None, agent_session_id: Optional[int] = None,
) -> None:
    """404 for the first given id that names nothing in the project; one
    primary-key probe per id given, none when no filter is."""
    given = {
        "host_id": host_id, "finding_id": finding_id,
        "host_test_id": host_test_id, "agent_session_id": agent_session_id,
    }
    for name, model, detail in _TARGETS:
        value = given[name]
        if value is None:
            continue
        found = (
            db.query(model.id)
            .filter(model.id == value, model.project_id == project_id)
            .first()
        )
        if found is None:
            raise HTTPException(status_code=404, detail=detail)
