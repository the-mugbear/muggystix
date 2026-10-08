"""A finding's endpoints carry their network segment (the finding page's groups).

``GET /findings/{id}`` returns every endpoint; the page groups them under the
project's ONE segment rule — ``subnet_insight_service.group_hosts_into_segments``
(sites; most-specific subnets when the project defines no site), the Posture
grid's and the Evidence matrix's columns — plus Evidence's ``unmapped`` for a
host outside every scoped subnet.  Pins:

* each endpoint's ``segment`` is the one that shared function assigns its host;
* the rule is decided over the project, not over the finding's own hosts;
* the detail route's statement count does not grow with the endpoints;
* a list row's preview carries no segment and costs no statement for one.
"""
from contextlib import contextmanager

from sqlalchemy import event

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.services.evidence_service import evidence_segments
from app.services.finding_service import endpoint_segments
from app.services.subnet_insight_service import group_hosts_into_segments, resolve_host_locations


@contextmanager
def selects(db):
    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", count)


def _scope(db, project):
    scope = models.Scope(name="s", project_id=project.id)
    db.add(scope)
    db.flush()
    return scope


def _subnet(db, scope, cidr, site=None):
    subnet = models.Subnet(scope_id=scope.id, cidr=cidr, site=site.name if site else None,
                           site_id=site.id if site else None)
    db.add(subnet)
    db.flush()
    return subnet


def _site(db, project, name):
    site = models.Site(project_id=project.id, name=name)
    db.add(site)
    db.flush()
    return site


def _host(db, project, ip, *subnets):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    for subnet in subnets:
        db.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
    return host


def _finding(db, project, user, hosts, title="Issue"):
    finding = Finding(project_id=project.id, title=title, severity="high", status="confirmed",
                      source="manual", created_by_id=user.id)
    db.add(finding)
    db.flush()
    for host in hosts:
        db.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    db.commit()
    return finding.id


