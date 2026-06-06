"""Tests for the query-UX router: /hosts/query/{schema,validate,history}."""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.models_auth import User, UserRole


def _base(pid):
    return f"/api/v1/projects/{pid}/hosts/query"


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_schema(client, test_project):
    r = client.get(f"{_base(test_project.id)}/schema")
    assert r.status_code == 200, r.text
    body = r.json()
    names = {f["name"] for f in body["fields"]}
    assert {"port", "cve", "has", "tag", "note"} <= names
    assert "nse" not in names
    assert body["examples"]


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def test_validate_valid_returns_match_count(client, db_session, test_project):
    scan = models.Scan(project_id=test_project.id, filename="f", scan_type="nmap")
    db_session.add(scan)
    db_session.flush()
    for ip in ("10.0.0.1", "10.0.0.2"):
        h = models.Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.flush()
        db_session.add(models.Port(host_id=h.id, port_number=80, protocol="tcp", state="open"))
    db_session.flush()

    r = client.post(f"{_base(test_project.id)}/validate", json={"q": "port:80"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True
    assert body["match_count"] == 2
    assert body["leaf_count"] == 1


def test_validate_invalid_is_200_with_position(client, test_project):
    r = client.post(f"{_base(test_project.id)}/validate", json={"q": "port:"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is False
    assert body["error"]["position"] is not None
    assert body["match_count"] is None


def test_validate_empty_is_valid_noop(client, test_project):
    r = client.post(f"{_base(test_project.id)}/validate", json={"q": "   "})
    assert r.status_code == 200
    assert r.json()["valid"] is True


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def test_history_record_and_list(client, test_project):
    pid = test_project.id
    r = client.post(f"{_base(pid)}/history", json={"q": "port:443", "result_count": 7})
    assert r.status_code == 201, r.text
    assert r.json()["q"] == "port:443"

    r = client.get(f"{_base(pid)}/history")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["q"] == "port:443"
    assert rows[0]["result_count"] == 7


def test_history_dedupes_consecutive(client, db_session, test_project):
    pid = test_project.id
    client.post(f"{_base(pid)}/history", json={"q": "tag:prod"})
    client.post(f"{_base(pid)}/history", json={"q": "tag:prod", "result_count": 3})
    rows = client.get(f"{_base(pid)}/history").json()
    assert len(rows) == 1, "consecutive identical queries should collapse"
    assert rows[0]["result_count"] == 3


def test_history_keeps_distinct_in_order(client, test_project):
    pid = test_project.id
    for q in ["port:80", "port:443", "os:linux"]:
        client.post(f"{_base(pid)}/history", json={"q": q})
    rows = client.get(f"{_base(pid)}/history").json()
    assert [r["q"] for r in rows] == ["os:linux", "port:443", "port:80"]  # newest first


def test_history_trim_to_cap(client, db_session, test_project):
    pid = test_project.id
    for n in range(55):
        client.post(f"{_base(pid)}/history", json={"q": f"port:{1000 + n}"})
    kept = db_session.query(models.HostQueryHistory).filter(
        models.HostQueryHistory.project_id == pid
    ).count()
    assert kept == 50, f"expected trim to 50, got {kept}"


def test_history_delete_entry(client, test_project):
    pid = test_project.id
    created = client.post(f"{_base(pid)}/history", json={"q": "port:22"}).json()
    r = client.delete(f"{_base(pid)}/history/{created['id']}")
    assert r.status_code == 204
    assert client.get(f"{_base(pid)}/history").json() == []


def test_history_clear_all(client, test_project):
    pid = test_project.id
    client.post(f"{_base(pid)}/history", json={"q": "port:80"})
    client.post(f"{_base(pid)}/history", json={"q": "port:443"})
    assert client.delete(f"{_base(pid)}/history").status_code == 204
    assert client.get(f"{_base(pid)}/history").json() == []


def test_history_isolated_per_user(client, db_session, test_project):
    """A row owned by another user must not appear in my history."""
    other = User(
        id=999, username="other", email="other@example.com", full_name="Other",
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(models.HostQueryHistory(
        user_id=other.id, project_id=test_project.id, q="secret:other"
    ))
    db_session.commit()

    rows = client.get(f"{_base(test_project.id)}/history").json()
    assert all(r["q"] != "secret:other" for r in rows)
