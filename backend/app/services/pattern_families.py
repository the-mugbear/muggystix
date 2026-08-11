"""Pattern families — the vocabulary the posture surface groups weaknesses by.

The systemic-insight service computes individual *conditions* (EOL OS, SMB
signing off, cleartext services, guest auth, cert hygiene) and per-plugin *vuln
monocultures*. On their own that's a flat list. A manager reasons in terms of
*programmes*: "is our identity story broken?", "is our encryption/PKI managed?",
"do we patch?". A **pattern family** is that grouping — the program-level bucket
a condition rolls up to, carrying the likely root cause and the recommended
program-level control.

This module is the single source of that taxonomy (Phase 1 of the posture
overhaul). It's deliberately pure data + two small pure functions — no DB, no
ORM — so the systemic service, the (later) Patterns page, and tests all agree on
one mapping.

`technology_monoculture` is emitted by the systemic service as per-technology
monocultures (keyed ``tech:<name>``), analogous to the per-plugin vuln
monoculture — a shared versioned technology concentrated estate-wide. Both are
computed by Python-side bucketing of already-typed/blob rows (no JSON WHERE /
GROUP BY), so no separate typed technologies table is required; one would only
become necessary for a per-technology SQL-scale drill-down filter, which the
current has: DSL does not offer for monocultures.
"""
from __future__ import annotations

from typing import Dict, Literal, Optional

# ---------------------------------------------------------------------------
# Classification — how widely a weakness has spread. Replaces the earlier
# is_blind_spot boolean with a three-level scale; estate_wide is the old
# "blind spot". The thresholds themselves live in systemic_insight_service
# (they depend on estate size / site spread); classify() only names the result.
# ---------------------------------------------------------------------------
Classification = Literal["isolated", "recurring", "estate_wide"]


def classify(*, is_systemic: bool, spans_estate: bool, estate_large_enough: bool) -> Classification:
    """Name a weakness's spread from the three booleans the caller already computes.

    - ``estate_wide``  — systemic AND spanning the estate AND the estate is big
      enough to generalise from (this is exactly the old blind-spot condition).
    - ``recurring``    — clears the systemic host-fraction/count floor, but isn't
      estate-wide (confined to part of the estate, or the estate is too small).
    - ``isolated``     — below the systemic floor; a handful of incidents.
    """
    if is_systemic and spans_estate and estate_large_enough:
        return "estate_wide"
    if is_systemic:
        return "recurring"
    return "isolated"


# ---------------------------------------------------------------------------
# Families — program-level buckets. Each carries a stable key, a human label,
# the likely root cause (a HYPOTHESIS, always labelled as such downstream), and
# the recommended program-level control.
# ---------------------------------------------------------------------------
class PatternFamily:
    __slots__ = ("key", "label", "root_cause_hypothesis", "recommended_control")

    def __init__(self, key: str, label: str, root_cause_hypothesis: str, recommended_control: str):
        self.key = key
        self.label = label
        self.root_cause_hypothesis = root_cause_hypothesis
        self.recommended_control = recommended_control


FAMILIES: Dict[str, PatternFamily] = {
    f.key: f for f in [
        PatternFamily(
            "identity_auth", "Identity & authentication",
            "Access control is not consistently enforced — accounts, guest/anonymous access, or weak auth are tolerated.",
            "Establish an authentication baseline: no guest/null sessions, least-privilege, enforced everywhere.",
        ),
        PatternFamily(
            "encryption_trust", "Encryption & trust",
            "No certificate / PKI governance — TLS trust is unmanaged.",
            "Stand up certificate issuance/renewal and a TLS baseline; replace self-signed/expired certs.",
        ),
        PatternFamily(
            "lifecycle_patching", "Lifecycle & patching",
            "No OS lifecycle / patch programme — unsupported systems accrete unpatched.",
            "Inventory and upgrade or isolate end-of-life systems; establish a patch cadence.",
        ),
        PatternFamily(
            "legacy_cleartext", "Legacy & cleartext protocols",
            "No policy against unencrypted / legacy protocols — credentials are observable on the wire.",
            "Disable cleartext services or migrate to encrypted equivalents.",
        ),
        PatternFamily(
            "lateral_movement", "Lateral-movement controls",
            "No hardening baseline against relay / lateral movement (e.g. SMB signing off).",
            "Enable and require SMB signing; harden against NTLM relay across the estate.",
        ),
        PatternFamily(
            "vuln_monoculture", "Vulnerability monoculture",
            "A single exposure replicated estate-wide — one root cause, many hosts.",
            "Remediate the shared root cause once across all affected hosts.",
        ),
        PatternFamily(
            "technology_monoculture", "Technology monoculture",
            "A single technology/version concentrated across the estate — one flaw exposes many hosts.",
            "Diversify or standardise-and-patch the concentrated technology.",
        ),
    ]
}


# Condition key (as produced by systemic_insight_service._CONDITIONS) → family key.
_CONDITION_FAMILY: Dict[str, str] = {
    "eol_os": "lifecycle_patching",
    "cleartext_services": "legacy_cleartext",
    "tls_hygiene": "encryption_trust",
    "weak_tls": "encryption_trust",
    "weak_auth": "identity_auth",
    "smb_signing": "lateral_movement",
}


def family_for_condition(condition_key: str) -> Optional[PatternFamily]:
    """Family for a per-host condition key. Per-plugin vuln monocultures use
    ``family_for_vuln`` instead (they carry a ``vuln:<plugin_id>`` key)."""
    fam = _CONDITION_FAMILY.get(condition_key)
    return FAMILIES.get(fam) if fam else None


def family_for_vuln() -> PatternFamily:
    """Every per-plugin monoculture rolls up to the vuln-monoculture family."""
    return FAMILIES["vuln_monoculture"]
