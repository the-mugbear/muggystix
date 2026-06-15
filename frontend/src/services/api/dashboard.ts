/**
 * Dashboard + Operations-workbench API client.
 *
 * Dashboard summary stats, the personal-work surface (My Queue / Team
 * Review / My Tasks / Workbench since-last-visit), new-scans-since, and
 * agent-activity analytics.
 *
 * Extracted from the api.ts monolith.  Consumers still import these from
 * ``../services/api`` — the barrel re-exports this module.
 *
 * NOTE: risk-insights (RiskInsightResponse etc.) stays in api.ts for now
 * because it depends on the PortOfInterest* types that live with the
 * scope/host code; it'll move when those domains are extracted.
 */
import { api, p } from './client';
import type { Scan } from './scans';
import type { NoteStatus } from './shared';

// --- Subnet stats (dashboard per-subnet rollup) ---
export interface SubnetStats {
  id: number;
  cidr: string;
  scope_name: string;
  description: string | null;
  host_count: number;
  total_addresses?: number;
  usable_addresses?: number;
  utilization_percentage?: number;
  risk_level?: string;
  network_address?: string;
  is_private?: boolean;
}

export interface VulnerabilityStats {
  total_vulnerabilities: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  hosts_with_vulnerabilities: number;
}

export interface DashboardStats {
  total_scans: number;
  total_hosts: number;
  total_ports: number;
  up_hosts: number;
  open_ports: number;
  total_subnets: number;
  recent_scans: Scan[];
  subnet_stats: SubnetStats[];
  vulnerability_stats?: VulnerabilityStats;
  note_activity?: NoteActivitySummary;
}

export interface NoteActivityEntry {
  note_id: number;
  host_id: number;
  ip_address: string;
  hostname: string | null;
  status: NoteStatus;
  preview: string;
  created_at: string;
  updated_at?: string | null;
}

export interface ReviewProgress {
  total_hosts: number;
  not_reviewed: number;
  watching: number;
  in_review: number;
  reviewed: number;
}

export interface NoteActivitySummary {
  total_notes: number;
  active_host_count: number;
  following_count: number;
  review_progress?: ReviewProgress;
  recent_notes: NoteActivityEntry[];
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

export const getMyAttentionQueue = async (limit = 10): Promise<MyAttentionResponse> => {
  const response = await api.get(`${p()}/dashboard/my-attention`, { params: { limit } });
  return response.data;
};

// --- Team Review — the project-wide review roster, grouped by reviewer ---
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

export const getTeamReview = async (): Promise<TeamReviewResponse> => {
  const response = await api.get(`${p()}/dashboard/team-review`);
  return response.data;
};

// --- My Tasks (test plan entries on hosts I'm reviewing) ---

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

export const getMyTasks = async (limit = 15): Promise<MyTasksResponse> => {
  const response = await api.get(`${p()}/dashboard/my-tasks`, { params: { limit } });
  return response.data;
};

// --- My Notes / My Findings (P0 — My Work resume pass) ---

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

export interface SinceLastVisit {
  last_viewed_at: string | null;
  is_first_visit: boolean;
  new_scan_count: number;
  latest_scan_id: number | null;
  latest_scan_filename: string | null;
  latest_scan_created_at: string | null;
  new_host_count: number;
  new_critical_findings: number;
  new_high_findings: number;
}

export interface WorkbenchResponse {
  my_queue: MyAttentionResponse;
  my_tasks: MyTasksResponse;
  my_notes: MyNotesResponse;
  recent_notes: MyRecentNotesResponse;
  my_findings: MyFindingsResponse;
  team_review: TeamReviewResponse;
  since_last_visit: SinceLastVisit;
}

export const getWorkbench = async (): Promise<WorkbenchResponse> => {
  const response = await api.get(`${p()}/workbench`);
  return response.data;
};

// (The /attention + /attention/sites client functions were removed once their
// last consumers — the Operations AttentionCard and the Subnet-Insights by-site
// rollup — moved to Security Posture, which composes site attention server-side
// via GET /posture. The backend routes remain for that composition.)

export const markWorkbenchSeen = async (): Promise<{ last_viewed_at: string }> => {
  const response = await api.post(`${p()}/workbench/seen`);
  return response.data;
};

// §27 — the caller's recent work history across notes, findings, and reviews.
export type ActivityEventKind = 'note' | 'finding_created' | 'finding_status' | 'host_reviewed';
export interface ActivityEvent {
  kind: ActivityEventKind;
  at: string;
  summary: string;
  host_id: number | null;
  note_id: number | null;
  finding_id: number | null;
  severity: string | null;
}
export interface MyActivityResponse {
  items: ActivityEvent[];
}
export const getMyActivity = async (limit = 20): Promise<MyActivityResponse> => {
  const response = await api.get(`${p()}/workbench/my-activity?limit=${limit}`);
  return response.data;
};

// --- New scans since last dashboard visit ---

export interface NewScansSinceResponse {
  count: number;
  latest_scan_id: number | null;
  latest_scan_filename: string | null;
  latest_scan_created_at: string | null;
}

export const getNewScansSince = async (
  since: string | null,
): Promise<NewScansSinceResponse> => {
  const params = since ? { since } : undefined;
  const response = await api.get(`${p()}/dashboard/new-scans-since`, { params });
  return response.data;
};

// --- Agent-activity analytics (project-level aggregate of agent_api_calls) ---

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
}

export const getAgentActivitySummary = async (
  windowDays = 14,
): Promise<AgentActivitySummary> => {
  const response = await api.get(`${p()}/agent-activity/summary`, {
    params: { window_days: windowDays },
  });
  return response.data;
};
