"""An agent proposes the report's written details; a person decides (2.540.0).

Owner, 2026-11-17: "In testing the executive summary and system description
fields don't seem to allow agents to propose input" — no report-level field
did (the proposal kinds covered findings, observations, endpoints and
subnets).  Decided the same day: an accepted proposal modifies the PROJECT'S
report defaults, which every draft follows for a detail it does not override.

One proposal per written detail (``kind=report_detail``, ``field`` names it).
Accepting is the defaults page's own write (``report_defaults.write_profile``).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import NoteAttachment
from app.db.models_auth import User, UserRole
from app.db.models_project import Notification, ProjectMembership, ProjectRole
from app.db.models_proposals import AgentProposal
from app.db.models_reports import ReportProfile
from app.services import proposal_service, report_defaults
from tests.test_client_reports import _base, _create, template_dir  # noqa: F401 — the fixture


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}, r.json()["agent_session_id"]


def _propose(client, key, fields, **extra):
    return client.post("/api/v1/agent/proposals/report-details", headers=key, json={"fields": fields, **extra})


def _proposed(client, key, fields, **extra):
    r = _propose(client, key, fields, **extra)
    assert r.status_code == 201, r.text
    return r.json()["proposals"]


def _defaults(client, project):
    r = client.get(f"/api/v1/projects/{project.id}/client-reports/profile")
    assert r.status_code == 200, r.text
    return r.json()


def _patch_defaults(client, project, **values):
    r = client.patch(f"/api/v1/projects/{project.id}/client-reports/profile", json=values)
    assert r.status_code == 200, r.text
    return r.json()


def _accept(client, project, proposal_id, **body):
    return client.post(f"/api/v1/projects/{project.id}/proposals/{proposal_id}/accept", json=body)


def _one(client, project, proposal_id):
    items = client.get(
        f"/api/v1/projects/{project.id}/proposals", params={"kind": "report_detail", "status": "any"},
    ).json()["items"]
    return next(p for p in items if p["id"] == proposal_id)


def _image(db, project, content_type="image/png"):
    att = NoteAttachment(
        project_id=project.id, annotation_id=None, filename="diagram.png", content_type=content_type,
        size_bytes=10, storage_path=f"report/{project.id}/diagram-{content_type.split('/')[-1]}",
    )
    db.add(att)
    db.commit()
    return att


@pytest.mark.parametrize("field", report_defaults.WRITTEN_KEYS)
def test_every_written_detail_can_be_proposed_and_accepted(client, db_session, test_project, field):
    """The owner's report: the executive summary and the system description
    took no agent input.  Every written detail does now, and accepting writes
    the project's default — what the defaults page then shows."""
    key, sid = _start(client, test_project)
    (row,) = _proposed(client, key, {field: "Proposed **text**."}, rationale="From the findings.")
    assert (row["kind"], row["status"], row["field"]) == ("report_detail", "pending", field)
    assert row["agent_session_id"] == sid
    # The acknowledgement gives lengths, never the text (as for a finding's sections).
    assert row["value_chars"] == len("Proposed **text**.") and "payload" not in row
    assert _defaults(client, test_project)[field] is None  # nothing changed yet

    accepted = _accept(client, test_project, row["id"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted" and accepted.json()["result_finding_id"] is None
    assert _defaults(client, test_project)[field] == "Proposed **text**."


def test_accepting_writes_what_the_defaults_page_writes(client, db_session, test_project, test_user):
    """One write: an accepted proposal and a hand edit with the same text end
    the same — the row, the text, who last wrote it — and leave every other
    default alone."""
    _patch_defaults(client, test_project, client_name="Acme", system_description="Old description.")
    key, _ = _start(client, test_project)
    (row,) = _proposed(client, key, {"system_description": "  New description.  "})
    assert _accept(client, test_project, row["id"]).status_code == 200
    after = _defaults(client, test_project)
    by_hand = _patch_defaults(client, test_project, system_description="  New description.  ")
    assert after["system_description"] == by_hand["system_description"] == "New description."
    assert after["client_name"] == "Acme"
    db_session.expire_all()
    profiles = db_session.query(ReportProfile).filter(ReportProfile.project_id == test_project.id).all()
    assert [p.updated_by_id for p in profiles] == [test_user.id]


def test_a_draft_that_follows_the_project_prints_the_accepted_text(client, db_session, test_project):
    """The target is the project's defaults: a draft that overrides nothing
    prints the accepted summary; one that overrides it keeps its own."""
    base = _base(test_project)
    follower = _create(client, test_project, kind="full", title="Follows")
    own = _create(client, test_project, kind="full", title="Own")
    r = client.patch(f"{base}/{own['id']}", json={"settings": {"executive_summary": "This report's own."}})
    assert r.status_code == 200, r.text
    key, _ = _start(client, test_project)
    (row,) = _proposed(client, key, {"executive_summary": "The project's summary."})
    assert _accept(client, test_project, row["id"]).status_code == 200
    assert client.get(f"{base}/{follower['id']}").json()["effective"]["executive_summary"] == "The project's summary."
    assert client.get(f"{base}/{own['id']}").json()["effective"]["executive_summary"] == "This report's own."


def test_the_reviewer_sees_the_current_text_and_whether_it_changed(client, db_session, test_project):
    _patch_defaults(client, test_project, executive_summary="As first written.")
    key, _ = _start(client, test_project)
    (row,) = _proposed(client, key, {"executive_summary": "The agent's draft."})
    seen = _one(client, test_project, row["id"])
    assert (seen["current_value"], seen["base_value"], seen["base_recorded"]) == (
        "As first written.", "As first written.", True)
    assert seen["payload"]["value"] == "The agent's draft."
    assert seen["changed_since_proposed"] is False

    _patch_defaults(client, test_project, executive_summary="A person rewrote it.")
    seen = _one(client, test_project, row["id"])
    assert (seen["current_value"], seen["base_value"]) == ("A person rewrote it.", "As first written.")
    assert seen["changed_since_proposed"] is True
    # The agent's own read of one proposal says the same.
    whole = client.get(f"/api/v1/agent/proposals/{row['id']}", headers=key).json()
    assert (whole["current_value"], whole["changed_since_proposed"]) == ("A person rewrote it.", True)


def test_accepting_with_an_edit_stores_the_edit(client, db_session, test_project):
    key, _ = _start(client, test_project)
    (row,) = _proposed(client, key, {"executive_summary": "Draft."})
    # Longer than a finding's section may be: the summary's own limit applies.
    edited = "Edited. " + "x" * 40000
    r = _accept(client, test_project, row["id"], edited_value=edited)
    assert r.status_code == 200, r.text
    assert r.json()["payload"]["accepted_value"] == edited
    assert _defaults(client, test_project)["executive_summary"] == edited.strip()


def test_an_edit_over_the_details_own_limit_is_refused_and_nothing_changes(client, db_session, test_project):
    key, _ = _start(client, test_project)
    (row,) = _proposed(client, key, {"disclaimer": "Draft."})
    r = _accept(client, test_project, row["id"], edited_value="y" * 40000)  # the disclaimer takes 32,768
    assert r.status_code == 422, r.text
    assert _defaults(client, test_project)["disclaimer"] is None
    assert _one(client, test_project, row["id"])["status"] == "pending"


def test_a_findings_section_keeps_its_own_limit_on_an_edit(client, db_session, test_project):
    """The decide body admits a report detail's length; a finding's section
    is still refused over its own."""
    from app.services.finding_service import FindingService
    from app.db import models

    host = models.Host(project_id=test_project.id, ip_address="10.90.0.1")
    db_session.add(host)
    db_session.flush()
    finding = FindingService(db_session).create_finding(
        project_id=test_project.id, title="T", severity="low", host_ids=[host.id], actor_id=None,
    )
    db_session.commit()
    key, _ = _start(client, test_project)
    r = client.post("/api/v1/agent/proposals/finding-text", headers=key,
                    json={"finding_id": finding.id, "fields": {"impact": "Draft."}})
    assert r.status_code == 201, r.text
    pid = r.json()["proposals"][0]["id"]
    r = _accept(client, test_project, pid, edited_value="z" * 40000)
    assert r.status_code == 422, r.text


def test_accepting_one_draft_supersedes_the_details_other_drafts_only(client, db_session, test_project):
    key, _ = _start(client, test_project)
    (first,) = _proposed(client, key, {"executive_summary": "One."}, agent_model="model-a")
    (second,) = _proposed(client, key, {"executive_summary": "Two."}, agent_model="model-b")
    (other,) = _proposed(client, key, {"system_description": "Kept."})
    assert _accept(client, test_project, second["id"]).status_code == 200
    db_session.expire_all()
    assert db_session.get(AgentProposal, first["id"]).status == "superseded"
    assert db_session.get(AgentProposal, other["id"]).status == "pending"
    assert _defaults(client, test_project)["executive_summary"] == "Two."
    assert _accept(client, test_project, first["id"]).status_code == 409


def test_bulk_accept_never_chooses_between_drafts_nor_replaces_rewritten_text(client, db_session, test_project):
    _patch_defaults(client, test_project, applications="As written.")
    key, _ = _start(client, test_project)
    (one,) = _proposed(client, key, {"executive_summary": "One."})
    (two,) = _proposed(client, key, {"executive_summary": "Two."})
    (stale,) = _proposed(client, key, {"applications": "Proposed."})
    (fine,) = _proposed(client, key, {"other_targets": "Proposed."})
    _patch_defaults(client, test_project, applications="A person rewrote it.")
    r = client.post(f"/api/v1/projects/{test_project.id}/proposals/bulk",
                    json={"ids": [one["id"], two["id"], stale["id"], fine["id"]], "action": "accept"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["decided"] == [fine["id"]]
    assert {f["id"]: f["detail"] for f in answer["failed"]} == {
        one["id"]: proposal_service.COMPETING_REPORT_DETAIL, two["id"]: proposal_service.COMPETING_REPORT_DETAIL,
    }
    assert answer["skipped"] == [{
        "id": stale["id"], "reason": proposal_service.REWRITTEN_SINCE_PROPOSED,
        "detail": proposal_service.REPORT_DETAIL_CHANGED_SINCE_PROPOSED_DETAIL,
    }]
    now = _defaults(client, test_project)
    assert (now["executive_summary"], now["applications"], now["other_targets"]) == (
        None, "A person rewrote it.", "Proposed.")
    # A single accept, the current text beside the draft, still applies it.
    assert _accept(client, test_project, stale["id"]).status_code == 200
    assert _defaults(client, test_project)["applications"] == "Proposed."


def test_a_bad_request_stores_nothing(client, db_session, test_project):
    key, _ = _start(client, test_project)
    cases = [
        {},                                                     # nothing proposed
        {"client_name": "Acme"},                                # a fact, a person's to state
        {"testers": "Everyone"},
        {"template": "pentest"},
        {"executive_summary": "Fine.", "nonsense": "x"},        # all or nothing
        {"executive_summary": "   "},                           # empty text: a person clears
        {"disclaimer": "d" * 32769},                            # the page's own limit
        {"executive_summary": "As shown in Finding #277, the domain fell."},   # names a record
        {"system_description": "![Diagram](evidence:999999)"},  # not an image of the report
        {"applications": "![Diagram](evidence:1)"},             # a detail that prints no image
    ]
    for fields in cases:
        r = _propose(client, key, fields)
        assert r.status_code == 422, (fields, r.text)
    assert db_session.query(AgentProposal).filter(AgentProposal.project_id == test_project.id).count() == 0
    assert db_session.query(ReportProfile).filter(ReportProfile.project_id == test_project.id).count() == 0


def test_a_text_may_place_the_reports_own_images_and_no_others(client, db_session, test_project):
    own = _image(db_session, test_project)
    unprintable = _image(db_session, test_project, content_type="image/webp")
    key, _ = _start(client, test_project)
    assert _propose(client, key, {"system_description": f"![x](evidence:{unprintable.id})"}).status_code == 422
    # A tolerated spelling is stored in the one placement grammar.
    (row,) = _proposed(client, key, {"system_description": f"The network:\n\n![Diagram](<evidence:{own.id}>)"})
    stored = _one(client, test_project, row["id"])["payload"]["value"]
    assert stored == f"The network:\n\n![Diagram](evidence:{own.id})"

    # Gone by the time it is accepted: refused, kept pending with the reason.
    db_session.delete(db_session.get(NoteAttachment, own.id))
    db_session.commit()
    r = _accept(client, test_project, row["id"])
    assert r.status_code == 422, r.text
    seen = _one(client, test_project, row["id"])
    assert seen["status"] == "pending" and f"evidence:{own.id}" in seen["error"]
    assert _defaults(client, test_project)["system_description"] is None


def test_an_agent_replaces_its_own_draft_of_the_same_detail(client, db_session, test_project):
    key, _ = _start(client, test_project)
    (old,) = _proposed(client, key, {"executive_summary": "With a typo."})
    wrong = _propose(client, key, {"system_description": "x"}, replaces=[old["id"]])
    assert wrong.status_code == 422, wrong.text
    (new,) = _proposed(client, key, {"executive_summary": "Fixed."}, replaces=[old["id"]])
    db_session.expire_all()
    replaced = db_session.get(AgentProposal, old["id"])
    assert (replaced.status, replaced.replaced_by_id, replaced.decided_by_user_id) == ("superseded", new["id"], None)


def test_the_project_admins_are_told_once_per_session(client, db_session, test_project):
    """The report defaults are the project's: its admins are told, in a
    notification of its own (not the "on no finding yet" one, nor the scope
    one), kept current as the run proposes more."""
    admin = User(id=9401, username="report-admin", email="9401@example.com", full_name="Report Admin",
                 hashed_password="x", role=UserRole.MEMBER, is_active=True, created_at=datetime.now(timezone.utc))
    db_session.add(admin)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=admin.id, role=ProjectRole.ADMIN.value))
    db_session.commit()
    key, sid = _start(client, test_project)
    _proposed(client, key, {"executive_summary": "A."})
    notes = db_session.query(Notification).filter(Notification.user_id == admin.id).all()
    assert [n.title for n in notes] == ["AI proposed text for the project's report defaults"]
    _proposed(client, key, {"system_description": "B.", "disclaimer": "C."})
    db_session.expire_all()
    notes = db_session.query(Notification).filter(Notification.user_id == admin.id).all()
    assert [(n.type, n.source_type, n.source_id) for n in notes] == [
        ("proposal", proposal_service.REPORT_PROPOSAL_SOURCE, sid)]
    assert notes[0].title == "AI proposed 3 texts for the project's report defaults"


def test_the_kind_filters_and_counts_on_both_doors(client, db_session, test_project):
    key, _ = _start(client, test_project)
    (row,) = _proposed(client, key, {"thick_clients": "None in scope."})
    page = client.get("/api/v1/agent/proposals", headers=key, params={"kind": "report_detail", "mine": True}).json()
    assert [p["id"] for p in page["items"]] == [row["id"]]
    assert page["items"][0]["value_chars"] == len("None in scope.") and "payload" not in page["items"][0]
    summary = client.get(f"/api/v1/projects/{test_project.id}/proposals/summary").json()
    assert summary["by_kind"]["report_detail"] == 1
    # The operator whose agent proposed it finds it under "mine".
    assert summary["by_kind_mine"]["report_detail"] == 1


def test_the_agent_reads_the_defaults_the_page_shows(client, db_session, test_project):
    """The text a proposal is written against: the defaults page's own answer,
    with the report's own images and the details an agent may propose."""
    own = _image(db_session, test_project)
    _patch_defaults(client, test_project, client_name="Acme",
                    system_description=f"Network.\n\n![Diagram](evidence:{own.id})")
    key, _ = _start(client, test_project)
    r = client.get("/api/v1/agent/assist/client-reports/defaults", headers=key)
    assert r.status_code == 200, r.text
    answer = r.json()
    page = _defaults(client, test_project)
    assert {k: answer[k] for k in page} == page
    assert answer["images"] == [{
        "id": own.id, "filename": "diagram.png", "caption": None, "printable": True, "placed_in": ["defaults"],
    }]
    assert answer["proposable"] == list(report_defaults.WRITTEN_KEYS)


def test_the_defaults_own_text_sent_back_is_refused(client, db_session, test_project):
    """Agent acceptance run, 2026-11-17: proposing the disclaimer's current
    text verbatim made a pending proposal that changed nothing.  Compared as
    stored (trimmed), with the whole request refused — and a real change to
    the same detail still goes through."""
    _patch_defaults(client, test_project, disclaimer="As it stands.", system_description="Described.")
    key, _ = _start(client, test_project)
    for fields in (
        {"disclaimer": "As it stands."},
        {"disclaimer": "  As it stands.\n"},
        {"executive_summary": "New.", "system_description": "Described."},   # all or nothing
    ):
        r = _propose(client, key, fields)
        assert r.status_code == 422, (fields, r.text)
        assert "current text" in r.json()["detail"]
    assert db_session.query(AgentProposal).filter(AgentProposal.project_id == test_project.id).count() == 0
    (row,) = _proposed(client, key, {"disclaimer": "As it stands, reworded."})
    assert row["base_value_chars"] == len("As it stands.")


def test_the_agents_read_of_a_draft_says_what_it_would_print(client, db_session, test_project):
    """Agent acceptance run, 2026-11-17: a draft that follows the project's
    defaults read ``executive_summary: null`` and ``effective: null`` — the
    agent had to read the defaults to know the draft's text.  The agents'
    read carries the page's details: what the draft prints, what it follows,
    and from where."""
    _patch_defaults(client, test_project, executive_summary="The project's summary.", client_name="Acme")
    follower = _create(client, test_project, kind="full", title="Follows")
    own = _create(client, test_project, kind="full", title="Own")
    r = client.patch(f"{_base(test_project)}/{own['id']}", json={"settings": {"executive_summary": "Its own."}})
    assert r.status_code == 200, r.text
    key, _ = _start(client, test_project)
    for report, summary, overridden in ((follower, "The project's summary.", False), (own, "Its own.", True)):
        agent = client.get(f"/api/v1/agent/assist/client-reports/{report['id']}", headers=key).json()
        page = client.get(f"{_base(test_project)}/{report['id']}").json()
        assert agent["executive_summary"] == summary
        assert agent["effective"]["executive_summary"] == summary and agent["effective"]["client_name"] == "Acme"
        # Field for field what the page shows.
        for part in ("effective", "followed", "followed_from", "settings", "executive_summary"):
            assert agent[part] == page[part], part
        assert ("executive_summary" in agent["settings"]) is overridden
    # The LIST stays lean: no details on its rows, and an overridden detail is
    # NAMED with its value withheld (2.543.0 — a draft's overridden summary
    # was several thousand characters on every listing).
    rows = {row["id"]: row for row in client.get("/api/v1/agent/assist/client-reports", headers=key).json()["items"]}
    assert all(row["effective"] is None and row["agent_written"] is None for row in rows.values())
    assert rows[own["id"]]["settings"] == {"executive_summary": None}
    assert rows[follower["id"]]["settings"] == {}
    # The people's list is unchanged: the page's row line reads the values' keys.
    people = {row["id"]: row for row in client.get(_base(test_project)).json()["items"]}
    assert people[own["id"]]["settings"] == {"executive_summary": "Its own."}


def test_a_findings_own_text_sent_back_is_refused_too(client, db_session, test_project):
    """2.543.0 — the same rule for a finding's section as for a report
    default: its current text sent back is no proposal."""
    from app.db import models
    from app.services.finding_service import FindingService

    host = models.Host(project_id=test_project.id, ip_address="10.91.0.1")
    db_session.add(host)
    db_session.flush()
    finding = FindingService(db_session).create_finding(
        project_id=test_project.id, title="T", severity="low", host_ids=[host.id], actor_id=None,
    )
    finding.impact = "As written."
    db_session.commit()
    key, _ = _start(client, test_project)
    url = "/api/v1/agent/proposals/finding-text"
    for fields in ({"impact": "As written."}, {"impact": " As written.\n"},
                   {"description": "New.", "impact": "As written."}):
        r = client.post(url, headers=key, json={"finding_id": finding.id, "fields": fields})
        assert r.status_code == 422 and "current text" in r.json()["detail"], (fields, r.text)
    assert db_session.query(AgentProposal).filter(AgentProposal.project_id == test_project.id).count() == 0
    r = client.post(url, headers=key, json={"finding_id": finding.id, "fields": {"impact": "As written, and more."}})
    assert r.status_code == 201, r.text


def test_a_draft_says_which_followed_defaults_an_agent_wrote(client, db_session, test_project):
    """2.542.0 — someone evaluating a draft could not see that a default it
    follows was written by an agent (the line was on the defaults page only).
    Only details the draft FOLLOWS, accepted from a proposal and not edited
    since; nothing for an issued report."""
    follower = _create(client, test_project, kind="full", title="Follows")
    own = _create(client, test_project, kind="full", title="Own")
    r = client.patch(f"{_base(test_project)}/{own['id']}", json={"settings": {"executive_summary": "Its own."}})
    assert r.status_code == 200, r.text

    def written(report):
        return client.get(f"{_base(test_project)}/{report['id']}").json()["agent_written"]

    assert written(follower) == [] and written(own) == []
    key, _ = _start(client, test_project)
    (summary,) = _proposed(client, key, {"executive_summary": "By the agent."})
    (system,) = _proposed(client, key, {"system_description": "Described by the agent."})
    for row in (summary, system):
        assert _accept(client, test_project, row["id"]).status_code == 200
    assert written(follower) == ["executive_summary", "system_description"]
    assert written(own) == ["system_description"]            # it overrides the summary
    # The agents' read says the same.
    agent = client.get(f"/api/v1/agent/assist/client-reports/{follower['id']}", headers=key).json()
    assert agent["agent_written"] == ["executive_summary", "system_description"]
    # A person's edit of the default makes it theirs.
    _patch_defaults(client, test_project, system_description="A person rewrote it.")
    assert written(follower) == ["executive_summary"]


def test_a_row_says_how_many_other_drafts_wait_for_the_same_detail(client, db_session, test_project):
    """2.541.0 — on a flat list two drafts of one detail were two
    unrelated-looking rows (browser pass 2026-11-17).  Each pending row says
    how many OTHERS change the same thing; a decided one says none."""
    key, _ = _start(client, test_project)
    (one,) = _proposed(client, key, {"executive_summary": "One."})
    (two,) = _proposed(client, key, {"executive_summary": "Two."})
    (three,) = _proposed(client, key, {"executive_summary": "Three."})
    (alone,) = _proposed(client, key, {"disclaimer": "Alone."})
    assert {p: _one(client, test_project, p)["other_pending"]
            for p in (one["id"], two["id"], three["id"], alone["id"])} == {
        one["id"]: 2, two["id"]: 2, three["id"]: 2, alone["id"]: 0}
    client.post(f"/api/v1/projects/{test_project.id}/proposals/{three['id']}/reject", json={})
    assert _one(client, test_project, one["id"])["other_pending"] == 1
    assert _one(client, test_project, three["id"])["other_pending"] == 0


def test_a_draft_report_says_which_followed_defaults_have_undecided_proposals(client, db_session, test_project):
    """2.541.0 — issued now, a draft prints the default as it stands: its
    "Before issuing" summary names the proposals for the details it FOLLOWS,
    never those for a detail it overrides, and nothing once they are decided."""
    follower = _create(client, test_project, kind="full", title="Follows")
    own = _create(client, test_project, kind="full", title="Own")
    r = client.patch(f"{_base(test_project)}/{own['id']}",
                     json={"settings": {"executive_summary": "This report's own."}})
    assert r.status_code == 200, r.text
    key, _ = _start(client, test_project)
    (a,) = _proposed(client, key, {"executive_summary": "One."})
    (b,) = _proposed(client, key, {"executive_summary": "Two."})
    (c,) = _proposed(client, key, {"disclaimer": "Three."})

    def pending(report):
        return client.get(f"{_base(test_project)}/{report['id']}").json()["summary"]["pending_detail_proposals"]

    assert pending(follower) == [{"field": "executive_summary", "count": 2}, {"field": "disclaimer", "count": 1}]
    assert pending(own) == [{"field": "disclaimer", "count": 1}]
    assert _accept(client, test_project, a["id"]).status_code == 200      # supersedes b
    client.post(f"/api/v1/projects/{test_project.id}/proposals/{c['id']}/reject", json={})
    assert pending(follower) == [] and pending(own) == []
    assert db_session.get(AgentProposal, b["id"]) is not None


def test_the_defaults_say_which_text_came_from_an_accepted_proposal(client, db_session, test_project, test_user):
    """2.541.0 — the report defaults keep no edit history, so nothing said an
    agent wrote a text once its proposal was accepted.  Derived: the last
    accepted proposal per detail, and whether a person edited the text since."""
    key, sid = _start(client, test_project)
    assert _defaults(client, test_project)["from_proposals"] == {}
    (summary,) = _proposed(client, key, {"executive_summary": "By the agent."}, agent_model="model-a")
    (described,) = _proposed(client, key, {"system_description": "Draft."})
    assert _defaults(client, test_project)["from_proposals"] == {}       # pending is not a source
    assert _accept(client, test_project, summary["id"]).status_code == 200
    assert _accept(client, test_project, described["id"], edited_value="  The reviewer's wording.  ").status_code == 200

    sources = _defaults(client, test_project)["from_proposals"]
    assert set(sources) == {"executive_summary", "system_description"}
    first = sources["executive_summary"]
    assert (first["proposal_id"], first["agent_session_id"], first["agent_model"]) == (summary["id"], sid, "model-a")
    assert first["accepted_by"] == (test_user.full_name or test_user.username) and first["accepted_at"]
    assert (first["edited_on_accept"], first["changed_since"]) == (False, False)
    # Accepted with an edit: the stored (trimmed) text IS the accepted text.
    assert (sources["system_description"]["edited_on_accept"], sources["system_description"]["changed_since"]) == (True, False)

    # A person edits one and clears the other: said, and then nothing to say.
    _patch_defaults(client, test_project, executive_summary="A person rewrote it.", system_description=None)
    sources = _defaults(client, test_project)["from_proposals"]
    assert set(sources) == {"executive_summary"} and sources["executive_summary"]["changed_since"] is True
    # The agent's read carries the same.
    agent = client.get("/api/v1/agent/assist/client-reports/defaults", headers=key).json()
    assert agent["from_proposals"]["executive_summary"]["proposal_id"] == summary["id"]

    # A later accepted proposal is the source from then on.
    (again,) = _proposed(client, key, {"executive_summary": "By the agent, again."})
    assert _accept(client, test_project, again["id"]).status_code == 200
    latest = _defaults(client, test_project)["from_proposals"]["executive_summary"]
    assert (latest["proposal_id"], latest["changed_since"]) == (again["id"], False)


def test_a_reject_changes_nothing_and_makes_no_empty_surprise(client, db_session, test_project):
    key, _ = _start(client, test_project)
    (row,) = _proposed(client, key, {"executive_summary": "No."})
    r = client.post(f"/api/v1/projects/{test_project.id}/proposals/{row['id']}/reject",
                    json={"note": "Too long."})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["decision_note"]) == ("rejected", "Too long.")
    # The decision's lock may have made the (empty) defaults row; it says what no row says.
    page = _defaults(client, test_project)
    assert page["executive_summary"] is None and page["testers_from_project"] is True
