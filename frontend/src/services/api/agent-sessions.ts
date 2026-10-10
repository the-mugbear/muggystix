/**
 * Agent sessions: the list, one session, its notes, End, Resume and the
 * per-(model, tool) table.  See backend
 * ``app/api/v1/endpoints/agent_sessions.py``.
 */
import { api, projectPath } from './client';
import type { McpClientSetup } from './assist';


/** One session.  There is no `kind` (5.383.0): every session carries the same
 *  fields and reads the same, whenever it was started. */
export interface AgentSessionRow {
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
  /** The label the session was given at start, if any. */
  purpose?: string | null;
  /** v5.214.0 — when the live key stops working (null once revoked) and until
   *  when the session can still be renewed or resumed. An active row whose
   *  key has expired but is still renewable is a session the operator can
   *  reconnect to, not a dead one (`utils/agentRuns.keyState`). */
  key_expires_at?: string | null;
  renewable_until?: string | null;
  /** v5.219.0 — how the session ended ('agent' is the clean exit; 'operator'
   *  and 'lapsed' mean the agent never called end) and how many feedback
   *  submissions it made. */
  end_reason?: 'agent' | 'operator' | 'lapsed' | string | null;
  feedback_count?: number;
  /** v5.288.0 — the operator's display name; shown before the username. */
  user_full_name?: string | null;
  /** 5.320.0 — the host tests the session proposed and the evidence records
   *  it wrote. */
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

/** v5.312.0 — one session as the list shows it; 404 for an id the list does
 *  not have. */
export const getAgentSession = async (projectId: number, sessionId: number, signal?: AbortSignal): Promise<AgentSessionRow> => {
  const response = await api.get<AgentSessionRow>(`${projectPath(projectId)}/agent-sessions/${sessionId}`, { signal });
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
  projectId: number,
  sessionId: number,
  limit = 50,
  signal?: AbortSignal,
): Promise<AgentSessionNotes> => {
  const response = await api.get<AgentSessionNotes>(
    `${projectPath(projectId)}/agent-sessions/${sessionId}/notes`,
    { params: { limit }, signal },
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
  projectId: number,
  sessionId: number,
): Promise<ResumeAgentSessionResponse> => {
  const response = await api.post<ResumeAgentSessionResponse>(
    `${projectPath(projectId)}/agent-sessions/${sessionId}/resume`,
  );
  return response.data;
};

export interface AgentSessionListResponse {
  project_id: number;
  sessions: AgentSessionRow[];
  total: number;
}

export interface AgentSessionFilters {
  agent_id?: number;
  model?: string;
  tool?: string;
  user_id?: number;
  /** Filter by the stored status — pass 'active' for the sessions still
   *  open (whether a key still works is `utils/agentRuns.hasLiveKey`). */
  status?: string;
  limit?: number;
  offset?: number;
}

export const listAgentSessions = async (
  projectId: number,
  filters: AgentSessionFilters = {},
  options: { signal?: AbortSignal } = {},
): Promise<AgentSessionListResponse> => {
  const response = await api.get<AgentSessionListResponse>(
    `${projectPath(projectId)}/agent-sessions`,
    { params: filters, signal: options.signal },
  );
  return response.data;
};

/** v5.212.0 — the operator's kill switch for a unified project session:
 *  revokes its key and closes what it left open. Owner or project admin only;
 *  the backend enforces that and answers 403 otherwise. */
export const endAgentSession = async (projectId: number, sessionId: number): Promise<void> => {
  await api.post(`${projectPath(projectId)}/agent-sessions/${sessionId}/end`);
};

export interface ModelToolSummaryRow {
  generated_by_model: string | null;
  generated_by_tool: string | null;
  /** Sessions run with this model and tool: the `total` of the list
   *  filtered by them. */
  total: number;
}

export interface ModelToolSummaryResponse {
  project_id: number;
  summary: ModelToolSummaryRow[];
}

export const getAgentSessionSummary = async (projectId: number, signal?: AbortSignal): Promise<ModelToolSummaryResponse> => {
  const response = await api.get<ModelToolSummaryResponse>(
    `${projectPath(projectId)}/agent-sessions/by-model-tool`,
    { signal },
  );
  return response.data;
};
