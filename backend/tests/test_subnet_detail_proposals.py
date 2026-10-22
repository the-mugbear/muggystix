"""An agent proposes a subnet's description and site; a person decides (2.522.0).

Prod agent feedback #76 (2026-10-22): an operator asked their agent to set the
site and description of scope subnets, and the agent surface had no way to.
Owner: "Let an agent set a subnet site and description as a proposal but
ensure proposals have a filter for scope proposals so a user can bulk accept
if they wish."  The filter is the kind (``kind=subnet_details``, the page's
"Scope details" chip); the bulk accept is ``POST /proposals/bulk``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import Notification, Project, ProjectMembership, ProjectRole
from app.db.models_proposals import AgentProposal
from app.services import proposal_service


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}, r.json()["agent_session_id"]


def _subnets(db, project, *cidrs):
    scope = db.query(models.Scope).filter(models.Scope.project_id == project.id).first()
    if scope is None:
        scope = models.Scope(project_id=project.id)
        db.add(scope)
        db.flush()
    rows = [models.Subnet(scope_id=scope.id, cidr=c) for c in cidrs]
    db.add_all(rows)
    db.commit()
    return rows


def _propose(client, key, subnets, **extra):
    return client.post("/api/v1/agent/proposals/subnet-details", headers=key, json={"subnets": subnets, **extra})


def _bulk(client, project, ids):
    r = client.post(f"/api/v1/projects/{project.id}/proposals/bulk", json={"ids": ids, "action": "accept"})
    assert r.status_code == 200, r.text
    return r.json()


def _subnet(db, subnet_id):
    db.expire_all()
    return db.get(models.Subnet, subnet_id)


def test_a_proposal_changes_nothing_until_a_person_accepts_it(client, db_session, test_project):
    (a,) = _subnets(db_session, test_project, "10.60.0.0/24")
    a.description = "old words"
    db_session.commit()
    key, sid = _start(client, test_project)
    r = _propose(client, key, [{"cidr": "10.60.0.0/24", "description": "Web tier", "site": "London DC"}],
                 rationale="The operator named them.")
    assert r.status_code == 201, r.text
    (row,) = r.json()["proposals"]
    assert (row["kind"], row["status"], row["subnet_id"]) == ("subnet_details", "pending", a.id)
    assert row["payload"] == {"description": "Web tier", "site": "London DC",
                              "base_description": "old words", "base_site": None}
    assert row["target"]["subnet_cidr"] == "10.60.0.0/24"
    assert row["agent_session_id"] == sid
    assert _subnet(db_session, a.id).description == "old words"

    # The page's filter is the kind.
    listed = client.get(f"/api/v1/projects/{test_project.id}/proposals", params={"kind": "subnet_details"}).json()
    assert [p["id"] for p in listed["items"]] == [row["id"]]
    summary = client.get(f"/api/v1/projects/{test_project.id}/proposals/summary").json()
    assert summary["by_kind"]["subnet_details"] == 1


def test_accepting_writes_what_the_scope_pages_edit_writes(client, db_session, test_project):
    """One write: the proposal's subnet and a subnet edited by hand with the
    same values end the same — the same site row, the same text."""
    a, b = _subnets(db_session, test_project, "10.61.0.0/24", "10.61.1.0/24")
    key, _ = _start(client, test_project)
    (row,) = _propose(client, key, [{"cidr": "10.61.0.0/24", "description": "DMZ", "site": "  Leeds  "}]).json()["proposals"]
    r = client.post(f"/api/v1/projects/{test_project.id}/proposals/{row['id']}/accept", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted" and r.json()["result_finding_id"] is None
    by_hand = client.patch(f"/api/v1/projects/{test_project.id}/scopes/subnets/{b.id}",
                           json={"description": "DMZ", "site": "Leeds"})
    assert by_hand.status_code == 200, by_hand.text
    accepted, edited = _subnet(db_session, a.id), _subnet(db_session, b.id)
    assert (accepted.description, accepted.site) == (edited.description, edited.site) == ("DMZ", "Leeds")
    assert accepted.site_id == edited.site_id is not None
    assert db_session.query(models.Site).filter(models.Site.project_id == test_project.id).count() == 1


def test_a_proposal_may_set_one_field_and_leaves_the_other(client, db_session, test_project):
    (a,) = _subnets(db_session, test_project, "10.62.0.0/24")
    a.description, a.site = "kept", "Kept site"
    db_session.commit()
    key, _ = _start(client, test_project)
    (row,) = _propose(client, key, [{"cidr": "10.62.0.0/24", "site": "New site"}]).json()["proposals"]
    assert "description" not in row["payload"]
    client.post(f"/api/v1/projects/{test_project.id}/proposals/{row['id']}/accept", json={})
    s = _subnet(db_session, a.id)
    assert (s.description, s.site) == ("kept", "New site")


def test_bulk_accept_applies_every_scope_proposal_shown(client, db_session, test_project):
    rows = _subnets(db_session, test_project, *[f"10.63.{i}.0/24" for i in range(5)])
    key, _ = _start(client, test_project)
    proposed = _propose(client, key, [
        {"cidr": s.cidr, "description": f"Segment {i}", "site": "Lab"} for i, s in enumerate(rows)
    ]).json()["proposals"]
    answer = _bulk(client, test_project, [p["id"] for p in proposed])
    assert sorted(answer["decided"]) == sorted(p["id"] for p in proposed)
    assert answer["failed"] == [] and answer["skipped"] == []
    assert [_subnet(db_session, s.id).description for s in rows] == [f"Segment {i}" for i in range(5)]


def test_bulk_accept_never_chooses_between_two_proposals_for_one_subnet(client, db_session, test_project):
    a, b = _subnets(db_session, test_project, "10.64.0.0/24", "10.64.1.0/24")
    key, _ = _start(client, test_project)
    (first,) = _propose(client, key, [{"cidr": a.cidr, "description": "One"}]).json()["proposals"]
    (second,) = _propose(client, key, [{"cidr": a.cidr, "description": "Two"}]).json()["proposals"]
    (other,) = _propose(client, key, [{"cidr": b.cidr, "description": "Fine"}]).json()["proposals"]
    answer = _bulk(client, test_project, [first["id"], second["id"], other["id"]])
    assert answer["decided"] == [other["id"]]
    assert {f["id"] for f in answer["failed"]} == {first["id"], second["id"]}
    assert all(f["detail"] == proposal_service.COMPETING_SUBNET_DETAIL for f in answer["failed"])
    assert _subnet(db_session, a.id).description is None

    # Accepting one supersedes the other: it was proposed against what the subnet held.
    client.post(f"/api/v1/projects/{test_project.id}/proposals/{second['id']}/accept", json={})
    db_session.expire_all()
    assert db_session.get(AgentProposal, first["id"]).status == "superseded"
    assert _subnet(db_session, a.id).description == "Two"


def test_bulk_accept_leaves_a_subnet_edited_after_its_proposal(client, db_session, test_project):
    """The report-text rule for a subnet: one click must not replace an edit
    nobody compared the proposal with.  A single accept still applies it."""
    a, b = _subnets(db_session, test_project, "10.65.0.0/24", "10.65.1.0/24")
    key, _ = _start(client, test_project)
    proposed = _propose(client, key, [
        {"cidr": a.cidr, "description": "Proposed"}, {"cidr": b.cidr, "site": "Proposed site"},
    ]).json()["proposals"]
    client.patch(f"/api/v1/projects/{test_project.id}/scopes/subnets/{a.id}", json={"description": "A person wrote this"})
    # An edit of a field the proposal does not set is not a reason to skip it.
    client.patch(f"/api/v1/projects/{test_project.id}/scopes/subnets/{b.id}", json={"description": "Unrelated"})

    listed = {p["id"]: p for p in client.get(
        f"/api/v1/projects/{test_project.id}/proposals", params={"kind": "subnet_details"}).json()["items"]}
    assert listed[proposed[0]["id"]]["changed_since_proposed"] is True
    assert listed[proposed[0]["id"]]["target"]["subnet_description"] == "A person wrote this"
    assert listed[proposed[1]["id"]]["changed_since_proposed"] is False

    answer = _bulk(client, test_project, [p["id"] for p in proposed])
    assert answer["decided"] == [proposed[1]["id"]]
    assert answer["skipped"] == [{
        "id": proposed[0]["id"], "reason": proposal_service.REWRITTEN_SINCE_PROPOSED,
        "detail": proposal_service.SUBNET_CHANGED_SINCE_PROPOSED_DETAIL,
    }]
    assert _subnet(db_session, a.id).description == "A person wrote this"
    single = client.post(f"/api/v1/projects/{test_project.id}/proposals/{proposed[0]['id']}/accept", json={})
    assert single.status_code == 200, single.text
    assert _subnet(db_session, a.id).description == "Proposed"


def test_a_bad_request_stores_nothing(client, db_session, test_project):
    _subnets(db_session, test_project, "10.66.0.0/24")
    other = Project(name="other-subnets", slug="other-subnets", description="x")
    db_session.add(other)
    db_session.commit()
    _subnets(db_session, other, "172.30.0.0/16")
    key, _ = _start(client, test_project)
    good = {"cidr": "10.66.0.0/24", "description": "ok"}
    cases = [
        ([good, {"cidr": "172.30.0.0/16", "site": "theirs"}], 404),   # another project's subnet
        ([good, {"cidr": "10.99.99.0/24", "site": "x"}], 404),        # not declared
        ([good, {"cidr": "not-a-network", "site": "x"}], 422),
        ([{"cidr": "10.66.0.0/24", "description": "   "}], 422),      # empty text: a person clears
        ([{"cidr": "10.66.0.0/24"}], 422),                            # nothing to set
        ([good, {"cidr": "10.66.0.0/24", "site": "again"}], 422),     # named twice
    ]
    for subnets, status in cases:
        r = _propose(client, key, subnets)
        assert r.status_code == status, (subnets, r.text)
    assert db_session.query(AgentProposal).filter(AgentProposal.project_id == test_project.id).count() == 0


def test_a_single_address_is_named_as_it_is_stored(client, db_session, test_project):
    (a,) = _subnets(db_session, test_project, "10.67.0.5/32")
    key, _ = _start(client, test_project)
    r = _propose(client, key, [{"cidr": "10.67.0.5", "description": "Jump host"}])
    assert r.status_code == 201, r.text
    assert r.json()["proposals"][0]["subnet_id"] == a.id


def test_deleting_the_subnet_deletes_its_proposals(client, db_session, test_project):
    (a,) = _subnets(db_session, test_project, "10.68.0.0/24")
    key, _ = _start(client, test_project)
    (row,) = _propose(client, key, [{"cidr": a.cidr, "description": "x"}]).json()["proposals"]
    r = client.delete(f"/api/v1/projects/{test_project.id}/scopes/subnets/{a.id}")
    assert r.status_code in (200, 204), r.text
    db_session.expire_all()
    assert db_session.get(AgentProposal, row["id"]) is None


def test_an_agent_replaces_its_own_proposal_for_the_same_subnet(client, db_session, test_project):
    a, b = _subnets(db_session, test_project, "10.69.0.0/24", "10.69.1.0/24")
    key, _ = _start(client, test_project)
    (old,) = _propose(client, key, [{"cidr": a.cidr, "description": "typo"}]).json()["proposals"]
    wrong = _propose(client, key, [{"cidr": b.cidr, "description": "x"}], replaces=[old["id"]])
    assert wrong.status_code == 422, wrong.text
    (new,) = _propose(client, key, [{"cidr": a.cidr, "description": "fixed"}], replaces=[old["id"]]).json()["proposals"]
    db_session.expire_all()
    replaced = db_session.get(AgentProposal, old["id"])
    assert (replaced.status, replaced.replaced_by_id) == ("superseded", new["id"])


def test_the_project_admins_are_told_once_per_session(client, db_session, test_project):
    """The scope is the project's: its admins are told, in a notification of
    its own (not the "on no finding yet" one), kept current as the run
    proposes more.  The operator is not told about their own run."""
    admin = User(id=9301, username="scope-admin", email="9301@example.com", full_name="Scope Admin",
                 hashed_password="x", role=UserRole.MEMBER, is_active=True, created_at=datetime.now(timezone.utc))
    db_session.add(admin)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=admin.id, role=ProjectRole.ADMIN.value))
    db_session.commit()
    rows = _subnets(db_session, test_project, "10.70.0.0/24", "10.70.1.0/24", "10.70.2.0/24")
    key, sid = _start(client, test_project)
    _propose(client, key, [{"cidr": rows[0].cidr, "site": "A"}])
    _propose(client, key, [{"cidr": rows[1].cidr, "site": "B"}, {"cidr": rows[2].cidr, "site": "C"}])
    db_session.expire_all()
    notes = db_session.query(Notification).filter(Notification.user_id == admin.id).all()
    assert [(n.source_type, n.source_id) for n in notes] == [(proposal_service.SCOPE_PROPOSAL_SOURCE, sid)]
    assert notes[0].title == "AI proposed descriptions or sites for 3 subnets"


def test_the_agent_lists_its_scope_proposals_by_kind(client, db_session, test_project):
    (a,) = _subnets(db_session, test_project, "10.71.0.0/24")
    key, _ = _start(client, test_project)
    _propose(client, key, [{"cidr": a.cidr, "description": "x"}])
    page = client.get("/api/v1/agent/proposals", headers=key, params={"kind": "subnet_details", "mine": True}).json()
    assert page["total"] == 1 and page["items"][0]["target"]["subnet_cidr"] == a.cidr
