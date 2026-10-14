"""Where a report's engagement details come from.

Owner, 2026-10-13: "make it inherit from a global setting but allow teams to
modify it per project" (the author and the disclaimer, 2.495.0); owner,
2026-10-14: "Every detail follows the project unless the report overrides
it".  The nearest level that holds a value wins:

1. the report's own override (a key present in its ``settings``),
2. the project's report defaults (``report_profiles``),
3. the installation's (``report_defaults``; a global admin, System settings)
   for the author and the disclaimer; the project's analysts and admins for
   the team.

``services/report_defaults.resolve`` is the one place that order is applied —
the dataset builder calls it — and the pages show what a detail follows
(``ReportProfileOut.inherited`` for the defaults' author and disclaimer;
``ReportOut.followed`` / ``effective`` for a report).
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
from app.services.client_report_service import REQUIRED_DETAILS, ClientReportService

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


def _lead(db, project, user):
    """The signed-in admin as a member of the project: its assessment team."""
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=ProjectRole.ADMIN.value))
    db.commit()


def _engagement(db, report_id):
    db.expire_all()
    dataset, _, summary = ClientReportService(db).build(db.get(Report, report_id))
    return dataset["engagement"], summary


# ---------------------------------------------------------------------------
# The installation's values
# ---------------------------------------------------------------------------

def test_the_installation_keys_are_details_and_the_author_is_a_required_detail():
    assert report_defaults.INSTALLATION_KEYS == ("author_name", "disclaimer")
    assert set(report_defaults.INSTALLATION_KEYS) <= set(report_defaults.DETAIL_KEYS)
    assert set(report_defaults.LIST_KEYS) <= set(report_defaults.DETAIL_KEYS)
    assert set(report_defaults.LABELS) == set(report_defaults.MAX_CHARS) == set(report_defaults.INSTALLATION_KEYS)
    for key in report_defaults.DETAIL_KEYS:                # every detail is a column of the project's defaults
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


def test_a_new_draft_overrides_nothing_and_follows_every_project_default(client, db_session, test_project, test_user):
    """Owner, 2026-10-14: a copied value looked like one someone typed, and
    silently diverged when the project's default changed.  A new draft holds
    no details of its own; each one it prints is the project's, as it stands."""
    _lead(db_session, test_project, test_user)
    _profile(
        client, test_project, client_name="Example Corp", classification="TLP:AMBER",
        engagement_type="Internal", system_description="The estate.", author_name="Project Team",
        disclaimer="The project's terms.", distribution=[{"name": "CISO", "email": "ciso@example.com"}],
        testers=[{"name": "Ana", "role": "Lead"}],
    )
    draft = _draft(client, test_project)
    assert draft["settings"] == {}
    db_session.expire_all()
    assert db_session.get(Report, draft["id"]).settings == {}
    assert draft["followed"]["client_name"] == draft["effective"]["client_name"] == "Example Corp"
    assert draft["effective"]["testers"] == [{"user_id": None, "name": "Ana", "role": "Lead", "email": None}]
    for label in ("client", "classification", "engagement type", "system description", "author",
                  "assessment team", "distribution list"):
        assert label not in draft["summary"]["missing_details"], label   # a followed value is present

    # Every detail follows a change of the project's defaults — in the
    # dataset the report prints, and on its page.
    _profile(
        client, test_project, client_name="Renamed Corp", classification="Public",
        author_name="Renamed Team", disclaimer="New terms.", distribution=[], testers=[],
    )
    engagement, summary = _engagement(db_session, draft["id"])
    assert (engagement["client_name"], engagement["classification"]) == ("Renamed Corp", "Public")
    assert (engagement["author_name"], engagement["disclaimer"]) == ("Renamed Team", "New terms.")
    assert engagement["engagement_type"] is None and engagement["distribution"] == []
    # No team saved any more: the project's analysts and admins.
    assert [t["name"] for t in engagement["testers"]] == ["Test Admin"]
    assert "distribution list" in summary["missing_details"] and "engagement type" in summary["missing_details"]
    one = client.get(f"{_reports(test_project)}/{draft['id']}").json()
    assert one["effective"]["client_name"] == one["followed"]["client_name"] == "Renamed Corp"
    assert [t["name"] for t in one["followed"]["testers"]] == ["Test Admin"]


