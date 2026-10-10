/**
 * Findings API client — the unified finding spine (promote-from-note,
 * triage, cross-host). Project-scoped: the project is each function's first
 * argument.
 */
import { api, projectPath } from './client';
import type { Annotation, NoteAttachment } from './hosts';
import type { Proposal } from './proposals';

export type FindingSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type FindingStatus =
  | 'open'
  | 'confirmed'
  | 'false_positive'
  | 'accepted_risk'
  | 'remediated'
  | 'retest';
export type FindingSource = 'note' | 'scanner' | 'execution' | 'manual';

/** The network segment an affected host belongs to: the project's one segment
 *  rule (sites; most-specific subnets when the project defines no site), the
 *  Posture grid's and the Evidence matrix's columns. */
export interface FindingEndpointSegment {
  /** A site id as a string, `unassigned`, `subnet:<id>` or `unmapped`. */
  key: string;
  label: string;
  kind: 'site' | 'subnet' | 'unassigned' | 'unmapped';
  /** Position among the project's segments (the grid's column order). */
  order: number;
}

export interface FindingHostInfo {
  /** The affected-endpoint ROW id — a host may carry one row per named endpoint. */
  id: number;
  host_id: number;
  ip_address: string | null;
  hostname: string | null;
  // v5.194.0 — the named endpoint on this host the finding applies to
  // (inherited from the scanner row); null = host-level.
  name_id?: number | null;
  fqdn?: string | null;
  /** This endpoint's own state: open | remediated | retest | false_positive.
   *  The finding's status is the issue's; this one is the host's (v5.225.0). */
  host_status: FindingHostStatus;
  /** Single-finding responses only; a list row's preview sends null. */
  segment?: FindingEndpointSegment | null;
}

/** `false_positive` (v5.238.0): the issue does not apply to THIS endpoint —
 *  it says nothing about the finding's other hosts. */
export type FindingHostStatus = 'open' | 'remediated' | 'retest' | 'false_positive';

