"""Per-project RBAC on shared-state mutation endpoints.

viewer/auditor are read-only project roles (models_project.ProjectRole), but
the host-assign, bulk-assign, and tag-mutation endpoints historically gated on
*membership* only (``get_current_project``) — so a viewer could assign hosts,
fire notifications/webhooks, and edit tags.  These tests pin the fix: those
routes require analyst+, while a read (list tags) stays open to a viewer.

The default ``client`` fixture authenticates as a GLOBAL admin, which bypasses
``require_project_role`` — so here we override ``get_current_user`` to a plain
member and drive their per-project role via a ProjectMembership row.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import models
from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership
from app.api.v1.endpoints.auth import get_current_user


@pytest.fixture
def member(db_session):
    """A non-admin user (global role 'member'); per-project role comes from
    the membership row each test creates."""
    user = User(
        id=2,  # explicit: test_user is id=1 and doesn't bump the sequence
        username="member-user",
        email="member@example.com",
        full_name="Member User",
        hashed_password="x",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def member_client(db_session, member):
    """A TestClient authenticated as ``member`` (not the admin test_user)."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: member
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _set_role(db_session, project, member, role):
    db_session.query(ProjectMembership).filter(
        ProjectMembership.project_id == project.id,
        ProjectMembership.user_id == member.id,
    ).delete()
    db_session.add(ProjectMembership(project_id=project.id, user_id=member.id, role=role))
    db_session.commit()


def _host(db_session, project):
    h = models.Host(ip_address="10.9.9.9", state="up", project_id=project.id)
    db_session.add(h)
    db_session.commit()
    db_session.refresh(h)
    return h


def _base(project):
    return f"/api/v1/projects/{project.id}/hosts"


def test_viewer_cannot_bulk_tag(member_client, db_session, test_project, member):
    """v2.242.0 — POST /tags retired; /bulk/tags is the tag-write surface."""
    _set_role(db_session, test_project, member, "viewer")
    host = _host(db_session, test_project)
    resp = member_client.post(
        f"{_base(test_project)}/bulk/tags",
        json={"host_ids": [host.id], "names": ["prod"], "action": "add"},
    )
    assert resp.status_code == 403


def test_auditor_cannot_bulk_assign(member_client, db_session, test_project, member):
    _set_role(db_session, test_project, member, "auditor")
    host = _host(db_session, test_project)
    resp = member_client.post(
        f"{_base(test_project)}/bulk/assign",
        json={"host_ids": [host.id], "assignee_user_id": member.id},
    )
    assert resp.status_code == 403


def test_viewer_can_still_list_tags(member_client, db_session, test_project, member):
    _set_role(db_session, test_project, member, "viewer")
    resp = member_client.get(f"{_base(test_project)}/tags")
    assert resp.status_code == 200