def test_a_draft_overrides_one_detail_and_reverts_it(client, db_session, test_project):
    """PATCH `settings` is the COMPLETE override map: a key sent overrides, a
    key left out follows.  The page overrides one detail and reverts it by
    leaving it out."""
    _profile(client, test_project, client_name="Example Corp", classification="Confidential",
             distribution=[{"name": "CISO", "email": "ciso@example.com"}])
    draft = _draft(client, test_project)

    one = _patch(client, test_project, draft["id"], client_name="  Other Corp  ")
    assert one["settings"] == {"client_name": "Other Corp"}             # trimmed, the only override
    assert one["effective"]["client_name"] == "Other Corp"
    assert one["followed"]["client_name"] == "Example Corp"             # what Revert returns to
    assert one["effective"]["classification"] == "Confidential"         # still followed

    # Another override; the first one is kept only because it is sent again.
    two = _patch(client, test_project, draft["id"], client_name="Other Corp", classification="Secret")
    assert two["settings"] == {"client_name": "Other Corp", "classification": "Secret"}
    # Revert the client: left out of the map.
    reverted = _patch(client, test_project, draft["id"], classification="Secret")
    assert reverted["settings"] == {"classification": "Secret"}
    assert reverted["effective"]["client_name"] == "Example Corp"
    # A blank or null text is not an override either: it follows again.
    blank = _patch(client, test_project, draft["id"], classification="   ", client_name=None)
    assert blank["settings"] == {}
    assert blank["effective"]["classification"] == "Confidential"
    db_session.expire_all()
    assert db_session.get(Report, draft["id"]).settings == {}

    # Other fields of the PATCH leave the overrides alone.
    _patch(client, test_project, draft["id"], engagement_type="Web")
    r = client.patch(f"{_reports(test_project)}/{draft['id']}", json={"title": "Renamed"})
    assert r.status_code == 200 and r.json()["settings"] == {"engagement_type": "Web"}


def test_a_list_is_overridden_as_a_whole_and_may_be_overridden_to_empty(client, db_session, test_project, test_user):
    _lead(db_session, test_project, test_user)
    _profile(client, test_project, distribution=[{"name": "CISO", "email": "ciso@example.com"}])
    draft = _draft(client, test_project)
    # On purpose, no distribution list: `[]` present is an override.
    nobody = _patch(client, test_project, draft["id"], distribution=[])
    assert nobody["settings"] == {"distribution": []}
    assert nobody["effective"]["distribution"] == []
    assert nobody["followed"]["distribution"] == [{"name": "CISO", "email": "ciso@example.com"}]
    engagement, summary = _engagement(db_session, draft["id"])
    assert engagement["distribution"] == [] and "distribution list" in summary["missing_details"]
    # The team too — and an empty override does not fall back to the project's members.
    team = _patch(client, test_project, draft["id"], testers=[])
    assert team["settings"] == {"testers": []}
    assert _engagement(db_session, draft["id"])[0]["testers"] == []
    # A list sent null follows again.
    back = _patch(client, test_project, draft["id"], distribution=None, testers=None)
    assert back["settings"] == {}
    assert back["effective"]["distribution"] == [{"name": "CISO", "email": "ciso@example.com"}]
    assert [t["name"] for t in back["effective"]["testers"]] == ["Test Admin"]


def test_an_override_is_validated_like_a_detail(client, db_session, test_project):
    draft = _draft(client, test_project)
    url = f"{_reports(test_project)}/{draft['id']}"
    assert client.patch(url, json={"settings": {"classification": "x" * 101}}).status_code == 422
    assert client.patch(url, json={"settings": {"testers": [{"name": ""}]}}).status_code == 422
    assert client.patch(url, json={"settings": {"distribution": [{"name": "x"}] * 101}}).status_code == 422
    # An unknown key is not stored.
    r = client.patch(url, json={"settings": {"surprise": "x", "client_name": "Example Corp"}})
    assert r.status_code == 200 and r.json()["settings"] == {"client_name": "Example Corp"}
    db_session.expire_all()
    assert db_session.get(Report, draft["id"]).settings == {"client_name": "Example Corp"}


