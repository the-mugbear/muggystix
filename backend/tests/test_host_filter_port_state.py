"""Port / service / product / version conditions match OPEN ports by default
(v2.403.0).

For a closed or filtered port nmap fills ``service_name`` from its
port-number table, so "ssh 22/tcp · closed" is no evidence SSH runs — the
Hosts page matched 24 hosts for "Endpoint: service ssh", 20 of them with
only a closed/filtered 22.  A state is honoured only when the same
condition names it: the structured ``port_states`` list (``any`` = every
state) or the DSL ``@state`` suffix (``service:ssh@closed``, ``port:22@any``).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db import models
from app.services import host_query_dsl as dsl


OPEN, CLOSED, FILTERED, OTHER = "10.9.0.1", "10.9.0.2", "10.9.0.3", "10.9.0.4"


def _seed(db_session, project_id):
    """Three hosts with ssh on 22 in a different state each, plus one with an
    open http port and a closed ssh port elsewhere."""
    def host(ip):
        h = models.Host(project_id=project_id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.flush()
        return h

    def port(h, number, state, service, product=None):
        db_session.add(models.Port(
            host_id=h.id, port_number=number, protocol="tcp", state=state,
            service_name=service, service_product=product,
        ))

    port(host(OPEN), 22, "open", "ssh", "OpenSSH")
    port(host(CLOSED), 22, "closed", "ssh", "OpenSSH")
    port(host(FILTERED), 22, "filtered", "ssh")
    other = host(OTHER)
    port(other, 80, "open", "http")
    port(other, 2222, "closed", "ssh")
    db_session.flush()


def _ips(resp):
    assert resp.status_code == 200, resp.text
    return {item["ip_address"] for item in resp.json()["items"]}


def _hosts(client, project_id, **params):
    return _ips(client.get(f"/api/v1/projects/{project_id}/hosts/", params=params))


# ---------------------------------------------------------------------------
# The query DSL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", ["service:ssh", "svc:ssh", "port:22", "version:OpenSSH", "product:OpenSSH"])
def test_dsl_port_conditions_match_open_ports_only(client, db_session, test_project, q):
    _seed(db_session, test_project.id)
    assert _hosts(client, test_project.id, q=q) == {OPEN}


@pytest.mark.parametrize("q, expected", [
    ("service:ssh@closed", {CLOSED, OTHER}),
    ("service:ssh@filtered", {FILTERED}),
    ("service:ssh@any", {OPEN, CLOSED, FILTERED, OTHER}),
    ("port:22@closed", {CLOSED}),
    ("port:22@any", {OPEN, CLOSED, FILTERED}),
    ("port:22@open", {OPEN}),
    ('version:"OpenSSH@any"', {OPEN, CLOSED}),
    # Per value: 22 closed OR 80 open.
    ("port:22@closed,80", {CLOSED, OTHER}),
    ("service:ssh@closed,ssh@filtered", {CLOSED, FILTERED, OTHER}),
])
def test_dsl_explicit_state_is_honoured(client, db_session, test_project, q, expected):
    _seed(db_session, test_project.id)
    assert _hosts(client, test_project.id, q=q) == expected


def test_dsl_state_is_on_the_same_port_row(client, db_session, test_project):
    # OTHER has an open port (80) and a closed ssh (2222): `service:ssh` must
    # not match it by pairing ssh on one row with open on another.
    _seed(db_session, test_project.id)
    assert OTHER not in _hosts(client, test_project.id, q="service:ssh")


def test_dsl_portstate_alone_is_unchanged(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _hosts(client, test_project.id, q="portstate:closed") == {CLOSED, OTHER}


def test_dsl_unknown_state_suffix_is_400(client, db_session, test_project):
    _seed(db_session, test_project.id)
    resp = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": "service:ssh@shut"})
    assert resp.status_code == 400
    assert "unknown port state" in resp.text


def test_dsl_state_suffix_parse():
    assert dsl._split_port_state("service", "ssh@closed") == ("ssh", "closed")
    assert dsl._split_port_state("port", "22") == ("22", None)
    with pytest.raises(dsl.DSLError):
        dsl._split_port_state("port", "@any")
    # The trigram minimum applies to the matched text, not the suffix.
    with pytest.raises(dsl.DSLError):
        dsl.parse_query('version:"ab@any"')


# ---------------------------------------------------------------------------
# The structured filter (services= / ports= / port_states=)
# ---------------------------------------------------------------------------

def test_structured_service_and_port_match_open_ports_only(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _hosts(client, test_project.id, services="ssh") == {OPEN}
    assert _hosts(client, test_project.id, ports="22") == {OPEN}


def test_structured_explicit_state_is_honoured(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _hosts(client, test_project.id, services="ssh", port_states="closed") == {CLOSED, OTHER}
    assert _hosts(client, test_project.id, ports="22", port_states="closed,filtered") == {CLOSED, FILTERED}
    assert _hosts(client, test_project.id, services="ssh", port_states="any") == {OPEN, CLOSED, FILTERED, OTHER}


def test_structured_state_alone_is_unchanged(client, db_session, test_project):
    _seed(db_session, test_project.id)
    assert _hosts(client, test_project.id, port_states="filtered") == {FILTERED}


# ---------------------------------------------------------------------------
# The shared rule, and the tool-ready export's per-host port narrowing
# ---------------------------------------------------------------------------

def test_resolve_endpoint_states():
    from app.services.host_query_predicates import resolve_endpoint_states

    assert resolve_endpoint_states(None, has_endpoint=True) == ["open"]
    assert resolve_endpoint_states([], has_endpoint=False) is None
    assert resolve_endpoint_states(["Closed", ""], has_endpoint=True) == ["closed"]
    assert resolve_endpoint_states(["closed", "any"], has_endpoint=True) is None


def test_tool_output_port_narrowing_follows_the_rule():
    from app.api.v1.endpoints.hosts import _get_filtered_output_ports

    host = SimpleNamespace(ports=[
        SimpleNamespace(port_number=22, state="open", service_name="ssh"),
        SimpleNamespace(port_number=2222, state="closed", service_name="ssh"),
    ])
    nums = lambda f: [p.port_number for p in _get_filtered_output_ports(host, f)]  # noqa: E731
    assert nums({"services": "ssh"}) == [22]
    assert nums({"services": "ssh", "port_states": "closed"}) == [2222]
    assert nums({"services": "ssh", "port_states": "any"}) == [22, 2222]
    # No port condition: nothing narrows by state.
    assert nums({}) == [22, 2222]


def test_service_picker_counts_what_the_default_filter_returns(client, db_session, test_project):
    """The Add-filter service list said "ssh 4" beside a filter that returns 1:
    it counted closed and filtered ports, the filter now does not."""
    _seed(db_session, test_project.id)
    body = client.get(f"/api/v1/projects/{test_project.id}/hosts/filters/data").json()
    counts = {s["name"]: s["count"] for s in body["services"]}
    assert counts["ssh"] == 1
    assert counts["http"] == 1
