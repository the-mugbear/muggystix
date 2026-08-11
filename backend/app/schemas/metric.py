"""The slim posture metric contract (Phase 2 of the posture overhaul).

Every posture number a manager sees should be explainable and reconcilable: it
carries its own numerator and denominator (so "8/9" is visible, not a bare "89%"
that hides its sample size) and a structured ``drilldown_filter`` the frontend
turns into the /hosts or /findings query that lists exactly those records.

Deliberately slim — four fields plus optional confidence — not an 11-field
enterprise-metric envelope. `value` is what to display; numerator/denominator are
the sample behind it; drilldown_filter is how to reconcile it. Methodology lives
in the UI tooltip copy, not in the payload.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class Metric(BaseModel):
    """A single explainable measure with its sample and a way to reconcile it."""

    model_config = ConfigDict(extra="forbid")

    value: float
    numerator: int
    denominator: int
    # Structured filter the frontend maps to a /hosts or /findings URL so the
    # displayed count and the list it opens reconcile. Shape is destination-
    # specific (e.g. {"condition": "smb_signing", "site": "HQ"}); the frontend's
    # drilldown-link builder is the one place that knows how to render it.
    drilldown_filter: Optional[Dict[str, Any]] = None
    # 0.0–1.0 when a producer computes a confidence for this measure; omitted
    # when it doesn't (never fabricate one).
    confidence: Optional[float] = None


def ratio_metric(
    numerator: int,
    denominator: int,
    *,
    drilldown_filter: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
) -> Metric:
    """Build a Metric whose value is numerator/denominator (0.0 when denom==0)."""
    value = (numerator / denominator) if denominator else 0.0
    return Metric(
        value=round(value, 4),
        numerator=numerator,
        denominator=denominator,
        drilldown_filter=drilldown_filter,
        confidence=confidence,
    )
