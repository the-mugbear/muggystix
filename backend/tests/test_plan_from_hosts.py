"""A Hosts-page selection becomes a test plan (v2.345.0; design review item 6).

Pins:

* a new plan from a selection records ``source_kind='manual_hosts'`` +
  ``source_host_ids`` (the fixed list), gets one entry per host with the
  operator's rationale, and says why in its description;
* adding to an existing draft skips hosts already in it and reports them;
* only a draft accepts a selection;
* hosts from another project are excluded and counted, never silently;
* ``dry_run`` reports the same numbers and writes nothing;
* hosts already carried by an approved / in-progress / completed plan are
  reported as ``planned_elsewhere``;
* the agent's planning context is restricted to the fixed list and says so.
"""
from app.db import models
from app.db.models_agent import TestPlan, TestPlanEntry, TestPlanStatus


def _host(db, project_id, ip, with_port=True):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    if with_port:
        db.add(models.Port(host_id=h.id, port_number=22, protocol="tcp", state="open"))
        db.flush()
    return h


def _url(project_id):
    return f"/api/v1/projects/{project_id}/test-plans/from-hosts"


def test_new_plan_from_selection_records_fixed_list_and_rationale(client, db_session, test_project):
    a = _host(db_session, test_project.id, "10.9.0.1")
    b = _host(db_session, test_project.id, "10.9.0.2")
    db_session.commit()

    r = client.post(_url(test_project.id), json={
        "host_ids": [a.id, b.id, a.id],
        "title": "DMZ web tier",
        "rationale": "Both expose admin interfaces on odd ports.",
        "selection_summary": "2 hosts checked on the Hosts page",
        "priority": "high",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_plan"] is True
    assert body["dry_run"] is False
    assert (body["requested"], body["added"], body["already_in_plan"], body["not_in_project"]) == (2, 2, 0, 0)

    plan = db_session.get(TestPlan, body["plan"]["id"])
    assert plan.status == TestPlanStatus.DRAFT.value
    assert plan.source_kind == "manual_hosts"
    assert sorted(plan.source_host_ids) == sorted([a.id, b.id])
    assert "fixed selection of 2 hosts" in plan.description
    assert "2 hosts checked on the Hosts page" in plan.description
    assert "Why these hosts: Both expose admin interfaces on odd ports." in plan.description

    entries = db_session.query(TestPlanEntry).filter(TestPlanEntry.test_plan_id == plan.id).all()
    assert sorted(e.host_id for e in entries) == sorted([a.id, b.id])
    assert {e.priority for e in entries} == {"high"}
    assert {e.test_phase for e in entries} == {"enumeration"}
    assert all(e.rationale == "Both expose admin interfaces on odd ports." for e in entries)
    assert all(e.proposed_tests == [] for e in entries)


def test_add_to_existing_draft_skips_hosts_already_in_it(client, db_session, test_project):
    a = _host(db_session, test_project.id, "10.9.1.1")
    b = _host(db_session, test_project.id, "10.9.1.2")
    db_session.commit()
    first = client.post(_url(test_project.id), json={
        "host_ids": [a.id], "title": "draft", "rationale": "first pass",
    }).json()
    plan_id = first["plan"]["id"]

    r = client.post(_url(test_project.id), json={
        "host_ids": [a.id, b.id], "plan_id": plan_id, "rationale": "second pass",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_plan"] is False
    assert body["plan"]["id"] == plan_id
    assert (body["requested"], body["added"], body["already_in_plan"]) == (2, 1, 1)
    hosts_in_plan = sorted(
        h for (h,) in db_session.query(TestPlanEntry.host_id).filter(TestPlanEntry.test_plan_id == plan_id)
    )
    assert hosts_in_plan == sorted([a.id, b.id])
    # Provenance of the existing plan is left as it was.
    plan = db_session.get(TestPlan, plan_id)
    assert plan.source_host_ids == [a.id]


def test_only_a_draft_accepts_a_selection(client, db_session, test_project, test_plan):
    a = _host(db_session, test_project.id, "10.9.2.1")
    test_plan.status = TestPlanStatus.APPROVED.value
    db_session.commit()
    r = client.post(_url(test_project.id), json={
        "host_ids": [a.id], "plan_id": test_plan.id, "rationale": "x",
    })
    assert r.status_code == 409
    assert "draft" in r.json()["detail"]


def test_title_required_for_a_new_plan(client, db_session, test_project):
    a = _host(db_session, test_project.id, "10.9.3.1")
    db_session.commit()
    r = client.post(_url(test_project.id), json={"host_ids": [a.id], "rationale": "x"})
    assert r.status_code == 422


def test_foreign_hosts_are_excluded_and_counted(client, db_session, test_project):
    from app.db.models_project import Project
    other = Project(name="other", slug="other", description="")
    db_session.add(other)
    db_session.flush()
    mine = _host(db_session, test_project.id, "10.9.4.1")
    theirs = _host(db_session, other.id, "10.9.4.2")
    db_session.commit()

    r = client.post(_url(test_project.id), json={
        "host_ids": [mine.id, theirs.id], "title": "t", "rationale": "x",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["added"], body["not_in_project"]) == (1, 1)
    plan = db_session.get(TestPlan, body["plan"]["id"])
    assert plan.source_host_ids == [mine.id]

    only_theirs = client.post(_url(test_project.id), json={
        "host_ids": [theirs.id], "title": "t2", "rationale": "x",
    })
    assert only_theirs.status_code == 422


def test_dry_run_reports_without_writing(client, db_session, test_project):
    a = _host(db_session, test_project.id, "10.9.5.1")
    b = _host(db_session, test_project.id, "10.9.5.2")
    db_session.commit()
    # b is already in an approved plan → planned_elsewhere.
    approved = client.post(_url(test_project.id), json={
        "host_ids": [b.id], "title": "approved", "rationale": "x",
    }).json()["plan"]["id"]
    db_session.get(TestPlan, approved).status = TestPlanStatus.APPROVED.value
    db_session.commit()
    plans_before = db_session.query(TestPlan).count()
    entries_before = db_session.query(TestPlanEntry).count()

    r = client.post(_url(test_project.id), json={
        "host_ids": [a.id, b.id], "title": "preview", "rationale": "(preview)", "dry_run": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["plan"] is None
    assert body["created_plan"] is False
    assert (body["requested"], body["added"], body["planned_elsewhere"]) == (2, 2, 1)
    assert db_session.query(TestPlan).count() == plans_before
    assert db_session.query(TestPlanEntry).count() == entries_before


def test_agent_planning_context_is_restricted_to_the_fixed_selection(client, db_session, test_project):
    picked = _host(db_session, test_project.id, "10.9.6.1")
    _host(db_session, test_project.id, "10.9.6.2")  # in the project, not picked
    db_session.commit()
    plan_id = client.post(_url(test_project.id), json={
        "host_ids": [picked.id], "title": "picked", "rationale": "x",
    }).json()["plan"]["id"]
    # Entries already exist for the picked host; the agent context excludes
    # hosts already in the plan, so clear them to see the candidate set.
    db_session.query(TestPlanEntry).filter(TestPlanEntry.test_plan_id == plan_id).delete()
    db_session.commit()

    session = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "ctx"},
    ).json()
    ctx = client.get(
        f"/api/v1/agent/test-plans/{plan_id}/context",
        headers={"X-API-Key": session["api_key"]},
    )
    assert ctx.status_code == 200, ctx.text
    body = ctx.json()
    assert [h["id"] for h in body["candidate_hosts"]] == [picked.id]
    assert body["source"]["kind"] == "manual_hosts"
    assert body["source"]["host_count"] == 1

    # A plan without a fixed selection carries no source block.
    plain = client.post(
        f"/api/v1/projects/{test_project.id}/test-plans/", json={"title": "plain"},
    ).json()["id"]
    plain_ctx = client.get(
        f"/api/v1/agent/test-plans/{plain}/context",
        headers={"X-API-Key": session["api_key"]},
    ).json()
    assert plain_ctx["source"] is None
    assert len(plain_ctx["candidate_hosts"]) == 2
