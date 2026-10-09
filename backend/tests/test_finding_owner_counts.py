"""``GET /findings`` → ``owner_counts``: the Findings page's Owner filter (v2.474.0).

Reported from a real deployment: several people had promoted findings (the
promoter owns what they promote) and the Owner filter could not list a
teammate's.  The page first offered every project MEMBER, which lists people
who own nothing; these counts are who owns the LISTED findings.  Pins:

* each count is the ``total`` of the list its owner opens (``owner_id=`` /
  ``unowned=true``) under the same other filters — the rule every count on a
  page follows;
* the counts respect every filter EXCEPT the owner, so everyone stays listed
  while one is chosen;
* nobody is listed who owns none of the listed findings; unowned comes last;
  an owner with no full name is named by username;
* the list, the severity roll-up and these counts are one selection
  (``FindingService._selection``): the totals agree.
"""
from datetime import datetime, timezone

from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding

from tests.conftest import TEST_USER_PW_HASH


def _user(db, user_id, username, full_name):
    u = User(
        id=user_id, username=username, email=f"{username}@example.com", full_name=full_name,
        hashed_password=TEST_USER_PW_HASH, role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(u)
    db.flush()
    return u


def _finding(db, project_id, title, owner_id, *, severity="high", status="open"):
    db.add(Finding(project_id=project_id, title=title, severity=severity, status=status,
                   source="manual", owner_id=owner_id))


def _seed(db, project):
    ana = _user(db, 21, "ana", "Ana Ruiz")
    cy = _user(db, 22, "cy", None)               # no full name: named by username
    _user(db, 23, "idle", "Idle Member")         # owns nothing: never listed
    # test_user (id 1, "Test Admin") owns two.
    _finding(db, project.id, "TLS weak A", 1)
    _finding(db, project.id, "TLS weak B", 1, severity="low")
    _finding(db, project.id, "SMB signing A", ana.id)
    _finding(db, project.id, "SMB signing B", ana.id, status="false_positive")
    _finding(db, project.id, "TLS weak C", ana.id, severity="critical")
    _finding(db, project.id, "Default creds", cy.id)
    _finding(db, project.id, "Orphan A", None)
    _finding(db, project.id, "TLS orphan B", None, severity="low")
    db.commit()
    return ana, cy


def _get(client, project, **params):
    r = client.get(f"/api/v1/projects/{project.id}/findings", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _counts(body):
    return [(c["owner_name"], c["count"]) for c in body["owner_counts"]]


def test_owner_counts_name_the_owners_of_the_listed_findings(client, db_session, test_project, test_user):
    _seed(db_session, test_project)
    body = _get(client, test_project)      # no status = every status
    # Named owners by name, unowned last; the member who owns nothing is absent.
    assert _counts(body) == [("Ana Ruiz", 3), ("cy", 1), ("Test Admin", 2), (None, 2)]
    assert body["owner_counts"][-1]["owner_id"] is None
    assert sum(c["count"] for c in body["owner_counts"]) == body["total"] == 8


def test_each_owner_count_is_the_total_of_the_list_it_opens(client, db_session, test_project, test_user):
    _seed(db_session, test_project)
    for filters in ({}, {"status": "active"}, {"search": "tls"}, {"severity": "low"}):
        body = _get(client, test_project, **filters)
        assert body["owner_counts"], filters
        for entry in body["owner_counts"]:
            chosen = {"unowned": "true"} if entry["owner_id"] is None else {"owner_id": entry["owner_id"]}
            opened = _get(client, test_project, **filters, **chosen)
            assert opened["total"] == entry["count"], (filters, entry)


def test_the_counts_ignore_the_owner_filter_and_follow_every_other(client, db_session, test_project, test_user):
    ana, _cy = _seed(db_session, test_project)
    everyone = _counts(_get(client, test_project))
    # Choosing an owner narrows the LIST, not the options.
    chosen = _get(client, test_project, owner_id=ana.id)
    assert chosen["total"] == 3
    assert _counts(chosen) == everyone
    assert _counts(_get(client, test_project, unowned="true")) == everyone

    # Another filter does narrow them: only owners of a matching finding remain.
    assert _counts(_get(client, test_project, search="tls")) == [
        ("Ana Ruiz", 1), ("Test Admin", 2), (None, 1),
    ]
    # "active" leaves out Ana's false positive.
    assert ("Ana Ruiz", 2) in _counts(_get(client, test_project, status="active"))


def test_the_severity_rollup_and_the_owner_counts_are_the_lists_own_selection(client, db_session, test_project, test_user):
    _seed(db_session, test_project)
    body = _get(client, test_project, status="active", search="tls")
    assert sum(body["severity_counts"].values()) == body["total"]
    assert sum(c["count"] for c in body["owner_counts"]) == body["total"]
