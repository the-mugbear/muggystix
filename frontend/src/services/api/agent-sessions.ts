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
// 5.313.1 — recon runs are gone, and their legacy rows with them.
// 5.320.0 — so are plans and execution runs ('plan_generation' / 'execution').
export type AgentSessionKind = 'project' | 'assist';

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
  /** v5.211.0 — the operator's stated purpose (project sessions only). */
  purpose?: string | null;
  /** v5.214.0 — project sessions only. When the live key stops working
   *  (null once revoked) and until when the session can still be renewed or
   *  resumed. An active row whose key has expired but is still renewable is
   *  a session the operator can reconnect to, not a dead one. */
  key_expires_at?: string | null;
  renewable_until?: string | null;
  /** v5.219.0 — project sessions only. How the session ended ('agent' is the
   *  clean exit; 'operator' and 'lapsed' mean the agent never called end) and
   *  how many feedback submissions it made. Both null/0 on older backends. */
  end_reason?: 'agent' | 'operator' | 'lapsed' | string | null;
  feedback_count?: number;
  /** v5.288.0 — the operator's display name; shown before the username. */
  user_full_name?: string | null;
  /** v5.288.0 — legacy assist rows still stored as active: whether the session
   *  can still act. Null/absent when not computed. */
  session_live?: boolean | null;
  /** 5.320.0 — project sessions only: the host tests the session proposed
   *  and the evidence records it wrote (they replace `phases`, the runs and
   *  plans a session used to open). */
  host_test_count?: number;
  evidence_count?: number;
  /** v5.312.0 — its last authenticated call, and the authority it acts with:
   *  the operator's CURRENT project role ('admin' | 'analyst' | 'auditor' |
   *  'viewer'), 'global_admin' for a global admin without an admin membership,
   *  null when the operator is no longer a member. */
  last_activity_at?: string | null;
  operator_role?: string | null;
  /** 5.328.0 — how much the session did (a session with zero calls is the
   *  common dead end: key minted, prompt never pasted) and how the agent
   *  reached it, from observed calls: 'none' = no authenticated call yet;
   *  'mcp' = at least one call through the MCP transport; 'curl' = direct HTTP
   *  only. A past call proves the client connected, not that it is still
   *  running — read it with last_activity_at.
   *  `id` is the session's ONLY id: these, its notes and its API-call feed are
   *  all read by it (they were keyed by a second `assist_session_id`). */
  call_count?: number;
  note_count?: number;
  connection?: 'none' | 'mcp' | 'curl';
  first_call_at?: string | null;
  /** v5.312.0 — what the CALLER may do to this active session: owner or
   *  project admin may end it, only the owner may resume it. */
  can_end?: boolean;
  can_resume?: boolean;
}

/** v5.312.0 — one session as the list shows it (a project session or a legacy
 *  assist one); 404 for any other legacy per-workflow row. */
export const getAgentSession = async (sessionId: number, signal?: AbortSignal): Promise<AgentSessionRow> => {
  const response = await api.get<AgentSessionRow>(`${p()}/agent-sessions/${sessionId}`, { signal });
  return response.data;
};

/** A note this session's agent wrote. */
export interface AgentSessionNote {
  id: number;
  host_id: number | null;
  host_ip: string | null;
  hostname: string | null;
  body: string;
  created_at: string | null;
}

export interface AgentSessionNotes {
  /** Every note the session wrote; `items` is the newest `limit` of them. */
  total: number;
  items: AgentSessionNote[];
}

/** 5.328.0 — the notes a session wrote, newest first, by the session id. */
export const getAgentSessionNotes = async (
  sessionId: number,
  limit = 50,
  signal?: AbortSignal,
): Promise<AgentSessionNotes> => {
  const response = await api.get<AgentSessionNotes>(
    `${p()}/agent-sessions/${sessionId}/notes`,
    { params: { limit }, signal },
  );
  return response.data;
};

/** 5.328.0 — the session an old `/assist-sessions/:id` link meant. Until
 *  2.449.0 a session had a second id (its `assist_sessions` row) and its page
 *  was addressed by that; only sessions from before then have one. 404 when
 *  nothing had the id. */
export const getAgentSessionByLegacyAssistId = async (
  legacyAssistSessionId: number,
  signal?: AbortSignal,
): Promise<AgentSessionRow> => {
  const response = await api.get<AgentSessionRow>(
    `${p()}/assist-sessions/${legacyAssistSessionId}`,
    { signal },
  );
  return response.data;
};

/** v5.214.0 — what a resume hands back: the same shape the start dialog
 *  renders (replacement key, prompt with the resumed notice, MCP setup), on
 *  the SAME session — its work and the audit trail continue. */
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
  assist: number;
  total: number;
}

export interface ModelToolSummaryResponse {
  project_id: number;
  summary: ModelToolSummaryRow[];
}

export const getAgentSessionSummary = async (signal?: AbortSignal): Promise<ModelToolSummaryResponse> => {
  const response = await api.get<ModelToolSummaryResponse>(
    `${p()}/agent-sessions/by-model-tool`,
    { signal },
  );
  return response.data;
};