def test_the_one_resolver_is_what_the_builder_uses(db_session, test_project):
    db_session.add(ReportDefault(key="author_name", text="Installation Ltd"))
    db_session.add(ReportDefault(key="disclaimer", text="  \n "))            # a stored blank is not a value
    profile = ReportProfile(
        project_id=test_project.id, disclaimer="The project's terms.", client_name="Example Corp",
        distribution=[{"name": "CISO"}],
    )
    db_session.add(profile)
    db_session.commit()
    team = [{"user_id": 1, "name": "Ana", "role": "Tester", "email": None}]
    asked = []

    def project_team():
        asked.append(1)
        return team

    assert report_defaults.installation(db_session) == {"author_name": "Installation Ltd", "disclaimer": None}
    follows = report_defaults.followed(db_session, profile, project_team)
    assert follows == {
        "client_name": "Example Corp", "classification": None, "engagement_type": None, "testers": team,
        "distribution": [{"name": "CISO"}], "system_description": None, "applications": None,
        "thick_clients": None, "other_targets": None,
        "author_name": "Installation Ltd", "disclaimer": "The project's terms.",
    }
    assert report_defaults.followed(db_session, None, lambda: [])["author_name"] == "Installation Ltd"
    asked.clear()
    resolved = report_defaults.resolve(
        db_session, profile, {"author_name": " Mine ", "disclaimer": None, "testers": [], "client_name": ""},
        project_team,
    )
    assert resolved["author_name"] == "Mine" and resolved["disclaimer"] == "The project's terms."
    assert resolved["testers"] == [] and resolved["client_name"] == "Example Corp"
    assert asked == []                     # an overridden team never asks for the project's members
    assert report_defaults.overrides(
        {"author_name": " Mine ", "disclaimer": None, "testers": [], "client_name": "", "distribution": None, "x": 1},
    ) == {"author_name": "Mine", "testers": []}


def test_the_pages_are_told_what_each_detail_follows(client, db_session, test_project):
    base = _reports(test_project)
    # No profile row yet: the defaults' answer still says what the installation holds.
    assert client.get(f"{base}/profile").json()["inherited"] == {"author_name": None, "disclaimer": None}
    _put(client, author_name="Installation Ltd", disclaimer="The installation's terms.")
    profile = client.get(f"{base}/profile").json()
    assert profile["inherited"] == {"author_name": "Installation Ltd", "disclaimer": "The installation's terms."}
    assert (profile["author_name"], profile["disclaimer"]) == (None, None)       # its own are still empty

    draft = _draft(client, test_project)
    saved = _profile(client, test_project, author_name="Project Team")
    assert saved["author_name"] == "Project Team"
    assert saved["inherited"] == {"author_name": "Installation Ltd", "disclaimer": "The installation's terms."}

    # A draft's page: what it follows — the project's, else the installation's.
    one = client.get(f"{base}/{draft['id']}").json()
    assert one["settings"] == {}
    assert (one["followed"]["author_name"], one["followed"]["disclaimer"]) == (
        "Project Team", "The installation's terms.")
    assert one["effective"] == one["followed"]
    # The same on the answer to a save, which the page replaces the report with.
    patched = _patch(client, test_project, draft["id"], author_name="This Report's Author")
    assert patched["settings"] == {"author_name": "This Report's Author"}
    assert patched["followed"]["author_name"] == "Project Team"
    assert patched["effective"]["author_name"] == "This Report's Author"
    # A list row does not pay for it.
    listed = client.get(base).json()["items"]
    assert listed and all(row["effective"] is None and row["followed"] is None for row in listed)
    assert listed[0]["settings"] == {"author_name": "This Report's Author"}


