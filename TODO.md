# TODO

Forward-looking / deferred work. (`CHANGELOG.md` records what changed; this records
what's intentionally left for later.)

## Risk scoring — hidden, needs rework

**Status:** hidden from the UI 2026-06-06.

Risk scoring (`HostRiskAssessment`) is in a broken state — the table is unpopulated, so
every host shows a 0/empty score and the `risk:` query field / "Min risk score" filter
match nothing. It was **hidden, not fixed**, pending a rework that makes the scoring
**weights admin-tunable** (prior product direction).

**Re-enable points (all marked `TODO(risk-scoring)` in code):**
- `frontend/src/config/featureFlags.ts` → flip `RISK_SCORING_ENABLED` to `true`. This
  re-shows the "Min risk score" filter (`HostFilters.tsx`) and its active-filter chip
  (`Hosts.tsx`).
- `backend/app/services/host_query_dsl.py` → un-comment `FieldSpec("risk", _b_risk)`
  (restores the `risk:` DSL field; `_b_risk` / `P.risk_predicate` are left intact).
- `backend/app/api/v1/endpoints/hosts.py` → re-add `risk` to the `q` DSL field description.
- Docs: restore the "Risk Assessment" section in `frontend/src/pages/UserGuide.tsx` and
  the card in `frontend/src/pages/Reference.tsx`.

**Before re-enabling:** `RiskAssessmentService` must actually populate `HostRiskAssessment`
(currently 0 rows), and the hardcoded scoring weights (vulnerability 0.4 / exposure 0.25 /
config 0.2 / attack-surface 0.15 in `risk_assessment_service.py`) should move to
admin-configurable settings. The standalone `/risk-assessment` page and `getRiskInsights`
remain in place but render empty until then.
