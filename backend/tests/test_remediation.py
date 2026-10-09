"""Remediation tracking: per finding on a host, admins only, never synced
with the assessor's statuses."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project, ProjectMembership
from app.db.models_remediation import FindingHostRemediation, RemediationEvent


@pytest.fixture(autouse=True)
def tracking_on(db_session):
    """The installation opted in (off, every route here is a 404 — see
    ``test_remediation_deadlines.py``)."""
    from app.db.models_remediation import RemediationPolicy
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30,
                                     days_medium=90, days_low=120, due_soon_days=7))
    db_session.commit()


def base(project):
    return f"/api/v1/projects/{project.id}/remediation"


# A deferral is a decision with a day to look at it again and a reason.
REVIEW = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
DEFERRED = {"status": "deferred", "deferred_review_on": REVIEW, "notes": [{"body": "Waiting for the vendor's fix."}]}


def _host(db, project, ip):
    row = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(row)
    db.flush()
    return row


def _finding(db, project, title, hosts, *, status="confirmed", endpoint="open"):
    finding = Finding(project_id=project.id, title=title, severity="high", status=status, source="manual")
    db.add(finding)
    db.flush()
    links = []
    for host in hosts:
        link = FindingHost(finding_id=finding.id, host_id=host.id, host_status=endpoint)
        db.add(link)
        links.append(link)
    db.flush()
    return finding, links


@pytest.fixture
def world(db_session, test_project):
    """One finding on hosts A and B, a second finding on host A."""
    a, b = _host(db_session, test_project, "10.9.0.1"), _host(db_session, test_project, "10.9.0.2")
    smb, (smb_a, smb_b) = _finding(db_session, test_project, "SMB signing not required", [a, b])
    tls, (tls_a,) = _finding(db_session, test_project, "TLS 1.0 enabled", [a])
    db_session.commit()
    return {"a": a.id, "b": b.id, "smb": smb.id, "tls": tls.id,
            "smb_a": smb_a.id, "smb_b": smb_b.id, "tls_a": tls_a.id}


def apply(client, project, rows, **flags):
    return client.post(f"{base(project)}/apply", json={"rows": rows, **flags})


def listing(client, project, **params):
    response = client.get(base(project), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_id(page):
    return {item["finding_host_id"]: item for item in page["items"]}


# --- the list ---------------------------------------------------------------

def test_an_untracked_finding_on_a_host_is_listed_open_with_no_contact(client, test_project, world):
    page = listing(client, test_project)
    assert page["total"] == 3
    assert page["status_counts"] == {"open": 3, "closed": 0, "deferred": 0}
    row = by_id(page)[world["smb_a"]]
    assert row["status"] == "open" and row["contact_email"] is None and row["notified_on"] is None


def test_the_same_finding_has_its_own_contact_and_status_on_each_host(client, test_project, world):
    response = apply(client, test_project, [
        {"finding_host_id": world["smb_a"], "contact_email": "Roger.Smith@Testdomain.com",
         "contact_name": "Roger Smith", "notified_on": "2026-10-03", "status": "closed",
         "closed_on": "2026-10-06"},
        {"finding_host_id": world["smb_b"], "contact_email": "jane@testdomain.com", **DEFERRED},
    ])
    assert response.status_code == 200, response.text
    rows = by_id(listing(client, test_project))
    assert rows[world["smb_a"]]["contact_email"] == "roger.smith@testdomain.com"   # the identity is lowercased
    assert rows[world["smb_a"]]["status"] == "closed" and rows[world["smb_a"]]["closed_on"] == "2026-10-06"
    assert rows[world["smb_b"]]["contact_email"] == "jane@testdomain.com"
    assert rows[world["smb_b"]]["status"] == "deferred"
    assert rows[world["tls_a"]]["contact_email"] is None


def test_filters_by_contact_name_or_address_status_and_unassigned(client, test_project, world):
    apply(client, test_project, [
        {"finding_host_id": world["smb_a"], "contact_email": "roger.smith@testdomain.com",
         "contact_name": "Roger Smith"},
        {"finding_host_id": world["tls_a"], "contact_email": "roger.smith@testdomain.com", "status": "closed"},
    ])
    assert listing(client, test_project, contact="SMITH")["total"] == 2
    assert listing(client, test_project, contact="roger smith")["total"] == 1   # the name, where one was given
    opened = listing(client, test_project, contact="roger.smith@", status="open")
    assert [r["finding_host_id"] for r in opened["items"]] == [world["smb_a"]]
    assert opened["status_counts"] == {"open": 1, "closed": 1, "deferred": 0}   # the whole selection
    assert [r["finding_host_id"] for r in listing(client, test_project, unassigned=True)["items"]] == [world["smb_b"]]
    assert listing(client, test_project, contact="100%")["total"] == 0   # a wildcard is text


def test_what_a_report_would_not_include_is_not_listed_unless_already_tracked(client, db_session, test_project, world):
    host = _host(db_session, test_project, "10.9.0.9")
    _, (investigating,) = _finding(db_session, test_project, "Under investigation", [host], status="open")
    _, (dismissed,) = _finding(db_session, test_project, "Not here", [host], endpoint="false_positive")
    db_session.commit()
    assert listing(client, test_project)["total"] == 3

    apply(client, test_project, [{"finding_host_id": investigating.id, "contact_email": "x@example.com"}])
    assert investigating.id in by_id(listing(client, test_project))
    assert dismissed.id not in by_id(listing(client, test_project))


def test_the_count_equals_the_list_paged_through(client, db_session, test_project):
    hosts = [_host(db_session, test_project, f"10.8.0.{n}") for n in range(1, 24)]
    _finding(db_session, test_project, "Everywhere", hosts)
    db_session.commit()
    seen, offset = [], 0
    while True:
        page = listing(client, test_project, limit=10, offset=offset)
        seen += [r["finding_host_id"] for r in page["items"]]
        if not page["has_more"]:
            break
        offset += 10
    assert len(seen) == len(set(seen)) == page["total"] == 23


def test_rows_are_in_address_order_and_each_grouping_keeps_its_rows_together(client, db_session, test_project):
    hosts = [_host(db_session, test_project, ip) for ip in ("10.6.0.10", "10.6.0.2", "10.6.0.9")]
    _finding(db_session, test_project, "B issue", hosts)
    _finding(db_session, test_project, "A issue", hosts[:2])
    db_session.commit()
    by_host = [(r["ip_address"], r["finding_title"]) for r in listing(client, test_project)["items"]]
    assert by_host == [("10.6.0.2", "A issue"), ("10.6.0.2", "B issue"), ("10.6.0.9", "B issue"),
                       ("10.6.0.10", "A issue"), ("10.6.0.10", "B issue")]
    by_finding = [(r["finding_title"], r["ip_address"]) for r in listing(client, test_project, group="finding")["items"]]
    assert by_finding == [("A issue", "10.6.0.2"), ("A issue", "10.6.0.10"),
                          ("B issue", "10.6.0.2"), ("B issue", "10.6.0.9"), ("B issue", "10.6.0.10")]
    first = by_id(listing(client, test_project))
    apply(client, test_project, [{"finding_host_id": max(first), "contact_email": "z@example.com"}])
    by_contact = listing(client, test_project, group="contact")["items"]
    assert by_contact[0]["contact_email"] == "z@example.com"          # contacts first, unassigned last
    assert all(r["contact_email"] is None for r in by_contact[1:])


# --- writing ----------------------------------------------------------------

def test_a_row_naming_only_the_finding_covers_every_host_of_it(client, test_project, world):
    plan = apply(client, test_project, [{"finding_id": world["smb"], "contact_email": "r@example.com"}],
                 dry_run=True).json()
    assert plan["summary"]["targets"] == 2 and plan["summary"]["changed"] == 2
    assert sorted(plan["rows"][0]["finding_host_ids"]) == sorted([world["smb_a"], world["smb_b"]])
    one = apply(client, test_project, [{"finding_id": world["smb"], "host_id": world["b"],
                                        "contact_email": "r@example.com"}], dry_run=True).json()
    assert one["rows"][0]["finding_host_ids"] == [world["smb_b"]]


def test_a_dry_run_writes_nothing(client, db_session, test_project, world):
    response = apply(client, test_project, [
        {"finding_host_id": world["smb_a"], "status": "closed", "notes": [{"body": "Called the owner"}]},
    ], dry_run=True)
    assert response.json()["summary"] == {
        "targets": 1, "changed": 1, "unchanged": 0, "conflicts": 0,
        "notes_added": 1, "notes_already_recorded": 0,
    }
    assert db_session.query(FindingHostRemediation).count() == 0
    assert db_session.query(RemediationEvent).count() == 0


def test_a_value_someone_set_is_kept_unless_overwrite_is_asked_for(client, test_project, world):
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed",
                                  "contact_email": "roger@example.com"}])
    stale = [{"finding_host_id": world["smb_a"], "status": "open", "contact_email": "other@example.com",
              "notified_on": "2026-10-01"}]
    result = apply(client, test_project, stale).json()
    assert result["summary"]["conflicts"] == 2 and result["summary"]["changed"] == 1
    assert {c["field"] for c in result["rows"][0]["conflicts"]} == {"status", "contact_email"}
    row = by_id(listing(client, test_project))[world["smb_a"]]
    assert (row["status"], row["contact_email"], row["notified_on"]) == ("closed", "roger@example.com", "2026-10-01")

    result = apply(client, test_project, stale, overwrite=True).json()
    assert result["summary"]["conflicts"] == 0 and result["summary"]["changed"] == 2
    row = by_id(listing(client, test_project))[world["smb_a"]]
    assert (row["status"], row["contact_email"]) == ("open", "other@example.com")


def test_reopening_clears_the_closed_date_and_says_so(client, test_project, world):
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed", "closed_on": "2026-10-06"}])
    result = apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "open"}],
                   overwrite=True).json()
    assert {c["field"] for c in result["rows"][0]["changed"]} == {"status", "closed_on"}
    assert by_id(listing(client, test_project))[world["smb_a"]]["closed_on"] is None


def test_an_explicit_null_clears_a_field_and_a_missing_one_is_untouched(client, test_project, world):
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "contact_email": "r@example.com",
                                  "notified_on": "2026-10-03"}])
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "notified_on": None}], overwrite=True)
    row = by_id(listing(client, test_project))[world["smb_a"]]
    assert row["notified_on"] is None and row["contact_email"] == "r@example.com"


@pytest.mark.parametrize("rows", [
    [{"finding_host_id": 999_999, "status": "closed"}],
    [{"finding_id": 999_999, "status": "closed"}],
    [{"status": "closed"}],
    [{"finding_host_id": 1, "finding_id": 1, "status": "closed"}],
    [{"finding_host_id": 1, "status": None}],
    [{"finding_host_id": 1, "status": "fixed"}],
    [{"finding_host_id": 1, "contact_email": "not an address"}],
])
def test_a_row_that_cannot_be_understood_refuses_the_whole_call(client, db_session, test_project, world, rows):
    good = {"finding_host_id": world["tls_a"], "status": "closed"}
    assert apply(client, test_project, [good] + rows).status_code == 422
    assert db_session.query(FindingHostRemediation).count() == 0


def test_two_rows_for_one_finding_on_a_host_are_refused(client, test_project, world):
    response = apply(client, test_project, [
        {"finding_id": world["smb"], "status": "closed"},
        {"finding_host_id": world["smb_a"], "status": "deferred"},
    ])
    assert response.status_code == 422
    assert "both name" in str(response.json())


def test_another_projects_finding_is_not_a_finding_here(client, db_session, test_project, world):
    other = Project(name="other", slug="other", description="x")
    db_session.add(other)
    db_session.flush()
    _, (foreign,) = _finding(db_session, other, "Elsewhere", [_host(db_session, other, "10.7.0.1")])
    db_session.commit()
    assert apply(client, test_project, [{"finding_host_id": foreign.id, "status": "closed"}]).status_code == 422
    assert foreign.id not in by_id(listing(client, test_project))


def test_tracking_never_moves_the_assessors_statuses(client, db_session, test_project, world):
    apply(client, test_project, [{"finding_id": world["smb"], "status": "closed", "closed_on": "2026-10-06"}])
    db_session.expire_all()
    assert db_session.get(Finding, world["smb"]).status == "confirmed"
    assert {fh.host_status for fh in db_session.query(FindingHost).filter_by(finding_id=world["smb"])} == {"open"}


# --- the timeline -----------------------------------------------------------

def events(client, project, host_id, **params):
    response = client.get(f"{base(project)}/hosts/{host_id}/events", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_every_field_change_is_an_entry_with_what_it_was(client, test_project, test_user, world):
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed",
                                  "contact_email": "roger@example.com"}])
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "open"}], overwrite=True)
    timeline = events(client, test_project, world["a"])
    assert timeline["total"] == 3
    changes = {(e["field"], e["from"], e["to"]) for e in timeline["items"]}
    assert changes == {("status", "open", "closed"), ("contact_email", None, "roger@example.com"),
                       ("status", "closed", "open")}
    entry = timeline["items"][0]
    assert entry["kind"] == "change" and entry["finding_title"] == "SMB signing not required"
    assert entry["author"] == "Test Admin" and entry["can_modify"] is False
    assert events(client, test_project, world["b"])["total"] == 0


def test_an_unchanged_value_writes_no_entry(client, test_project, world):
    row = [{"finding_host_id": world["smb_a"], "status": "closed"}]
    apply(client, test_project, row)
    assert apply(client, test_project, row).json()["summary"] == {
        "targets": 1, "changed": 0, "unchanged": 1, "conflicts": 0,
        "notes_added": 0, "notes_already_recorded": 0,
    }
    assert events(client, test_project, world["a"])["total"] == 1


def test_a_note_has_when_it_happened_and_when_it_was_recorded(client, test_project, world):
    response = client.post(f"{base(project=test_project)}/events", json={
        "host_id": world["a"], "finding_host_id": world["smb_a"],
        "body": "Contacted owner, will respond on Friday", "occurred_at": "2026-09-29T14:00:00Z",
    })
    assert response.status_code == 201, response.text
    note = response.json()
    assert note["kind"] == "note" and note["occurred_at"].startswith("2026-09-29T14:00")
    assert note["recorded_at"] > note["occurred_at"]
    assert note["finding_title"] == "SMB signing not required" and note["can_modify"] is True

    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed"}])
    ordered = [e["kind"] for e in events(client, test_project, world["a"])["items"]]
    assert ordered == ["change", "note"]   # by when it happened, newest first


def test_a_note_about_the_host_needs_no_finding_and_a_foreign_one_is_refused(client, test_project, world):
    plain = client.post(f"{base(test_project)}/events", json={"host_id": world["a"], "body": "Owner on leave"})
    assert plain.status_code == 201 and plain.json()["finding_host_id"] is None
    wrong = client.post(f"{base(test_project)}/events", json={
        "host_id": world["a"], "finding_host_id": world["smb_b"], "body": "x"})
    assert wrong.status_code == 422
    assert client.post(f"{base(test_project)}/events", json={"host_id": 999_999, "body": "x"}).status_code == 404


def test_a_repeated_request_key_is_the_same_note(client, test_project, world):
    body = {"host_id": world["a"], "body": "Emailed the report", "request_key": "sheet-row-7"}
    first = client.post(f"{base(test_project)}/events", json=body)
    again = client.post(f"{base(test_project)}/events", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    changed = client.post(f"{base(test_project)}/events", json={**body, "body": "Something else"})
    assert changed.status_code == 409

    rows = [{"finding_id": world["smb"], "notes": [{"body": "From the sheet", "request_key": "sheet-row-9"}]}]
    assert apply(client, test_project, rows).json()["summary"]["notes_added"] == 2   # one per host
    assert apply(client, test_project, rows).json()["summary"] == {
        "targets": 2, "changed": 0, "unchanged": 0, "conflicts": 0,
        "notes_added": 0, "notes_already_recorded": 2,
    }
    assert events(client, test_project, world["b"])["total"] == 1


def test_a_note_is_its_authors_and_a_change_is_nobodys(client, db_session, test_project, test_user, world):
    from app.db.models_auth import User, UserRole
    other = User(id=2, username="second-admin", email="second@example.com", hashed_password="x",
                 role=UserRole.ADMIN, is_active=True)
    db_session.add(other)
    db_session.flush()
    theirs = RemediationEvent(project_id=test_project.id, host_id=world["a"], kind="note",
                              body="Theirs", author_id=other.id)
    db_session.add(theirs)
    db_session.commit()
    mine = client.post(f"{base(test_project)}/events", json={"host_id": world["a"], "body": "Mine"}).json()
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed"}])
    change = next(e for e in events(client, test_project, world["a"])["items"] if e["kind"] == "change")

    assert client.patch(f"{base(test_project)}/events/{theirs.id}", json={"body": "x"}).status_code == 403
    assert client.delete(f"{base(test_project)}/events/{theirs.id}").status_code == 403
    assert client.patch(f"{base(test_project)}/events/{change['id']}", json={"body": "x"}).status_code == 409
    assert client.delete(f"{base(test_project)}/events/{change['id']}").status_code == 409

    edited = client.patch(f"{base(test_project)}/events/{mine['id']}", json={"body": "Mine, corrected"})
    assert edited.status_code == 200 and edited.json()["edited_at"] is not None
    assert client.delete(f"{base(test_project)}/events/{mine['id']}").status_code == 204
    assert events(client, test_project, world["a"])["total"] == 2


def test_the_timeline_outlives_the_finding(client, db_session, test_project, world):
    apply(client, test_project, [{"finding_host_id": world["tls_a"], "status": "closed",
                                  "notes": [{"body": "Patched last week"}]}])
    db_session.delete(db_session.get(Finding, world["tls"]))
    db_session.commit()
    db_session.expire_all()
    timeline = events(client, test_project, world["a"])
    assert timeline["total"] == 2
    assert {e["finding_title"] for e in timeline["items"]} == {"TLS 1.0 enabled"}
    assert {e["finding_host_id"] for e in timeline["items"]} == {None}
    assert db_session.query(FindingHostRemediation).count() == 0


# --- who --------------------------------------------------------------------

def test_contacts_are_listed_with_their_counts(client, test_project, world):
    apply(client, test_project, [
        {"finding_id": world["smb"], "contact_email": "roger@example.com", "contact_name": "Roger Smith"},
        {"finding_host_id": world["tls_a"], "contact_email": "roger@example.com", "status": "closed"},
    ])
    response = client.get(f"{base(test_project)}/contacts")
    (roger,) = response.json()["items"]
    assert {k: roger[k] for k in ("contact_email", "contact_name", "total", "open", "closed")} == {
        "contact_email": "roger@example.com", "contact_name": "Roger Smith", "total": 3, "open": 2, "closed": 1}


@pytest.mark.parametrize("role,read", [("analyst", 200), ("auditor", 200), ("viewer", 403)])
def test_an_auditor_reads_and_only_a_project_admin_writes(
    client, db_session, test_project, test_user, world, role, read,
):
    from app.db.models_auth import UserRole
    note = client.post(f"{base(test_project)}/events", json={"host_id": world["a"], "body": "x"}).json()
    test_user.role = UserRole.MEMBER
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role=role))
    db_session.commit()
    assert client.get(base(test_project)).status_code == read
    assert client.get(f"{base(test_project)}/contacts").status_code == read
    assert client.get(f"{base(test_project)}/hosts/{world['a']}/events").status_code == read
    assert apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed"}]).status_code == 403
    assert apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed"}],
                 dry_run=True).status_code == 403
    assert client.post(f"{base(test_project)}/events", json={"host_id": world["a"], "body": "x"}).status_code == 403
    # Their own note too: writing is the admin's, whoever wrote it.
    assert client.patch(f"{base(test_project)}/events/{note['id']}", json={"body": "y"}).status_code == 403
    assert client.delete(f"{base(test_project)}/events/{note['id']}").status_code == 403


# --- agents: the same contract, as the session's operator --------------------

AGENT = "/api/v1/agent/remediation"


def _agent(client, project):
    response = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert response.status_code == 201, response.text
    return {"X-API-Key": response.json()["api_key"]}


def _operator_becomes(db_session, project, user, role):
    """A key carries its operator's CURRENT role, so demoting the operator
    after the session started is the test."""
    from app.db.models_auth import UserRole
    user.role = UserRole.MEMBER
    db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db_session.commit()


def test_an_agent_reads_and_writes_what_the_page_does_and_is_named(client, db_session, test_project, world):
    key = _agent(client, test_project)
    rows = [{"finding_id": world["smb"], "host_id": world["a"], "contact_email": "roger@example.com",
             "status": "closed", "notes": [{"body": "From the tracking sheet", "request_key": "r1"}]}]
    plan = client.post(f"{AGENT}/apply", json={"rows": rows, "dry_run": True}, headers=key)
    assert plan.status_code == 200, plan.text
    assert plan.json()["summary"]["changed"] == 2 and db_session.query(RemediationEvent).count() == 0

    done = client.post(f"{AGENT}/apply", json={"rows": rows, "agent_model": "test-model"}, headers=key)
    assert done.status_code == 200, done.text
    listed = client.get(AGENT, params={"contact": "roger"}, headers=key).json()
    assert [r["finding_host_id"] for r in listed["items"]] == [world["smb_a"]]
    assert listed == listing(client, test_project, contact="roger")   # the page's answer

    timeline = client.get(f"{AGENT}/hosts/{world['a']}/events", headers=key).json()
    assert timeline["total"] == 3
    assert all(e["agent_session_id"] is not None and e["author"] == "Test Admin" for e in timeline["items"])
    assert client.get(f"{AGENT}/contacts", headers=key).json()["items"][0]["contact_email"] == "roger@example.com"


@pytest.mark.parametrize("role,read", [("analyst", 200), ("auditor", 200), ("viewer", 403)])
def test_an_agent_writes_only_for_a_project_admin(client, db_session, test_project, test_user, world, role, read):
    key = _agent(client, test_project)
    _operator_becomes(db_session, test_project, test_user, role)
    assert client.get(AGENT, headers=key).status_code == read
    assert client.get(f"{AGENT}/contacts", headers=key).status_code == read
    assert client.get(f"{AGENT}/hosts/{world['a']}/events", headers=key).status_code == read
    row = {"rows": [{"finding_host_id": world["smb_a"], "status": "closed"}]}
    assert client.post(f"{AGENT}/apply", json=row, headers=key).status_code == 403
    assert client.post(f"{AGENT}/apply", json={**row, "dry_run": True}, headers=key).status_code == 403
    assert client.post(f"{AGENT}/events", json={"host_id": world["a"], "body": "x"}, headers=key).status_code == 403
    assert db_session.query(FindingHostRemediation).count() == 0


def test_an_agent_of_a_project_admin_may_write(client, db_session, test_project, test_user, world):
    key = _agent(client, test_project)
    _operator_becomes(db_session, test_project, test_user, "admin")
    response = client.post(f"{AGENT}/apply", headers=key,
                           json={"rows": [{"finding_host_id": world["smb_a"], "status": "closed"}]})
    assert response.status_code == 200, response.text


def test_a_project_admin_who_is_not_a_global_admin_may(client, db_session, test_project, test_user, world):
    from app.db.models_auth import UserRole
    test_user.role = UserRole.MEMBER
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role="admin"))
    db_session.commit()
    assert client.get(base(test_project)).status_code == 200
    assert apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed"}]).status_code == 200


# ---------------------------------------------------------------------------
# External review, 2026-10-08: status and closed date are one decision; a
# call is planned whole before anything is written; a key is not a retry.
# ---------------------------------------------------------------------------

def _stored(db_session, finding_host_id):
    db_session.expire_all()
    row = db_session.query(FindingHostRemediation).filter_by(finding_host_id=finding_host_id).first()
    return None if row is None else (row.status, row.closed_on.isoformat() if row.closed_on else None)


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("row", [
    {"closed_on": "2026-10-06"},                          # alone, on a row that is open
    {"status": "open", "closed_on": "2026-10-06"},        # said outright
    {"status": "deferred", "closed_on": "2026-10-06"},
])
def test_a_closed_date_without_a_closed_status_is_refused(client, db_session, test_project, world, dry_run, row):
    response = apply(client, test_project, [{"finding_host_id": world["smb_a"], **row}], dry_run=dry_run)
    assert response.status_code == 422, response.text
    assert _stored(db_session, world["smb_a"]) is None


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_status_that_conflicts_takes_its_closed_date_with_it(client, db_session, test_project, world, dry_run):
    apply(client, test_project, [{"finding_host_id": world["smb_a"], **DEFERRED}])
    result = apply(client, test_project, [
        {"finding_host_id": world["smb_a"], "status": "closed", "closed_on": "2026-10-06"},
    ], dry_run=dry_run)
    assert result.status_code == 200, result.text
    assert result.json()["summary"]["changed"] == 0 and result.json()["summary"]["conflicts"] == 2
    assert {c["field"] for c in result.json()["rows"][0]["conflicts"]} == {"status", "closed_on"}
    assert _stored(db_session, world["smb_a"]) == ("deferred", None)


def test_a_closed_date_alone_is_fine_on_a_row_that_is_closed(client, db_session, test_project, world):
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed"}])
    assert apply(client, test_project, [{"finding_host_id": world["smb_a"], "closed_on": "2026-10-06"}]).status_code == 200
    assert _stored(db_session, world["smb_a"]) == ("closed", "2026-10-06")


def test_the_database_refuses_the_contradiction_from_any_writer(db_session, test_project, world):
    from datetime import date
    from sqlalchemy.exc import IntegrityError
    db_session.add(FindingHostRemediation(finding_host_id=world["smb_a"], project_id=test_project.id,
                                          status="open", closed_on=date(2026, 10, 6)))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize("dry_run", [True, False])
def test_the_same_keyed_note_twice_in_one_call_is_one_note(client, db_session, test_project, world, dry_run):
    note = {"body": "From the sheet", "request_key": "row-3"}
    result = apply(client, test_project, [
        {"finding_host_id": world["smb_a"], "status": "deferred", "deferred_review_on": REVIEW,
         "notes": [note, dict(note)]},
    ], dry_run=dry_run)
    assert result.status_code == 200, result.text
    assert result.json()["summary"]["notes_added"] == 1
    assert result.json()["summary"]["notes_already_recorded"] == 1
    assert db_session.query(RemediationEvent).filter_by(kind="note").count() == (0 if dry_run else 1)


@pytest.mark.parametrize("dry_run", [True, False])
def test_one_key_carrying_two_different_notes_refuses_the_call(client, db_session, test_project, world, dry_run):
    result = apply(client, test_project, [
        {"finding_host_id": world["smb_a"], "status": "deferred", "deferred_review_on": REVIEW, "notes": [
            {"body": "First", "request_key": "row-3"}, {"body": "Second", "request_key": "row-3"}]},
    ], dry_run=dry_run)
    assert result.status_code == 422, result.text
    assert _stored(db_session, world["smb_a"]) is None        # the field change went with it
    assert db_session.query(RemediationEvent).count() == 0


def test_a_stored_key_with_different_text_refuses_the_call(client, db_session, test_project, world):
    rows = [{"finding_host_id": world["smb_a"], "notes": [{"body": "First", "request_key": "row-3"}]}]
    assert apply(client, test_project, rows).status_code == 200
    rows[0]["notes"][0]["body"] = "Changed"
    assert apply(client, test_project, rows).status_code == 422
    assert apply(client, test_project, rows, dry_run=True).status_code == 422


def test_a_reused_note_key_is_a_retry_only_for_the_same_note(client, test_project, world):
    url = f"{base(test_project)}/events"
    first = {"host_id": world["a"], "finding_host_id": world["smb_a"], "body": "Emailed", "request_key": "key",
             "occurred_at": "2026-10-01T09:00:00Z"}
    assert client.post(url, json=first).status_code == 201
    assert client.post(url, json=first).status_code == 200
    assert client.post(url, json={k: v for k, v in first.items() if k != "occurred_at"}).status_code == 200
    # Another finding on the same host, the host alone, or another time: not this note.
    assert client.post(url, json={**first, "finding_host_id": world["tls_a"]}).status_code == 409
    assert client.post(url, json={k: v for k, v in first.items() if k != "finding_host_id"}).status_code == 409
    assert client.post(url, json={**first, "occurred_at": "2026-10-02T09:00:00Z"}).status_code == 409
    assert events(client, test_project, world["a"])["total"] == 1


def test_five_hundred_findings_on_hosts_is_a_call_and_one_more_is_not(client, db_session, test_project):
    hosts = [_host(db_session, test_project, f"10.5.{n // 250}.{n % 250 + 1}") for n in range(501)]
    everywhere, _ = _finding(db_session, test_project, "Everywhere", hosts)
    most, _ = _finding(db_session, test_project, "Almost everywhere", hosts[:500])
    db_session.commit()
    over = apply(client, test_project, [{"finding_id": everywhere.id, "status": "closed"}])
    assert over.status_code == 422 and "more than 500" in str(over.json())
    assert db_session.query(FindingHostRemediation).count() == 0
    ok = apply(client, test_project, [{"finding_id": most.id, "status": "closed"}])
    assert ok.status_code == 200 and ok.json()["summary"]["changed"] == 500


def test_naming_one_host_of_a_wide_finding_loads_only_that_host(client, db_session, test_project):
    from sqlalchemy import event
    hosts = [_host(db_session, test_project, f"10.4.0.{n}") for n in range(1, 41)]
    wide, links = _finding(db_session, test_project, "Wide", hosts)
    other, _ = _finding(db_session, test_project, "Other", hosts[:1])
    db_session.commit()
    rows = [
        {"finding_id": wide.id, "host_id": hosts[3].id, **DEFERRED},
        {"finding_id": wide.id, "host_id": hosts[7].id, "status": "closed"},      # the same finding, twice
        {"finding_id": other.id, "contact_email": "x@example.com"},                # a whole finding
        {"finding_host_id": links[20].id, **DEFERRED},                             # and by id
    ]
    loaded = []
    listen = lambda target, context: loaded.append(target) if isinstance(target, FindingHost) else None  # noqa: E731
    for held in [o for o in db_session if isinstance(o, FindingHost)]:
        db_session.expunge(held)   # none is in the session: every FindingHost used is a load
    event.listen(FindingHost, "load", listen)
    try:
        result = apply(client, test_project, rows)
    finally:
        event.remove(FindingHost, "load", listen)
    assert result.status_code == 200, result.text
    assert result.json()["summary"]["targets"] == 4
    assert len(loaded) == 4        # not the finding's forty


# --- review 2026-10-07 --------------------------------------------------------
def test_a_row_with_a_name_and_no_address_is_not_listed_as_having_no_contact(client, test_project, world):
    """The Contact column shows the name, so "No contact yet" must not list it."""
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "contact_name": "Roger Smith"}])
    unassigned = [r["finding_host_id"] for r in listing(client, test_project, unassigned=True)["items"]]
    assert world["smb_a"] not in unassigned
    assert set(unassigned) == {world["smb_b"], world["tls_a"]}


def test_a_keyed_note_sent_again_with_another_time_is_not_called_already_recorded(client, test_project, world):
    """``add_note`` answers 409 for this; ``apply`` reported the corrected
    date as "already recorded" and kept the wrong one."""
    note = {"body": "Called the owner", "request_key": "sheet-row-3", "occurred_at": "2026-09-29T14:00:00Z"}
    rows = [{"finding_host_id": world["smb_a"], "notes": [note]}]
    assert apply(client, test_project, rows).json()["summary"]["notes_added"] == 1
    assert apply(client, test_project, rows).json()["summary"]["notes_already_recorded"] == 1
    moved = [{"finding_host_id": world["smb_a"], "notes": [{**note, "occurred_at": "2026-09-30T09:00:00Z"}]}]
    for dry_run in (True, False):
        refused = apply(client, test_project, moved, dry_run=dry_run)
        assert refused.status_code == 422, refused.text
    # The same key with no time is still the same note.
    untimed = [{"finding_host_id": world["smb_a"], "notes": [{"body": note["body"], "request_key": note["request_key"]}]}]
    assert apply(client, test_project, untimed).json()["summary"]["notes_already_recorded"] == 1


def test_a_change_entry_happened_when_it_was_recorded(client, test_project, world):
    """Both timestamps from one clock: the database's default for
    ``created_at`` is the transaction's start, so a save that straddled a
    minute read as backdated on the timeline."""
    apply(client, test_project, [{"finding_host_id": world["smb_a"], "status": "closed"}])
    client.post(f"{base(test_project)}/events", json={"host_id": world["a"], "body": "Just now"})
    for entry in events(client, test_project, world["a"])["items"]:
        assert entry["occurred_at"] == entry["recorded_at"], entry