export interface Finding {
  id: number;
  project_id: number;
  title: string;
  severity: FindingSeverity;
  status: FindingStatus;
  source: FindingSource;
  owner_id: number | null;
  owner_name: string | null;
  evidence_annotation_id: number | null;
  vuln_id: number | null;
  host_count: number;
  hosts: FindingHostInfo[];
  /** v5.225.0 — {open, remediated, retest} over the endpoint rows. */
  endpoint_status_counts?: Partial<Record<FindingHostStatus, number>>;
  /** v5.256.0 — who recorded it, and whether the caller may rename or delete
   *  it (its author or a project admin). Triage is not gated by this. */
  created_by_id?: number | null;
  created_by_name?: string | null;
  can_modify?: boolean;
  /** v5.260.0 — what the client report says (Markdown). Single-finding
   *  responses only; the list sends null. Edited under `can_modify`. */
  report_text?: FindingReportText | null;
  /** v5.260.0 — the caller is a project admin: may mark any evidence image
   *  for the report, not only their own uploads. */
  viewer_is_project_admin?: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface FindingReportText {
  description: string | null;
  impact: string | null;
  recommendation: string | null;
  references: string | null;
  steps_to_reproduce: string | null;
  cvss_vector: string | null;
  cvss_score: number | null;
  /** A 3.x / 2.0 vector decides the score; the editor shows it read-only. */
  cvss_score_from_vector: boolean;
}

export type FindingReportTextField =
  'description' | 'impact' | 'recommendation' | 'references' | 'steps_to_reproduce';

export type FindingReportTextUpdate = Partial<
  Record<FindingReportTextField | 'cvss_vector', string | null> & { cvss_score: number | null }
>;

/** A draft of a finding's report text with your LLM provider.  Since v5.316.0
 *  (backend 2.437.0) it is a set of PROPOSALS, one per field — reviewed in each
 *  section of the finding's Report text like an agent's (5.334.0).  Nothing is written until
 *  accepted.  Errors: 400 (no provider / nothing empty), 403 (below analyst),
 *  502 (provider failed or answered unreadably). */
export interface FindingTextDraft {
  proposals: Proposal[];
  /** Backend 2.455.0: sections the model declined because the finding's data
   *  does not support them — {field: what would let it be written}.  No
   *  proposal was made for them. */
  declined?: Record<string, string>;
  provider_id: number;
  provider_type: string;
  model_id: string | null;
}

export const draftFindingText = async (
  projectId: number,
  findingId: number,
  fields?: FindingReportTextField[],
  opts?: { signal?: AbortSignal },
): Promise<FindingTextDraft> => {
  const response = await api.post<FindingTextDraft>(
    `${projectPath(projectId)}/reports/draft/finding-text`,
    { finding_id: findingId, ...(fields ? { fields } : {}) },
    { signal: opts?.signal },
  );
  return response.data;
};

export interface FindingListResponse {
  items: Finding[];
  total: number;
  // Per-severity counts for the rollup header (all filters except severity).
  severity_counts?: Partial<Record<FindingSeverity, number>>;
  /** Who owns the listed findings and how many each — every filter except
   *  the owner, independent of the page.  `owner_id: null` = unowned.  Each
   *  count is the total of the list that owner opens.  (An older server
   *  sends none.) */
  owner_counts?: Array<{ owner_id: number | null; owner_name: string | null; count: number }>;
}

export type FindingSortField = 'severity' | 'status' | 'title' | 'host_count' | 'source' | 'created_at' | 'owner';

/** A real status, or a server-side group: 'active' / 'resolved'. */
export type FindingStatusQuery = FindingStatus | 'active' | 'resolved';

export interface FindingFilters {
  status?: FindingStatusQuery;
  severity?: FindingSeverity;
  owner_id?: number;
  /** Only findings with no owner (overrides owner_id server-side). */
  unowned?: boolean;
  source?: FindingSource;
  host_id?: number;
  /** Case-insensitive substring match on the finding title. */
  search?: string;
  sort?: FindingSortField;
  dir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

/** One finding's comment thread on Collaboration (v5.294.0). */
export interface FindingDiscussion {
  finding_id: number;
  title: string;
  severity: FindingSeverity;
  status: FindingStatus;
  comment_count: number;
  last_activity_at: string | null;
  latest: {
    note_id: number;
    body: string;
    author_name: string | null;
    actor_type: 'user' | 'agent';
    created_at: string | null;
  } | null;
  participants: string[];
}

export interface FindingDiscussionList {
  items: FindingDiscussion[];
  total: number;
}

/** The project's finding discussions, most recently active first. */
export const getFindingDiscussions = async (
  projectId: number,
  params: { search?: string; author_id?: number; limit?: number } = {},
  signal?: AbortSignal,
): Promise<FindingDiscussionList> => {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  const q = qs.toString();
  const response = await api.get<FindingDiscussionList>(
    `${projectPath(projectId)}/findings/comments/activity${q ? `?${q}` : ''}`, { signal },
  );
  return response.data;
};

export const listFindings = async (
  projectId: number,
  filters: FindingFilters = {},
  // Lets the caller abort a superseded request (Findings page: a newer filter
  // set cancels the in-flight one so a slow response can't overwrite it).
  signal?: AbortSignal,
): Promise<FindingListResponse> => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null) params.set(k, String(v));
  });
  const qs = params.toString();
  const response = await api.get<FindingListResponse>(`${projectPath(projectId)}/findings${qs ? `?${qs}` : ''}`, { signal });
  return response.data;
};

export const getFinding = async (projectId: number, findingId: number, signal?: AbortSignal): Promise<Finding> => {
  const response = await api.get<Finding>(`${projectPath(projectId)}/findings/${findingId}`, { signal });
  return response.data;
};

export interface FindingCreatePayload {
  title: string;
  severity: FindingSeverity;
  status?: FindingStatus;
  owner_id?: number | null;
  host_ids?: number[];
}
/** v5.346.0 — write a finding directly (the host page's "Add finding"). */
export const createFinding = async (projectId: number, payload: FindingCreatePayload): Promise<Finding> => {
  const response = await api.post<Finding>(`${projectPath(projectId)}/findings`, payload);
  return response.data;
};

export const updateFinding = async (
  projectId: number,
  findingId: number,
  payload: { title?: string; severity?: FindingSeverity; owner_id?: number | null } & FindingReportTextUpdate,
): Promise<Finding> => {
  const response = await api.patch<Finding>(`${projectPath(projectId)}/findings/${findingId}`, payload);
  return response.data;
};