def test_viewer_can_unassign_themselves(member_client, db_session, test_project, member):
    """A viewer assigned to a host must be able to remove their OWN assignment,
    even though assigning is analyst+ (bug: no unassign path existed)."""
    from datetime import datetime, timezone
    from app.db.models import HostFollow, FollowStatus

    _set_role(db_session, test_project, member, "viewer")
    host = _host(db_session, test_project)
    db_session.add(HostFollow(
        host_id=host.id, user_id=member.id, status=FollowStatus.IN_REVIEW,
        assigned_at=datetime.now(timezone.utc), assigned_by_id=member.id,
    ))
    db_session.commit()

    resp = member_client.post(
        f"{_base(test_project)}/bulk/unassign", json={"host_ids": [host.id]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 1
    follow = db_session.query(HostFollow).filter_by(host_id=host.id, user_id=member.id).one()
    assert follow.assigned_at is None
    # The follow row itself survives — unassigning isn't unfollowing.
    assert follow.status == FollowStatus.IN_REVIEW


def test_assigned_predicate_resolves_username(db_session, test_project, member):
    """The assigned/assignee filter accepts a username, not just a user id
    (ids aren't surfaced in the UI)."""
    from datetime import datetime, timezone
    from app.db.models import HostFollow, FollowStatus
    from app.services.host_query_predicates import assigned_predicate

    host = _host(db_session, test_project)
    db_session.add(HostFollow(
        host_id=host.id, user_id=member.id, status=FollowStatus.IN_REVIEW,
        assigned_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    # By username (the reported case) resolves to the same host as by id.
    pred_name = assigned_predicate(db_session, member.username, member)
    pred_id = assigned_predicate(db_session, str(member.id), member)
    assert pred_name is not None and pred_id is not None
    ids_by_name = {h.id for h in db_session.query(models.Host).filter(pred_name).all()}
    ids_by_id = {h.id for h in db_session.query(models.Host).filter(pred_id).all()}
    assert ids_by_name == ids_by_id == {host.id}
    # An unknown username yields no filter (None), not a crash.
    assert assigned_predicate(db_session, "no-such-user", member) is None


def test_analyst_can_bulk_tag(member_client, db_session, test_project, member):
    _set_role(db_session, test_project, member, "analyst")
    host = _host(db_session, test_project)
    resp = member_client.post(
        f"{_base(test_project)}/bulk/tags",
        json={"host_ids": [host.id], "names": ["prod"], "action": "add"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected"] == 1


def test_viewer_cannot_rename_or_delete_a_tag(
    member_client, db_session, test_project, member,
):
    """RBAC on the surviving tag-management routes, which the new UI uses.

    The tag is seeded directly rather than through the admin `client`
    fixture: `client` and `member_client` both override get_current_user, so
    a test that requests both authenticates as whichever was built last.
    """
    from app.db.models import HostTag

    tag = HostTag(project_id=test_project.id, name="prod", created_by_id=member.id)
    db_session.add(tag)
    db_session.commit()
    tag_id = tag.id
    _set_role(db_session, test_project, member, "viewer")
    assert member_client.patch(
        f"{_base(test_project)}/tags/{tag_id}", json={"name": "nope"},
    ).status_code == 403
    assert member_client.delete(
        f"{_base(test_project)}/tags/{tag_id}",
    ).status_code == 403


def test_require_project_role_rejects_unknown_role_at_construction():
    """A typo'd role must fail LOUD when the dependency is built, not silently
    grant access at request time.  ``check_permissions`` resolves an unknown
    role to level 0 (gate passes for everyone), so the factory coerces to the
    ``ProjectRole`` enum up front and ``ProjectRole("analist")`` raises."""
    from app.api.deps import require_project_role
    from app.db.models_project import ProjectRole

    with pytest.raises(ValueError):
        require_project_role("analist")  # typo

    # The valid forms (enum and its string value) both construct cleanly.
    require_project_role(ProjectRole.ANALYST)
    require_project_role("analyst")


# ---------------------------------------------------------------------------
# Review remediation: shared assignee validation + explicit-null owner clear (#4)
# ---------------------------------------------------------------------------

def test_resolve_project_assignee_rules(db_session, test_project, member):
    """The shared validator: None passes (unassignment); an outsider or inactive
    user is rejected 400; a project member is accepted."""
    import pytest
    from fastapi import HTTPException
    from app.api.deps import resolve_project_assignee

    assert resolve_project_assignee(db_session, test_project.id, None) is None
    # member is not in the project yet → rejected.
    with pytest.raises(HTTPException) as e:
        resolve_project_assignee(db_session, test_project.id, member.id)
    assert e.value.status_code == 400
    # add to project → accepted.
    _set_role(db_session, test_project, member, "analyst")
    assert resolve_project_assignee(db_session, test_project.id, member.id) == member.id
    # inactive → rejected.
    member.is_active = False
    db_session.commit()
    with pytest.raises(HTTPException):
        resolve_project_assignee(db_session, test_project.id, member.id)


def test_finding_owner_explicit_null_clears(member_client, db_session, test_project, member):
    """PATCH owner_id: null actually clears ownership (was silently ignored)."""
    from app.db.models_findings import Finding
    _set_role(db_session, test_project, member, "analyst")
    f = Finding(project_id=test_project.id, title="t", severity="high",
                status="open", source="manual", owner_id=member.id)
    db_session.add(f)
    db_session.commit()

    resp = member_client.patch(
        f"/api/v1/projects/{test_project.id}/findings/{f.id}", json={"owner_id": None},
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(f)
    assert f.owner_id is None


def test_finding_owner_rejects_non_member(member_client, db_session, test_project, member):
    """Setting an owner who isn't an active project member is refused (400)."""
    from app.db.models_findings import Finding
    from app.db.models_auth import User, UserRole
    _set_role(db_session, test_project, member, "analyst")
    outsider = User(id=99, username="outsider", email="o@x.co", full_name="O",
                    hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True)
    db_session.add(outsider)
    f = Finding(project_id=test_project.id, title="t", severity="high",
                status="open", source="manual")
    db_session.add(f)
    db_session.commit()

    resp = member_client.patch(
        f"/api/v1/projects/{test_project.id}/findings/{f.id}",
        json={"owner_id": outsider.id},
    )
    assert resp.status_code == 400, resp.text


def test_dns_lookup_returns_dict_and_persists(member_client, db_session, test_project, member, monkeypatch):
    """DNS lookup returns the record dict (200, not a 500 from the old `list`
    contract) and commits the staged records (was rolled back on session close)."""
    from app.db import models
    from app.services.dns_service import DNSService
    _set_role(db_session, test_project, member, "analyst")

    def fake_get_dns_records(self, hostname, record_types=None):
        self.db.add(models.DNSRecord(
            domain=hostname, record_type="A", value="1.2.3.4", ttl=60,
            project_id=self.project_id,
        ))
        return {"A": ["1.2.3.4"]}

    monkeypatch.setattr(DNSService, "get_dns_records", fake_get_dns_records)
    resp = member_client.post(f"/api/v1/projects/{test_project.id}/dns/lookup/example.com")
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"] == {"A": ["1.2.3.4"]}
    # Persisted (a fresh query sees it — proves the commit).
    assert db_session.query(models.DNSRecord).filter_by(
        project_id=test_project.id, domain="example.com").count() == 1
