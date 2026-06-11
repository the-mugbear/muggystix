"""Project-default Hosts filter view (admin-promoted saved view).

Pins the behaviour the feature commit (3e5be3d) only verified by hand:
  * promote sets one view as the project default; GET returns it
  * promoting a second view demotes the first (single-default invariant)
  * DELETE clears the default; GET then returns null
  * the promote ownership guard (a view you don't own → 404)
  * the partial-unique index backstop actually rejects a second default
    written behind the endpoint's back

The ``client`` fixture authenticates as a global admin, so it passes the
``require_project_role(ProjectRole.ADMIN)`` gate; these tests exercise the
invariant logic, not the authz tier (which has its own coverage in deps).
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import HostFilterView


def _make_view(db_session, project_id, user_id, name):
    view = HostFilterView(
        user_id=user_id,
        project_id=project_id,
        name=name,
        filter_json={"filters": {"state": "up"}},
    )
    db_session.add(view)
    db_session.commit()
    db_session.refresh(view)
    return view


def _base(project_id):
    return f"/api/v1/projects/{project_id}/hosts"


def test_promote_sets_default_and_get_returns_it(client, db_session, test_project, test_user):
    view = _make_view(db_session, test_project.id, test_user.id, "Critical web")

    resp = client.post(f"{_base(test_project.id)}/views/{view.id}/promote")
    assert resp.status_code == 200
    assert resp.json()["is_project_default"] is True

    got = client.get(f"{_base(test_project.id)}/default-view")
    assert got.status_code == 200
    assert got.json()["id"] == view.id


def test_promoting_second_view_demotes_first(client, db_session, test_project, test_user):
    a = _make_view(db_session, test_project.id, test_user.id, "View A")
    b = _make_view(db_session, test_project.id, test_user.id, "View B")

    assert client.post(f"{_base(test_project.id)}/views/{a.id}/promote").status_code == 200
    assert client.post(f"{_base(test_project.id)}/views/{b.id}/promote").status_code == 200

    # Exactly one default remains, and it's B.
    got = client.get(f"{_base(test_project.id)}/default-view")
    assert got.json()["id"] == b.id

    db_session.expire_all()
    defaults = (
        db_session.query(HostFilterView)
        .filter(
            HostFilterView.project_id == test_project.id,
            HostFilterView.is_project_default.is_(True),
        )
        .all()
    )
    assert [v.id for v in defaults] == [b.id]


def test_clear_default_removes_it(client, db_session, test_project, test_user):
    view = _make_view(db_session, test_project.id, test_user.id, "View")
    client.post(f"{_base(test_project.id)}/views/{view.id}/promote")

    resp = client.delete(f"{_base(test_project.id)}/default-view")
    assert resp.status_code == 204

    got = client.get(f"{_base(test_project.id)}/default-view")
    assert got.status_code == 200
    assert got.json() is None


def test_get_default_returns_null_when_none_set(client, db_session, test_project, test_user):
    _make_view(db_session, test_project.id, test_user.id, "Not promoted")
    got = client.get(f"{_base(test_project.id)}/default-view")
    assert got.status_code == 200
    assert got.json() is None


def test_promote_view_not_owned_returns_404(client, db_session, test_project, test_user):
    """The promote query filters on ``user_id == current_user.id`` — a view
    owned by another user (even within the same project) must 404, not be
    silently promotable."""
    from app.db.models_auth import User, UserRole

    # Explicit id: test_user is inserted with id=1, which doesn't bump the
    # users_pkey sequence, so an auto-id here would collide on id=1.
    other = User(
        id=2,
        username="other-admin",
        email="other@example.com",
        full_name="Other Admin",
        hashed_password="x",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    foreign_view = _make_view(db_session, test_project.id, other.id, "Theirs")
    resp = client.post(f"{_base(test_project.id)}/views/{foreign_view.id}/promote")
    assert resp.status_code == 404


def test_promote_missing_view_returns_404(client, test_project):
    resp = client.post(f"{_base(test_project.id)}/views/999999/promote")
    assert resp.status_code == 404


def test_partial_unique_index_rejects_two_defaults(db_session, test_project, test_user):
    """DB-level backstop: the partial unique index forbids a second
    is_project_default row in the same project even if the endpoint's
    demote-first logic is bypassed."""
    a = _make_view(db_session, test_project.id, test_user.id, "A")
    a.is_project_default = True
    db_session.commit()

    b = _make_view(db_session, test_project.id, test_user.id, "B")
    b.is_project_default = True
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
