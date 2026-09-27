"""``/validate`` counts coverage over the plan's own candidates (v2.428.1).

Agent feedback #9: a plan filtered to one host (``search=10.10.0.10``) got
``matching_filter=1`` from ``/context`` but ``eligible_hosts_remaining=415``
from ``/validate`` — validation counted every host in the project. Both now
read one candidate query (``_plan_candidate_query``).
"""
from app.db import models


def _host_with_open_port(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    db.add(models.Port(host_id=host.id, port_number=80, protocol="tcp", state="open"))
    return host


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "plan"})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def test_validate_counts_only_the_plans_filtered_candidates(client, db_session, test_project):
    for ip in ("10.10.0.10", "10.10.0.20", "10.10.0.30"):
        _host_with_open_port(db_session, test_project, ip)
    db_session.commit()
    headers = _start(client, test_project)

    r = client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "One host", "filter_criteria": {"search": "10.10.0.10"},
    })
    assert r.status_code in (200, 201), r.text
    plan_id = r.json()["id"]

    ctx = client.get(f"/api/v1/agent/test-plans/{plan_id}/context", headers=headers)
    assert ctx.status_code == 200, ctx.text
    matching = ctx.json()["summary"]["matching_filter"]
    assert matching == 1

    v = client.get(f"/api/v1/agent/test-plans/{plan_id}/validate", headers=headers)
    assert v.status_code == 200, v.text
    cov = v.json()["coverage"]
    # The same set /context pages through — not the project's three hosts.
    assert cov["eligible_hosts_remaining"] == matching
    assert cov["policy_matching_remaining"] + cov["non_policy_with_open_ports"] == matching


def test_validate_counts_the_whole_project_for_an_unfiltered_plan(client, db_session, test_project):
    for ip in ("10.10.1.10", "10.10.1.20"):
        _host_with_open_port(db_session, test_project, ip)
    db_session.commit()
    headers = _start(client, test_project)

    plan_id = client.post("/api/v1/agent/test-plans", headers=headers,
                          json={"title": "Everything"}).json()["id"]
    v = client.get(f"/api/v1/agent/test-plans/{plan_id}/validate", headers=headers).json()
    assert v["coverage"]["eligible_hosts_remaining"] == 2


def test_list_filters_from_the_agent_route_do_not_crash_context_or_validate(client, db_session, test_project):
    """POST /agent/test-plans stores subnets/ports/services as LISTS; the host
    filter took comma strings, so /context answered 500 (agent feedback #19)."""
    for ip in ("10.10.2.10", "10.10.2.20", "10.10.3.30"):
        _host_with_open_port(db_session, test_project, ip)
    db_session.commit()
    headers = _start(client, test_project)
    r = client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "Two /32s", "filter_criteria": {
            "subnets": ["10.10.2.10/32", "10.10.2.20/32"], "ports": [80], "services": ["http"],
        },
    })
    assert r.status_code in (200, 201), r.text
    plan_id = r.json()["id"]
    ctx = client.get(f"/api/v1/agent/test-plans/{plan_id}/context", headers=headers)
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["summary"]["matching_filter"] == 2
    v = client.get(f"/api/v1/agent/test-plans/{plan_id}/validate", headers=headers)
    assert v.status_code == 200, v.text
    assert v.json()["coverage"]["eligible_hosts_remaining"] == 2


def test_a_plan_can_target_exact_hosts_by_id_or_query(client, db_session, test_project):
    """MCP acceptance feedback #19: planning "the hosts in review by me" had
    to be faked with /32 subnet filters.  host_ids / q make a fixed selection."""
    hosts = [_host_with_open_port(db_session, test_project, f"10.10.4.{i}") for i in (1, 2, 3)]
    db_session.commit()
    headers = _start(client, test_project)

    by_ids = client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "Two hosts", "host_ids": [hosts[0].id, hosts[2].id]})
    assert by_ids.status_code == 201, by_ids.text
    ctx = client.get(f"/api/v1/agent/test-plans/{by_ids.json()['id']}/context", headers=headers).json()
    assert {h["id"] for h in ctx["candidate_hosts"]} == {hosts[0].id, hosts[2].id}

    by_q = client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "One host", "q": "ip:10.10.4.2"})
    assert by_q.status_code == 201, by_q.text
    v = client.get(f"/api/v1/agent/test-plans/{by_q.json()['id']}/validate", headers=headers).json()
    assert v["coverage"]["eligible_hosts_remaining"] == 1
    # The reviewer can see how the list was chosen (MCP acceptance run 2:
    # the q was lost, and the description was empty).
    assert by_q.json()["description"] == (
        "Host selection: 1 host(s) from q=ip:10.10.4.2, resolved when the plan was created."
    )
    described = client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "Why", "description": "Hosts in review.", "host_ids": [hosts[1].id]}).json()
    assert described["description"].startswith("Hosts in review.\n\nHost selection: 1 host(s) from host_ids")

    assert client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "x", "q": "ip:10.99.99.99"}).status_code == 422
    assert client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "x", "host_ids": [999999]}).status_code == 422
    assert client.post("/api/v1/agent/test-plans", headers=headers, json={
        "title": "x", "host_ids": [hosts[0].id], "q": "ip:10.10.4.1"}).status_code == 422
