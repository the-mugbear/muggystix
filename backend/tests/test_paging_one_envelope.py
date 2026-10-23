"""``has_more`` is written once (review 2026-10-10, R1f).

The host-test list, the remediation list and a host's remediation timeline
each wrote ``offset + len(rows) < total``, and the agents' door wrote it again
(``agent_schemas.as_agent_page``).  The services now answer the page envelope
(``services/paging.page_envelope``), which is also the agents' ``agent_page``,
and every other ``has_more`` under ``app/`` is ``paging.rows_remain``.

Old == new: on both doors the answers' paging words are what the old
arithmetic gives, page after page, past the end included.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.api.v1.endpoints.agent_schemas import agent_page
from app.db import models
from app.services.paging import PAGE_KEYS, page_envelope

APP = Path(__file__).resolve().parents[1] / "app"


def test_the_agents_helper_is_the_services_envelope():
    assert agent_page is page_envelope


def _old_words(offset, returned, total, limit):
    """The paging words as the old code wrote them."""
    return {"total": total, "limit": limit, "offset": offset, "returned": returned,
            "has_more": offset + returned < total}


@pytest.mark.parametrize("returned,total,limit,offset", [
    (0, 0, 50, 0), (2, 5, 2, 0), (2, 5, 2, 2), (1, 5, 2, 4), (0, 5, 2, 9), (5, 5, 5, 0),
])
def test_the_envelope_is_the_old_arithmetic(returned, total, limit, offset):
    page = page_envelope(list(range(returned)), total=total, limit=limit, offset=offset)
    assert tuple(page) == PAGE_KEYS
    assert {k: v for k, v in page.items() if k != "items"} == _old_words(offset, returned, total, limit)


def test_both_doors_of_the_host_test_list_answer_the_same_envelope(client, db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.75.0.1", state="up")
    db_session.add(host)
    db_session.commit()
    pid = test_project.id
    made = client.post(f"/api/v1/projects/{pid}/host-tests", json={"tests": [
        {"request_key": f"r1f-{i}", "host_id": host.id, "tool": "curl", "description": f"check {i}",
         "rationale": "r1f"} for i in range(5)
    ]})
    assert made.status_code == 201, made.text
    key = client.post(f"/api/v1/projects/{pid}/assist/start", json={}).json()["api_key"]
    for offset in (0, 2, 4, 9):
        people = client.get(f"/api/v1/projects/{pid}/host-tests", params={"limit": 2, "offset": offset}).json()
        agents = client.get("/api/v1/agent/host-tests", params={"limit": 2, "offset": offset},
                            headers={"X-API-Key": key}).json()
        assert people == agents
        returned = len(people["items"])
        assert {k: people[k] for k in PAGE_KEYS if k != "items"} == _old_words(offset, returned, 5, 2)


def _has_more_arithmetic(tree):
    """Comparisons written as the value of a ``has_more`` / ``*_has_more``."""
    found = []
    for node in ast.walk(tree):
        pairs = []
        if isinstance(node, ast.Dict):
            pairs = [(k.value, v) for k, v in zip(node.keys, node.values)
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        elif isinstance(node, ast.Call):
            pairs = [(kw.arg, kw.value) for kw in node.keywords if kw.arg]
        for name, value in pairs:
            if name.endswith("has_more") and isinstance(value, ast.Compare):
                found.append(node.lineno)
    return found


def test_no_has_more_is_computed_outside_the_paging_module():
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "paging.py" and path.parent.name == "services":
            continue
        lines = _has_more_arithmetic(ast.parse(path.read_text()))
        offenders += [f"{path.relative_to(APP)}:{n}" for n in lines]
    assert not offenders, f"has_more computed by hand at {offenders}: use services.paging"
