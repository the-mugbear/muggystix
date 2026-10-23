"""Release notes (2.525.0): the file's shape, a line for every release, who reads them.

The owner's rule (2026-10-23): every change writes the maintainers' changelog
AND a concise line here.  It is kept by this file, not by memory — the version
in ``platform_version.json`` must have an entry with at least one line, so a
bumped release with no notes fails the gate (``scripts/bump-version.py`` adds
the empty entry that says so).
"""
import json

import pytest

from app.api.deps import get_current_user
from app.core.config import settings
from app.main import app
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.services import release_notes

URL = "/api/v1/references/release-notes"


def _file():
    return json.loads(release_notes.NOTES_PATH.read_text(encoding="utf-8"))


def test_the_notes_have_the_promised_shape():
    releases = release_notes.check(_file())
    assert releases, "the notes start at 2.448.0"
    assert (releases[-1]["backend"], releases[-1]["frontend"]) == ("2.448.0", "5.327.0")


def test_the_running_version_has_its_notes():
    current = (settings.APP_VERSION, settings.FRONTEND_VERSION)
    entry = next((r for r in release_notes.check(_file()) if (r["backend"], r["frontend"]) == current), None)
    assert entry is not None, (
        f"release_notes.json has no entry for {current[0]} / {current[1]}: write what this release changed, "
        "in a line each, for the people using BlueStick (Reference -> Release notes)"
    )
    assert entry["entries"], f"the notes for {current[0]} / {current[1]} are empty: write this release's lines"


def _release(backend="2.1.0", frontend="5.0.0", date="2026-01-01", entries=None):
    return {"backend": backend, "frontend": frontend, "date": date,
            "entries": [{"kind": "fixed", "text": "Something."}] if entries is None else entries}


@pytest.mark.parametrize("bad, says", [
    ({"releases": [_release(backend="2.1")]}, "'backend'"),
    ({"releases": [_release(date="1 Jan")]}, "'date'"),
    ({"releases": [_release(entries=[{"kind": "misc", "text": "x"}])]}, "'kind'"),
    ({"releases": [_release(entries=[{"kind": "new", "text": "x" * 301}])]}, "'text'"),
    ({"releases": [_release(), _release(backend="2.2.0", date="2025-12-31")]}, "newest first"),
    ({"releases": [_release(), _release()]}, "twice"),
    # 2.525.1 (review of the release notes): what the file must already be,
    # so nothing is re-sorted or trimmed per request.
    ({"releases": [_release(entries=[{"kind": "fixed", "text": "a"}, {"kind": "new", "text": "b"}])]},
     "kind order"),
    ({"releases": [_release(entries=[{"kind": "new", "text": " padded "}])]}, "spaces"),
    ({"releases": [_release(entries=[{"kind": "new", "text": 42}])]}, "'text'"),
    ({"releases": [_release(entries=["just a string"])]}, "an object"),
    ({"releases": ["2.1.0"]}, "must be an object"),
    ({"releases": [_release(date="2026-01-01"), _release(backend="2.0.0", date="2026-01-02")]}, "dated after"),
    # Only the top release — the one being prepared — may be empty: an empty
    # one below it is a version that was never released.
    ({"releases": [_release(), _release(backend="2.0.0", entries=[])]}, "only the top release may be empty"),
    ([], "'releases'"),
])
def test_a_malformed_file_is_refused_naming_the_problem(bad, says):
    with pytest.raises(release_notes.ReleaseNotesError) as refused:
        release_notes.check(bad)
    assert says in str(refused.value)


def test_the_top_release_may_be_empty_while_it_is_written():
    assert release_notes.check({"releases": [_release(backend="2.2.0", entries=[]), _release()]})


# --- who reads them ---------------------------------------------------------

def _member(db, project, role, user_id):
    user = User(
        id=user_id, username=f"rn-{user_id}", email=f"rn-{user_id}@example.com",
        hashed_password="x", role=UserRole.MEMBER, is_active=True,
    )
    db.add(user)
    db.flush()
    if role is not None:
        db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return user


@pytest.fixture
def as_user(client):
    """The suite's switch (the ``client`` fixture clears the overrides)."""
    def use(user):
        app.dependency_overrides[get_current_user] = lambda: user
    return use


def test_an_analyst_somewhere_reads_them(client, db_session, test_project, as_user):
    as_user(_member(db_session, test_project, ProjectRole.ANALYST.value, 95101))
    body = client.get(URL)
    assert body.status_code == 200, body.text
    body = body.json()
    assert body["current"] == {"backend": settings.APP_VERSION, "frontend": settings.FRONTEND_VERSION}
    assert body["kinds"] == list(release_notes.KINDS)
    assert body["releases"] == release_notes.releases()


@pytest.mark.parametrize("role", [ProjectRole.VIEWER.value, ProjectRole.AUDITOR.value, None])
def test_below_analyst_everywhere_is_refused(client, db_session, test_project, as_user, role):
    as_user(_member(db_session, test_project, role, 95102))
    refused = client.get(URL)
    assert refused.status_code == 403
    assert "analysts" in refused.json()["detail"]


def test_an_analyst_of_another_project_reads_them_too(client, db_session, test_project, as_user):
    """The notes are the installation's, not a project's: analyst anywhere."""
    other = Project(name="rn-other", slug="rn-other")
    db_session.add(other)
    db_session.flush()
    user = _member(db_session, test_project, ProjectRole.VIEWER.value, 95103)
    db_session.add(ProjectMembership(project_id=other.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    db_session.commit()
    as_user(user)
    assert client.get(URL).status_code == 200


def test_the_global_admin_reads_them(client):
    assert client.get(URL).status_code == 200


def test_no_one_signed_out_reads_them(client):
    app.dependency_overrides.pop(get_current_user)
    assert client.get(URL).status_code == 401
