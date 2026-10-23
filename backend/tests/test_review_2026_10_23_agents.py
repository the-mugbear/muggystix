"""Review 2026-10-23 — the agent surface (build C).

* H-AG1 — renewal ignored project membership: a global admin with no
  membership who lost the admin role could renew for the session's whole life
  and, re-added to the project, use the old key again.
* H-AG2 — an agent's OS correction was replaced by the next ``nmap -O``.
* R-AG1 — the rate-bucket sweep could never run; the table only grew.
* R-AG2 — ``/agent/assist/notes`` was a second Collaboration feed.
* R-AG3 — ``/agent/assist/names`` re-assembled the Names page's query
  without the ``shared`` state; the page answered an unknown state with 400.
* Nits — a key with no expiry never lapsed; renewal had no rate limit; the
  upload door let an expired key spool its body; the claim of replaced
  proposals locked by proposal id.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.api import deps
from app.db import models
from app.db.models_agent import AgentRateBucket, AgentSession
from app.db.models_auth import APIKey, UserRole
from app.db.models_confidence import ConflictHistory
from app.db.models_project import ProjectMembership, ProjectRole
from app.parsers.nmap_parser import NmapXMLParser
from app.services import dns_name_service
from app.services import host_work
from tests.test_review_2026_10_10_agent_accounts import _analyst, _identity, _renew, _session_key


# --------------------------------------------------------------------- H-AG1

def _non_member_admin(db, project, user_id):
    user = _analyst(db, project, user_id=user_id)
    user.role = UserRole.ADMIN
    db.query(ProjectMembership).filter_by(project_id=project.id, user_id=user.id).delete()
    db.commit()
    return user


def test_a_demoted_non_member_admin_cannot_renew_and_the_old_key_stays_dead(
    client, db_session, test_project,
):
    user = _non_member_admin(db_session, test_project, 95101)
    raw, session = _session_key(db_session, test_project, user)
    assert _renew(client, raw).status_code == 200          # an admin's authority

    user.role = UserRole.MEMBER                             # demoted, still not a member
    db_session.commit()

    refused = _renew(client, raw)
    assert refused.status_code == 401, refused.text       # was 200
    assert refused.json()["detail"]["error"] == "operator_not_a_member"
    assert refused.json()["detail"]["recoverable"] is False
    db_session.expire_all()
    assert db_session.get(AgentSession, session.id).status == "ended"

    # Re-added as an analyst: the old key does not come back.
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    db_session.commit()
    assert _identity(client, raw).status_code == 401       # was 200
    assert _renew(client, raw).status_code == 401


def test_the_gate_ends_the_session_of_an_operator_who_left_too(client, db_session, test_project):
    """Without a renewal: the demoted operator's agent calls before its key
    lapses; the 403 ends the session, so a later membership revives nothing."""
    user = _non_member_admin(db_session, test_project, 95102)
    raw, session = _session_key(db_session, test_project, user)
    assert _identity(client, raw).status_code == 200
    user.role = UserRole.MEMBER
    db_session.commit()

    assert _identity(client, raw).status_code == 403
    db_session.expire_all()
    assert db_session.get(AgentSession, session.id).status == "ended"
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    db_session.commit()
    assert _identity(client, raw).status_code == 401       # was 200


# --------------------------------------------------------------------- H-AG2

_NMAP_OS = """<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -O" start="1758800000" version="7.94">
<host><status state="up" reason="syn-ack"/><address addr="{ip}" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/><service name="ssh"/></port></ports>
<os><osmatch name="Linux 5.4" accuracy="98"><osclass type="general purpose" vendor="Linux" osfamily="Linux" osgen="5.X" accuracy="98"/></osmatch></os>
</host>
<runstats><finished time="1758800100"/></runstats>
</nmaprun>"""


def _import_nmap(db, project, tmp_path, ip, name):
    path = tmp_path / name
    path.write_text(_NMAP_OS.format(ip=ip))
    return NmapXMLParser(db).parse_file(str(path), name, project_id=project.id)


def test_an_agents_os_correction_survives_the_next_nmap_and_clearing_it_hands_it_back(
    client, db_session, test_project, tmp_path,
):
    host = models.Host(project_id=test_project.id, ip_address="10.95.0.1", state="up",
                       os_name="Linux 3.X", os_family="Linux", os_vendor="Linux", os_type="general purpose",
                       os_generation="3.X", os_accuracy=90)
    db_session.add(host)
    db_session.commit()
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    key = {"X-API-Key": started.json()["api_key"]}

    fixed = client.patch(f"/api/v1/agent/hosts/{host.id}", headers=key, json={"os_name": "Windows Server 2019"})
    assert fixed.status_code == 200, fixed.text
    db_session.expire_all()
    host = db_session.get(models.Host, host.id)
    assert (host.os_name, host.os_family, host.os_source) == ("Windows Server 2019", "Windows", "operator")
    # The scanner's description of the guess it replaced is gone with it.
    assert (host.os_vendor, host.os_type, host.os_generation, host.os_accuracy) == (None, None, None, None)

    _import_nmap(db_session, test_project, tmp_path, "10.95.0.1", "one.xml")
    db_session.expire_all()
    host = db_session.get(models.Host, host.id)
    assert host.os_name == "Windows Server 2019"           # was "Linux 5.4"
    assert host.os_family == "Windows"
    # The disagreement is still recorded.
    assert db_session.query(ConflictHistory).filter_by(host_id=host.id, field_name="os_name").count() >= 1
    # A corrected host carries work: a scan delete asks first, the cleanup keeps it.
    assert host_work.work_on_hosts(db_session, [host.id]) == {host.id: {"corrections": 1}}

    cleared = client.patch(f"/api/v1/agent/hosts/{host.id}", headers=key, json={"os_name": ""})
    assert cleared.status_code == 200
    db_session.expire_all()
    assert db_session.get(models.Host, host.id).os_source is None
    _import_nmap(db_session, test_project, tmp_path, "10.95.0.1", "two.xml")
    db_session.expire_all()
    host = db_session.get(models.Host, host.id)
    assert (host.os_name, host.os_accuracy, host.os_vendor) == ("Linux 5.4", 98, "Linux")
    assert host_work.work_on_hosts(db_session, [host.id]) == {}


def test_a_host_with_both_corrections_is_one_piece_of_work(db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.95.0.2", state="up",
                       hostname="typed.example", hostname_source="operator",
                       os_name="Windows", os_source="operator")
    db_session.add(host)
    db_session.flush()
    assert host_work.work_on_hosts(db_session, [host.id]) == {host.id: {"corrections": 1}}


# --------------------------------------------------------------------- R-AG1

def test_an_agents_spent_rate_windows_are_deleted(client, db_session, test_project):
    user = _analyst(db_session, test_project, user_id=95103)
    raw, session = _session_key(db_session, test_project, user)
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp()) // 60 * 60
    for minutes in (1, 2, 30):
        db_session.add(AgentRateBucket(
            agent_id=session.agent_id,
            window_start=datetime.fromtimestamp(epoch - 60 * minutes, tz=timezone.utc), count=5,
        ))
    db_session.commit()

    assert _identity(client, raw).status_code == 200
    db_session.expire_all()
    left = db_session.query(AgentRateBucket).filter_by(agent_id=session.agent_id).all()
    # Only the current window is left (was: all four — the sweep ran on the
    # 500th request of a window, which the 429 at 240 never let through).
    assert [b.window_start for b in left] == [datetime.fromtimestamp(epoch, tz=timezone.utc)] or (
        # the minute turned during the call: still one bucket, the newest
        len(left) == 1
    )


def test_renewal_counts_against_the_keys_rate_limit(client, db_session, test_project):
    user = _analyst(db_session, test_project, user_id=95104)
    raw, session = _session_key(db_session, test_project, user)
    from app.db.models_agent import Agent
    agent = db_session.get(Agent, session.agent_id)
    agent.rate_limit_rpm = 2
    db_session.commit()
    codes = [_renew(client, raw).status_code for _ in range(4)]
    assert codes[:2] == [200, 200]
    assert 429 in codes[2:], codes                         # was all 200


# --------------------------------------------------------------- key expiry nits

def test_a_key_with_no_expiry_is_expired(client, db_session, test_project):
    user = _analyst(db_session, test_project, user_id=95105)
    raw, session = _session_key(db_session, test_project, user)
    key = db_session.query(APIKey).filter_by(agent_session_id=session.id, is_active=True).one()
    key.expires_at = None
    db_session.commit()
    refused = _identity(client, raw)
    assert refused.status_code == 401                      # was 200: a key that never lapsed
    assert refused.json()["detail"]["error"] == "key_expired"


def test_the_upload_door_refuses_an_expired_but_renewable_key(client, db_session, test_project):
    user = _analyst(db_session, test_project, user_id=95106)
    raw, session = _session_key(db_session, test_project, user)
    key = db_session.query(APIKey).filter_by(agent_session_id=session.id, is_active=True).one()
    key.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    assert deps.key_is_renewable(session)
    refusal = deps.large_upload_refusal("/api/v1/agent/uploads", {"x-api-key": raw})
    assert refusal is not None and refusal[0] == 401 and "expired" in refusal[1]   # was None: the body was spooled first
    assert _renew(client, raw).status_code == 200
    assert deps.large_upload_refusal("/api/v1/agent/uploads", {"x-api-key": raw}) is None


# --------------------------------------------------------------------- R-AG2

def _note(db, host, user_id, body, created, updated=None):
    note = models.Annotation(host_id=host.id, user_id=user_id, body=body,
                             created_at=created, updated_at=updated)
    db.add(note)
    db.flush()
    return note


def _legacy_feed_ids(db, project_id, *, author_id=None, search=None, skip=0, limit=50):
    """The page's query as it was before the shared read (old == new)."""
    from sqlalchemy import desc, func
    from app.services.host_query_common import escape_like
    A = models.Annotation
    q = db.query(A).join(models.Host, A.host_id == models.Host.id).filter(models.Host.project_id == project_id)
    if author_id:
        q = q.filter(A.user_id == author_id)
    if search:
        e = escape_like(search)
        q = q.filter(models.Host.ip_address.ilike(f"%{e}%") | models.Host.hostname.ilike(f"%{e}%")
                     | A.body.ilike(f"%{e}%"))
    return [n.id for n in q.order_by(desc(func.coalesce(A.updated_at, A.created_at))).offset(skip).limit(limit)]


