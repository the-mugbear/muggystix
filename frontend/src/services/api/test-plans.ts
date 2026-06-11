/**
 * Test plans, entries, execution sessions, execution results,
 * activity log, agent attribution, bundle export/import, and
 * the recon-start API.  All the user-facing agent surface that
 * lives under the TestPlans + TestPlanDetail pages.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api, p } from './client';


// ---------------------------------------------------------------------------
// Test Plans
// ---------------------------------------------------------------------------

export interface ProposedTestObject {
  tool: string;
  description: string;
  command?: string;
  expected_result?: string;
  references?: string[];
}

/** A proposed test can be a plain string (legacy) or a structured object. */
export type ProposedTestItem = string | ProposedTestObject;

export interface TestPlanEntryResponse {
  id: number;
  host_id: number;
  host_ip?: string;
  host_hostname?: string;
  priority: string;
  test_phase: string;
  proposed_tests: ProposedTestItem[];
  rationale: string;
  status: string;
  findings?: string;
  results_data?: Record<string, unknown>;
  notes?: string;
  assigned_to_id?: number;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  updated_at: string;
}

export interface TestPlanSummary {
  id: number;
  project_id: number;
  version: number;
  title: string;
  description?: string;
  status: string;
  agent_name?: string;
  created_by_username?: string;
  entry_count: number;
  completion_pct: number;
  approved_by_id?: number;
  approved_at?: string;
  rejected_by_id?: number;
  rejected_at?: string;
  rejection_reason?: string;
  // Generation provenance (v2.19.0).  Stamped by the agent during the
  // plan-generation PATCH step; null on plans created before the feature
  // or where the agent skipped the PATCH.
  generated_by_model?: string;
  generated_by_tool?: string;
  prompt_version?: string;
  // Source provenance (v3 alpha.3).  Tells the UI what the plan was
  // scoped against — a recon run, a manual host set, a filter
  // expression, or an earlier plan.  ``source_kind='unspecified'`` for
  // pre-alpha.3 plans; UI renders "(provenance not recorded)".  Only
  // one of the payload columns is populated, discriminated by kind.
  source_kind?: string;
  source_recon_session_id?: number | null;
  source_host_ids?: number[] | null;
  source_plan_id?: number | null;
  created_at: string;
  updated_at: string;
  completed_at?: string;
  /** Timestamp of the most recent plan-generation agent call against
   *  this plan (audit log filtered to `execution_session_id IS NULL`),
   *  or null if the agent never called in.  Mirrors the same field on
   *  ExecutionSessionSummary and ReconSessionRow. */
  last_activity_at?: string | null;
  /** Server-side "looks interrupted" judgment for plan generation —
   *  true only when status is `draft` AND silent past the 15-minute
   *  threshold.  Plans in any post-draft status never report stale. */
  is_stale?: boolean;
}

/** Snapshot of the selection filters the user applied when generating the plan.
 *  Mirrors the backend FilterCriteria model in test_plans.py. */
export interface PlanFilterCriteria {
  subnets?: string;
  ports?: string;
  services?: string;
  /** Minimum severity tier — preferred over the has_*_vulns pair, which
   *  was kept for backward-compat with plans created before v2.21.0.
   *  "high" matches any host with a high OR critical vulnerability. */
  min_severity?: 'critical' | 'high' | 'medium' | 'low';
  /** @deprecated Use min_severity. */
  has_critical_vulns?: boolean;
  /** @deprecated Use min_severity. */
  has_high_vulns?: boolean;
  search?: string;
}

/** Per-plan agent API-key status surfaced on TestPlanDetail.
 *  Drives the TTL chip and the "Regenerate key" button on the plan detail page. */
export interface ApiKeyStatus {
  has_key: boolean;
  is_active: boolean;
  expires_at?: string;
  /** Seconds until expiry; negative once expired.  Computed server-side. */
  expires_in_seconds?: number;
  key_prefix?: string;
}

/** ExecutionSession metadata surfaced on TestPlanDetail + the session
 *  picker (v2.28.0).  A plan can be executed multiple times so the
 *  attribution fields (started_by_username, agent_name, generated_by_model)
 *  are what lets the UI distinguish runs. */
