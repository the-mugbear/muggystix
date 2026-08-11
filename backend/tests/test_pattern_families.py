"""Pattern-family taxonomy (Phase 1): the classify() scale and the condition→
family mapping the systemic surface groups by."""
import pytest

from app.services.pattern_families import (
    classify,
    family_for_condition,
    family_for_vuln,
    FAMILIES,
    _CONDITION_FAMILY,
)


@pytest.mark.parametrize("is_systemic,spans_estate,estate_large,expected", [
    (True, True, True, "estate_wide"),
    (True, True, False, "recurring"),   # estate too small to generalise
    (True, False, True, "recurring"),   # systemic but confined
    (False, True, True, "isolated"),    # below the systemic floor
    (False, False, False, "isolated"),
])
def test_classify_scale(is_systemic, spans_estate, estate_large, expected):
    assert classify(
        is_systemic=is_systemic, spans_estate=spans_estate,
        estate_large_enough=estate_large,
    ) == expected


def test_every_condition_maps_to_a_known_family():
    """The five per-host conditions each resolve to a defined family."""
    from app.services.systemic_insight_service import _CONDITIONS
    condition_keys = {c[0] for c in _CONDITIONS}
    # The taxonomy covers exactly the conditions the systemic service produces.
    assert set(_CONDITION_FAMILY) == condition_keys
    for key in condition_keys:
        fam = family_for_condition(key)
        assert fam is not None
        assert fam.key in FAMILIES


def test_vuln_family_is_monoculture():
    assert family_for_vuln().key == "vuln_monoculture"


def test_unknown_condition_has_no_family():
    assert family_for_condition("not_a_condition") is None
