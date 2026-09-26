/**
 * Dashboard + Operations-workbench API client.
 *
 * Dashboard summary stats, the Operations workbench (`GET /workbench`: my
 * queue, tasks, notes, findings, team review, since-last-visit, the
 * investigation queue, follow-ups, blockers) and agent-activity analytics.
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

export const getDashboardStats = async (): Promise<DashboardStats> => {
  const response = await api.get(`${p()}/dashboard/stats`);
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
  watching_count: number;
}
export interface TeamReviewHostRow {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  follow_updated_at: string | null;
}

export interface TeamReviewerGroup {
  user_id: number;
  username: string;
  full_name: string | null;
  host_count: number;
  hosts: TeamReviewHostRow[];
}

export interface TeamReviewResponse {
  reviewers: TeamReviewerGroup[];
  total_hosts_in_review: number;
}
/** Why a task is in your queue. Overlapping — a task can carry several. */
export type MyTaskReason = 'assigned' | 'in_review' | 'triage';

export interface MyTaskItem {
  entry_id: number;
  plan_id: number;
  plan_title: string;
  plan_status: string;
  host_id: number;
  host_ip: string;
  host_hostname: string | null;
  priority: string;
  test_phase: string;
  entry_status: string;
  proposed_test_count: number;
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
}
export interface MyNoteItem {
  note_id: number;
  host_id: number | null;
  host_ip: string | null;
  host_hostname: string | null;
  body_preview: string;
  note_type: string | null; // observation|finding|question|decision|action|handoff
  status: string;
  due_at: string | null;
  is_overdue: boolean;
  updated_at: string | null;
}

export interface MyNotesResponse {
  items: MyNoteItem[];
  total_open: number;
  handoff_count: number;
  overdue_count: number;
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
}

export interface MyFindingsResponse {
  items: MyFindingItem[];
  total_open: number;
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

export interface InterruptedExecution {
  session_id: number;
  test_plan_id: number;
  plan_title?: string | null;
  /** `session_ended`: the run is still "active" but its agent session is not. */
  reason: 'paused' | 'session_ended';
  started_at?: string | null;
}

export interface OperationsBlockers {
  failed_import_count: number;
  partial_import_count: number;
  imports: BlockedImport[];
  interrupted_execution_count: number;
  executions: InterruptedExecution[];
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
// hosts nobody has touched (no review, assignment, note, plan entry or
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
}

export interface WorkbenchResponse {
  my_queue: MyAttentionResponse;
  my_tasks: MyTasksResponse;
  my_notes: MyNotesResponse;
  recent_notes: MyRecentNotesResponse;
  my_findings: MyFindingsResponse;
  team_review: TeamReviewResponse;
  since_last_visit: SinceLastVisit;
  /** `null` when requested with `includeInvestigate: false` (v2.424.1). */
  investigate?: InvestigationQueueResponse | null;
  /** The queue could not be computed: `investigate` is an empty placeholder
   *  and must read as "unavailable", never as "no work". */
  investigate_unavailable?: boolean;
  /** Reviewed hosts that are not done (v2.359.0). */
  followups?: ReviewFollowupsResponse;
  followups_unavailable?: boolean;
  blockers?: OperationsBlockers;
  /** Could not be computed — must read as "unavailable", never "nothing blocked". */
  blockers_unavailable?: boolean;
}

// v2.359.0 — a reviewed host left every queue for good. Two kinds are not
// done: a review concluded "needs more evidence", and a host that changed
// AFTER it was reviewed. Re-opening the review is the one action.
export interface ReviewFollowupRow {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  reviewer_id: number;
  reviewer: string | null;
  mine: boolean;
  reviewed_at: string | null;
  review_conclusion: string | null;
  review_summary: string | null;
  reasons: Array<{ kind: string; text: string }>;
}

export interface ReviewFollowupsResponse {
  items: ReviewFollowupRow[];
  total: number;
  mine_total: number;
}

export const getWorkbench = async (
  opts: { includeInvestigate?: boolean } = {},
): Promise<WorkbenchResponse> => {
  // v2.424.1 — Operations leaves the "Worth a look" queue out and loads it
  // with getInvestigationQueue(): on a large project it is most of the time,
  // and the personal sections should not wait for it.
  const params = opts.includeInvestigate === false ? { include_investigate: false } : undefined;
  const response = await api.get(`${p()}/workbench`, { params });
  return response.data;
};

/** The "Worth a look" queue alone. Rejects (503) when it could not be
 *  computed — callers show "unavailable", never an empty queue. */
export const getInvestigationQueue = async (): Promise<InvestigationQueueResponse> => {
  const response = await api.get(`${p()}/workbench/investigate`);
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

// §27 — the caller's recent work history across notes, findings, and reviews.
export type ActivityEventKind =
  | 'note' | 'finding_created' | 'finding_status' | 'host_reviewed' | 'session';
export interface ActivityEvent {
  kind: ActivityEventKind;
  at: string;
  summary: string;
  host_id: number | null;
  note_id: number | null;
  finding_id: number | null;
  severity: string | null;
  link: string | null;
}
export interface MyActivityResponse {
  items: ActivityEvent[];
}
export const getMyActivity = async (
  params?: { limit?: number; kinds?: string; days?: number; search?: string },
): Promise<MyActivityResponse> => {
  const sp = new URLSearchParams({ limit: String(params?.limit ?? 20) });
  if (params?.kinds) sp.set('kinds', params.kinds);
  if (params?.days) sp.set('days', String(params.days));
  if (params?.search) sp.set('search', params.search);
  const response = await api.get(`${p()}/workbench/my-activity?${sp.toString()}`);
  return response.data;
};

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
): Promise<AgentActivitySummary> => {
  const response = await api.get(`${p()}/agent-activity/summary`, {
    params: { window_days: windowDays },
  });
  return response.data;
};