/** Operator-environment probe captured at execution-session start — parity
 *  with recon's ReconEnvironmentSnapshot. */
export interface ExecutionEnvironmentSnapshot {
  probed_at?: string | null;
  probed_from_ip?: string | null;
  os_family?: string | null;
  os_release?: string | null;
  shell?: string | null;
  arch?: string | null;
  python?: string | null;
  notes?: string | null;
  tools_status?: Array<Record<string, unknown>>;
  raw?: Record<string, unknown> | null;
}

export interface ExecutionSessionSummary {
  id: number;
  status: string;
  mode?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  started_by_username?: string | null;
  agent_name?: string | null;
  generated_by_model?: string | null;
  generated_by_tool?: string | null;
  prompt_version?: string | null;
  environment_os_family?: string | null;
  environment_shell?: string | null;
  environment_probed_at?: string | null;
  /** Full operator-environment probe (tools on PATH, python real-vs-stub,
   *  arch, notes) — parity with recon. Null when no probe arrived. */
  environment?: ExecutionEnvironmentSnapshot | null;
  /** Timestamp of the most recent agent API call against this session
   *  (from the agent_api_calls audit log), or null if the agent never
   *  called in.  An `active` session with no recent activity is likely
   *  interrupted — drives the Resume affordance. */
  last_activity_at?: string | null;
  /** Server-side "looks interrupted" judgment.  Authoritative because
   *  it's computed against the server clock, not the browser's — a
   *  client subtracting `Date.now() - started_at` could drift if the
   *  operator's clock disagreed with the server's. */
  is_stale?: boolean;
}

export interface ExecutionSessionListResponse {
  plan_id: number;
  sessions: ExecutionSessionSummary[];
  total: number;
}

export const listExecutionSessions = async (
  planId: number,
): Promise<ExecutionSessionListResponse> => {
  const response = await api.get<ExecutionSessionListResponse>(
    `${p()}/test-plans/${planId}/execution-sessions`,
  );
  return response.data;
};

export interface TestPlanDetail extends TestPlanSummary {
  entries: TestPlanEntryResponse[];
  /** v2.85.0 — total entry count regardless of the requested page.
   *  Always populated so the frontend can drive a "load more" affordance
   *  when entries_limit is set. */
  entries_total: number;
  /** v2.85.0 — echoed slice metadata.  Both null when the caller did not
   *  pass entries_limit (legacy full-list response). */
  entries_skip?: number | null;
  entries_limit?: number | null;
  new_hosts_since_creation: number;
  /** Filters the user applied at plan-generation time.  Null on manual
   *  plans and plans created before the column existed. */
  filter_criteria?: PlanFilterCriteria;
  /** Per-plan agent API-key status.  ``has_key === false`` for manual plans. */
  api_key: ApiKeyStatus;
  /** Snapshot of the latest execution session — null when the plan has
   *  never been ``/execute``-d.  v2.28.0. */
  latest_execution_session?: ExecutionSessionSummary | null;
  /** Total execution sessions recorded for this plan.  Drives the
   *  multi-execution session picker UI when > 1.  v2.28.0. */
  execution_session_count?: number;
}

export interface TestPlanProgress {
  plan_id: number;
  total_entries: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  by_phase: Record<string, number>;
  completion_pct: number;
  hosts_tested: number;
  hosts_remaining: number;
}

export interface TestPlanHistoryItem {
  id: number;
  entry_id?: number;
  actor_type: string;
  actor_id: number;
  action: string;
  field_changed?: string;
  old_value?: string;
  new_value?: string;
  timestamp: string;
}

/**
 * List test plans for the current project.
 *
 * v2.43.0 — added optional `search` + `limit` so type-ahead surfaces
 * (e.g. CommandPalette) can push the filter to the server instead of
 * fetching every plan and client-side matching.  Both params map
 * straight through to the backend `list_test_plans` endpoint.
 */
