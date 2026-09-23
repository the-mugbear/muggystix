"""CVSS vectors on a finding's report text (v2.379.0).

A finding stores a vector and a score.  For a CVSS 3.0 / 3.1 or 2.0 vector
the score is DETERMINED by the vector, so it is computed here (the base
equations from the FIRST specifications) rather than typed — a report never
shows a score that disagrees with its own vector.  CVSS 4.0 scores come from
a large lookup table that is not reimplemented here: a 4.0 vector is checked
for its base metrics and its score is entered by the analyst.

``normalize_cvss`` is the one entry point.  ``strict=False`` is for seeding
from scanner rows, whose vectors are not always well formed: an unreadable
vector is dropped and the scanner's own score kept.
"""
from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Optional, Tuple


class CvssError(ValueError):
    """The vector (or score) cannot be accepted."""


# --- CVSS 3.x ----------------------------------------------------------------

_V3_WEIGHTS: Dict[str, Dict[str, float]] = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "PR": {"N": 0.85, "L": 0.62, "H": 0.27},
    "UI": {"N": 0.85, "R": 0.62},
    "S": {"U": 0.0, "C": 0.0},
    "C": {"H": 0.56, "L": 0.22, "N": 0.0},
    "I": {"H": 0.56, "L": 0.22, "N": 0.0},
    "A": {"H": 0.56, "L": 0.22, "N": 0.0},
}
# Privileges Required weighs more when the scope changes.
_V3_PR_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.5}


def _roundup_31(value: float) -> float:
    """CVSS 3.1 Roundup: the smallest one-decimal number >= value, computed
    on integers so 4.0000001 does not become 4.1 (spec Appendix A)."""
    as_int = round(value * 100000)
    if as_int % 10000 == 0:
        return as_int / 100000.0
    return (math.floor(as_int / 10000) + 1) / 10.0


def _roundup_30(value: float) -> float:
    return math.ceil(value * 10) / 10.0


def _parse_metrics(body: str) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    for part in body.split("/"):
        if ":" not in part:
            raise CvssError(f"'{part}' is not a metric (expected NAME:VALUE).")
        key, _, value = part.partition(":")
        if not key or not value:
            raise CvssError(f"'{part}' is not a metric (expected NAME:VALUE).")
        if key in metrics:
            raise CvssError(f"The metric {key} appears twice.")
        metrics[key] = value
    return metrics


def _score_v3(version: str, metrics: Dict[str, str]) -> float:
    for key, allowed in _V3_WEIGHTS.items():
        if key not in metrics:
            raise CvssError(f"A CVSS {version} vector needs the base metric {key}.")
        if metrics[key] not in allowed:
            raise CvssError(f"{key}:{metrics[key]} is not a CVSS {version} value.")
    changed = metrics["S"] == "C"
    av = _V3_WEIGHTS["AV"][metrics["AV"]]
    ac = _V3_WEIGHTS["AC"][metrics["AC"]]
    pr = (_V3_PR_CHANGED if changed else _V3_WEIGHTS["PR"])[metrics["PR"]]
    ui = _V3_WEIGHTS["UI"][metrics["UI"]]
    c = _V3_WEIGHTS["C"][metrics["C"]]
    i = _V3_WEIGHTS["I"][metrics["I"]]
    a = _V3_WEIGHTS["A"][metrics["A"]]

    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    if changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss
    exploitability = 8.22 * av * ac * pr * ui
    if impact <= 0:
        return 0.0
    roundup = _roundup_31 if version == "3.1" else _roundup_30
    total = impact + exploitability
    if changed:
        total *= 1.08
    return roundup(min(total, 10.0))


# --- CVSS 2.0 ----------------------------------------------------------------

_V2_WEIGHTS: Dict[str, Dict[str, float]] = {
    "AV": {"L": 0.395, "A": 0.646, "N": 1.0},
    "AC": {"H": 0.35, "M": 0.61, "L": 0.71},
    "Au": {"M": 0.45, "S": 0.56, "N": 0.704},
    "C": {"N": 0.0, "P": 0.275, "C": 0.660},
    "I": {"N": 0.0, "P": 0.275, "C": 0.660},
    "A": {"N": 0.0, "P": 0.275, "C": 0.660},
}


