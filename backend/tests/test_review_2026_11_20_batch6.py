"""Review 2026-11-20, batch 6 — presentation mode's slide list (C9).

Nothing marked a slide list as being built, so every request that missed the
cache built its own copy — at a filter change, every following tab and every
Display at once — and each viewer's GET encoded the whole deck again.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from app.services import presentation_service as svc
from app.services import presentation_slides as slides_mod


@pytest.fixture
def builds(monkeypatch):
    """The slide list's inputs replaced by fixed ones, and a builder that takes
    a moment and counts how often it ran."""
    svc._CACHE.clear()
    svc._BUILDING.clear()
    svc._ENCODED.clear()
    calls = {"n": 0, "fail": False}
    counting = threading.Lock()

    def build(dataset, flt, images, shown):
        with counting:
            calls["n"] += 1
        time.sleep(0.2)
        if calls["fail"]:
            raise RuntimeError("the build died")
        return {"stage": {"width": 1600, "height": 900}, "filter": flt,
                "slides": [{"key": "overview:1", "anchors": []}, {"key": "f:F-01:text:1", "anchors": []}]}

    monkeypatch.setattr(svc, "frozen_images", lambda db, report: frozenset())
    monkeypatch.setattr(svc, "status_notes", lambda report: {})
    monkeypatch.setattr(svc, "_dataset", lambda db, report: {})
    monkeypatch.setattr(slides_mod, "check_filter", lambda dataset, kind, value: None)
    monkeypatch.setattr(slides_mod, "presentation_slides", build)
    monkeypatch.setattr(slides_mod, "scope_sites", lambda dataset: [])
    monkeypatch.setattr(slides_mod, "scope_segments", lambda dataset: [])
    yield calls
    svc._CACHE.clear()
    svc._BUILDING.clear()
    svc._ENCODED.clear()


def _together(n, fn):
    results, errors = [], []

    def run():
        try:
            results.append(fn())
        except Exception as exc:               # noqa: BLE001 — collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    return results, errors


def test_requests_that_miss_together_build_the_list_once(builds):
    report = SimpleNamespace(id=71)

    results, errors = _together(8, lambda: svc.slides_for(None, report, None, None))

    assert errors == []
    assert builds["n"] == 1                                  # it was eight
    assert len(results) == 8 and all(r is results[0] for r in results)
    assert svc._BUILDING == {}
    # …and the next reader does not build at all.
    assert svc.slides_for(None, report, None, None) is results[0] and builds["n"] == 1


def test_another_filter_is_another_list_and_is_not_made_to_wait(builds):
    report = SimpleNamespace(id=72)

    everyone = svc.slides_for(None, report, None, None)
    the_dmz = svc.slides_for(None, report, "site", "DMZ")

    assert builds["n"] == 2 and everyone is not the_dmz
    assert svc.slides_for(None, report, "site", " DMZ ") is the_dmz      # the same filter, the same list


def test_a_build_that_fails_leaves_nobody_waiting_for_it(builds):
    report = SimpleNamespace(id=73)
    builds["fail"] = True

    results, errors = _together(4, lambda: svc.slides_for(None, report, None, None))

    assert results == [] and len(errors) == 4               # each was told, none hung
    assert svc._BUILDING == {}
    builds["fail"] = False
    assert svc.slides_for(None, report, None, None)["slides"]


def test_a_wait_that_outlasts_its_limit_builds_for_itself(builds, monkeypatch):
    monkeypatch.setattr(svc, "_BUILD_WAIT_S", 0.01)
    report = SimpleNamespace(id=74)

    results, errors = _together(3, lambda: svc.slides_for(None, report, None, None))

    assert errors == [] and len(results) == 3               # nobody is held on another's build for ever
    assert svc._BUILDING == {}


def test_the_answer_is_encoded_once_per_list_and_leaves_the_servers_keys_out(builds):
    report = SimpleNamespace(id=75)
    result = svc.slides_for(None, report, None, None)

    first = svc.slides_answer(result)
    assert svc.slides_answer(result) is first               # the same bytes, not encoded again
    body = json.loads(first)
    assert set(body) == {"stage", "slides", "filter", "sites", "segments"}
    assert [s["key"] for s in body["slides"]] == ["overview:1", "f:F-01:text:1"]

    # A list the cache no longer holds takes its encoded form with it.
    svc._CACHE.clear()
    other = svc.slides_for(None, SimpleNamespace(id=76), None, None)
    svc.slides_answer(other)
    assert list(svc._ENCODED) == [id(other)]