export const getTestPlans = async (
  options?: { status?: string; search?: string; limit?: number; signal?: AbortSignal },
): Promise<TestPlanSummary[]> => {
  const { status, search, limit, signal } = options ?? {};
  const params: Record<string, string | number> = {};
  if (status) params.status = status;
  if (search) params.search = search;
  if (limit) params.limit = limit;
  const response = await api.get(`${p()}/test-plans/`, { params, signal });
  return response.data;
};

/**
 * Fetch a test plan with entries.
 *
 * v2.85.0 — optional server-side entries pagination.  Passing
 * `entriesLimit` (e.g. 50) caps the returned entries array and
 * populates `entries_total` / `entries_skip` / `entries_limit` so the
 * caller can implement load-more.  When omitted, the response includes
 * every entry (the pre-v2.85.0 default) so older callers don't break.
 */
export const getTestPlan = async (
  planId: number,
  options?: { entriesSkip?: number; entriesLimit?: number },
): Promise<TestPlanDetail> => {
  const params: Record<string, number> = {};
  if (options?.entriesSkip !== undefined) params.entries_skip = options.entriesSkip;
  if (options?.entriesLimit !== undefined) params.entries_limit = options.entriesLimit;
  const response = await api.get(`${p()}/test-plans/${planId}`, { params });
  return response.data;
};

export const approveTestPlan = async (planId: number): Promise<TestPlanSummary> => {
  const response = await api.post(`${p()}/test-plans/${planId}/approve`);
  return response.data;
};

export const rejectTestPlan = async (
  planId: number,
  reason?: string,
): Promise<TestPlanSummary> => {
  const response = await api.post(`${p()}/test-plans/${planId}/reject`, { reason });
  return response.data;
};

// Abandon (archive) a non-terminal plan — the non-destructive terminal
// action for approved/in-progress plans (v2.76.0).
export const archiveTestPlan = async (
  planId: number,
  reason?: string,
): Promise<TestPlanSummary> => {
  const response = await api.post(`${p()}/test-plans/${planId}/archive`, { reason });
  return response.data;
};

export const updateTestPlanEntry = async (
  planId: number,
  entryId: number,
  data: Partial<TestPlanEntryResponse> & { expected_updated_at?: string },
): Promise<TestPlanEntryResponse> => {
  const response = await api.patch(`${p()}/test-plans/${planId}/entries/${entryId}`, data);
  return response.data;
};

export const getTestPlanProgress = async (planId: number): Promise<TestPlanProgress> => {
  const response = await api.get(`${p()}/test-plans/${planId}/progress`);
  return response.data;
};

export const updateTestPlanMetadata = async (
  planId: number,
  body: { title?: string; description?: string },
): Promise<TestPlanSummary> => {
  const response = await api.patch<TestPlanSummary>(`${p()}/test-plans/${planId}`, body);
  return response.data;
};

// --- Per-entry execution results + sanity checks (v2.28.0) ---
//
// The agent records per-test results into test_execution_results and
// per-host sanity checks into host_sanity_checks as it works through a
// plan.  This endpoint serves them back so TestPlanDetail can render
// a per-entry "Test results" panel without forcing the user to click
// Generate Report.

export interface TestExecutionResultRow {
  id: number;
  test_index: number;
  status: string;
  command_run?: string | null;
  raw_output?: string | null;
  findings_summary?: string | null;
  severity?: string | null;
  is_finding: boolean;
  executed_at?: string | null;
  created_at?: string | null;
}

export interface HostSanityCheckRow {
  id: number;
  method: string;
  target_ip?: string | null;
  port_checked?: number | null;
  expected_value?: string | null;
  actual_value?: string | null;
  source_ip?: string | null;
  dns_result?: string | null;
  passed: boolean;
  details?: string | null;
  checked_at?: string | null;
}

export interface EntryExecutionResultsResponse {
  entry_id: number;
  execution_session_id?: number | null;
  execution_session_status?: string | null;
  tests: TestExecutionResultRow[];
  sanity_checks: HostSanityCheckRow[];
}