def _detail(client, project, finding_id):
    response = client.get(f"/api/v1/projects/{project.id}/findings/{finding_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _by_ip(detail):
    return {h["ip_address"]: h["segment"] for h in detail["hosts"]}


def _shared(db, project):
    """host id -> segment key, straight from the shared grouping."""
    grouping = group_hosts_into_segments(resolve_host_locations(db, project.id))
    return grouping, {hid: key for key, ids in grouping["hosts"].items() for hid in ids}


def test_a_project_with_sites_groups_by_site(client, db_session, test_project, test_user):
    scope = _scope(db_session, test_project)
    london, paris = _site(db_session, test_project, "London DC"), _site(db_session, test_project, "Paris")
    wide = _subnet(db_session, scope, "10.1.0.0/16", london)
    narrow = _subnet(db_session, scope, "10.1.5.0/24")              # unlabelled: inherits London DC
    other = _subnet(db_session, scope, "10.2.0.0/24", paris)
    bare = _subnet(db_session, scope, "10.3.0.0/24")                # no site anywhere above it
    hosts = [
        _host(db_session, test_project, "10.1.0.4", wide),
        _host(db_session, test_project, "10.1.5.9", wide, narrow),
        _host(db_session, test_project, "10.2.0.7", other),
        _host(db_session, test_project, "10.3.0.2", bare),
        _host(db_session, test_project, "172.16.0.9"),              # outside every scoped subnet
    ]
    # A London host that is NOT on the finding: the segment order is the project's.
    _host(db_session, test_project, "10.1.0.200", wide)
    fid = _finding(db_session, test_project, test_user, hosts)

    segments = _by_ip(_detail(client, test_project, fid))
    assert segments["10.1.0.4"] == {"key": str(london.id), "label": "London DC", "kind": "site", "order": 0}
    assert segments["10.1.5.9"] == segments["10.1.0.4"]
    assert segments["10.2.0.7"]["key"] == str(paris.id) and segments["10.2.0.7"]["kind"] == "site"
    assert segments["10.3.0.2"]["key"] == "unassigned" and segments["10.3.0.2"]["label"] == "Unassigned"
    assert segments["10.3.0.2"]["kind"] == "unassigned"
    assert segments["172.16.0.9"] == {
        "key": "unmapped", "label": "Outside scoped subnets", "kind": "unmapped", "order": 3,
    }

    # Exactly what the shared function says, key, label and order.
    grouping, key_of = _shared(db_session, test_project)
    for host in hosts[:4]:
        segment = segments[host.ip_address]
        assert segment["key"] == key_of[host.id]
        assert segment["label"] == grouping["labels"][segment["key"]]
        assert segment["order"] == grouping["keys"].index(segment["key"])
    # …and Evidence's matrix names the same columns, the unmapped one included.
    evidence = evidence_segments(db_session, test_project.id)
    assert evidence["keys"] == [s["key"] for s in sorted(
        {s["key"]: s for s in segments.values()}.values(), key=lambda s: s["order"])]
    assert evidence["labels"]["unmapped"] == segments["172.16.0.9"]["label"]


def test_a_project_with_only_subnets_groups_by_most_specific_subnet(client, db_session, test_project, test_user):
    scope = _scope(db_session, test_project)
    wide = _subnet(db_session, scope, "10.1.0.0/16")
    narrow = _subnet(db_session, scope, "10.1.5.0/24")
    hosts = [
        _host(db_session, test_project, "10.1.0.4", wide),
        _host(db_session, test_project, "10.1.5.9", wide, narrow),
        _host(db_session, test_project, "10.1.5.10", wide, narrow),
    ]
    fid = _finding(db_session, test_project, test_user, hosts)
    segments = _by_ip(_detail(client, test_project, fid))
    assert segments["10.1.5.9"] == {
        "key": f"subnet:{narrow.id}", "label": "10.1.5.0/24", "kind": "subnet", "order": 0,
    }
    assert segments["10.1.5.10"] == segments["10.1.5.9"]
    assert segments["10.1.0.4"] == {
        "key": f"subnet:{wide.id}", "label": "10.1.0.0/16", "kind": "subnet", "order": 1,
    }


def test_the_rule_is_the_projects_not_the_findings(client, db_session, test_project, test_user):
    """The finding's own hosts carry no site, but the project defines one: the
    other pages group by site, so the finding's hosts are "Unassigned" — not
    grouped by subnet as they would be if only they were asked."""
    scope = _scope(db_session, test_project)
    site = _site(db_session, test_project, "HQ")
    _host(db_session, test_project, "10.9.0.1", _subnet(db_session, scope, "10.9.0.0/24", site))
    bare = _subnet(db_session, scope, "10.3.0.0/24")
    fid = _finding(db_session, test_project, test_user, [_host(db_session, test_project, "10.3.0.2", bare)])
    [endpoint] = _detail(client, test_project, fid)["hosts"]
    assert endpoint["segment"]["key"] == "unassigned" and endpoint["segment"]["kind"] == "unassigned"


def test_no_scope_puts_every_endpoint_in_one_bucket(client, db_session, test_project, test_user):
    hosts = [_host(db_session, test_project, f"10.7.0.{i}") for i in range(1, 6)]
    fid = _finding(db_session, test_project, test_user, hosts)
    detail = _detail(client, test_project, fid)
    assert {h["segment"]["key"] for h in detail["hosts"]} == {"unmapped"}
    assert {h["segment"]["order"] for h in detail["hosts"]} == {0}


def test_a_change_to_an_endpoint_answers_with_the_segments_too(client, db_session, test_project, test_user):
    """The page replaces the finding with a change's answer: an answer without
    segments would flatten its groups."""
    scope = _scope(db_session, test_project)
    subnet = _subnet(db_session, scope, "10.1.5.0/24")
    fid = _finding(db_session, test_project, test_user, [_host(db_session, test_project, "10.1.5.9", subnet)])
    [row] = _detail(client, test_project, fid)["hosts"]
    base = f"/api/v1/projects/{test_project.id}/findings/{fid}"
    one = client.patch(f"{base}/endpoints/{row['id']}", json={"host_status": "retest"})
    assert one.status_code == 200, one.text
    assert one.json()["hosts"][0]["segment"] == row["segment"]
    several = client.patch(f"{base}/endpoints", json={"finding_host_ids": [row["id"]], "host_status": "remediated"})
    assert several.status_code == 200, several.text
    assert several.json()["hosts"][0]["segment"] == row["segment"]


def test_the_service_is_two_statements_for_any_number_of_hosts(db_session, test_project, test_user):
    scope = _scope(db_session, test_project)
    subnets = [_subnet(db_session, scope, f"10.{n}.0.0/24") for n in range(1, 5)]
    hosts = [_host(db_session, test_project, f"10.{1 + i % 4}.0.{1 + i // 4}", subnets[i % 4]) for i in range(40)]
    db_session.commit()
    ids, pid = [h.id for h in hosts], test_project.id   # read before counting: the commit expired them
    with selects(db_session) as few:
        endpoint_segments(db_session, pid, ids[:2])
    with selects(db_session) as many:
        out = endpoint_segments(db_session, pid, ids)
    assert len(few) == len(many) == 2, many
    assert set(out) == set(ids)
    with selects(db_session) as none:
        assert endpoint_segments(db_session, pid, []) == {}
    assert none == []


def test_the_detail_route_does_not_grow_with_the_endpoints(client, db_session, test_project, test_user):
    scope = _scope(db_session, test_project)
    subnets = [_subnet(db_session, scope, f"10.{n}.0.0/24") for n in range(1, 5)]

    def finding_on(count, net):
        hosts = [
            _host(db_session, test_project, f"10.{1 + i % 4}.{net}.{1 + i // 4}", subnets[i % 4])
            for i in range(count)
        ]
        return _finding(db_session, test_project, test_user, hosts, title=f"Issue {net}")

    small, large = finding_on(3, 0), finding_on(120, 1)

    def statements_for(finding_id):
        db_session.expire_all()
        with selects(db_session) as statements:
            detail = _detail(client, test_project, finding_id)
        return detail, statements

    few, base = statements_for(small)
    many, statements = statements_for(large)
    assert len(few["hosts"]) == 3 and len(many["hosts"]) == 120
    assert all(h["segment"]["kind"] == "subnet" for h in many["hosts"])
    assert len(statements) == len(base), "\n".join(statements)
    # The segments are two of them: the project's subnets, and its host mappings.
    assert len([s for s in statements if "host_subnet_mappings" in s]) == 1, "\n".join(statements)


def test_a_list_rows_preview_carries_no_segment(client, db_session, test_project, test_user):
    scope = _scope(db_session, test_project)
    subnet = _subnet(db_session, scope, "10.1.5.0/24")
    _finding(db_session, test_project, test_user, [_host(db_session, test_project, "10.1.5.9", subnet)])
    db_session.expire_all()
    with selects(db_session) as statements:
        body = client.get(f"/api/v1/projects/{test_project.id}/findings").json()
    [row] = body["items"]
    assert row["host_count"] == 1
    assert row["hosts"] == [{
        "id": row["hosts"][0]["id"], "host_id": row["hosts"][0]["host_id"], "ip_address": "10.1.5.9",
        "hostname": None, "name_id": None, "fqdn": None, "host_status": "open", "segment": None,
    }]
    # The list asks nothing about subnets.
    assert not [s for s in statements if "host_subnet_mappings" in s or "subnets" in s], "\n".join(statements)
