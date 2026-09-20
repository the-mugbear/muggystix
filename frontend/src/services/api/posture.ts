/**
 * Security Posture API client — the manager-facing roll-up. One composed
 * snapshot (deterministic label + headline measures + ranked priorities +
 * site/systemic/disposition breakdowns). Project-scoped via p().
 */
import { api, p } from './client';
import type { SystemicCondition, SeverityCounts } from './insights';

export type PostureLabel =
  | 'action_required'
  | 'needs_assessment'
  | 'insufficient_evidence'
  | 'no_urgent_signals';
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

export interface PostureReason {
  text: string;
  severity: Severity;
}

export interface PriorityItem {
  kind: string;          // exposure | ownership | systemic | site | coverage | triage | onboard
  /** action | assess set the label; `work` (an unassigned finding) never does. */
  tier?: 'action' | 'assess' | 'work';
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
  /** Reviewed hosts whose review concluded "needs more evidence" — the list
   *  is the hosts filter `conclusion:needs_evidence`. */
  open_questions?: { needs_evidence_hosts: number };
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

/** A single explainable measure — mirrors app/schemas/metric.py. */
export interface Metric {
  value: number;
  numerator: number;
  denominator: number;
  drilldown_filter?: Record<string, unknown> | null;
  confidence?: number | null;
}

/** One cell of the condition-family × site heatmap (backend 2.329.0).
 *  value = affected / assessed. `assessed` is the site's in-scope hosts that
 *  carry evidence in the row's assessment domain — the honest denominator;
 *  `in_scope` is the site inventory; `unassessed` (assessed === 0) is a
 *  different state from affected === 0. */
export interface HeatmapCell extends Metric {
  segment: string;
  affected: number;
  assessed: number;
  in_scope: number;
  unassessed: boolean;
  /** Evidence completeness (backend 2.373.0): the site's hosts this family's
   *  domain applies to, and how many of those carry its evidence. */
  eligible?: number;
  eligible_assessed?: number;
  /** `subnet` / `exclude_subnets` are set when the grid's columns are subnets. */
  drilldown_filter?: {
    conditions: string[]; site: string | null;
    subnet?: string | null; exclude_subnets?: string[];
  } | null;
}

export interface HeatmapSegment {
  key: string;
  label: string;
  in_scope: number;
  /** Legacy alias of in_scope — the per-cell `assessed` is authoritative. */
  assessed: number;
}

export interface HeatmapRow {
  family: string;
  family_label: string;
  conditions: string[];
  /** evidence_service domain whose evidence detects this family. */
  evidence_domain: string;
  evidence_domain_label: string;
  affected_total: number;
  cells: HeatmapCell[];
}

export interface PostureHeatmap {
  /** What the columns are: sites, or — when the project defines no site at
   *  all — the hosts' most-specific subnets (backend 2.373.1). */
  group_by?: 'site' | 'subnet';
  segments: HeatmapSegment[];
  rows: HeatmapRow[];
}

export interface PostureConclusion {
  text: string;
  tone: 'negative' | 'caution' | 'neutral' | 'positive';
}

export interface PostureResponse {
  label: PostureLabel;
  conclusion: PostureConclusion;
  reasons: PostureReason[];
  heatmap: PostureHeatmap | null;
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

// --- Evidence coverage (Phase 4) -------------------------------------------
export interface EvidenceDomain {
  /** The collection or planning step that closes this domain's gap. */
  action?: { kind: 'collect' | 'plan'; text: string };
  key: string;
  label: string;
  note: string;
  coverage: Metric;
}

/** One cell of the evidence matrix (backend 2.374.0). Three states and no more:
 *  assessed, not assessed (`gap`), not applicable (`eligible === 0`). A project is
 *  one assessment window — evidence does not go "stale" inside it. */
export interface EvidenceMatrixCell {
  segment: string;
  eligible: number;
  assessed: number;
  gap: number;
}

export interface EvidenceMatrix {
  /** Same columns as the Overview grid — sites, or subnets when no site is
   *  defined — plus `unmapped` (hosts outside every scoped subnet). */
  group_by: 'site' | 'subnet';
  segments: { key: string; label: string; hosts: number }[];
  rows: { domain: string; label: string; cells: EvidenceMatrixCell[] }[];
}

export interface EvidenceCoverageResponse {
  total_hosts: number;
  domains: EvidenceDomain[];
  matrix?: EvidenceMatrix | null;
  contributing_tools: { tool: string; scans: number }[];
  data_quality: { scans: number; parse_errors_unresolved: number };
}

/** v2.348.0 — a coverage gap as a list: the eligible-but-unassessed hosts,
 *  the open ports that made them eligible, and the step that closes it. */
export interface EvidenceGapHost {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  ports: number[];
}

export interface EvidenceGapsResponse {
  domain: string;
  label: string;
  /** Set when the list was narrowed to one matrix cell. */
  segment?: string | null;
  segment_label?: string | null;
  total: number;
  items: EvidenceGapHost[];
  action: { kind: 'collect' | 'plan'; text: string };
}

export const getEvidenceGaps = async (
  domain: string,
  options: { limit?: number; segment?: string; signal?: AbortSignal } = {},
): Promise<EvidenceGapsResponse> => {
  const params: Record<string, string | number> = {};
  if (options.limit) params.limit = options.limit;
  if (options.segment) params.segment = options.segment;
  const response = await api.get<EvidenceGapsResponse>(`${p()}/posture/evidence/${domain}/gaps`, {
    params: Object.keys(params).length ? params : undefined,
    signal: options.signal,
  });
  return response.data;
};

export const getEvidenceCoverage = async (
  options: { signal?: AbortSignal } = {},
): Promise<EvidenceCoverageResponse> => {
  const response = await api.get<EvidenceCoverageResponse>(`${p()}/posture/evidence`, {
    signal: options.signal,
  });
  return response.data;
};
