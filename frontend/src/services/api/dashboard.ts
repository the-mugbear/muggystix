/**
 * Dashboard + Operations-workbench API client.
 *
 * Dashboard summary stats, the Operations workbench (`GET /workbench`: the
 * caller's counts, since-last-visit, blockers; one paged route per tab's
 * list) and agent-activity analytics.
 *
 * Extracted from the api.ts monolith.  Consumers still import these from
 * ``../services/api`` — the barrel re-exports this module.
 */
import { api, p } from './client';

export interface VulnerabilityStats {
  total_vulnerabilities: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  hosts_with_vulnerabilities: number;
  /** v2.424.0 — distinct hosts per severity: what a severity drill-down opens. */
  hosts_by_severity?: Partial<Record<'critical' | 'high' | 'medium' | 'low' | 'info', number>>;
}

// The response also carries recent_scans, subnet_stats and note_activity
// (left from the old dashboard); nothing here reads them, so they are not
// typed.
export interface DashboardStats {
  total_scans: number;
  total_hosts: number;
  total_ports: number;
  up_hosts: number;
  open_ports: number;
  total_subnets: number;
  vulnerability_stats?: VulnerabilityStats;
}

export const getDashboardStats = async (signal?: AbortSignal): Promise<DashboardStats> => {
  const response = await api.get(`${p()}/dashboard/stats`, { signal });
  return response.data;
};

// --- My Queue (hosts I've marked In Review) ---
export interface MyAttentionHost {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  follow_status: 'in_review' | 'watching';
  open_port_count: number;
  critical_vulns: number;
  high_vulns: number;
  last_viewed_at: string | null;
  follow_updated_at: string | null;
}

export interface MyAttentionResponse {
  items: MyAttentionHost[];
  in_review_count: number;
}
/** Why a task is in your queue. Overlapping — a task can carry several. */
export type MyTaskReason = 'assigned' | 'in_review' | 'triage';

export interface MyTaskItem {
  test_id: number;
  /** What runs the test (v2.452.0). */
  tool?: string | null;
  description: string;
  label: string | null;
  revision: number;
  host_id: number;
  host_ip: string;
  host_hostname: string | null;
  priority: string;
  status: string;
  rationale: string | null;
  updated_at: string | null;
  reasons: MyTaskReason[];
  assigned_to_id: number | null;
}

/** Per-bucket counts. Buckets overlap, so these do NOT sum to total_open. */
export interface MyTasksReasonCounts {
  assigned: number;
  in_review: number;
  triage: number;
}

export interface MyTasksResponse {
  items: MyTaskItem[];
  total_open: number;
  reason_counts: MyTasksReasonCounts;
  /** Each test counted ONCE, under its strongest reason — the group it is
   *  listed in. These add up to `total_open` (v2.450.0). */
  group_counts?: MyTasksReasonCounts;
}
export interface MyRecentNoteItem {
  note_id: number;
  host_id: number | null;
  host_ip: string | null;
  body_preview: string;
  note_type: string | null;
  created_at: string | null;
}

export interface MyRecentNotesResponse {
  items: MyRecentNoteItem[];
}

export interface MyFindingItem {
  finding_id: number;
  title: string;
  severity: string;
  status: string;
  host_id: number | null;
  host_count: number;
  evidence_annotation_id: number | null;
  updated_at: string | null;
  /** Why it is listed, in the order to act on (v2.450.0). */
  needs?: Array<{ kind: 'under_investigation' | 'missing_text' | 'proposals'; text: string }>;
  /** Required report sections still empty (a finding the report includes). */
  missing_text?: string[];
  pending_proposals?: number;
}

export interface MyFindingsResponse {
  items: MyFindingItem[];
  /** Findings the caller owns that NEED them — under investigation, required
   *  report text missing, or a proposal to decide (v2.450.0; it counted every
   *  active finding owned before). */
  total_open: number;
  /** `total_open` in its two parts (v2.453.0) — whole-list figures whatever
   *  `need` and the page say; each is the size of the list its `need` returns. */
  need_counts?: Record<FindingNeed, number>;
}