def test_the_collaboration_feed_and_the_agents_notes_are_one_read(client, db_session, test_project, test_user):
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    web = models.Host(project_id=test_project.id, ip_address="10.95.1.1", hostname="web.corp", state="up")
    db_ = models.Host(project_id=test_project.id, ip_address="10.95.1.2", hostname="db.corp", state="up")
    db_session.add_all([web, db_])
    db_session.flush()
    other = _analyst(db_session, test_project, user_id=95107)
    notes = [
        _note(db_session, web, test_user.id, "first look", t0),
        _note(db_session, db_, other.id, "creds reused", t0 + timedelta(hours=1)),
        # Written first, EDITED last: the feed's newest activity.
        _note(db_session, web, other.id, "TLS weak (edited)", t0 - timedelta(days=1), t0 + timedelta(hours=5)),
        _note(db_session, db_, test_user.id, "backup share", t0 + timedelta(hours=2)),
    ]
    db_session.commit()

    def page_ids(**params):
        r = client.get(f"/api/v1/projects/{test_project.id}/hosts/notes/activity", params=params)
        assert r.status_code == 200, r.text
        return [n["note_id"] for n in r.json()["notes"]]

    # Old == new for the page, across its filters and paging.
    for params, legacy in (
        ({}, {}),
        ({"author_id": other.id}, {"author_id": other.id}),
        ({"search": "10.95.1.2"}, {"search": "10.95.1.2"}),
        ({"search": "web.corp"}, {"search": "web.corp"}),
        ({"search": "TLS"}, {"search": "TLS"}),
        ({"skip": 1, "limit": 2}, {"skip": 1, "limit": 2}),
    ):
        assert page_ids(**params) == _legacy_feed_ids(db_session, test_project.id, **legacy), params
    assert page_ids()[0] == notes[2].id

    # The agents' read: the same notes in the same order (was: by created_at,
    # the edited note last), and the same search.
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    key = {"X-API-Key": started.json()["api_key"]}

    def agent_ids(**params):
        r = client.get("/api/v1/agent/assist/notes", headers=key, params=params)
        assert r.status_code == 200, r.text
        return [n["id"] for n in r.json()["items"]], r.json()["total"]

    assert agent_ids() == (page_ids(), 4)
    assert agent_ids(search="10.95.1.2") == (page_ids(search="10.95.1.2"), 2)
    assert agent_ids(author=other.username) == (page_ids(author_id=other.id), 2)