def test_an_issued_report_keeps_what_it_printed(client, db_session, test_project):
    _put(client, author_name="Installation Ltd", disclaimer="The installation's terms.")
    _profile(client, test_project, client_name="Example Corp")
    draft = _draft(client, test_project)
    issued = client.post(f"{_reports(test_project)}/{draft['id']}/issue")
    assert issued.status_code == 200, issued.text
    # It overrode nothing; what it PRINTED is its frozen dataset's, and it
    # follows nothing any more.
    assert issued.json()["settings"] == {}
    assert issued.json()["followed"] is None
    assert issued.json()["effective"]["author_name"] == "Installation Ltd"
    assert issued.json()["effective"]["client_name"] == "Example Corp"

    _put(client, author_name="Renamed Ltd", disclaimer=None)
    _profile(client, test_project, author_name="A later project value", client_name="Later Corp")
    db_session.expire_all()
    frozen = db_session.get(Report, draft["id"]).snapshot["dataset"]["engagement"]
    assert (frozen["author_name"], frozen["disclaimer"]) == ("Installation Ltd", "The installation's terms.")
    # The page of an issued report says what it was issued with, never today's default.
    after = client.get(f"{_reports(test_project)}/{draft['id']}").json()
    assert after["effective"]["author_name"] == "Installation Ltd"
    assert after["effective"]["client_name"] == "Example Corp"
    _profile(client, test_project)
    # A revision starts from what was issued, held as overrides — not from today's default.
    revision = client.post(f"{_reports(test_project)}/{draft['id']}/revise")
    assert revision.status_code == 201, revision.text
    assert revision.json()["settings"]["author_name"] == "Installation Ltd"
    assert revision.json()["settings"]["client_name"] == "Example Corp"
    assert revision.json()["followed"]["author_name"] == "Renamed Ltd"

    # A report issued before the details existed has none to show.
    report = db_session.get(Report, draft["id"])
    snapshot = json.loads(json.dumps(report.snapshot))
    for key in report_defaults.INSTALLATION_KEYS:
        snapshot["dataset"]["engagement"].pop(key)
    report.snapshot = snapshot
    db_session.commit()
    effective = client.get(f"{_reports(test_project)}/{draft['id']}").json()["effective"]
    assert (effective["author_name"], effective["disclaimer"]) == (None, None)


# ---------------------------------------------------------------------------
# A new report follows the project's defaults
# ---------------------------------------------------------------------------

def test_a_new_draft_prints_the_projects_classification(client, db_session, test_project):
    """Reported by the owner, 2026-10-13: "classification ignored in the
    defaults for new report".  Server-side, a draft created after the
    defaults were saved prints them — in its dataset, on its page, and not
    among the missing details — and keeps following them."""
    saved = _profile(
        client, test_project, classification="  TLP:AMBER  ", client_name="Example Corp",
        engagement_type="Internal", system_description="The estate.",
    )
    assert saved["classification"] == "TLP:AMBER"
    assert client.get(f"{_reports(test_project)}/profile").json()["classification"] == "TLP:AMBER"

    draft = _draft(client, test_project)
    assert draft["effective"]["classification"] == "TLP:AMBER"
    assert draft["effective"]["client_name"] == "Example Corp"
    assert "classification" not in draft["summary"]["missing_details"]
    engagement, _ = _engagement(db_session, draft["id"])
    assert engagement["classification"] == "TLP:AMBER"

    # The defaults change: the draft follows them; a new draft too.
    _profile(client, test_project, classification="Public")
    assert client.get(f"{_reports(test_project)}/{draft['id']}").json()["effective"]["classification"] == "Public"
    assert _draft(client, test_project)["effective"]["classification"] == "Public"
    # A save of the draft that leaves the classification out of `settings`
    # follows the project — it is not cleared.
    saved = _patch(client, test_project, draft["id"], client_name="Example Corp")
    assert saved["effective"]["classification"] == "Public"
    assert "classification" not in saved["summary"]["missing_details"]


def test_an_addendum_takes_the_classification_its_baseline_was_issued_with(client, db_session, test_project):
    _profile(client, test_project, classification="Confidential", client_name="Example Corp")
    draft = _draft(client, test_project)
    assert client.post(f"{_reports(test_project)}/{draft['id']}/issue").status_code == 200
    _profile(client, test_project, classification="Public", engagement_type="Web")
    r = client.post(_reports(test_project), json={"kind": "addendum"})
    assert r.status_code == 201, r.text
    # What the client already has is held as an override…
    assert r.json()["settings"]["classification"] == "Confidential"
    assert r.json()["settings"]["client_name"] == "Example Corp"
    # …and a detail the baseline went out without follows the project.
    assert "engagement_type" not in r.json()["settings"]
    assert r.json()["effective"]["engagement_type"] == "Web"
