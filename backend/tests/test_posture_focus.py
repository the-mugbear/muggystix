""""Where to focus" ranking — the acceptance scenarios from the Posture redesign
review, as arithmetic over one grid row.

Moved from ``frontend/src/tests/postureConcentration.test.ts`` with the same
numbers when the ranking moved to the server (``posture_focus``) so the page
and the Posture briefing read one implementation.
"""
from app.services.posture_focus import (
    compute_focus, describe_concentration, leading_family, rank_concentration,
)


def cell(segment, affected, assessed, in_scope, eligible=None, eligible_assessed=None):
    return {
        "segment": segment, "affected": affected, "assessed": assessed, "in_scope": in_scope,
        "eligible": in_scope if eligible is None else eligible,
        "eligible_assessed": assessed if eligible_assessed is None else eligible_assessed,
        "unassessed": assessed == 0,
    }


SEGMENTS = [{"key": k, "label": k.capitalize(), "in_scope": 0, "assessed": 0}
            for k in ("east", "west", "core", "north", "lab")]

LIFECYCLE = {
    "family": "lifecycle_patching", "family_label": "Lifecycle & patching", "conditions": ["eol_os"],
    "evidence_domain": "os_detection", "evidence_domain_label": "OS identification", "affected_total": 46,
    "cells": [
        cell("core", 16, 160, 170),   # most affected hosts, ordinary rate (10%)
        cell("east", 18, 30, 36),     # 60% with good coverage — the story
        cell("west", 8, 80, 84),
        cell("north", 2, 2, 25),      # 100% of a tiny sample
        cell("lab", 0, 0, 12),        # never assessed
    ],
}


def _by_key():
    ranked = rank_concentration(LIFECYCLE, SEGMENTS)
    return ranked, {r["segment"]: r for r in ranked}


def test_leads_with_the_disproportionate_segment_not_the_largest():
    ranked, by = _by_key()
    assert [r["segment"] for r in ranked] == ["east", "core", "west", "north", "lab"]
    assert abs(by["east"]["rate"] - 0.6) < 1e-9
    # Rest of the assessed project EXCLUDES the segment itself: 26 of 242.
    assert (by["east"]["rest_affected"], by["east"]["rest_assessed"]) == (26, 242)
    assert round(by["east"]["delta_points"]) == 49
    assert by["east"]["sentence"].endswith("49 points higher.")


def test_separates_workload_from_prevalence_for_a_large_ordinary_site():
    _, by = _by_key()
    assert by["core"]["affected"] > by["west"]["affected"]
    assert abs(by["core"]["rate"] - 0.1) < 1e-9
    assert by["core"]["state"] == "comparable"
    assert "points higher" not in describe_concentration(by["core"], "Lifecycle & patching")


def test_keeps_a_tiny_full_sample_visible_but_not_ranked_worst():
    ranked, by = _by_key()
    north = by["north"]
    assert north["state"] == "limited"
    assert north["rate"] == 1
    assert north["unknown"] == 23
    assert "only 2 assessed" in north["limited_because"]
    assert "8% of eligible hosts assessed" in north["limited_because"]
    assert ranked.index(north) > ranked.index(by["west"])
    assert "Limited comparison" in north["sentence"]


def test_never_reads_an_unassessed_segment_as_clean():
    _, by = _by_key()
    assert by["lab"]["state"] == "unassessed"
    assert by["lab"]["rate"] is None
    assert "unknown, not clean" in by["lab"]["sentence"]


def test_reports_no_comparator_rather_than_an_invented_one():
    [only] = rank_concentration({**LIFECYCLE, "cells": [cell("east", 18, 30, 36)]}, SEGMENTS)
    assert only["rest_rate"] is None
    assert only["delta_points"] is None
    assert "No other segment was assessed" in only["sentence"]


def test_judges_coverage_against_eligible_hosts_not_every_host():
    # 12 of 40 hosts expose a web port and all 12 were assessed: complete, not thin.
    [r] = rank_concentration({**LIFECYCLE, "cells": [cell("east", 6, 12, 40, 12, 12)]}, SEGMENTS)
    assert r["coverage"] == 1
    assert r["state"] == "comparable"


def test_prefers_the_sharpest_concentration_over_the_most_affected_hosts():
    broad = {
        **LIFECYCLE, "family": "encryption_trust", "family_label": "Encryption & trust", "affected_total": 90,
        "cells": [cell("core", 45, 150, 170), cell("east", 9, 30, 36), cell("west", 36, 120, 124)],
    }
    assert leading_family([broad, LIFECYCLE], SEGMENTS)["family"] == "lifecycle_patching"
    assert leading_family([{**LIFECYCLE, "affected_total": 0, "cells": []}], SEGMENTS) is None


def test_focus_ranks_every_affected_family_and_names_the_lead():
    quiet = {**LIFECYCLE, "family": "quiet", "affected_total": 0, "cells": [cell("east", 0, 30, 36)]}
    focus = compute_focus({"segments": SEGMENTS, "rows": [quiet, LIFECYCLE]})
    assert focus["lead_family"] == "lifecycle_patching"
    assert list(focus["families"]) == ["lifecycle_patching"]
    assert focus["families"]["lifecycle_patching"][0]["segment"] == "east"
    assert compute_focus(None) is None
