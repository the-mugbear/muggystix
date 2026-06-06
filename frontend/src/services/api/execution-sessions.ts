/**
 * JWT-facing execution-session client (v3 alpha.7 + alpha.12).
 *
 * Two endpoints:
 *   - LIST  /projects/{id}/execution-sessions/        (alpha.12)
 *   - DETAIL /projects/{id}/execution-sessions/{id}   (alpha.7)
 *
 * The detail payload matches the existing plan-scoped
 * all-entry-results endpoint exactly; the list payload is a lighter
 * summary shape with per-session result + finding counts.
 */
import { api, p } from './client';

import { AllEntryResultsResponse } from './test-plans';


/** Summary row for the v3 alpha.12 /executions list page. */
export interface ExecutionSessionRow {
  id: number;
  test_plan_id: number;
  plan_title?: string | null;
  plan_version?: number | null;
  status: string;
  mode?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  started_by_username?: string | null;
  agent_name?: string | null;
  generated_by_model?: string | null;
  generated_by_tool?: string | null;
  prompt_version?: string | null;
  result_count: number;
  finding_count: number;
}


export interface ExecutionSessionListFilters {
  status?: string;
  test_plan_id?: number;
  model?: string;
  user_id?: number;
  search?: string;
  skip?: number;
  limit?: number;
}

export interface ExecutionSessionListResult {
  items: ExecutionSessionRow[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

/** v2.86.10 — wrapped the bare-array return in an {items,total} shape
 *  using the ``X-Total-Count`` response header.
 *  v2.86.13 — endpoint now returns the standard
 *  ``Paginated[ExecutionSessionRow]`` envelope; wrapper passes it
 *  through. */
export const listExecutionSessionsProjectWide = async (
  filters: ExecutionSessionListFilters = {},
): Promise<ExecutionSessionListResult> => {
  const response = await api.get<ExecutionSessionListResult>(
    `${p()}/execution-sessions/`,
    { params: filters },
  );
  return response.data;
};


/** Fetch a full execution-session bundle by session id alone.
 *  Project-scoped (returns 404 with actionable detail when the
 *  session belongs to a plan in another project). */
export interface ExecutionSessionDetailQuery {
  entriesSkip?: number;
  entriesLimit?: number;
}

export const getExecutionSessionById = async (
  sessionId: number,
  query: ExecutionSessionDetailQuery = {},
): Promise<AllEntryResultsResponse> => {
  // v2.86.7 — query params let the caller paginate the entries list
  // so a 5000-entry session doesn't ship its whole bundle on initial
  // page entry.  Back-compat default (no params) keeps the pre-v2.86.7
  // "return everything" behaviour.
  const params = new URLSearchParams();
  if (query.entriesSkip !== undefined) params.set('entries_skip', String(query.entriesSkip));
  if (query.entriesLimit !== undefined) params.set('entries_limit', String(query.entriesLimit));
  const qs = params.toString();
  const response = await api.get<AllEntryResultsResponse>(
    `${p()}/execution-sessions/${sessionId}${qs ? `?${qs}` : ''}`,
  );
  return response.data;
};


/**
 * Operator-driven completion path for stuck execution sessions (v4
 * beta.7 backend).  Mirrors abandonReconSession — same semantics for
 * the test-plan execution side.  Used when the terminal-side agent
 * never reached the terminal state (agent crashed mid-plan, user
 * killed the terminal, etc.).  Requires the analyst role on the
 * project.  Returns 409 if the session is already terminal.
 */
export const abandonExecutionSession = async (
  sessionId: number,
  reason?: string,
): Promise<ExecutionSessionRow> => {
  const response = await api.post<ExecutionSessionRow>(
    `${p()}/execution-sessions/${sessionId}/abandon`,
    { reason: reason?.trim() || null },
  );
  return response.data;
};
