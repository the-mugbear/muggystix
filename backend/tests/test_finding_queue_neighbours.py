"""``GET /findings/{id}/neighbours``: the finding page's Previous / Next
through the Findings list it was opened from (2.529.0).

The list decides what is walked (owner, 2026-10-23) — an analyst on
Owner: me walks their own findings, an admin on an unfiltered list walks all —
so the read is the list's own selection and order.  Pins:

* for every sort, direction and a set of filters, stepping from each row
  gives exactly the rows the list pages through, its position and its total;
* a finding the list no longer holds (closed under ``active``) has no
  position, and its neighbours are the rows either side of where it sat;
* a finding of another project is a 404, as is a host filter naming a host
  that is not here.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Host
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project

SORTS = [None, "severity", "status", "title", "host_count", "source", "created_at", "owner"]


def _seed(db, project, test_user):
    host = Host(project_id=project.id, ip_address="10.0.0.5")
    db.add(host)
    db.flush()
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = [
        # title, severity, status, owner, source, hosts — duplicates on every
        # sort key, so ties fall to the id and are walked the same way.
        ("TLS weak", "high", "open", test_user.id, "manual", 2),
        ("SMB signing", "high", "confirmed", None, "scanner", 0),
        ("Default creds", "critical", "open", test_user.id, "manual", 1),
        ("Old SSH", "low", "false_positive", None, "scanner", 0),
        ("TLS expired", "medium", "open", None, "manual", 1),
        ("Banner", "info", "remediated", test_user.id, "scanner", 0),
        ("TLS weak", "high", "open", None, "manual", 0),
    ]
    made = []
    for i, (title, sev, status, owner, source, n_hosts) in enumerate(rows):
        f = Finding(project_id=project.id, title=title, severity=sev, status=status,
                    owner_id=owner, source=source,
                    created_at=base + timedelta(days=i % 3))
        db.add(f)
        db.flush()
        for _ in range(n_hosts):
            # One endpoint row per host+name pair; a second row needs a name,
            # so host_count is carried by distinct hosts instead.
            h = Host(project_id=project.id, ip_address=f"10.1.{i}.{len(made)}{_}")
            db.add(h)
            db.flush()
            db.add(FindingHost(finding_id=f.id, host_id=h.id, host_status="open"))
        made.append(f)
    db.commit()
    return made, host


def _list_ids(client, project, params):
    ids, offset = [], 0
    while True:
        r = client.get(f"/api/v1/projects/{project.id}/findings",
                       params={**params, "limit": 2, "offset": offset})
        assert r.status_code == 200, r.text
        body = r.json()
        ids += [f["id"] for f in body["items"]]
        offset += 2
        if offset >= body["total"]:
            return ids, body["total"]


def _neighbours(client, project, finding_id, params):
    r = client.get(f"/api/v1/projects/{project.id}/findings/{finding_id}/neighbours", params=params)
    assert r.status_code == 200, r.text
    return r.json()


FILTERS = [{}, {"status": "active"}, {"owner_id": 1}, {"unowned": "true"}, {"search": "tls"},
           {"source": "manual", "severity": "high"}]


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize("direction", [None, "asc", "desc"])
def test_stepping_walks_the_rows_the_list_pages_through(client, db_session, test_project, test_user,
                                                        sort, direction):
    _seed(db_session, test_project, test_user)
    for filters in FILTERS:
        params = dict(filters)
        if sort:
            params["sort"] = sort
        if direction:
            params["dir"] = direction
        ids, total = _list_ids(client, test_project, params)
        assert ids, params
        for i, fid in enumerate(ids):
            got = _neighbours(client, test_project, fid, params)
            assert got["position"] == i + 1, (params, fid)
            assert got["total"] == total
            assert (got["previous"] or {}).get("id") == (ids[i - 1] if i > 0 else None), (params, i)
            assert (got["next"] or {}).get("id") == (ids[i + 1] if i + 1 < len(ids) else None), (params, i)


def test_a_finding_that_left_the_list_steps_from_where_it_sat(client, db_session, test_project, test_user):
    made, _host = _seed(db_session, test_project, test_user)
    params = {"status": "active", "sort": "title", "dir": "asc"}
    before, total = _list_ids(client, test_project, params)
    middle = before[2]
    db_session.get(Finding, middle).status = "remediated"   # closed while it was open
    db_session.commit()

    got = _neighbours(client, test_project, middle, params)
    assert got["position"] is None
    assert got["total"] == total - 1
    assert got["previous"]["id"] == before[1]
    assert got["next"]["id"] == before[3]
    assert got["next"]["title"] == db_session.get(Finding, before[3]).title


def test_another_projects_finding_is_not_found(client, db_session, test_project, test_user):
    _seed(db_session, test_project, test_user)
    other = Project(name="other", slug="other", description="x", is_default=False)
    db_session.add(other)
    db_session.flush()
    theirs = Finding(project_id=other.id, title="Elsewhere", severity="low", status="open", source="manual")
    db_session.add(theirs)
    db_session.commit()
    r = client.get(f"/api/v1/projects/{test_project.id}/findings/{theirs.id}/neighbours")
    assert r.status_code == 404


def test_the_hosts_filter_is_the_lists(client, db_session, test_project, test_user):
    made, _host = _seed(db_session, test_project, test_user)
    r = client.get(f"/api/v1/projects/{test_project.id}/findings/{made[0].id}/neighbours",
                   params={"host_id": 999_999})
    assert r.status_code == 404
