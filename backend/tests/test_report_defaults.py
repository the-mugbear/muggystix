"""The report's author and disclaimer, and where a report gets them (2.495.0).

Owner, 2026-10-13: "make it inherit from a global setting but allow teams to
modify it per project".  Three levels, the nearest one written wins:

1. the report's own settings,
2. the project's report defaults (``report_profiles``),
3. the installation's (``report_defaults``; a global admin, System settings).

``services/report_defaults.resolve`` is the one place that order is applied —
the dataset builder calls it — and the two pages show what an empty field
will use (``ReportProfileOut.inherited``, ``ReportOut.inherited``).

Also here, because it is the same question ("does a new report take the
project's defaults?"): a new draft carries its project's classification.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_reports import Report, ReportDefault, ReportProfile
from app.main import app
from app.services import report_defaults
from app.services.client_report_service import REQUIRED_DETAILS, SETTINGS_KEYS, ClientReportService

URL = "/api/v1/report-defaults"


@pytest.fixture(autouse=True)
def template_dir(tmp_path, monkeypatch):
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({
        "title": "Test template", "entry": "report.qmd", "formats": ["html", "docx"],
    }))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(tmp_path / "report-templates"))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "reports"))


def _user(db, user_id, username, role=UserRole.MEMBER):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com", full_name=username.title(),
        hashed_password="x", role=role, is_active=True, created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return user


def _reports(project):
    return f"/api/v1/projects/{project.id}/client-reports"


def _put(client, **values):
    return client.put(URL, json={"values": values})


def _profile(client, project, **body):
    r = client.put(f"{_reports(project)}/profile", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _draft(client, project):
    r = client.post(_reports(project), json={"kind": "full"})
    assert r.status_code == 201, r.text
    return r.json()


def _patch(client, project, report_id, **settings_):
    r = client.patch(f"{_reports(project)}/{report_id}", json={"settings": settings_})
    assert r.status_code == 200, r.text
    return r.json()


def _engagement(db, report_id):
    db.expire_all()
    dataset, _, summary = ClientReportService(db).build(db.get(Report, report_id))
    return dataset["engagement"], summary


# ---------------------------------------------------------------------------
# The installation's values
# ---------------------------------------------------------------------------

def test_the_inherited_keys_are_settings_and_the_author_is_a_required_detail():
    assert report_defaults.KEYS == ("author_name", "disclaimer")
    assert set(report_defaults.KEYS) <= set(SETTINGS_KEYS)
    assert set(report_defaults.LABELS) == set(report_defaults.MAX_CHARS) == set(report_defaults.KEYS)
    for key in report_defaults.KEYS:                       # a column of the project's defaults too
        assert hasattr(ReportProfile, key)
    # Asked for right after the client; a disclaimer is never owed (the template has its own).
    labels = [label for _, label in REQUIRED_DETAILS]
    assert labels.index("author") == labels.index("client") + 1
    assert "disclaimer" not in dict(REQUIRED_DETAILS)


def test_nothing_set_reads_as_nothing(client, db_session):
    r = client.get(URL)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "values": {"author_name": None, "disclaimer": None},
        "labels": {"author_name": "Author", "disclaimer": "Disclaimer"},
        "max_chars": {"author_name": 255, "disclaimer": 32768},
        "updated_at": None, "updated_by": None,
    }
    assert report_defaults.installation(db_session) == {"author_name": None, "disclaimer": None}


def test_a_global_admin_sets_them_and_the_change_is_audited(client, db_session, test_user):
    r = _put(client, author_name="  Example Security  ", disclaimer="Point in time.\n\n- No warranty.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["values"] == {"author_name": "Example Security", "disclaimer": "Point in time.\n\n- No warranty."}
    assert body["updated_by"] == "Test Admin" and body["updated_at"]
    assert client.get(URL).json() == body
    rows = {row.key: row for row in db_session.query(ReportDefault)}
    assert set(rows) == {"author_name", "disclaimer"} and rows["author_name"].updated_by_id == test_user.id

    (entry,) = db_session.query(AuditLog).filter(AuditLog.action == "report_defaults_updated").all()
    assert (entry.user_id, entry.resource_type, entry.resource_id) == (test_user.id, "system", "report_defaults")
    assert entry.details == {"changed": {
        "author_name": {"from": None, "to": "Example Security"},
        "disclaimer": {"from": None, "to": "Point in time.\n\n- No warranty."},
    }}

    # A key left out is not touched; saving what is there writes no audit row.
    r = _put(client, author_name="Example Security")
    assert r.status_code == 200 and r.json()["values"]["disclaimer"].startswith("Point in time.")
    assert db_session.query(AuditLog).filter(AuditLog.action == "report_defaults_updated").count() == 1
    # A changed one is audited with what it was.
    assert _put(client, author_name="Example Assessments").status_code == 200
    latest = (
        db_session.query(AuditLog).filter(AuditLog.action == "report_defaults_updated")
        .order_by(AuditLog.id.desc()).first()
    )
    assert latest.details == {"changed": {"author_name": {"from": "Example Security", "to": "Example Assessments"}}}


@pytest.mark.parametrize("blank", [None, "", "   \n "])
def test_a_blank_value_removes_the_key(client, db_session, blank):
    _put(client, author_name="Example Security", disclaimer="Ours.")
    r = _put(client, disclaimer=blank)
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"author_name": "Example Security", "disclaimer": None}
    assert [row.key for row in db_session.query(ReportDefault)] == ["author_name"]
    assert report_defaults.installation(db_session)["disclaimer"] is None
    # Removing what is not there is no change.
    before = db_session.query(AuditLog).filter(AuditLog.action == "report_defaults_updated").count()
    assert _put(client, disclaimer=blank).status_code == 200
    assert db_session.query(AuditLog).filter(AuditLog.action == "report_defaults_updated").count() == before


def test_an_unknown_key_or_an_overlong_text_is_refused_and_nothing_is_saved(client, db_session):
    r = _put(client, author_name="Kept out", client_name="Not inherited")
    assert r.status_code == 422 and "client_name" in r.json()["detail"]
    assert "author_name, disclaimer" in r.json()["detail"]
    r = _put(client, author_name="x" * 256)
    assert r.status_code == 422 and "255" in r.json()["detail"]
    assert _put(client, disclaimer="x" * 32769).status_code == 422
    assert client.put(URL, json={}).status_code == 422                       # `values` is the body
    db_session.rollback()
    assert db_session.query(ReportDefault).count() == 0
    assert db_session.query(AuditLog).filter(AuditLog.action == "report_defaults_updated").count() == 0
    assert _put(client, author_name="x" * 255).status_code == 200


def test_only_a_global_admin_writes_and_every_signed_in_user_reads(client, db_session, test_project):
    _put(client, author_name="Example Security")
    member = _user(db_session, 402, "mel")
    # A PROJECT admin is not a global one.
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role=ProjectRole.ADMIN.value))
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: member
    try:
        r = client.get(URL)
        assert r.status_code == 200 and r.json()["values"]["author_name"] == "Example Security"
        assert _put(client, author_name="Mine").status_code == 403
        assert _put(client, disclaimer=None).status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert report_defaults.installation(db_session)["author_name"] == "Example Security"


# ---------------------------------------------------------------------------
# The order: the report, else its project, else the installation
# ---------------------------------------------------------------------------

def test_a_report_uses_the_nearest_value_that_is_written(client, db_session, test_project):
    draft = _draft(client, test_project)                 # nothing written anywhere yet

    engagement, summary = _engagement(db_session, draft["id"])
    assert engagement["author_name"] is None and engagement["disclaimer"] is None
    assert "author" in summary["missing_details"]

    # 3. The installation's.
    _put(client, author_name="Installation Ltd", disclaimer="The installation's terms.")
    engagement, summary = _engagement(db_session, draft["id"])
    assert (engagement["author_name"], engagement["disclaimer"]) == ("Installation Ltd", "The installation's terms.")
    assert "author" not in summary["missing_details"]    # inherited is not missing

    # 2. The project's — each key on its own: the disclaimer stays the installation's.
    _profile(client, test_project, author_name="Project Team")
    engagement, _ = _engagement(db_session, draft["id"])
    assert (engagement["author_name"], engagement["disclaimer"]) == ("Project Team", "The installation's terms.")
    _profile(client, test_project, author_name="Project Team", disclaimer="The project's terms.")
    assert _engagement(db_session, draft["id"])[0]["disclaimer"] == "The project's terms."

    # 1. The report's own.
    _patch(client, test_project, draft["id"], author_name="This Report's Author")
    engagement, _ = _engagement(db_session, draft["id"])
    assert (engagement["author_name"], engagement["disclaimer"]) == ("This Report's Author", "The project's terms.")
    _patch(client, test_project, draft["id"], author_name="This Report's Author", disclaimer="This report's terms.")
    assert _engagement(db_session, draft["id"])[0]["disclaimer"] == "This report's terms."

    # Emptied again, each level falls back to the next — a blank is not a value.
    _patch(client, test_project, draft["id"], author_name="   ", disclaimer="")
    engagement, _ = _engagement(db_session, draft["id"])
    assert (engagement["author_name"], engagement["disclaimer"]) == ("Project Team", "The project's terms.")
    _profile(client, test_project)
    engagement, _ = _engagement(db_session, draft["id"])
    assert (engagement["author_name"], engagement["disclaimer"]) == ("Installation Ltd", "The installation's terms.")
    _put(client, author_name=None, disclaimer=None)
    engagement, summary = _engagement(db_session, draft["id"])
    assert engagement["author_name"] is None and engagement["disclaimer"] is None
    assert "author" in summary["missing_details"]


def test_a_draft_started_after_the_project_wrote_its_own_still_follows_the_project(client, db_session, test_project):
    """A new draft does not COPY the project's author and disclaimer: copied,
    it would keep printing the old ones after the project changed them (every
    other detail is a copy; these two are inherited)."""
    _profile(client, test_project, author_name="Project Team", disclaimer="The project's terms.")
    draft = _draft(client, test_project)
    assert draft["settings"]["author_name"] is None and draft["settings"]["disclaimer"] is None
    assert draft["inherited"] == {"author_name": "Project Team", "disclaimer": "The project's terms."}

    _profile(client, test_project, author_name="Renamed Team", disclaimer="New terms.")
    engagement, _ = _engagement(db_session, draft["id"])
    assert (engagement["author_name"], engagement["disclaimer"]) == ("Renamed Team", "New terms.")


def test_the_one_resolver_is_what_the_builder_uses(db_session, test_project):
    db_session.add(ReportDefault(key="author_name", text="Installation Ltd"))
    db_session.add(ReportDefault(key="disclaimer", text="  \n "))            # a stored blank is not a value
    profile = ReportProfile(project_id=test_project.id, disclaimer="The project's terms.")
    db_session.add(profile)
    db_session.commit()
    assert report_defaults.installation(db_session) == {"author_name": "Installation Ltd", "disclaimer": None}
    assert report_defaults.for_project(db_session, None) == {"author_name": "Installation Ltd", "disclaimer": None}
    assert report_defaults.for_project(db_session, profile) == {
        "author_name": "Installation Ltd", "disclaimer": "The project's terms.",
    }
    assert report_defaults.resolve(db_session, profile, {"author_name": " Mine ", "disclaimer": None}) == {
        "author_name": "Mine", "disclaimer": "The project's terms.",
    }
    assert report_defaults.resolve(db_session, None, {}) == {"author_name": "Installation Ltd", "disclaimer": None}


def test_the_pages_are_told_what_an_empty_field_inherits(client, db_session, test_project):
    base = _reports(test_project)
    # No profile row yet: the answer still says what would be inherited.
    assert client.get(f"{base}/profile").json()["inherited"] == {"author_name": None, "disclaimer": None}
    _put(client, author_name="Installation Ltd", disclaimer="The installation's terms.")
    profile = client.get(f"{base}/profile").json()
    assert profile["inherited"] == {"author_name": "Installation Ltd", "disclaimer": "The installation's terms."}
    assert (profile["author_name"], profile["disclaimer"]) == (None, None)       # its own are still empty

    draft = _draft(client, test_project)
    # The project's defaults page: its own value beside the installation's.
    saved = _profile(client, test_project, author_name="Project Team")
    assert saved["author_name"] == "Project Team"
    assert saved["inherited"] == {"author_name": "Installation Ltd", "disclaimer": "The installation's terms."}

    # A draft's page: what ITS empty fields use — the project's, else the installation's.
    one = client.get(f"{base}/{draft['id']}").json()
    assert one["settings"]["author_name"] is None
    assert one["inherited"] == {"author_name": "Project Team", "disclaimer": "The installation's terms."}
    # The same on the answer to a save, which the page replaces the report with.
    patched = _patch(client, test_project, draft["id"], author_name="This Report's Author")
    assert patched["settings"]["author_name"] == "This Report's Author"
    assert patched["inherited"] == {"author_name": "Project Team", "disclaimer": "The installation's terms."}
    # A list row does not pay for it.
    listed = client.get(base).json()["items"]
    assert listed and all(row["inherited"] is None for row in listed)


def test_an_issued_report_keeps_what_it_printed(client, db_session, test_project):
    _put(client, author_name="Installation Ltd", disclaimer="The installation's terms.")
    draft = _draft(client, test_project)
    issued = client.post(f"{_reports(test_project)}/{draft['id']}/issue")
    assert issued.status_code == 200, issued.text
    as_issued = {"author_name": "Installation Ltd", "disclaimer": "The installation's terms."}
    # Its own settings stay empty; what it PRINTED is its frozen dataset's.
    assert issued.json()["settings"]["author_name"] is None
    assert issued.json()["inherited"] == as_issued

    _put(client, author_name="Renamed Ltd", disclaimer=None)
    _profile(client, test_project, author_name="A later project value")
    db_session.expire_all()
    frozen = db_session.get(Report, draft["id"]).snapshot["dataset"]["engagement"]
    assert (frozen["author_name"], frozen["disclaimer"]) == ("Installation Ltd", "The installation's terms.")
    # The page of an issued report says what it was issued with, never today's default.
    assert client.get(f"{_reports(test_project)}/{draft['id']}").json()["inherited"] == as_issued
    _profile(client, test_project)
    # A revision starts from what was issued, not from today's default.
    revision = client.post(f"{_reports(test_project)}/{draft['id']}/revise")
    assert revision.status_code == 201, revision.text
    assert revision.json()["settings"]["author_name"] == "Installation Ltd"
    assert revision.json()["inherited"] == {"author_name": "Renamed Ltd", "disclaimer": None}

    # A report issued before the details existed has none to show.
    report = db_session.get(Report, draft["id"])
    snapshot = json.loads(json.dumps(report.snapshot))
    for key in report_defaults.KEYS:
        snapshot["dataset"]["engagement"].pop(key)
    report.snapshot = snapshot
    db_session.commit()
    assert client.get(f"{_reports(test_project)}/{draft['id']}").json()["inherited"] == {
        "author_name": None, "disclaimer": None,
    }


# ---------------------------------------------------------------------------
# A new report starts from the project's defaults
# ---------------------------------------------------------------------------

def test_a_new_draft_carries_the_projects_classification(client, db_session, test_project):
    """Reported by the owner, 2026-10-13: "classification ignored in the
    defaults for new report".  Server-side, a draft created after the
    defaults were saved has them — in its settings, in its dataset, and not
    among the missing details."""
    saved = _profile(
        client, test_project, classification="  TLP:AMBER  ", client_name="Example Corp",
        engagement_type="Internal", system_description="The estate.",
    )
    assert saved["classification"] == "TLP:AMBER"
    assert client.get(f"{_reports(test_project)}/profile").json()["classification"] == "TLP:AMBER"

    draft = _draft(client, test_project)
    assert draft["settings"]["classification"] == "TLP:AMBER"
    assert draft["settings"]["client_name"] == "Example Corp"
    assert "classification" not in draft["summary"]["missing_details"]
    one = client.get(f"{_reports(test_project)}/{draft['id']}").json()
    assert one["settings"]["classification"] == "TLP:AMBER"
    engagement, _ = _engagement(db_session, draft["id"])
    assert engagement["classification"] == "TLP:AMBER"
    db_session.expire_all()
    assert db_session.get(Report, draft["id"]).settings["classification"] == "TLP:AMBER"

    # The defaults are a starting point: changing them later leaves the draft's copy.
    _profile(client, test_project, classification="Public")
    assert client.get(f"{_reports(test_project)}/{draft['id']}").json()["settings"]["classification"] == "TLP:AMBER"
    assert _draft(client, test_project)["settings"]["classification"] == "Public"
    # A save of the draft that leaves the classification out of `settings` clears it
    # (the page sends the whole form): it is not silently re-inherited.
    cleared = _patch(client, test_project, draft["id"], client_name="Example Corp")
    assert cleared["settings"]["classification"] is None
    assert "classification" in cleared["summary"]["missing_details"]


def test_an_addendum_takes_the_classification_its_baseline_was_issued_with(client, db_session, test_project):
    _profile(client, test_project, classification="Confidential")
    draft = _draft(client, test_project)
    assert client.post(f"{_reports(test_project)}/{draft['id']}/issue").status_code == 200
    _profile(client, test_project, classification="Public")
    r = client.post(_reports(test_project), json={"kind": "addendum"})
    assert r.status_code == 201, r.text
    assert r.json()["settings"]["classification"] == "Confidential"
