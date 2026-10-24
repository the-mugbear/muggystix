"""Presentation mode, stage A (2.530.0): the shared state of a driven view of
an issued report — `planning`'s contract, pinned.

* who: auditor and above start, join and drive; a viewer is refused every
  route; a draft has no presentation;
* the revision: only a move, Take control and End raise it — each one
  conditional write; a move must come from the signed-in driver AND the
  driving tab, at the revision it saw, while live; presence never raises it;
* the position: a slide the report's list for that filter does not hold is a
  422, a filter change lands on the current finding's first endpoint slide;
* E1: a Display's acknowledgment is stored with its position, monotonically;
* images: only a frozen copy is ever served; a printed image whose copy is
  gone is "not available";
* idle end after 30 minutes with no move and no heartbeat.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_presentations import Presentation, PresentationViewer
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_reports import Report, ReportImage
from app.main import app


def _member(db, project, user_id, username, role):
    user = User(id=user_id, username=username, email=f"{username}@example.com", full_name=username.title(),
                hashed_password="x", role=UserRole.MEMBER, is_active=True, created_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


@pytest.fixture
def people(db_session, test_project):
    return {
        "ana": _member(db_session, test_project, 501, "ana", ProjectRole.ANALYST),
        "ben": _member(db_session, test_project, 502, "ben", ProjectRole.AUDITOR),
        "vic": _member(db_session, test_project, 503, "vic", ProjectRole.VIEWER),
        "adm": _member(db_session, test_project, 504, "adm", ProjectRole.ADMIN),
    }


@pytest.fixture
def act_as():
    def _act(user):
        app.dependency_overrides[get_current_user] = lambda: user
    return _act


@pytest.fixture(autouse=True)
def report_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "client_reports"))
    return tmp_path / "client_reports"


def _endpoint(i):
    return {"address": f"10.0.{i // 250}.{i % 250 + 1}", "hostname": f"host-{i}", "name": None,
            "port": "443/tcp", "state": None}


def _dataset():
    return {
        "report": {"title": "Internal assessment", "heading": "Acme", "date": "2026-10-20", "number": 3},
        "counts": {"critical": 0, "high": 1, "medium": 1, "low": 0, "info": 0, "total": 2},
        "executive_summary": "We tested the network. It went well.",
        "scope": {"subnets": [{"cidr": "10.0.0.0/24", "site": "DMZ", "description": None},
                              {"cidr": "10.0.1.0/24", "site": "HQ", "description": None}], "domains": []},
        "findings": [
            {"ref": "F-01", "title": "Weak TLS", "severity": "high", "cvss_score": 7.5, "status_note": None,
             "description": "TLS 1.0 is enabled.\n\n![The scan](evidence:11)", "impact": "Downgrade.",
             "recommendation": "Disable it.", "steps_to_reproduce": None,
             "images": [{"attachment_id": 11, "caption": "The scan", "printed": True},
                        {"attachment_id": 12, "caption": "Not printed", "printed": False},
                        {"attachment_id": 13, "caption": "Gone", "printed": True}],
             "affected": [_endpoint(i) for i in range(400)], "affected_count": 400},
            {"ref": "F-02", "title": "Default credentials", "severity": "medium", "cvss_score": None,
             "status_note": None, "description": "admin/admin", "impact": None, "recommendation": None,
             "steps_to_reproduce": None, "images": [],
             "affected": [{"address": "10.0.1.5", "hostname": None, "name": None, "port": None, "state": None}],
             "affected_count": 1},
        ],
    }


@pytest.fixture
def report(db_session, test_project, test_user, report_files):
    r = Report(project_id=test_project.id, kind="full", status="issued", title="Internal assessment",
               template="pentest", number=3, snapshot={"dataset": _dataset()}, created_by_id=test_user.id,
               issued_at=datetime.now(timezone.utc))
    db_session.add(r)
    db_session.flush()
    folder = report_files / str(test_project.id) / str(r.id) / "evidence"
    folder.mkdir(parents=True)
    (folder / "11.png").write_bytes(b"\x89PNG\r\n\x1a\nframe")
    db_session.add(ReportImage(report_id=r.id, attachment_id=11, content_type="image/png", size_bytes=13,
                               sha256="0" * 64, storage_path=f"{test_project.id}/{r.id}/evidence/11.png"))
    # 13 was frozen but its copy is gone from storage.
    db_session.add(ReportImage(report_id=r.id, attachment_id=13, content_type="image/png", size_bytes=13,
                               sha256="0" * 64, storage_path=f"{test_project.id}/{r.id}/evidence/13.png"))
    db_session.commit()
    return r


def _base(project, report):
    return f"/api/v1/projects/{project.id}/client-reports/{report.id}/presentation"


def _p(project, pid, action):
    return f"/api/v1/projects/{project.id}/presentations/{pid}/{action}"


def _start(client, project, report, cid="tab-a"):
    r = client.post(_base(project, report), json={"client_id": cid})
    assert r.status_code == 200, r.text
    return r.json()


def _move(client, project, state, cid, key, flt=None):
    body = {"client_id": cid, "revision": state["revision"], "slide_key": key}
    if flt:
        body["host_filter"] = flt
    return client.post(_p(project, state["id"], "move"), json=body)


def _slides(client, project, report, **flt):
    params = {}
    if flt:
        params = {"host_filter_kind": flt["kind"], "host_filter_value": flt["value"]}
    r = client.get(_base(project, report) + "/slides", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --- who ---------------------------------------------------------------------

def test_a_viewer_is_refused_every_presentation_route(client, test_project, report, people, act_as):
    st = _start(client, test_project, report)
    act_as(people["vic"])
    calls = [
        ("get", _base(test_project, report), None),
        ("post", _base(test_project, report), {"client_id": "v"}),
        ("get", _base(test_project, report) + "/slides", None),
        ("get", _base(test_project, report) + "/images/11", None),
        ("post", _base(test_project, report) + "/layout-reports", {"slide_key": "overview:1", "overflow_px": 3, "font": "x"}),
        ("post", _p(test_project, st["id"], "heartbeat"), {"client_id": "v", "kind": "controller"}),
        ("post", _p(test_project, st["id"], "move"), {"client_id": "v", "revision": 1, "slide_key": "overview:1"}),
        ("post", _p(test_project, st["id"], "take-control"), {"client_id": "v"}),
        ("post", _p(test_project, st["id"], "end"), {}),
    ]
    for method, url, body in calls:
        r = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
        assert r.status_code == 403, (method, url, r.text)


def test_an_auditor_starts_joins_and_drives(client, test_project, report, people, act_as):
    act_as(people["ben"])
    st = _start(client, test_project, report, "ben-1")
    assert st["you_are_driving"] and st["revision"] == 1 and st["slide_key"] == "overview:1"
    assert st["driver"]["name"] == "Ben"
    r = _move(client, test_project, st, "ben-1", "summary:1")
    assert r.status_code == 200 and r.json()["revision"] == 2


def test_a_draft_is_not_presented(client, db_session, test_project, test_user):
    draft = Report(project_id=test_project.id, kind="full", status="draft", title="d", template="pentest",
                   created_by_id=test_user.id)
    db_session.add(draft)
    db_session.commit()
    assert client.post(_base(test_project, draft), json={"client_id": "x"}).status_code == 404
    assert client.get(_base(test_project, draft) + "/slides").status_code == 404


def test_a_second_start_joins_the_live_presentation(client, test_project, report, people, act_as):
    st = _start(client, test_project, report, "a")
    act_as(people["ana"])
    again = _start(client, test_project, report, "b")
    assert again["id"] == st["id"]
    assert not again["you_are_driving"] and again["driver"]["id"] == st["driver"]["id"]


# --- the revision and the driver ----------------------------------------------

def test_a_move_needs_the_driver_and_the_driving_tab(client, test_project, report, people, act_as):
    st = _start(client, test_project, report, "driver-tab")
    # The same person, another tab: not the driver.
    r = _move(client, test_project, st, "other-tab", "summary:1")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_driver"
    # Another account sending the driver's tab id: not the driver either.
    act_as(people["ana"])
    r = _move(client, test_project, st, "driver-tab", "summary:1")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_driver"
    # No answer, to anyone, names the driving tab.
    hb = client.post(_p(test_project, st["id"], "heartbeat"), json={"client_id": "ana-1", "kind": "controller"})
    assert "driver-tab" not in hb.text and "driver-tab" not in r.text


def test_a_stale_move_is_refused_with_the_current_state(client, test_project, report):
    st = _start(client, test_project, report, "t")
    assert _move(client, test_project, st, "t", "summary:1").status_code == 200
    r = _move(client, test_project, st, "t", "scope:1")       # still at revision 1
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["reason"] == "stale" and body["presentation"]["revision"] == 2
    assert body["presentation"]["slide_key"] == "summary:1"


def test_take_control_keeps_the_slide_and_the_old_driver_cannot_move(client, test_project, report, people, act_as):
    st = _start(client, test_project, report, "a-tab")
    st = _move(client, test_project, st, "a-tab", "f:F-01").json()
    st = _move(client, test_project, st, "a-tab", "f:F-01:hosts:1", {"kind": "site", "value": "DMZ"}).json()
    act_as(people["ana"])
    taken = client.post(_p(test_project, st["id"], "take-control"), json={"client_id": "ana-tab"}).json()
    assert taken["revision"] == st["revision"] + 1
    assert taken["slide_key"] == "f:F-01:hosts:1" and taken["host_filter"] == {"kind": "site", "value": "DMZ"}
    assert taken["you_are_driving"] and taken["driver"]["name"] == "Ana"
    app.dependency_overrides.pop(get_current_user)
    act_as(report.created_by)
    r = _move(client, test_project, taken, "a-tab", "f:F-01:hosts:2", {"kind": "site", "value": "DMZ"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_driver"


def test_end_raises_the_revision_and_stops_every_move(client, test_project, report, people, act_as):
    st = _start(client, test_project, report, "t")
    act_as(people["ben"])
    assert client.post(_p(test_project, st["id"], "end"), json={}).status_code == 403   # not the starter
    act_as(report.created_by)
    ended = client.post(_p(test_project, st["id"], "end"), json={"client_id": "t"}).json()
    assert ended["revision"] == 2 and ended["ended"]["reason"] == "ended" and not ended["you_are_driving"]
    r = _move(client, test_project, ended, "t", "summary:1")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "ended"
    assert client.post(_p(test_project, st["id"], "take-control"), json={"client_id": "t"}).status_code == 409


def test_start_end_and_take_control_are_audited_moves_are_not(client, db_session, test_project, report, people, act_as):
    st = _start(client, test_project, report, "t")
    _move(client, test_project, st, "t", "summary:1")
    act_as(people["ana"])
    client.post(_p(test_project, st["id"], "take-control"), json={"client_id": "x"})
    act_as(people["adm"])
    client.post(_p(test_project, st["id"], "end"), json={})
    actions = [a for (a,) in db_session.query(AuditLog.action).filter(AuditLog.resource_type == "presentation")]
    assert sorted(actions) == ["presentation_control_taken", "presentation_ended", "presentation_started"]


def test_presence_never_raises_the_revision(client, db_session, test_project, report, people, act_as):
    st = _start(client, test_project, report, "drv")
    act_as(people["ana"])
    for i in range(100):
        client.post(_p(test_project, st["id"], "heartbeat"), json={"client_id": f"w{i % 5}", "kind": "controller"})
    act_as(report.created_by)
    r = _move(client, test_project, st, "drv", "summary:1")      # the move it had pending, at revision 1
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert len(r.json()["watching"]) == 5


# --- the position ---------------------------------------------------------------

def test_a_slide_outside_the_report_or_the_filter_is_refused(client, test_project, report):
    st = _start(client, test_project, report, "t")
    assert _move(client, test_project, st, "t", "f:F-99").status_code == 422
    unfiltered = [s["key"] for s in _slides(client, test_project, report)["slides"]]
    assert "f:F-01:hosts:20" in unfiltered
    st = _move(client, test_project, st, "t", "f:F-01").json()
    st = _move(client, test_project, st, "t", "f:F-01:hosts:1", {"kind": "site", "value": "HQ"}).json()
    hq = [s["key"] for s in _slides(client, test_project, report, kind="site", value="HQ")["slides"]]
    assert "f:F-01:hosts:20" not in hq
    r = _move(client, test_project, st, "t", "f:F-01:hosts:20", {"kind": "site", "value": "HQ"})
    assert r.status_code == 422


def test_a_filter_change_lands_on_the_findings_first_endpoint_slide(client, test_project, report):
    st = _start(client, test_project, report, "t")
    st = _move(client, test_project, st, "t", "f:F-01:hosts:3").json()
    r = _move(client, test_project, st, "t", "f:F-01:hosts:2", {"kind": "site", "value": "DMZ"})
    assert r.status_code == 422
    st = _move(client, test_project, st, "t", "f:F-01:hosts:1", {"kind": "site", "value": "DMZ"}).json()
    assert st["host_filter"] == {"kind": "site", "value": "DMZ"} and st["slide_key"] == "f:F-01:hosts:1"
    # The filter stays on through later moves, across findings.
    st = _move(client, test_project, st, "t", "f:F-02", {"kind": "site", "value": "DMZ"}).json()
    assert st["host_filter"] == {"kind": "site", "value": "DMZ"}
    # Clearing it is a change too: on F-02's first endpoint slide.
    assert _move(client, test_project, st, "t", "f:F-02").status_code == 422
    st = _move(client, test_project, st, "t", "f:F-02:hosts:1").json()
    assert st["host_filter"] is None
    # Off a finding, a filter change stays on the slide.
    st = _move(client, test_project, st, "t", "overview:1").json()
    assert _move(client, test_project, st, "t", "f:F-01:hosts:1", {"kind": "site", "value": "DMZ"}).status_code == 422
    st = _move(client, test_project, st, "t", "overview:1", {"kind": "site", "value": "DMZ"}).json()
    assert st["host_filter"] == {"kind": "site", "value": "DMZ"}
    # A filter naming nothing in the scope is refused; one that matches nothing is one empty slide.
    assert _move(client, test_project, st, "t", "f:F-01:hosts:1", {"kind": "site", "value": "Mars"}).status_code == 422
    empty = _slides(client, test_project, report, kind="text", value="nothing-matches")
    hosts = [s for s in empty["slides"] if s["kind"] == "hosts" and s["ref"] == "F-01"]
    assert len(hosts) == 1 and hosts[0]["empty"] and "nothing-matches" in hosts[0]["blocks"][0]["md"]


def test_the_endpoint_footer_counts_the_filtered_list(client, test_project, report):
    dmz = _slides(client, test_project, report, kind="site", value="DMZ")
    hosts = [s for s in dmz["slides"] if s["kind"] == "hosts" and s["ref"] == "F-01"]
    rows = sum(len(b["rows"]) for s in hosts for b in s["blocks"] if b["kind"] == "table")
    assert rows == 250                                    # 10.0.0.x of the 400
    assert hosts[0]["footer"].endswith("of 250 matching DMZ · 400 affected")
    assert hosts[0]["footer"].startswith("Report #3 · issued 2026-10-20 · F-01 · Endpoints 1–")
    assert hosts[-1]["footer"].split(" of ")[0].endswith("–250")


def test_the_slide_list_is_the_same_on_every_read_and_has_only_printed_images(client, test_project, report):
    a = _slides(client, test_project, report)
    b = _slides(client, test_project, report)
    assert a == b
    images = {s["image"]["attachment_id"]: s["image"]["available"] for s in a["slides"] if s["kind"] == "image"}
    assert images == {11: True, 13: False}               # 12 is not printed; 13's copy is gone
    keys = [s["key"] for s in a["slides"]]
    # The placed image follows its section's page; the unplaced one comes after the sections.
    assert keys.index("f:F-01:image:11") == keys.index("f:F-01:description:1") + 1
    assert keys.index("f:F-01:image:13") > keys.index("f:F-01:recommendation:1")


def test_only_a_frozen_copy_is_ever_served(client, test_project, report):
    base = _base(test_project, report)
    ok = client.get(base + "/images/11")
    assert ok.status_code == 200 and ok.content.startswith(b"\x89PNG")
    assert client.get(base + "/images/13").status_code == 410     # frozen, copy gone
    assert client.get(base + "/images/12").status_code == 404     # never frozen: no live fall back


# --- E1: a Display's acknowledgment -------------------------------------------------

def _beat(client, project, st, cid, rendered=None, kind="display"):
    body = {"client_id": cid, "kind": kind}
    if rendered:
        body["rendered"] = rendered
    return client.post(_p(project, st["id"], "heartbeat"), json=body)


def test_a_displays_acknowledgment_is_its_position_and_never_goes_back(client, test_project, report):
    st = _start(client, test_project, report, "drv")
    st = _move(client, test_project, st, "drv", "summary:1").json()            # revision 2
    st = _move(client, test_project, st, "drv", "scope:1").json()              # revision 3
    r = _beat(client, test_project, st, "disp", {"revision": 3, "slide_key": "scope:1"})
    d = r.json()["displays"][0]
    assert d["status"] == "synchronized" and d["rendered"] == {"revision": 3, "slide_key": "scope:1", "host_filter": None}
    # The acknowledgment of revision 2, delayed, arrives after 3's: nothing changes.
    r = _beat(client, test_project, st, "disp", {"revision": 2, "slide_key": "summary:1"})
    assert r.json()["displays"][0]["rendered"]["revision"] == 3
    # The presentation moves on: the Display is catching up, and still says what it shows.
    st = _move(client, test_project, st, "drv", "f:F-01").json()
    d = _beat(client, test_project, st, "disp").json()["displays"][0]
    assert d["status"] == "catching_up" and d["rendered"]["slide_key"] == "scope:1"


def test_an_acknowledgment_must_name_a_slide_of_the_report_and_a_revision_that_exists(client, test_project, report):
    st = _start(client, test_project, report, "drv")
    assert _beat(client, test_project, st, "disp", {"revision": 1, "slide_key": "f:F-99"}).status_code == 422
    assert _beat(client, test_project, st, "disp", {"revision": 7, "slide_key": "overview:1"}).status_code == 422


def test_a_silent_display_is_disconnected(client, db_session, test_project, report):
    st = _start(client, test_project, report, "drv")
    _beat(client, test_project, st, "disp", {"revision": 1, "slide_key": "overview:1"})
    db_session.query(PresentationViewer).update({"last_seen_at": datetime.now(timezone.utc) - timedelta(seconds=20)})
    db_session.commit()
    d = client.get(_base(test_project, report)).json()["live"]["displays"][0]
    assert d["status"] == "disconnected"


# --- idle end ------------------------------------------------------------------------

def test_an_idle_presentation_ends_and_a_watched_one_does_not(client, db_session, test_project, report):
    st = _start(client, test_project, report, "drv")
    _beat(client, test_project, st, "disp")
    old = datetime.now(timezone.utc) - timedelta(minutes=31)
    db_session.query(Presentation).update({"last_move_at": old})
    db_session.commit()
    # Paused but watched: still live.
    assert client.get(_base(test_project, report)).json()["live"]["id"] == st["id"]
    db_session.query(PresentationViewer).update({"last_seen_at": old})
    db_session.commit()
    assert client.get(_base(test_project, report)).json()["live"] is None
    ended = db_session.get(Presentation, st["id"])
    db_session.refresh(ended)
    assert ended.ended_reason == "idle"
    # A new Start makes a new presentation.
    assert _start(client, test_project, report, "drv")["id"] != st["id"]


def test_a_layout_report_is_recorded_once_an_hour(client, test_project, report):
    body = {"slide_key": "summary:1", "overflow_px": 14, "font": "Arial"}
    assert client.post(_base(test_project, report) + "/layout-reports", json=body).json() == {"recorded": True}
    assert client.post(_base(test_project, report) + "/layout-reports", json=body).json() == {"recorded": False}


def test_the_answer_is_json_serialisable_and_names_people_one_way(client, test_project, report):
    st = _start(client, test_project, report, "t")
    json.dumps(st)
    assert set(st["driver"]) == {"id", "name", "profile_image"}