export const getEntryExecutionResults = async (
  planId: number,
  entryId: number,
  sessionId?: number,
): Promise<EntryExecutionResultsResponse> => {
  const response = await api.get<EntryExecutionResultsResponse>(
    `${p()}/test-plans/${planId}/entries/${entryId}/execution-results`,
    { params: sessionId != null ? { session_id: sessionId } : undefined },
  );
  return response.data;
};


// --- All-entries-for-one-session bundle (v3 alpha.2) ---
//
// Powers the cross-execution comparison page so a 50-entry plan can
// load both sessions' per-entry results in 2 requests instead of 100.

export interface EntryResultsBundle {
  entry_id: number;
  host_id: number;
  host_ip?: string | null;
  host_hostname?: string | null;
  entry_status: string;
  tests: TestExecutionResultRow[];
  sanity_checks: HostSanityCheckRow[];
}

export interface AllEntryResultsResponse {
  plan_id: number;
  execution_session_id: number;
  execution_session_status: string;
  started_at?: string | null;
  completed_at?: string | null;
  started_by_username?: string | null;
  agent_name?: string | null;
  generated_by_model?: string | null;
  generated_by_tool?: string | null;
  prompt_version?: string | null;
  entries: EntryResultsBundle[];
  // v2.86.6 — pagination metadata.  ``entries_total`` is the full count
  // regardless of the slice the caller got; ``entries_skip`` /
  // ``entries_limit`` are non-null only when the caller passed
  // entries_limit (back-compat default returns every entry).
  entries_total?: number;
  entries_skip?: number | null;
  entries_limit?: number | null;
}

export interface AllEntryResultsQuery {
  entriesSkip?: number;
  entriesLimit?: number;
}

export const getAllEntryResults = async (
  planId: number,
  sessionId: number,
  query: AllEntryResultsQuery = {},
): Promise<AllEntryResultsResponse> => {
  const params = new URLSearchParams();
  if (query.entriesSkip !== undefined) params.set('entries_skip', String(query.entriesSkip));
  if (query.entriesLimit !== undefined) params.set('entries_limit', String(query.entriesLimit));
  const qs = params.toString();
  const response = await api.get<AllEntryResultsResponse>(
    `${p()}/test-plans/${planId}/execution-sessions/${sessionId}/all-entry-results${qs ? `?${qs}` : ''}`,
  );
  return response.data;
};


// --- Agent API activity log (v2.24.0) ---
//
// One row per inbound /agent/* request the agent made while authenticated
// to this plan or its execution session.  Drives the "what did my agent
// actually do?" review panel on TestPlanDetail.

/** One captured agent → BlueStick request. */
export interface AgentApiCallRow {
  id: number;
  created_at: string;
  agent_id: number;
  /** Who engaged this agent — joined from Agent.owner; null if deleted. */
  agent_name?: string | null;
  owner_id?: number | null;
  owner_username?: string | null;
  api_key_prefix?: string | null;
  source_ip?: string | null;
  method: string;
  path: string;
  path_template?: string | null;
  path_params?: Record<string, unknown> | null;
  query_params?: Record<string, unknown> | null;
  request_body_summary?: Record<string, unknown> | null;
  status_code: number;
  response_bytes?: number | null;
  duration_ms: number;
  test_plan_id?: number | null;
  execution_session_id?: number | null;
  recon_session_id?: number | null;
  scope_id?: number | null;
  referenced_host_ids?: number[] | null;
  referenced_entry_ids?: number[] | null;
  referenced_target_ips?: string[] | null;
}

export interface AgentApiCallListResponse {
  total: number;
  items: AgentApiCallRow[];
}

export interface AgentActivityFilters {
  method?: string;
  status_min?: number;
  status_max?: number;
  host_id?: number;
  target_ip?: string;
  /** Only calls made by agents the current user owns. */
  mine?: boolean;
  limit?: number;
  offset?: number;
}

export const getPlanApiActivity = async (
  planId: number,
  filters: AgentActivityFilters = {},
): Promise<AgentApiCallListResponse> => {
  const response = await api.get<AgentApiCallListResponse>(
    `${p()}/test-plans/${planId}/api-activity`,
    { params: filters },
  );
  return response.data;
};

