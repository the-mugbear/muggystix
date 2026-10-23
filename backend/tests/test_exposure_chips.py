"""The Hosts list's Exposure chips come from the ONE ports-of-interest table
(review 2026-10-23b, FEP-4).

The browser held a hand-mirrored copy of the table (numbers and weights) and
a service-name map that existed only there.  `ports_of_interest.exposure_chips`
is that rule, and these are the browser's own cases
(`frontend/src/tests/hostTableSummaries.test.ts`, "exposure chips", before the
move), unchanged: old == new.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.db import models
from app.services.ports_of_interest import CHIP_LABELS, SERVICE_NAMES, exposure_chips, ports_by_number


def _p(port_number, state, service_name, service_method):
    return SimpleNamespace(port_number=port_number, state=state, service_name=service_name,
                           service_method=service_method)


def test_a_probed_service_on_an_unusual_port_is_shown_by_name_as_detected():
    assert exposure_chips([_p(3390, "open", "ms-wbt-server", "probed")]) == [
        {"key": "RDP", "label": "RDP", "port": 3390, "detected": True, "weight": 7},
    ]


def test_a_well_known_port_with_no_probe_is_a_guess():
    assert exposure_chips([_p(445, "open", "microsoft-ds", "table")]) == [
        {"key": "SMB", "label": "SMB", "port": 445, "detected": False, "weight": 7},
    ]


def test_probed_web_services_still_show_when_no_high_value_port_is_open():
    chips = exposure_chips([_p(8443, "open", "https", "probed"), _p(8080, "open", "http", "probed")])
    assert [c["label"] for c in chips] == ["http", "https"]
    assert all(c["detected"] and c["weight"] == 0 for c in chips)


def test_high_value_first_detected_ahead_of_guessed_then_other_probes():
    chips = exposure_chips([
        _p(80, "open", "http", "probed"),
        _p(22, "open", "ssh", "table"),
        _p(3389, "open", "ms-wbt-server", "probed"),
        _p(445, "open", None, None),
    ])
    assert [f"{c['label']}{'' if c['detected'] else '?'}" for c in chips] == ["RDP", "SMB?", "SSH?", "http"]


def test_closed_ports_and_unprobed_unknown_ports_produce_nothing():
    assert exposure_chips([
        _p(3389, "closed", "ms-wbt-server", "probed"),
        _p(9999, "open", None, None),
        _p(9998, "open", "unknown-svc", "table"),
    ]) == []


def test_the_same_service_on_two_ports_is_one_chip_and_a_detection_beats_a_guess():
    assert exposure_chips([_p(22, "open", "ssh", "table"), _p(2222, "open", "ssh", "probed")]) == [
        {"key": "SSH", "label": "SSH", "port": 2222, "detected": True, "weight": 5},
    ]


def test_every_port_of_interest_has_a_chip_and_every_service_name_a_port_of_interest():
    assert set(CHIP_LABELS) == set(ports_by_number())
    assert set(SERVICE_NAMES.values()) <= set(ports_by_number())


def test_a_hosts_list_row_carries_its_chips(client, db_session, test_project):
    h = models.Host(project_id=test_project.id, ip_address="10.236.0.1", state="up")
    db_session.add(h)
    db_session.flush()
    db_session.add_all([
        models.Port(host_id=h.id, port_number=3390, protocol="tcp", state="open",
                    service_name="ms-wbt-server", service_method="probed"),
        models.Port(host_id=h.id, port_number=22, protocol="tcp", state="open"),
    ])
    db_session.commit()
    rows = client.get(f"/api/v1/projects/{test_project.id}/hosts/").json()["items"]
    row = next(r for r in rows if r["ip_address"] == "10.236.0.1")
    assert [(c["label"], c["detected"]) for c in row["exposure"]] == [("RDP", True), ("SSH", False)]
