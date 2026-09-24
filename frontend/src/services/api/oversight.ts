/**
 * Oversight — the global administrators' programme dashboard (5.258.0).
 * One request returns the whole filtered cohort; the backend is
 * app/api/v1/endpoints/oversight.py (admin-only at the router).
 */
import { api } from './client';

export interface OversightSeverity {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

/** Findings by where they stand — the three add up to the findings total;
 *  false positives are not results and are counted apart. */
export interface OversightFindingStates {
  /** open / retest */
  under_investigation: number;
  confirmed: number;
  /** accepted risk / remediated */
  closed: number;
}

/** Percent of tested targets; null when nothing has been tested. */
export interface OversightSeverityRate {
  critical: number | null;
  high: number | null;
  medium: number | null;
  low: number | null;
}

export interface OversightProjectRow {
  id: number;
  name: string;
  status: string;
  start_date: string | null;
  end_date: string | null;
  admins: string[];
  // Current
  host_count: number;
  hosts_tested: number;
  hosts_in_review: number;
  hosts_reviewed: number;
  findings: OversightSeverity;
  finding_states: OversightFindingStates;
  findings_false_positive: number;
  finding_affected_targets: number;
  /** Every scanner observation (issue × host), and the judged / not yet
   *  judged split of the same rows. Informational is left out. */
  observations: OversightSeverity;
  observations_judged: OversightSeverity;
  observations_unjudged: OversightSeverity;
  /** "Tested targets with a finding": % of tested targets with at least one
   *  non-false-positive finding endpoint at that severity. */
  defect_rate: OversightSeverityRate;
  last_scan_at: string | null;
  pending_plan_reviews: number;
  blocked_sessions: number;
  // Selected period
  targets_added: number;
  reviews_concluded: number;
  imports: number;
  contributors: number;
  /** critical | high | pending_review | blocked_session | no_admin | quiet | no_data */
  attention_reasons: string[];
}

export interface OversightTesterProject {
  project_id: number;
  project_name: string;
  /** null: no longer a member of this project. */
  role: string | null;
  tested: number;
  in_review: number;
  reviewed: number;
  reviewed_in_period: number;
  findings: OversightSeverity;
}

export interface OversightTesterRow {
  user_id: number;
  username: string;
  full_name: string | null;
  is_active: boolean;
  /** Selected projects where they have a target in review or reviewed (v5.289.0; was `active_projects`, memberships). */
  projects_tested: number;
  tested: number;
  in_review: number;
  reviewed: number;
  reviewed_in_period: number;
  findings: OversightSeverity;
  open_tasks: number;
  last_contribution_at: string | null;
  projects: OversightTesterProject[];
}

export interface OversightSummary {
  projects_total: number;
  projects_in_progress: number;
  projects_complete: number;
  targets_current: number;
  targets_through_end: number;
  targets_added: number;
  targets_tested: number;
  targets_in_review: number;
  targets_reviewed: number;
  reviews_concluded: number;
  imports: number;
  contributors: number;
  unattributed_events: number;
  severity: {
    findings: OversightSeverity;
    finding_states: OversightFindingStates;
    findings_false_positive: number;
    finding_affected_targets: number;
    observations: OversightSeverity;
    observations_judged: OversightSeverity;
    observations_unjudged: OversightSeverity;
    tested_targets: number;
    defect_targets: OversightSeverity;
    defect_rate: OversightSeverityRate;
  };
}

export interface OversightAttention {
  critical_projects: number;
  pending_approval_plans: number;
  blocked_runs: number;
  no_admin_projects: number;
  quiet_projects: number;
  no_inventory_projects: number;
}

export interface OversightOption {
  id: number;
  name: string;
  status?: string | null;
}

export interface OversightGrowthPoint {
  /** First UTC day of the bucket (YYYY-MM-DD). */
  start: string;
  targets_added: number;
  reviews_concluded: number;
  cumulative_targets: number;
}

export type SeverityBasis = 'current' | 'period';

export interface OversightResponse {
  window: { start: string | null; end: string | null; timezone: string };
  generated_at: string;
  /** current = latest state; period = first recorded inside the dates. */
  severity_basis: SeverityBasis;
  summary: OversightSummary;
  growth: { unit: 'day' | 'week' | 'month'; points: OversightGrowthPoint[] };
  attention: OversightAttention;
  accounts: { total: number; enabled: number; disabled: number; without_membership: number };
  projects: OversightProjectRow[];
  testers: OversightTesterRow[];
  project_options: OversightOption[];
  tester_options: OversightOption[];
}

export interface OversightQuery {
  /** YYYY-MM-DD, UTC day, inclusive. */
  start?: string;
  end?: string;
  project_id?: number[];
  status?: string[];
  tester_id?: number;
  window_overlap?: boolean;
  severity_basis?: SeverityBasis;
}

export const getOversightDashboard = async (q: OversightQuery): Promise<OversightResponse> => {
  const params = new URLSearchParams();
  if (q.start) params.set('start', q.start);
  if (q.end) params.set('end', q.end);
  (q.project_id ?? []).forEach((id) => params.append('project_id', String(id)));
  (q.status ?? []).forEach((s) => params.append('status', s));
  if (q.tester_id != null) params.set('tester_id', String(q.tester_id));
  if (q.window_overlap) params.set('window_overlap', 'true');
  if (q.severity_basis === 'period') params.set('severity_basis', 'period');
  const response = await api.get('/oversight/dashboard', { params });
  return response.data;
};
