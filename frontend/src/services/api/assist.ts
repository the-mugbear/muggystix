/**
 * Starting an agent session — the operator-side call.
 * The endpoint keeps its v2.64.0 `/assist` name, but since v2.337.0 a session
 * started here is THE agent session: one project key that reads the inventory,
 * uploads scans, proposes host tests and records evidence, within the
 * operator's project role. The agent-side (X-API-Key) surface lives at
 * /agent/* and is consumed by the agent directly, not by this client.
 *
 * 5.328.0 — everything that reads or ends a session is in `agent-sessions.ts`,
 * keyed by the session id this call returns. The session rows, detail and
 * list that lived here were keyed by a second id (the session's
 * `assist_sessions` row), which no longer exists.
 */
import { api, projectPath } from './client';

export interface McpClientSetup {
  id: string;
  /** Client name as the operator knows it — the tab label. */
  label: string;
  /** 'file' → `payload` is JSON to save at `path`; 'command' → a shell command to run. */
  kind: 'file' | 'command';
  path: string;
  payload: string;
  hint: string;
  /** v5.203.0 — the handoff after the config: how this client shows
   *  "connected", the first prompt to give the agent, and what its answer
   *  should contain for the session that was actually minted. Optional so
   *  fixtures and older payloads still render the config alone. */
  verify_check?: string;
  verify_prompt?: string;
  verify_expected?: string;
}

export interface StartAssistResponse {
  /** The session's id — its only one: the id the agent reports, Agent
   *  Sessions lists and `/agent-sessions/:id` opens. (The response also
   *  repeats it as a deprecated `assist_session_id`; do not read that.) */
  agent_session_id: number;
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
  // The route still takes an optional `purpose` (scripts label their
  // sessions with it); the app's dialog does not ask for one.
  ttl_hours?: number;
  // `can_write_assigned` removed in v5.189.0 with the capability system — the
  // session's authority is the operator's project role, decided per request.
}

export const startAssistSession = async (
  projectId: number,
  body: StartAssistRequest,
): Promise<StartAssistResponse> => {
  const res = await api.post<StartAssistResponse>(
    `${projectPath(projectId)}/assist/start`,
    body,
  );
  return res.data;
};