/** What a finding asks of its owner (v2.453.0): `decide` — it is under
 *  investigation, or a proposal about it waits for a decision; `write` — only
 *  required report text is missing.  A finding with both is a `decide`. */
export type FindingNeed = 'decide' | 'write';

/** The caller's work, counted (v2.450.0); `to_claim` is shared work, outside
 *  `total`.  The page says the kinds apart and never shows `total`. */
export interface MyWorkTotals {
  total: number;
  hosts_in_review: number;
  tests_assigned: number;
  tests_on_hosts_in_review: number;
  findings_needing_me: number;
  /** `findings_needing_me` in its two parts (v2.453.0); they add up to it. */
  findings_to_decide?: number;
  findings_to_write?: number;
  to_claim: number;
}

// --- Operations workbench (batched personal surface + since-last-visit) ---

// v2.363.0 — work that has stopped and will not resume by itself.
export interface BlockedImport {
  job_id: number;
  filename: string;
  kind: 'failed' | 'partial';
  message?: string | null;
  at?: string | null;
}

export interface OperationsBlockers {
  failed_import_count: number;
  partial_import_count: number;
  imports: BlockedImport[];
}

export interface SinceLastVisit {
  last_viewed_at: string | null;
  is_first_visit: boolean;
  new_scan_count: number;
  latest_scan_id: number | null;
  latest_scan_filename: string | null;
  latest_scan_created_at: string | null;
  new_host_count: number;
  /** Hosts already known before the window that gained a port or a scanner
   *  observation in it. Disjoint from `new_host_count`. (v2.363.0) */
  changed_host_count?: number;
  /** SCANNER OBSERVATIONS, despite the legacy field names — not judged findings. */
  new_critical_findings: number;
  new_high_findings: number;
  /** Hosts carrying those observations — what the drill-down lists. */
  new_critical_hosts?: number;
  new_high_hosts?: number;
  /** When these counts were taken — handed back on acknowledge so it covers
   *  the snapshot shown, not changes that arrived after it loaded. */
  as_of?: string | null;
}

// v5.223.0 — the engagement-wide investigation queue (design review item 2):
// hosts nobody has touched (no review, assignment, note, host test, evidence or
// finding) that carry an observed weakness or a relevant change.  Ordered by
// a stated tier, never a composite score; every row says why.
export interface InvestigateReason {
  kind: string;
  text: string;
}

export interface InvestigateRow {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  tier: number;
  tier_label: string;
  reasons: InvestigateReason[];
  evidence: {
    /** Tools whose scans observed this host. */
    sources: string[];
    last_seen: string | null;
    /** What backs the reasons: scanner output only, a finding, or a test. */
    confirmation: 'scanner' | 'finding' | 'tested';
  };
  next_action: {
    /** review = take it into review under the caller (the row's button);
     *  collect = evidence is missing first. */
    kind: 'review' | 'collect';
    text: string;
    /** v2.424.0 — says nothing the queue does not ("take it into review"). */
    generic?: boolean;
  };
}

export interface InvestigationQueueResponse {
  items: InvestigateRow[];
  /** Hosts in the project nobody has touched, with or without a reason. */
  untouched_total: number;
  /** Untouched hosts that carry at least one reason (the queue's true size). */
  queue_total: number;
  /** The tier labels in order, for the legend. */
  tiers: string[];
  /** Hosts per tier, aligned with `tiers`; they add up to `queue_total`
   *  (v2.427.0). Whole-queue even when the rows are narrowed to one tier. */
  tier_counts?: number[];
}

export interface WorkbenchResponse {
  my_queue: MyAttentionResponse;
  my_tasks: MyTasksResponse;
  recent_notes: MyRecentNotesResponse;
  my_findings: MyFindingsResponse;
  since_last_visit: SinceLastVisit;
  /** `null` when requested with `includeInvestigate: false` (v2.424.1). */
  investigate?: InvestigationQueueResponse | null;
  /** The queue could not be computed: `investigate` is an empty placeholder
   *  and must read as "unavailable", never as "no work". */
  investigate_unavailable?: boolean;
  /** The caller's own reviewed hosts that are not done (v2.359.0; the
   *  caller's only since v2.451.0). */
  followups?: ReviewFollowupsResponse;
  followups_unavailable?: boolean;
  blockers?: OperationsBlockers;
  /** Could not be computed — must read as "unavailable", never "nothing blocked". */
  blockers_unavailable?: boolean;
  /** The caller's queue as one number (v2.450.0). */
  my_work?: MyWorkTotals;
}

