/**
 * Unified agent-session timeline (v2.30.0 backend, v3 UI consumer).
 *
 * Drives the Project Activity timeline + the per-(model, tool)
 * rollup card.  See backend
 * ``app/api/v1/endpoints/agent_sessions.py``.
 */
import { api, p } from './client';
import type { McpClientSetup } from './assist';


// v5.185.0 — assist joined the timeline. The backend model always described
// four workflows; the service and this type both enumerated three, so assist
// sessions were invisible on Agent Runs.
export type AgentSessionKind = 'project' | 'recon' | 'plan_generation' | 'execution' | 'assist';

export interface AgentSessionRow {
  kind: AgentSessionKind;
  id: number;
  project_id: number;
  agent_id?: number | null;
  agent_name?: string | null;
  user_id?: number | null;
  user_username?: string | null;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  generated_by_model?: string | null;
  generated_by_tool?: string | null;
  prompt_version?: string | null;
  scope_id?: number | null;
  /** v5.187.0 — what this session declared it is working on: scope name +
   *  CIDRs for recon, the plan title for plan work. Null for assist, which is
   *  project-wide by design. An id alone can't tell a colleague that a range is
   *  already being scanned, which is the reason a session declares a target. */
  target_label?: string | null;
  test_plan_id?: number | null;
  /** v5.211.0 — the operator's stated purpose (project sessions only). */
  purpose?: string | null;
  /** v5.214.0 — project sessions only. When the live key stops working
   *  (null once revoked) and until when the session can still be renewed or
   *  resumed. An active row whose key has expired but is still renewable is
   *  a session the operator can reconnect to, not a dead one. */
  key_expires_at?: string | null;
  renewable_until?: string | null;
}

/** v5.214.0 — what a resume hands back: the same shape the start dialog
 *  renders (replacement key, prompt with the resumed notice, MCP setup), on
 *  the SAME session — open phases and the audit trail continue. */
export interface ResumeAgentSessionResponse {
  session_id: number;
  project_id: number;
  project_name: string;
  agent_id: number;
  api_key: string;
  instructions: string;
  mcp_clients: McpClientSetup[];
  mcp_url: string;
  key_ttl_hours: number;
  key_expires_at: string;
  renewable_until?: string | null;
  active_recon_session_ids: number[];
  active_execution_session_ids: number[];
}

/** Rotate the key on an active project session and get the prompt + MCP
 *  setup again. Owner only (the key acts under their name); the backend
 *  answers 403 for anyone else and 409 for a session that is not active or
 *  is past its lifetime cap. */
export const resumeAgentSession = async (
  sessionId: number,
): Promise<ResumeAgentSessionResponse> => {
  const response = await api.post<ResumeAgentSessionResponse>(
    `${p()}/agent-sessions/${sessionId}/resume`,
  );
  return response.data;
};

export interface AgentSessionListResponse {
  project_id: number;
  sessions: AgentSessionRow[];
  total: number;
}

export interface AgentSessionFilters {
  kind?: AgentSessionKind;
  agent_id?: number;
  model?: string;
  tool?: string;
  user_id?: number;
  /** Filter by native session status — pass 'active' for the in-flight
   *  banner.  v3 alpha.3. */
  status?: string;
  limit?: number;
  offset?: number;
}

export const listAgentSessions = async (
  filters: AgentSessionFilters = {},
  options: { signal?: AbortSignal } = {},
): Promise<AgentSessionListResponse> => {
  const response = await api.get<AgentSessionListResponse>(
    `${p()}/agent-sessions`,
    { params: filters, signal: options.signal },
  );
  return response.data;
};

/** v5.212.0 — the operator's kill switch for a unified project session:
 *  revokes its key and closes what it left open. Owner or project admin only;
 *  the backend enforces that and answers 403 otherwise. */
export const endAgentSession = async (sessionId: number): Promise<void> => {
  await api.post(`${p()}/agent-sessions/${sessionId}/end`);
};

export interface ModelToolSummaryRow {
  generated_by_model: string | null;
  generated_by_tool: string | null;
  project: number;
  recon: number;
  plan_generation: number;
  assist: number;
  execution: number;
  total: number;
}

export interface ModelToolSummaryResponse {
  project_id: number;
  summary: ModelToolSummaryRow[];
}

export const getAgentSessionSummary = async (): Promise<ModelToolSummaryResponse> => {
  const response = await api.get<ModelToolSummaryResponse>(
    `${p()}/agent-sessions/by-model-tool`,
  );
  return response.data;
};