export const getReconSessionApiActivity = async (
  reconSessionId: number,
  filters: AgentActivityFilters = {},
): Promise<AgentApiCallListResponse> => {
  const response = await api.get<AgentApiCallListResponse>(
    `${p()}/recon-sessions/${reconSessionId}/api-activity`,
    { params: filters },
  );
  return response.data;
};

export const deleteTestPlan = async (planId: number): Promise<void> => {
  await api.delete(`${p()}/test-plans/${planId}`);
};

export interface RotateKeyResponse {
  plan_id: number;
  /** Plaintext key — shown ONCE.  Paste into your agent session. */
  api_key: string;
  expires_at: string;
}

export const rotateTestPlanKey = async (planId: number): Promise<RotateKeyResponse> => {
  const response = await api.post<RotateKeyResponse>(
    `${p()}/test-plans/${planId}/rotate-key`,
  );
  return response.data;
};

export interface GeneratePlanRequest {
  title: string;
  description?: string;
  filter_criteria?: PlanFilterCriteria;
  // Provenance — records WHERE the plan's candidate hosts came from so the
  // audit lineage survives.  When the plan is generated from a specific
  // recon run, send source_kind='recon_session' + source_recon_session_id.
  // Omitting both lets the backend infer 'filter_set' (non-null
  // filter_criteria) or 'unspecified'.  The source_* payloads are mutually
  // exclusive; only recon_session is wired from the UI today.
  source_kind?: 'recon_session' | 'manual_hosts' | 'filter_set' | 'inherited' | 'unspecified';
  source_recon_session_id?: number;
}

export interface GeneratePlanResponse {
  plan_id: number;
  plan_title: string;
  plan_status: string;
  agent_id: number;
  api_key: string;
  instructions: string;
}

export const generateTestPlan = async (
  data: GeneratePlanRequest,
): Promise<GeneratePlanResponse> => {
  const response = await api.post(`${p()}/test-plans/generate`, data);
  return response.data;
};

/** Resume an interrupted plan-generation session.  Re-mints a fresh
 *  agent key (revoking any prior active key for the plan) and rebuilds
 *  the plan-generation instructions block.  The plan must be in
 *  `draft` status; any other status returns 409.  Existing entries
 *  are preserved — the resumed agent continues via /context with the
 *  `not_in_plan_id` cursor. */
export const resumePlanGeneration = async (
  planId: number,
): Promise<GeneratePlanResponse> => {
  const response = await api.post<GeneratePlanResponse>(
    `${p()}/test-plans/${planId}/resume-generation`,
  );
  return response.data;
};

export interface HostTestPlanEntry {
  id: number;
  test_plan_id: number;
  plan_title: string;
  plan_status: string;
  agent_name?: string;
  host_id: number;
  priority: string;
  test_phase: string;
  proposed_tests: ProposedTestItem[];
  rationale: string;
  status: string;
  findings?: string;
  notes?: string;
  created_at: string;
  updated_at: string;
}

export const getHostTestPlanEntries = async (hostId: number): Promise<HostTestPlanEntry[]> => {
  const response = await api.get(`${p()}/test-plans/hosts/${hostId}/entries`);
  return response.data;
};

// ---------------------------------------------------------------------------
// Test Execution — start execution session (mirrors generateTestPlan)
// ---------------------------------------------------------------------------

export interface ExecuteResponse {
  execution_session_id: number;
  plan_id: number;
  plan_title: string;
  agent_id: number;
  api_key: string;
  instructions: string;
}

export const executeTestPlan = async (planId: number): Promise<ExecuteResponse> => {
  const response = await api.post(`${p()}/test-plans/${planId}/execute`);
  return response.data;
};

/** Resume an interrupted execution session.  Re-mints a fresh agent API
 *  key for the SAME session (prior per-test results preserved) and
 *  returns the same shape as executeTestPlan, so the caller can reuse
 *  the agent-instructions dialog. */
export const resumeExecutionSession = async (
  planId: number,
  sessionId: number,
): Promise<ExecuteResponse> => {
  const response = await api.post(
    `${p()}/test-plans/${planId}/execution-sessions/${sessionId}/resume`,
  );
  return response.data;
};