// v2.359.0 — a reviewed host left every queue for good. Two kinds are not
// done: a review concluded "needs more evidence", and a host that changed
// AFTER it was reviewed.  v2.451.0 — every row is the CALLER'S own review
// (one row per host); a teammate's is never listed.
export interface ReviewFollowupRow {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  reviewed_at: string | null;
  review_conclusion: string | null;
  review_summary: string | null;
  reasons: Array<{ kind: string; text: string }>;
}

export interface ReviewFollowupsResponse {
  items: ReviewFollowupRow[];
  /** The caller's hosts in the list — the section's count, and the length of
   *  the Hosts list it opens (`follow:revisit`). */
  total: number;
}

export const getWorkbench = async (
  opts: { includeInvestigate?: boolean; includeRows?: boolean } = {},
  signal?: AbortSignal,
): Promise<WorkbenchResponse> => {
  // v2.424.1 — Operations leaves the untouched queue out and loads it with
  // getInvestigationQueue(): on a large project it is most of the time, and
  // the personal sections should not wait for it.
  // v2.452.0 — and the rows: `includeRows: false` is the light call the tab
  // bar counts from; each tab pages its own list (the functions below).
  const params: Record<string, boolean> = {};
  if (opts.includeInvestigate === false) params.include_investigate = false;
  if (opts.includeRows === false) params.include_rows = false;
  const response = await api.get(`${p()}/workbench`, {
    params: Object.keys(params).length ? params : undefined,
    signal,
  });
  return response.data;
};

/** One page of an Operations tab's list. */
export interface WorkbenchPage {
  limit?: number;
  offset?: number;
  signal?: AbortSignal;
}

const pageParams = (page: WorkbenchPage): Record<string, number | string> => {
  const params: Record<string, number | string> = {};
  if (page.limit != null) params.limit = page.limit;
  if (page.offset) params.offset = page.offset;
  return params;
};

/** The untouched queue alone. Rejects (503) when it could not be computed —
 *  callers show "unavailable", never an empty queue. `offset` pages it in
 *  the queue's own order; the totals stay whole-queue. */
export const getInvestigationQueue = async (
  tier?: number | null,
  page: WorkbenchPage = {},
): Promise<InvestigationQueueResponse> => {
  const params = pageParams(page);
  if (tier) params.tier = tier;
  const response = await api.get(`${p()}/workbench/investigate`, {
    params: Object.keys(params).length ? params : undefined,
    signal: page.signal,
  });
  return response.data;
};

// -- the Operations tabs' lists (v2.452.0) -----------------------------------
// Each is the function that produces the tab's count in `GET /workbench`,
// paged: the count on a tab is the size of the list it pages through.

/** Findings the caller owns that need them. With `need`, only that kind of
 *  work; the list's size is `total_open` (both kinds) or `need_counts[need]`. */
export const getMyFindingsPage = async (
  need: FindingNeed | null = null,
  page: WorkbenchPage = {},
): Promise<MyFindingsResponse> => {
  const params = pageParams(page);
  if (need) params.need = need;
  const response = await api.get(`${p()}/workbench/findings`, { params, signal: page.signal });
  return response.data;
};

/** Hosts the caller has In Review; `in_review_count` is the whole list. */
export const getMyReviewHostsPage = async (page: WorkbenchPage = {}): Promise<MyAttentionResponse> => {
  const response = await api.get(`${p()}/workbench/hosts`, { params: pageParams(page), signal: page.signal });
  return response.data;
};

/** Tests to do that are the caller's or free to claim — one list, each test
 *  under its strongest reason. With `kind`, only that kind; the list's size
 *  is `total_open` (every kind) or `group_counts[kind]`. */
