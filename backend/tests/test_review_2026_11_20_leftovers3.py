"""Review 2026-11-20, what the seven batches left — presentation mode and
Tool Activity.

* a tab that lost control is told so before it is told its slide is wrong;
* a viewer row left on an ended presentation is removed by the retention pass;
* an evidence activity row names its host, so "Open detail" opens that host.
"""
from __future__ import annotations

from app.db.models_presentations import Presentation, PresentationViewer
from app.services import presentation_service
from tests.test_activity_attribution import _at, attribution_dataset  # noqa: F401 — fixture
from tests.test_presentation_mode import (  # noqa: F401 — fixtures
    _move, _p, _start, act_as, people, report, report_files,
)


def test_a_tab_that_is_not_driving_is_told_so_whatever_it_asked_for(client, test_project, report):  # noqa: F811
    st = _start(client, test_project, report, "driver-tab")
    refused = _move(client, test_project, st, "other-tab", "no-such-slide:9")
    assert refused.status_code == 409 and refused.json()["detail"]["reason"] == "not_driver"
    # The driver asking for a slide the report does not have is still told that.
    assert _move(client, test_project, st, "driver-tab", "no-such-slide:9").status_code == 422


def test_a_tab_row_left_on_an_ended_presentation_is_removed(client, db_session, test_project, report, test_user):  # noqa: F811
    ended = _start(client, test_project, report, "a")
    assert client.post(_p(test_project, ended["id"], "end"), json={"client_id": "a"}).status_code == 200
    live = _start(client, test_project, report, "b")
    assert live["id"] != ended["id"]
    # What a heartbeat racing the End leaves behind.
    db_session.add(PresentationViewer(
        presentation_id=ended["id"], user_id=test_user.id, client_id="late", kind="browse"))
    db_session.add(PresentationViewer(
        presentation_id=live["id"], user_id=test_user.id, client_id="watching", kind="browse"))
    db_session.commit()

    assert presentation_service.forget_viewers_of_ended(db_session) == 1
    db_session.commit()
    left = {(v.presentation_id, v.client_id) for v in db_session.query(PresentationViewer)}
    assert (ended["id"], "late") not in left and (live["id"], "watching") in left
    assert db_session.get(Presentation, live["id"]).ended_at is None


def test_a_recorded_command_names_its_host(client, attribution_dataset):  # noqa: F811
    rows = _at(client)
    command = next(i for i in rows if i["kind"] == "evidence")
    assert command["host_id"] == attribution_dataset["seen"].id
    assert all(i["host_id"] is None for i in rows if i["kind"] == "scan")
