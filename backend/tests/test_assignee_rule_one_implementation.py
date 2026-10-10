"""Who work may be assigned to is ONE rule (``deps.load_project_assignee``).

It was written three times: ``deps.resolve_project_assignee`` (a finding's
owner on create / promote / update) and an inline copy in each bulk-assign
route (``POST /findings/bulk/assign``, ``POST /hosts/bulk/assign``).  The bulk
routes call the shared function now (2.489.0).

Old == new: ``_old_bulk_rule`` below is the inline copy as it stood, and each
route's answer is compared with it for the people the rule tells apart — a
member, a non-member, an inactive account, a global admin with no membership,
and an id nobody has.

The three copies agreed on WHO is acceptable and differed in one answer, which
each route keeps: for an account that does not exist or is inactive the bulk
routes say 404 "Assignee not found", the finding routes 400 "Assignee is not
an active user".
"""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.deps import (
    BULK_ASSIGNEE_UNKNOWN,
    load_project_assignee,
    resolve_project_assignee,
)
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding
from app.db.models_project import ProjectMembership


def _old_bulk_rule(db, project_id, assignee_user_id):
    """The inline validation both bulk routes carried until 2.489.0:
    ``(status, detail)`` of the refusal, or ``None`` when accepted."""
    assignee = (
        db.query(User)
        .filter(User.id == assignee_user_id, User.is_active.is_(True))
        .first()
    )
    if not assignee:
        return 404, "Assignee not found"
    if assignee.role != UserRole.ADMIN:
        is_member = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == assignee.id,
            )
            .first()
        )
        if not is_member:
            return 400, "Assignee is not a member of this project"
    return None


def _user(db, user_id, username, *, role=UserRole.MEMBER, active=True):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com",
        hashed_password="x", role=role, is_active=active,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def people(db_session, test_project):
    member = _user(db_session, 701, "i4-member")
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role="viewer"))
    inactive = _user(db_session, 703, "i4-inactive", active=False)
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=inactive.id, role="analyst"))
    ids = {
        "member": member.id,
        "non_member": _user(db_session, 702, "i4-outsider").id,
        "inactive_member": inactive.id,
        "global_admin_no_membership": _user(db_session, 704, "i4-admin", role=UserRole.ADMIN).id,
        "inactive_global_admin": _user(
            db_session, 705, "i4-admin-off", role=UserRole.ADMIN, active=False,
        ).id,
        "nobody": 999_704,
    }
    db_session.commit()
    return ids


#: who -> the refusal every copy gave: None = accepted.
EXPECTED = {
    "member": None,
    "non_member": (400, "Assignee is not a member of this project"),
    "inactive_member": "unknown",
    "global_admin_no_membership": None,
    "inactive_global_admin": "unknown",
    "nobody": "unknown",
}


def _refusal(response):
    if response.status_code == 200:
        return None
    return response.status_code, response.json()["detail"]


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_the_bulk_routes_answer_as_their_inline_copies_did(
    client, db_session, test_project, people, who,
):
    host = models.Host(project_id=test_project.id, ip_address="10.74.0.1", state="up")
    finding = Finding(
        project_id=test_project.id, title="I4", severity="high", status="open", source="manual",
    )
    db_session.add_all([host, finding])
    db_session.commit()
    host_id, finding_id, user_id = host.id, finding.id, people[who]
    base = f"/api/v1/projects/{test_project.id}"
    old = _old_bulk_rule(db_session, test_project.id, user_id)
    assert old == (BULK_ASSIGNEE_UNKNOWN if EXPECTED[who] == "unknown" else EXPECTED[who])

    hosts = client.post(
        f"{base}/hosts/bulk/assign", json={"host_ids": [host_id], "assignee_user_id": user_id},
    )
    findings = client.post(
        f"{base}/findings/bulk/assign",
        json={"finding_ids": [finding_id], "assignee_user_id": user_id},
    )
    assert _refusal(hosts) == old, hosts.text
    assert _refusal(findings) == old, findings.text

    db_session.expire_all()
    assigned = db_session.query(models.HostFollow).filter(
        models.HostFollow.host_id == host_id, models.HostFollow.user_id == user_id,
    ).count()
    owner = db_session.get(Finding, finding_id).owner_id
    if old is None:
        assert hosts.json()["affected"] == 1 and findings.json()["affected"] == 1
        assert assigned == 1 and owner == user_id
    else:
        # A refusal wrote nothing.
        assert assigned == 0 and owner is None


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_the_finding_routes_accept_the_same_people_and_keep_their_400(
    client, db_session, test_project, people, who,
):
    user_id = people[who]
    expected = (
        (400, "Assignee is not an active user") if EXPECTED[who] == "unknown" else EXPECTED[who]
    )
    if expected is None:
        assert resolve_project_assignee(db_session, test_project.id, user_id) == user_id
        assert load_project_assignee(db_session, test_project.id, user_id).id == user_id
    else:
        with pytest.raises(HTTPException) as refused:
            resolve_project_assignee(db_session, test_project.id, user_id)
        assert (refused.value.status_code, refused.value.detail) == expected
    # …and through a route that uses it: writing a finding with that owner.
    created = client.post(
        f"/api/v1/projects/{test_project.id}/findings",
        json={"title": f"I4 {who}", "severity": "low", "owner_id": user_id},
    )
    if expected is None:
        assert created.status_code == 201, created.text
        assert created.json()["owner_id"] == user_id
    else:
        assert (created.status_code, created.json()["detail"]) == expected


def test_the_bulk_unassign_names_nobody_and_is_not_validated(client, db_session, test_project):
    """``assignee_user_id: null`` on the findings' bulk route clears the owner;
    there is nobody to check."""
    finding = Finding(
        project_id=test_project.id, title="I4 owned", severity="high", status="open",
        source="manual", owner_id=1,
    )
    db_session.add(finding)
    db_session.commit()
    finding_id = finding.id
    cleared = client.post(
        f"/api/v1/projects/{test_project.id}/findings/bulk/assign",
        json={"finding_ids": [finding_id], "assignee_user_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    db_session.expire_all()
    assert db_session.get(Finding, finding_id).owner_id is None


def test_no_route_carries_its_own_copy_of_the_rule():
    """The bulk routes hold no membership query of their own any more."""
    import inspect

    from app.api.v1.endpoints import findings_bulk, host_bulk

    for module in (findings_bulk, host_bulk):
        source = inspect.getsource(module)
        assert "load_project_assignee(" in source, module.__name__
        assert "ProjectMembership" not in source, module.__name__
