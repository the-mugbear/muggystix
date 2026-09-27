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
