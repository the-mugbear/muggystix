/**
 * Assist session API — operator-side calls for the v2.64.0
 * interactive assist workflow.  The agent-side (X-API-Key) surface
 * lives at /agent/assist/* and is consumed by the agent directly,
 * not by this client.
 */
import { api, p } from './client';

export interface McpClientSetup {
  id: string;
  /** Client name as the operator knows it — the tab label. */
  label: string;
  /** 'file' → `payload` is JSON to save at `path`; 'command' → a shell command to run. */
  kind: 'file' | 'command';
  path: string;
  payload: string;
  hint: string;
}

export interface StartAssistResponse {
  assist_session_id: number;
  project_id: number;
  project_name: string;
  agent_id: number;
  api_key: string;
  instructions: string;
  // Per-client MCP setup. Not one blob: VS Code wraps servers under `servers`
  // while Claude Code uses `mcpServers` and Codex takes neither, each wanting a
  // different place — so the backend emits the shape each host actually reads.
  mcp_clients: McpClientSetup[];
  mcp_url: string;
  // v2.65.0 — resolved at mint time; dialog reads this instead of
  // hardcoding "4 h" so an env override (or future ASSIST_KEY_TTL
  // bump) doesn't require a frontend change in lockstep.
  key_ttl_hours: number;
  // v5.189.0 — `capabilities` / `capability_constraint` removed. A session does
  // what its operator may do on the project, so there is no grant to echo back.
}

export interface StartAssistRequest {
  purpose?: string;
  ttl_hours?: number;
  // `can_write_assigned` removed in v5.189.0 with the capability system — the
  // session's authority is the operator's project role, decided per request.
}

export interface AssistSessionRow {
  id: number;
  project_id: number;
  purpose: string | null;
  status: string;
  started_by_id: number | null;
  started_by_username: string | null;
  started_at: string | null;
  ended_at: string | null;
  last_activity_at: string | null;
  environment_probed: boolean;
  /** When the session's agent key stops working — the field that answers
   *  "end it now, or let it lapse?". This is the KEY's expiry, not the
   *  session's: the session row has no lifetime of its own and can outlive
   *  its key. Null means no active key remains, i.e. the session is dead in
   *  practice even though `status` still reads 'active'.
   *  Never derive this from started_at + 4h — AGENT_KEY_TTL_HOURS and the
   *  per-start ttl_hours both move it. */
  key_expires_at: string | null;
  /** How much the session actually did. A session with zero calls is the
   *  common dead end — key minted, prompt never pasted — and reads identically
   *  to a busy one without these. */
  call_count: number;
  note_count: number;
}

/** A note this session's agent wrote — its only durable output; everything
 *  else it did was a read. */
export interface AssistSessionNote {
  id: number;
  host_id: number | null;
  host_ip: string | null;
  hostname: string | null;
  body: string;
  status: string | null;
  created_at: string | null;
}

export interface AssistSessionDetail extends AssistSessionRow {
  /** The operator's machine as the agent reported it. */
  environment: Record<string, unknown> | null;
  environment_probed_at: string | null;
  agent_model: string | null;
  agent_tool: string | null;
  prompt_version: string | null;
  notes: AssistSessionNote[];
  feedback_count: number;
}

export interface AssistSessionFilters {
  status?: string;
  mine?: boolean;
  limit?: number;
  offset?: number;
}

export const startAssistSession = async (
  body: StartAssistRequest,
): Promise<StartAssistResponse> => {
  const res = await api.post<StartAssistResponse>(
    `${p()}/assist/start`,
    body,
  );
  return res.data;
};

export const endAssistSession = async (sessionId: number): Promise<void> => {
  await api.post(`${p()}/assist/sessions/${sessionId}/end`);
};

export const listAssistSessions = async (
  filters: AssistSessionFilters = {},
): Promise<AssistSessionRow[]> => {
  const res = await api.get<AssistSessionRow[]>(`${p()}/assist/sessions`, {
    params: filters,
  });
  return res.data;
};

export const getAssistSession = async (
  sessionId: number,
): Promise<AssistSessionDetail> => {
  const res = await api.get<AssistSessionDetail>(
    `${p()}/assist/sessions/${sessionId}`,
  );
  return res.data;
};