/** v5.256.0 — delete a finding recorded in error (its author or a project
 *  admin). Its comments and history go with it; the evidence it pointed at
 *  (source note, scanner rows) stays. */
export const deleteFinding = async (projectId: number, findingId: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/findings/${findingId}`);
};

export const setFindingStatus = async (
  projectId: number,
  findingId: number,
  status: FindingStatus,
  summary?: string,
): Promise<Finding> => {
  const response = await api.post<Finding>(`${projectPath(projectId)}/findings/${findingId}/status`, { status, summary });
  return response.data;
};

/** v5.225.0 — set ONE endpoint row's state (open / remediated / retest)
 *  without touching the finding's own status. */
export const setFindingEndpointStatus = async (
  projectId: number,
  findingId: number,
  findingHostId: number,
  hostStatus: FindingHostStatus,
): Promise<Finding> => {
  const response = await api.patch<Finding>(
    `${projectPath(projectId)}/findings/${findingId}/endpoints/${findingHostId}`,
    { host_status: hostStatus },
  );
  return response.data;
};

/** Review 2026-10-01 B13 — set SEVERAL endpoint rows' state in one call
 *  (`PATCH /findings/{id}/endpoints`): at most 500 ids, all-or-nothing, the
 *  same rules as the single-endpoint route.  `summary` is recorded with each
 *  endpoint's history line.  Returns the finding with every endpoint. */
export const setFindingEndpointsStatus = async (
  projectId: number,
  findingId: number,
  body: { finding_host_ids: number[]; host_status: FindingHostStatus; summary?: string },
): Promise<Finding> => {
  const response = await api.patch<Finding>(`${projectPath(projectId)}/findings/${findingId}/endpoints`, body);
  return response.data;
};

export interface FindingEndpointRef {
  host_id: number;
  name_id?: number | null;
  host_status?: string | null;
}

export const addFindingHosts = async (
  projectId: number,
  findingId: number,
  hostIds: number[],
  endpoints: FindingEndpointRef[] = [],
): Promise<Finding> => {
  const response = await api.post<Finding>(`${projectPath(projectId)}/findings/${findingId}/hosts`, {
    host_ids: hostIds,
    endpoints,
  });
  return response.data;
};

/** v5.195.0 — detach exactly one affected endpoint (a FindingHost row). */
export const removeFindingEndpoint = async (projectId: number, findingId: number, findingHostId: number): Promise<Finding> => {
  const response = await api.delete<Finding>(`${projectPath(projectId)}/findings/${findingId}/endpoints/${findingHostId}`);
  return response.data;
};

export interface FindingStatusHistoryEntry {
  id: number;
  from_status: string | null;
  to_status: string;
  changed_by_id: number | null;
  changed_by_name: string | null;
  summary: string | null;
  created_at: string;
}

export const getFindingHistory = async (
  projectId: number,
  findingId: number,
  signal?: AbortSignal,
): Promise<FindingStatusHistoryEntry[]> => {
  const response = await api.get<FindingStatusHistoryEntry[]>(`${projectPath(projectId)}/findings/${findingId}/history`, { signal });
  return response.data;
};

// --- Finding comment / evidence thread ---
// A finding hosts its own annotation thread (the notes→findings→reports flow):
// discussion + repro/rationale + screenshots, refined here before reports.

export const getFindingNotes = async (projectId: number, findingId: number, signal?: AbortSignal): Promise<Annotation[]> => {
  const response = await api.get<Annotation[]>(`${projectPath(projectId)}/findings/${findingId}/notes`, { signal });
  return response.data;
};

export const createFindingNote = async (
  projectId: number,
  findingId: number,
  body: string,
  parentId?: number | null,
): Promise<Annotation> => {
  const response = await api.post<Annotation>(`${projectPath(projectId)}/findings/${findingId}/notes`, {
    body,
    parent_id: parentId ?? null,
  });
  return response.data;
};

/** v5.256.0 — the comment's author only. */
export const updateFindingNote = async (
  projectId: number,
  findingId: number,
  noteId: number,
  body: string,
): Promise<Annotation> => {
  const response = await api.patch<Annotation>(`${projectPath(projectId)}/findings/${findingId}/notes/${noteId}`, { body });
  return response.data;
};

/** v5.256.0 — the comment's author only; 409 while it has replies. */
export const deleteFindingNote = async (projectId: number, findingId: number, noteId: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/findings/${findingId}/notes/${noteId}`);
};

