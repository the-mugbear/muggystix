/**
 * Temporary feature flags.
 *
 * Each flag MUST carry a TODO with its revisit condition (mirrored in the
 * repo-root TODO.md).  These are meant to be short-lived toggles, not a
 * growing config surface — flip and delete when the underlying work lands.
 */

/**
 * Risk scoring (HostRiskAssessment) is currently in a broken state — the
 * table is unpopulated, so every host shows a 0/empty score and the
 * `risk:` query field / "Min risk score" filter match nothing.  Hidden from
 * the Hosts UI until risk scoring is reworked (planned: admin-tunable
 * scoring weights).  See TODO.md.
 *
 * Re-enable: flip to true once HostRiskAssessment is populated again (and
 * un-comment the `risk` field in backend host_query_dsl.py).
 */
export const RISK_SCORING_ENABLED = false;