# --------------------------------------------------------------------- R-AG3

def test_the_agents_names_are_the_pages_names_in_every_state(client, db_session, test_project):
    scan = models.Scan(filename="dnsx.json", tool_name="dnsx", scan_type="t", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    for fqdn, ip in (("portal.example.com", "203.0.113.20"), ("api.example.com", "203.0.113.20"),
                     ("solo.example.com", "203.0.113.99")):
        dns_name_service.record_observation(db_session, project_id=test_project.id, name=fqdn,
                                            record_type="A", value=ip, scan_id=scan.id)
    dns_name_service.import_names(db_session, project_id=test_project.id, raw_names=["new.example.com", "*.dev.example.com"],
                                  created_by_id=None)
    db_session.commit()
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    key = {"X-API-Key": started.json()["api_key"]}

    from app.services.names_list_service import NAME_STATES
    for state in NAME_STATES:
        page = client.get(f"/api/v1/projects/{test_project.id}/names/", params={"state": state})
        agent = client.get("/api/v1/agent/assist/names", headers=key, params={"state": state})
        assert page.status_code == agent.status_code == 200, (state, page.text, agent.text)
        assert [r["id"] for r in agent.json()["items"]] == [r["id"] for r in page.json()["items"]], state
        assert agent.json()["total"] == page.json()["total"], state
    shared = client.get("/api/v1/agent/assist/names", headers=key, params={"state": "shared"}).json()
    assert sorted(r["fqdn"] for r in shared["items"]) == ["api.example.com", "portal.example.com"]  # was all five

    # A state nobody understands is a 422 on both doors (the page said 400).
    assert client.get(f"/api/v1/projects/{test_project.id}/names/", params={"state": "bogus"}).status_code == 422
    assert client.get(f"/api/v1/projects/{test_project.id}/names/export", params={"state": "bogus"}).status_code == 422
    assert client.get("/api/v1/agent/assist/names", headers=key, params={"state": "bogus"}).status_code == 422


# ------------------------------------------------------- claim_replaced lock order

def test_replacing_several_proposals_locks_their_subnets_in_one_order(db_session, test_project, test_user):
    """Two requests replacing proposals for subnets A and B, numbered the
    other way round, locked A-then-B and B-then-A: a deadlock.  The subnets
    are now locked in id order whatever the proposals' ids."""
    from app.db.models_proposals import AgentProposal
    from app.services import proposal_service
    from app.services.default_scope import get_or_create_default_scope

    if db_session.bind.dialect.name != "postgresql":
        import pytest
        pytest.skip("row locks are the Postgres path")
    scope = get_or_create_default_scope(db_session, test_project.id)
    low = models.Subnet(scope_id=scope.id, cidr="10.96.1.0/24")
    high = models.Subnet(scope_id=scope.id, cidr="10.96.2.0/24")
    db_session.add_all([low, high])
    db_session.flush()
    who = proposal_service.Attribution(user_id=test_user.id, source="llm_draft")
    # The proposal on the HIGHER subnet has the LOWER id.
    first = AgentProposal(project_id=test_project.id, kind="subnet_details", subnet_id=high.id,
                          status="pending", payload={"site": "b"}, source=who.source,
                          proposed_by_user_id=who.user_id)
    second = AgentProposal(project_id=test_project.id, kind="subnet_details", subnet_id=low.id,
                           status="pending", payload={"site": "a"}, source=who.source,
                           proposed_by_user_id=who.user_id)
    db_session.add(first)
    db_session.flush()
    db_session.add(second)
    db_session.flush()
    assert first.id < second.id and high.id > low.id

    locked = []

    def _watch(conn, cursor, statement, params, context, executemany):
        if "FOR UPDATE" in statement and "subnets" in statement:
            locked.append(params)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _watch)
    try:
        proposal_service.claim_replaced(db_session, test_project.id, who, [first.id, second.id], [
            AgentProposal(kind="subnet_details", subnet_id=high.id),
            AgentProposal(kind="subnet_details", subnet_id=low.id),
        ])
    finally:
        event.remove(engine, "before_cursor_execute", _watch)
    order = [next(v for v in (p.values() if isinstance(p, dict) else p) if v in (low.id, high.id)) for p in locked]
    assert order == [low.id, high.id], order               # was [high, low]