export const getMyTestsPage = async (
  kind: MyTaskReason | null = null,
  page: WorkbenchPage = {},
): Promise<MyTasksResponse> => {
  const params = pageParams(page);
  if (kind) params.kind = kind;
  const response = await api.get(`${p()}/workbench/tests`, { params, signal: page.signal });
  return response.data;
};

/** The caller's finished reviews that are not done. Rejects (503) when they
 *  could not be checked — never an empty list. */
export const getReviewFollowupsPage = async (page: WorkbenchPage = {}): Promise<ReviewFollowupsResponse> => {
  const response = await api.get(`${p()}/workbench/followups`, { params: pageParams(page), signal: page.signal });
  return response.data;
};

/** "Still reviewed": the caller looked at what changed after their review and
 *  it stands — the review date moves to now, the conclusion stays. All or
 *  nothing (409 names the hosts that could not be confirmed). */
export const markStillReviewed = async (hostIds: number[]): Promise<{ host_ids: number[] }> => {
  const response = await api.post(`${p()}/workbench/followups/still-reviewed`, { host_ids: hostIds });
  return response.data;
};

// (The /attention + /attention/sites client functions were removed once their
// last consumers — the Operations AttentionCard and the Subnet-Insights by-site
// rollup — moved to Security Posture, which composes site attention server-side
// via GET /posture. The backend routes remain for that composition.)

export const markWorkbenchSeen = async (
  asOf?: string | null,
): Promise<{ last_viewed_at: string }> => {
  const response = await api.post(`${p()}/workbench/seen`, asOf ? { as_of: asOf } : undefined);
  return response.data;
};

// (getMyActivity went with Operations' "My recent activity" column in
// 5.329.0 — its only reader — and the server's `GET /workbench/my-activity`
// with v2.451.1.  The workbench's `team_review` roster went then too.)

export interface AgentActivityStatusBreakdown {
  success: number;
  client_error: number;
  server_error: number;
  other: number;
}

export interface AgentActivityWorkflowCount {
  workflow: string;
  calls: number;
}

export interface AgentActivityDayBucket {
  day: string;
  calls: number;
  errors: number;
}

export interface AgentActivitySessionRow {
  workflow: string;
  session_id: number;
  calls: number;
  last_activity?: string | null;
}

/** v5.219.0 — whether sessions in the window exit cleanly and say anything on
 *  the way out. The feedback loop depends on both, and neither was measured. */
export interface AgentSessionHygiene {
  sessions_started: number;
  sessions_active: number;
  sessions_ended: number;
  ended_by_agent: number;
  ended_by_operator: number;
  lapsed: number;
  sessions_with_feedback: number;
}

export interface AgentActivitySummary {
  window_days: number;
  total_calls: number;
  distinct_agents: number;
  first_call_at?: string | null;
  last_call_at?: string | null;
  status_breakdown: AgentActivityStatusBreakdown;
  by_workflow: AgentActivityWorkflowCount[];
  daily: AgentActivityDayBucket[];
  busiest_sessions: AgentActivitySessionRow[];
  /** Absent on backends before 2.343.0. */
  session_hygiene?: AgentSessionHygiene | null;
}

export const getAgentActivitySummary = async (
  windowDays = 14,
  signal?: AbortSignal,
): Promise<AgentActivitySummary> => {
  const response = await api.get(`${p()}/agent-activity/summary`, {
    params: { window_days: windowDays },
    signal,
  });
  return response.data;
};

// v2.426.0 — the address terrain (on Posture since 5.330.0): hosts by address block (/24, IPv6 /64),
// counted by how far the team has taken them.  tested / planned / worked /
// untouched are exclusive and add up to `hosts`.
export interface TerrainBlock {
  cidr: string;
  hosts: number;
  tested: number;
  planned: number;
  worked: number;
  untouched: number;
  critical: number;
  critical_untouched: number;
}

export interface AddressTerrainResponse {
  blocks: TerrainBlock[];
  total_hosts: number;
  unplaced_hosts: number;
  truncated: boolean;
}

/** Rejects (503) when it could not be computed — never an empty map. */
export const getAddressTerrain = async (signal?: AbortSignal): Promise<AddressTerrainResponse> => {
  const response = await api.get(`${p()}/workbench/terrain`, { signal });
  return response.data;
};