export const downloadTestPlanBundle = async (planId: number): Promise<{ bundleId: string; sessionId: number }> => {
  const response = await api.post(
    `${p()}/test-plans/${planId}/export-bundle`,
    null,
    { responseType: 'blob' },
  );
  const disposition = (response.headers['content-disposition'] || '') as string;
  const match = disposition.match(/filename=([^;]+)/);
  const filename = match
    ? match[1].trim().replace(/^"|"$/g, '')
    : `networkmapper_plan_${planId}_bundle.zip`;
  const blobUrl = URL.createObjectURL(response.data as Blob);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);
  return {
    bundleId: (response.headers['x-bundle-id'] || '') as string,
    sessionId: Number(response.headers['x-execution-session-id'] || 0),
  };
};

export interface StartReconRequest {
  notes?: string;
}

export interface StartReconResponse {
  recon_session_id: number;
  scope_id: number;
  scope_name: string;
  subnets: string[];
  agent_id: number;
  api_key: string;      // plaintext, shown exactly once
  instructions: string;
  // v2.65.0 — resolved at mint time so the dialog can show the
  // actual key expiry without hardcoding a value that drifts when
  // AGENT_KEY_TTL_HOURS is overridden in .env.
  key_ttl_hours: number;
}

/**
 * v2.11.0 — replaces generateReconPlan().  Recon is now an ingest
 * workflow: a ReconSession is created and the agent's key is bound
 * to the scope (not a test plan).  The agent uses /agent/recon/*
 * endpoints to upload raw scanner output instead of creating test
 * plan entries against discovered hosts.
 */
export const startReconSession = async (
  scopeId: number,
  body: StartReconRequest = {},
): Promise<StartReconResponse> => {
  const response = await api.post<StartReconResponse>(
    `${p()}/scopes/${scopeId}/recon/start`,
    body,
  );
  return response.data;
};

/** Resume an interrupted recon session.  Re-mints a session-pinned
 *  agent API key for the SAME session (prior uploads preserved; the
 *  old key is revoked).  Mirrors the execution-session resume client
 *  and returns the same StartReconResponse shape as /recon/start. */
export const resumeReconSession = async (
  scopeId: number,
  sessionId: number,
): Promise<StartReconResponse> => {
  const response = await api.post<StartReconResponse>(
    `${p()}/scopes/${scopeId}/recon/sessions/${sessionId}/resume`,
  );
  return response.data;
};

export interface ImportResultsResponse {
  execution_session_id: number;
  plan_id: number;
  bundle_id: string;
  results_imported: number;
  sanity_checks_imported: number;
  feedback_extracted: boolean;
  is_final: boolean;
  session_status: string;
  plan_status: string;
  parse_errors: string[];
}

export const importTestPlanResults = async (
  planId: number,
  file: File,
): Promise<ImportResultsResponse> => {
  const form = new FormData();
  form.append('file', file);
  const response = await api.post<ImportResultsResponse>(
    `${p()}/test-plans/${planId}/import-results`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return response.data;
};

export type ExecutionReportFormat = 'html' | 'pdf' | 'json' | 'csv';

/**
 * Download a test-plan execution report. Triggers a browser file save via
 * an anchor element rather than loading the blob into React state — some
 * formats (PDF, HTML) can be large and are not meaningful to render inline.
 */
export const downloadExecutionReport = async (
  planId: number,
  format: ExecutionReportFormat,
  sessionId?: number,
): Promise<void> => {
  const params = new URLSearchParams({ format });
  if (sessionId != null) params.set('session_id', String(sessionId));
  const response = await api.get(
    `${p()}/test-plans/${planId}/execution-report?${params.toString()}`,
    { responseType: 'blob' },
  );
  const disposition = (response.headers['content-disposition'] || '') as string;
  const match = disposition.match(/filename=([^;]+)/);
  const filename = match
    ? match[1].trim().replace(/^"|"$/g, '')
    : `test_plan_${planId}_execution_report.${format}`;
  const blobUrl = URL.createObjectURL(response.data as Blob);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);
};
