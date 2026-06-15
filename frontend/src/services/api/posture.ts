/**
 * Security Posture API client — the manager-facing roll-up. One composed
 * snapshot (deterministic label + headline measures + ranked priorities +
 * site/systemic/disposition breakdowns). Project-scoped via p().
 */
import { api, p } from './client';
import type { SystemicCondition, SeverityCounts } from './insights';

export type PostureLabel = 'action_required' | 'needs_assessment' | 'no_urgent_signals';
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

export interface PostureReason {
  text: string;
  severity: Severity;
}

export interface PriorityItem {
  kind: string;          // ownership | systemic | site | blocked | coverage | triage | approval | onboard
  title: string;
  blast_radius: string;
  action: string;
  severity: Severity;
  owner: string | null;
  link: string | null;
  score: number;
}

export interface PostureHeadline {
  active_exposure: { active_findings: number; by_severity: SeverityCounts };
  review_coverage: { reviewed: number; total: number; pct: number | null; validated_hosts: number };
  ownership: { owned: number; unowned: number; total: number; pct: number | null };
  systemic: { adopted: boolean; blind_spot_count: number; condition_count: number };
  detected_exposure: { vuln_count: number };
}

export interface PostureSiteExposure {
  raw_score: number;
  weighted_score: number;
  active_findings: number;
  finding_host_incidences: number;
  by_severity: SeverityCounts;
}

export interface PostureSite {
  site: string | null;
  site_id: number | null;
  unassigned: boolean;
  criticality_tier: number | null;
  owner_name: string | null;
  host_count: number;
  expected_host_count: number | null;
  coverage_gap: number | null;
  exposure: PostureSiteExposure;
  neglect: { unowned_active_findings: number; unreviewed_hosts: number };
  recommended_action: { kind: string; text: string };
}

export interface PostureResponse {
  label: PostureLabel;
  reasons: PostureReason[];
  headline: PostureHeadline;
  priorities: PriorityItem[];
  decisions: { pending_approvals: number; blocked_sessions: number };
  sites: { adopted: boolean; items: PostureSite[] };
  systemic: {
    adopted: boolean;
    estate: { hosts_in_scope: number; subnets: number; sites: number; blind_spot_count: number };
    conditions: SystemicCondition[];
    blind_spots: SystemicCondition[];
  };
  disposition: {
    by_status: Record<string, number>;
    by_status_severity: Record<string, Partial<SeverityCounts>>;
    active_total: number;
    scanner_active: number;
    non_scanner_active: number;
  };
  evidence: { scan_count: number; scan_staleness_days: number | null };
}

export const getPosture = async (
  options: { signal?: AbortSignal } = {},
): Promise<PostureResponse> => {
  const response = await api.get<PostureResponse>(`${p()}/posture`, {
    signal: options.signal,
  });
  return response.data;
};
