""""Where to focus" — one pattern family's grid row, ranked into a comparison
a presenter can defend.

The Posture overview draws it and the Posture briefing prints it; both read
this one ranking (it was arithmetic in the page until the briefing needed the
same answer).  Everything here is arithmetic over ONE row of the family ×
segment grid (``systemic_insight_service._build_family_site_matrix``).
Segment columns are disjoint (a host inherits exactly one site, or lands in
Unassigned), so "the rest of the assessed project" is a plain sum of the other
cells — no second aggregation that could drift from the grid.

The rules, all shown to the reader rather than buried:
 - rate = affected / assessed, never affected / in scope: an unassessed host
   is unknown, not clean.
 - the comparator is the REST OF THE ASSESSED PROJECT (every other segment's
   affected / assessed).  It is not called "peers": nothing records asset role
   or scan method, so nothing supports "comparable".
 - a difference is in percentage points.  A multiplier is undefined against a
   zero baseline and flatters small ones.
 - a LIMITED comparison (few assessed hosts, or thin evidence) keeps its
   counts on screen but is ranked after the comparable segments: 2 of 2 is
   not "the worst-run subnet".  These are product heuristics, not statistics.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

# Below this many assessed hosts a rate is too unstable to rank on.
LIMITED_MIN_ASSESSED = 10
# Below this share of eligible hosts assessed, the picture is mostly unknown.
LIMITED_MIN_COVERAGE = 0.6

_STATE_ORDER = {"comparable": 0, "limited": 1, "unassessed": 2}


def _round(value: float) -> int:
    """Half up, as a reader rounds (Python's ``round`` goes to even)."""
    return math.floor(value + 0.5)


def _ratio(n: int, d: int) -> Optional[float]:
    return n / d if d > 0 else None


def _pct(value: float) -> str:
    return f"{_round(value * 100)}%"


def rank_concentration(row: Dict[str, Any], segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The row's segments, comparable ones first by rate, then the limited
    ones by what is known (count), then the unassessed."""
    labels = {s["key"]: s["label"] for s in segments}
    cells = row.get("cells") or []
    total_affected = sum(c["affected"] for c in cells)
    total_assessed = sum(c["assessed"] for c in cells)

    ranked: List[Dict[str, Any]] = []
    for cell in cells:
        affected, assessed = cell["affected"], cell["assessed"]
        rate = _ratio(affected, assessed)
        rest_affected = total_affected - affected
        rest_assessed = total_assessed - assessed
        rest_rate = _ratio(rest_affected, rest_assessed)
        eligible, eligible_assessed = cell.get("eligible"), cell.get("eligible_assessed")
        known = eligible is not None and eligible_assessed is not None
        coverage = _ratio(eligible_assessed, eligible) if known else None
        unknown = max(0, eligible - eligible_assessed) if known else None

        reasons: List[str] = []
        if 0 < assessed < LIMITED_MIN_ASSESSED:
            reasons.append(f"only {assessed} assessed")
        if coverage is not None and coverage < LIMITED_MIN_COVERAGE:
            reasons.append(f"{_round(coverage * 100)}% of eligible hosts assessed")
        state = "unassessed" if assessed == 0 else "limited" if reasons else "comparable"

        ranked.append({
            "segment": cell["segment"],
            "label": labels.get(cell["segment"], cell["segment"]),
            "affected": affected,
            "assessed": assessed,
            "in_scope": cell["in_scope"],
            "eligible": eligible,
            "eligible_assessed": eligible_assessed,
            "rate": rate,
            "coverage": coverage,
            "unknown": unknown,
            "rest_affected": rest_affected,
            "rest_assessed": rest_assessed,
            "rest_rate": rest_rate,
            "delta_points": None if rate is None or rest_rate is None else (rate - rest_rate) * 100,
            "share_of_affected": _ratio(affected, total_affected),
            "state": state,
            "limited_because": " · ".join(reasons),
        })

    ranked.sort(key=lambda r: (
        _STATE_ORDER[r["state"]],
        -(r["rate"] or 0) if r["state"] == "comparable" else 0,
        -r["affected"],
        -r["in_scope"],
        str(r["label"]).casefold(),
    ))
    for r in ranked:
        r["sentence"] = describe_concentration(r, row["family_label"])
    return ranked


def leading_family(rows: List[Dict[str, Any]], segments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The family worth leading with: the one whose top comparable segment
    stands furthest above the rest of the project; failing that, the most
    affected (the first affected row, in the grid's order)."""
    best: Optional[Dict[str, Any]] = None
    best_delta = -math.inf
    for row in rows:
        if row["affected_total"] == 0:
            continue
        top = next(
            (r for r in rank_concentration(row, segments) if r["state"] == "comparable" and r["affected"] > 0),
            None,
        )
        delta = top["delta_points"] if top and top["delta_points"] is not None else -math.inf
        if best is None or delta > best_delta:
            best, best_delta = row, delta
    return best


def describe_concentration(r: Dict[str, Any], family_label: str) -> str:
    """One sentence a presenter can read out; every figure is on the row."""
    if r["state"] == "unassessed":
        return (
            f"{r['label']}: no evidence for {family_label.lower()} — "
            f"{r['in_scope']} hosts in scope are unknown, not clean."
        )
    head = f"{r['label']}: {r['affected']} of {r['assessed']} assessed hosts affected ({_pct(r['rate'] or 0)})"
    if r["state"] == "limited":
        return (
            f"{head}. Limited comparison — {r['limited_because']}; "
            "collect the missing evidence before reading much into the rate."
        )
    if r["rest_rate"] is None:
        return f"{head}. No other segment was assessed to compare it with."
    delta = _round(r["delta_points"] or 0)
    versus = (
        f"{r['rest_affected']} of {r['rest_assessed']} ({_pct(r['rest_rate'])}) "
        "across the rest of the assessed project"
    )
    if delta >= 10:
        return f"{head}, against {versus} — {delta} points higher."
    share = r["share_of_affected"]
    if share is not None and share >= 0.5 and r["affected"] > 0:
        return (
            f"{head}, against {versus}. It holds {_pct(share)} of the affected hosts "
            "because it is large, not because its rate stands out."
        )
    return f"{head}, against {versus} — in line with the rest."


def compute_focus(heatmap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """``/posture``'s ``focus``: every affected family's ranking and the one to
    lead with.  None when there is no grid (nothing scoped)."""
    if not heatmap:
        return None
    segments = heatmap.get("segments") or []
    rows = heatmap.get("rows") or []
    lead = leading_family(rows, segments)
    return {
        "lead_family": lead["family"] if lead else None,
        "limited_min_assessed": LIMITED_MIN_ASSESSED,
        "limited_min_coverage": LIMITED_MIN_COVERAGE,
        "families": {
            row["family"]: rank_concentration(row, segments)
            for row in rows if row["affected_total"] > 0
        },
    }