export const uploadFindingNoteAttachment = async (
  projectId: number,
  findingId: number,
  noteId: number,
  file: File,
): Promise<NoteAttachment> => {
  const form = new FormData();
  form.append('file', file);
  const response = await api.post(
    `${projectPath(projectId)}/findings/${findingId}/notes/${noteId}/attachments`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return response.data;
};

/** One image attached to a finding (its comments or its source-note thread),
 *  as the client report sees it. */
export interface FindingImage {
  id: number;
  note_id: number;
  filename: string;
  /** The figure caption; null → the report prints the file name. */
  caption: string | null;
  content_type: string;
  size_bytes: number;
  /** Ticked "In report". */
  in_report: boolean;
  /** PNG / JPEG / GIF — a format the report can print. */
  printable: boolean;
  /** The report-text fields whose Markdown places it (`![…](evidence:<id>)`). */
  placed_in: string[];
  uploaded_by_id: number | null;
  by_agent: boolean;
  created_at: string | null;
  /** This viewer may tick, caption or delete it. */
  can_edit: boolean;
}

export interface FindingImageList {
  items: FindingImage[];
  caption_max: number;
}

/** The finding's images with where each is placed in its report text. */
export const getFindingImages = async (projectId: number, findingId: number, signal?: AbortSignal): Promise<FindingImageList> => {
  const response = await api.get<FindingImageList>(`${projectPath(projectId)}/findings/${findingId}/images`, { signal });
  return response.data;
};

export interface PromoteVulnerabilityPreview {
  plugin_id: string | null;
  /** Scanner-agnostic issue identity the fan-out keys on. */
  issue_key: string | null;
  affected_host_count: number;
  affected_host_sample: string[];
  /** Hosts not already attached — what this action would actually change.
   *  Equals affected_host_count for a fresh promote. */
  new_host_count: number;
  already_promoted: boolean;
  finding_id: number | null;
  finding_status: string | null;
  /** v5.238.0 — the inspected host, for the "this host only" choice, and its
   *  endpoint state on the existing finding (null = not on it / no finding). */
  host_ip?: string | null;
  host_endpoint_status?: string | null;
}

// Blast radius of promoting a vuln (read-only) — how many project hosts carry
// the same ISSUE and would be attached to the one finding (§11). Keyed on the
// issue, not the plugin, so it matches what promote actually does: a
// plugin-keyed preview under-reported whenever two scanners saw one problem.
export const previewPromoteVulnerability = async (
  projectId: number,
  vulnId: number,
  signal?: AbortSignal,
): Promise<PromoteVulnerabilityPreview> => {
  const response = await api.get<PromoteVulnerabilityPreview>(
    `${projectPath(projectId)}/vulnerabilities/${vulnId}/promote-preview`,
    { signal },
  );
  return response.data;
};

// v5.272.0 — scanner observations grouped by ISSUE across the project's hosts,
// and their bulk promotion (the Findings page's "Scanner observations" view).
export interface ObservationIssue {
  issue_key: string;
  title: string;
  severity: string;
  cve_id: string | null;
  sources: string[];
  host_count: number;
  /** Hosts a finding already covers for this issue. */
  judged_host_count: number;
  finding_id: number | null;
  finding_status: string | null;
  /** v5.298.0 — misconfiguration (a catalog check, whichever tool reported
   *  it) / vulnerability / informational. */
  kind?: WeaknessKind;
  /** A scanner reports an exploit for it on at least one host — a lead for
   *  what to test first, not a statement that it was exploited. */
  exploitable?: boolean;
}

export type WeaknessKind = 'misconfiguration' | 'vulnerability' | 'informational';

export interface ObservationIssueHost {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  severity: string;
  ports: number[];
  judged: boolean;
  endpoint_status: string | null;
  /** Tests naming THIS issue on the host: still to do, and results recorded. */
  tests_to_do?: number;
  tests_recorded?: number;
}

export interface ObservationIssueFilters {
  search?: string;
  severity?: string;
  includeJudged?: boolean;
  minHosts?: number;
  skip?: number;
  limit?: number;
  kind?: WeaknessKind;
  /** Only issues a scanner reports an exploit for. */
  exploitable?: boolean;
  /** `hosts` = most widespread first; omitted = most severe first. */
  sort?: 'severity' | 'hosts';
}

export const getObservationIssues = async (
  projectId: number,
  filters: ObservationIssueFilters = {},
  signal?: AbortSignal,
): Promise<{ items: ObservationIssue[]; total: number }> => {
  const response = await api.get(`${projectPath(projectId)}/scanner-observations`, {
    params: {
      search: filters.search || undefined,
      severity: filters.severity || undefined,
      include_judged: filters.includeJudged || undefined,
      min_hosts: filters.minHosts && filters.minHosts > 1 ? filters.minHosts : undefined,
      kind: filters.kind || undefined,
      exploitable: filters.exploitable || undefined,
      sort: filters.sort === 'hosts' ? 'hosts' : undefined,
      skip: filters.skip || undefined,
      limit: filters.limit ?? 50,
    },
    signal,
  });
  return response.data;
};

/** The first `limit` hosts by address (omitted = all). */
export const getObservationIssueHosts = async (
  projectId: number, issueKey: string, limit?: number, signal?: AbortSignal,
): Promise<ObservationIssueHost[]> => {
  const response = await api.get(`${projectPath(projectId)}/scanner-observations/hosts`, { params: { issue_key: issueKey, limit }, signal });
  return response.data;
};

/** Each issue becomes (or joins) its finding; `host_ids` omitted = every host carrying it. */
export const promoteObservationIssues = async (
  projectId: number,
  items: { issue_key: string; host_ids?: number[] }[],
): Promise<{ results: { issue_key: string; finding_id: number; created: boolean; host_count: number }[] }> => {
  const response = await api.post(`${projectPath(projectId)}/scanner-observations/promote`, { items });
  return response.data;
};

// Promote (or dismiss) a scanner vulnerability as a finding. Severity defaults
// to the vuln's own; a terminal status (false_positive/accepted_risk)
// dismisses it. Idempotent per vuln.
export const promoteVulnerability = async (
  projectId: number,
  vulnId: number,
  payload: {
    severity?: string;
    status?: FindingStatus;
    owner_id?: number;
    summary?: string;
    /** How far a false-positive dismissal reaches: `host` (the server's
     *  default) = this host's endpoint only; `issue` = every host carrying
     *  it. Promotion and accepted risk are always about the issue. */
    scope?: 'host' | 'issue';
  } = {},
): Promise<Finding> => {
  const response = await api.post<Finding>(
    `${projectPath(projectId)}/vulnerabilities/${vulnId}/promote`,
    { vuln_id: vulnId, ...payload },
  );
  return response.data;
};

// --------------------------------------------------------------------------
// Bulk operations (v5.135.0)
// --------------------------------------------------------------------------
// The page previously looped `setFindingStatus` per id from the browser —
// unbounded, partially failable, and with no single audit moment. These route
// the whole selection through one request that validates project scope,
// enforces the terminal-justification rule across the batch, and emits one
// assignment notification instead of N.

export interface BulkFindingResult {
  affected: number;
  requested: number;
  /** Ids the server refused (not in this project / already gone). */
  skipped_ids: number[];
}

export const bulkSetFindingStatus = async (
  projectId: number,
  findingIds: number[],
  status: FindingStatus,
  summary?: string,
): Promise<BulkFindingResult> => {
  const res = await api.post<BulkFindingResult>(`${projectPath(projectId)}/findings/bulk/status`, {
    finding_ids: findingIds,
    status,
    summary,
  });
  return res.data;
};

/** `assigneeUserId: null` unassigns — the single-finding PATCH can't express
 *  that, since it skips owner_id when null. */
export const bulkAssignFindings = async (
  projectId: number,
  findingIds: number[],
  assigneeUserId: number | null,
): Promise<BulkFindingResult> => {
  const res = await api.post<BulkFindingResult>(`${projectPath(projectId)}/findings/bulk/assign`, {
    finding_ids: findingIds,
    assignee_user_id: assigneeUserId,
  });
  return res.data;
};