def _score_v2(metrics: Dict[str, str]) -> float:
    for key, allowed in _V2_WEIGHTS.items():
        if key not in metrics:
            raise CvssError(f"A CVSS 2.0 vector needs the base metric {key}.")
        if metrics[key] not in allowed:
            raise CvssError(f"{key}:{metrics[key]} is not a CVSS 2.0 value.")
    w = {k: _V2_WEIGHTS[k][metrics[k]] for k in _V2_WEIGHTS}
    impact = 10.41 * (1 - (1 - w["C"]) * (1 - w["I"]) * (1 - w["A"]))
    exploitability = 20 * w["AV"] * w["AC"] * w["Au"]
    f_impact = 0.0 if impact == 0 else 1.176
    base = ((0.6 * impact) + (0.4 * exploitability) - 1.5) * f_impact
    return float(Decimal(str(base)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


# --- CVSS 4.0 (checked, not scored) -----------------------------------------

_V4_BASE = {
    "AV": {"N", "A", "L", "P"}, "AC": {"L", "H"}, "AT": {"N", "P"},
    "PR": {"N", "L", "H"}, "UI": {"N", "P", "A"},
    "VC": {"H", "L", "N"}, "VI": {"H", "L", "N"}, "VA": {"H", "L", "N"},
    "SC": {"H", "L", "N"}, "SI": {"H", "L", "N"}, "SA": {"H", "L", "N"},
}


def _check_v4(metrics: Dict[str, str]) -> None:
    for key, allowed in _V4_BASE.items():
        if key not in metrics:
            raise CvssError(f"A CVSS 4.0 vector needs the base metric {key}.")
        if metrics[key] not in allowed:
            raise CvssError(f"{key}:{metrics[key]} is not a CVSS 4.0 value.")


# --- entry point ---------------------------------------------------------------

_PREFIX = re.compile(r"^CVSS:(3\.0|3\.1|4\.0)/(.+)$")


def _clean_score(score: Optional[float]) -> Optional[float]:
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        raise CvssError("A CVSS score is a number from 0.0 to 10.0.")
    if math.isnan(value) or value < 0 or value > 10:
        raise CvssError("A CVSS score is a number from 0.0 to 10.0.")
    return round(value, 1)


def score_vector(vector: str) -> Tuple[str, Optional[float]]:
    """``(normalised vector, computed score)`` — the score is None for 4.0.
    Raises ``CvssError`` for anything that is not a readable base vector."""
    text = (vector or "").strip()
    if not text:
        raise CvssError("The CVSS vector is empty.")
    match = _PREFIX.match(text)
    if match:
        version, body = match.group(1), match.group(2)
        metrics = _parse_metrics(body)
        if version == "4.0":
            _check_v4(metrics)
            return text, None
        return text, _score_v3(version, metrics)
    # CVSS 2.0 has no version prefix; scanners write it bare, as "CVSS2#…",
    # or in parentheses.
    body = text
    if body.upper().startswith("CVSS2#"):
        body = body[6:]
    body = body.strip("()")
    if not body.startswith("AV:"):
        raise CvssError(
            "Unrecognised CVSS vector — expected CVSS:3.1/…, CVSS:3.0/…, CVSS:4.0/… "
            "or a CVSS 2.0 vector (AV:…/AC:…/Au:…/C:…/I:…/A:…)."
        )
    metrics = _parse_metrics(body)
    return body, _score_v2(metrics)


def normalize_cvss(
    vector: Optional[str], score: Optional[float], *, strict: bool = True,
) -> Tuple[Optional[str], Optional[float]]:
    """``(vector, score)`` to store.  A 3.x / 2.0 vector decides the score; a
    4.0 vector keeps the given score; no vector keeps the given score.
    ``strict=False`` drops an unreadable vector (and an out-of-range score)
    instead of raising — for seeding from scanner output."""
    try:
        clean_score = _clean_score(score)
    except CvssError:
        if strict:
            raise
        clean_score = None
    if vector is None or not str(vector).strip():
        return None, clean_score
    try:
        normalised, computed = score_vector(str(vector))
    except CvssError:
        if strict:
            raise
        return None, clean_score
    if len(normalised) > 200:
        if strict:
            raise CvssError("A CVSS vector is at most 200 characters.")
        return None, clean_score
    return normalised, computed if computed is not None else clean_score
