"""Review 2026-10-10, H3 — a discrete host filter that names nobody / nothing
was DROPPED, and the answer was the whole project (``/hosts/ids`` then fed
``/hosts/bulk/*``).

Every discrete parameter is now checked inside the shared builder
(``host_query.check_discrete_filters`` + the assignee lookup), so both doors
refuse the same values.  The line: a value that cannot be understood is a 422
naming it; a value that is VALID and matches no host is an empty list.
"""
from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models import FollowStatus, HostFollow
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.services import agent_session_service as sessions
from app.services import host_query_predicates as P

PAGE_READS = ["hosts/", "hosts/ids", "hosts/filters/data"]
AGENT_READS = ["/api/v1/agent/assist/hosts", "/api/v1/agent/assist/hosts/count"]


def _user(db, uid, name, role=UserRole.MEMBER, active=True):
    user = User(
        id=uid, username=name, email=f"{name}@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=role, is_active=active,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def world(db_session, test_project, test_user):
    """Four hosts; ``alice`` (member) holds one; ``idle`` is a member with
    nothing; ``outsider`` exists but belongs to another project only;
    ``leaver`` is no longer a member and still holds a host."""
    db = db_session
    other = Project(name="other-h3", slug="other-h3")
    db.add(other)
    db.flush()
    alice = _user(db, 93001, "alice")
    idle = _user(db, 93002, "idle")
    outsider = _user(db, 93003, "outsider")
    leaver = _user(db, 93004, "leaver")
    for user, project in ((alice, test_project), (idle, test_project), (outsider, other)):
        db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    hosts = []
    for last in (1, 2, 3, 4):
        host = models.Host(project_id=test_project.id, ip_address=f"10.93.0.{last}", state="up")
        db.add(host)
        hosts.append(host)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(HostFollow(host_id=hosts[0].id, user_id=alice.id, status=FollowStatus.IN_REVIEW, assigned_at=now))
    db.add(HostFollow(host_id=hosts[1].id, user_id=leaver.id, status=FollowStatus.IN_REVIEW, assigned_at=now))
    tag = models.HostTag(project_id=test_project.id, name="unused")
    db.add(tag)
    db.commit()
    agent = sessions.resolve_project_agent(db, project_id=test_project.id, user=alice)
    session = sessions.create_agent_session(
        db, project_id=test_project.id, agent_id=agent.id, started_by_id=alice.id,
    )
    raw = sessions.mint_session_key(db, agent=agent, session=session)
    db.commit()
    return {
        "pid": test_project.id, "hosts": hosts, "tag": tag.id, "idle": idle.id,
        "headers": {"X-API-Key": raw},
    }


REFUSED = [
    # (params, a piece of the refusal that names the value or the parameter)
    ({"assigned_to": "alcie"}, "alcie"),            # the review's typo
    ({"assigned_to": "outsider"}, "outsider"),      # exists, but not here
    ({"assigned_to": "93003"}, "93003"),            # the same account by id
    ({"assigned_to": "99999999"}, "99999999"),
    ({"scan_ids": ","}, "scan_ids"),
    ({"scan_ids": "12,abc"}, "abc"),
    ({"tech": " , "}, "tech"),
    ({"sites": ","}, "sites"),
    ({"weaknesses": ","}, "weaknesses"),
    ({"weaknesses": "bogus"}, "bogus"),
    ({"checks": ","}, "checks"),
    ({"checks": "nope"}, "nope"),
    ({"port_states": ","}, "port_states"),
    ({"port_states": "opne"}, "opne"),
    ({"follow_status": "revieved"}, "revieved"),
    ({"asns": "foo"}, "foo"),
    ({"asns": ["15169", "AS-x"]}, "AS-x"),
]


@pytest.mark.parametrize("path", PAGE_READS)
@pytest.mark.parametrize("params,named", REFUSED)
def test_a_filter_that_names_nothing_is_refused_not_dropped(client, world, path, params, named):
    resp = client.get(f"/api/v1/projects/{world['pid']}/{path}", params=params)
    assert resp.status_code == 422, f"{path} {params}: {resp.status_code} {resp.text[:300]}"
    assert named in resp.text


def _ids(client, world, **params):
    resp = client.get(f"/api/v1/projects/{world['pid']}/hosts/ids", params=params)
    assert resp.status_code == 200, f"{params}: {resp.status_code} {resp.text[:300]}"
    return sorted(resp.json()["ids"])


def test_a_valid_value_that_matches_no_host_is_an_empty_list(client, world):
    """The other side of the line: these are understood, and nobody has them."""
    for params in (
        {"assigned_to": "idle"},                # a member with nothing assigned
        {"assigned_to": str(world["idle"])},
        {"scan_ids": "999999"},
        {"tags": str(world["tag"])},            # a real tag on no host
        {"tech": "nosuchtech"},
        {"sites": "Nowhere"},
        {"orgs": "Nobody Inc"},
        {"countries": "ZZ"},
        {"asns": "AS64500"},
        {"asns": "4200000000"},                 # a 32-bit ASN no row can hold
        {"os_filter": "plan9"},
    ):
        assert _ids(client, world, **params) == [], params


def test_the_assignee_filter_still_filters(client, world):
    first, second = world["hosts"][0].id, world["hosts"][1].id
    assert _ids(client, world, assigned_to="alice") == [first]
    assert _ids(client, world, assigned_to="ALICE") == [first]
    assert _ids(client, world, assigned_to="93001") == [first]
    # No longer a member, still holding a host: still nameable.
    assert _ids(client, world, assigned_to="leaver") == [second]
    assert _ids(client, world, assigned_to="any") == sorted([first, second])
    assert len(_ids(client, world, assigned_to="none")) == 2
    assert _ids(client, world, assigned_to="me") == []
    # A blank value is "not given", as for every other parameter.
    assert len(_ids(client, world, assigned_to=" ", scan_ids="", tech="")) == 4


@pytest.mark.parametrize("path", AGENT_READS)
@pytest.mark.parametrize("q,named", [
    ("assigned:alcie", "alcie"),
    ("assigned:outsider", "outsider"),
    ("asn:foo", "foo"),
])
def test_the_agents_door_refuses_the_same_names(client, world, path, q, named):
    """The agents' host reads take no ``assigned_to``; they name an assignee
    through ``q=``, which is the same lookup and refuses the same people."""
    resp = client.get(path, headers=world["headers"], params={"q": q})
    assert resp.status_code == 400, f"{path} {q}: {resp.status_code} {resp.text[:300]}"
    assert named in resp.text


@pytest.mark.parametrize("path", AGENT_READS)
@pytest.mark.parametrize("params,named", [({"ports": ","}, "ports"), ({"services": " , "}, "services")])
def test_the_agents_door_refuses_a_list_that_names_nothing(client, world, path, params, named):
    resp = client.get(path, headers=world["headers"], params=params)
    assert resp.status_code == 422, f"{path} {params}: {resp.status_code} {resp.text[:300]}"
    assert named in resp.text


def test_an_unknown_name_and_an_account_elsewhere_get_the_same_answer(client, world):
    """No existence oracle: the refusal does not depend on whether the
    installation has such an account."""
    def refusal(name):
        resp = client.get(f"/api/v1/projects/{world['pid']}/hosts/ids", params={"assigned_to": name})
        return resp.status_code, resp.json()["detail"].replace(name, "<name>")

    assert refusal("outsider") == refusal("nobody-at-all")

    def dsl(name):
        resp = client.get(
            "/api/v1/agent/assist/hosts", headers=world["headers"], params={"q": f"assigned:{name}"},
        )
        return resp.status_code, resp.json()["detail"].replace(name, "<name>")

    assert dsl("outsider") == dsl("nobody-at-all")
    assert dsl("outsider")[0] == 400


def test_the_vocabulary_lists_exactly_who_the_filter_accepts(client, db_session, world):
    """ONE definition: every username the agents' vocabulary offers is accepted
    by ``assigned:``, and it is ``project_assignees``."""
    names = client.get("/api/v1/agent/assist/vocabulary", headers=world["headers"]).json()["usernames"]
    assert names == sorted({u for _id, u in P.project_assignees(db_session, world["pid"]).all()})
    # members, the global admin (the test user), and the leaver who holds a host
    assert set(names) == {"alice", "idle", "leaver", "test-admin"}
    for name in names:
        resp = client.get(
            "/api/v1/agent/assist/hosts/count", headers=world["headers"], params={"q": f"assigned:{name}"},
        )
        assert resp.status_code == 200, f"{name}: {resp.text[:200]}"


def test_an_at_sign_in_version_text_is_text_not_a_port_state(client, db_session, world):
    host = world["hosts"][2]
    db_session.add(models.Port(
        host_id=host.id, port_number=22, protocol="tcp", state="open",
        service_name="ssh", service_product="git@github shell",
    ))
    db_session.commit()

    def ips(q):
        resp = client.get("/api/v1/agent/assist/hosts", headers=world["headers"], params={"q": q})
        assert resp.status_code == 200, f"{q}: {resp.status_code} {resp.text[:300]}"
        return [h["ip_address"] for h in resp.json()["items"]]

    assert ips('version:"git@github"') == ["10.93.0.3"]
    # A real state after "@" is still a qualifier, and service: still refuses a bad one.
    assert ips('version:"git@github@any"') == ["10.93.0.3"]
    assert ips('version:"git@github@closed"') == []
    bad = client.get("/api/v1/agent/assist/hosts", headers=world["headers"], params={"q": "service:ssh@opne"})
    assert bad.status_code == 400 and "unknown port state" in bad.text
