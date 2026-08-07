/**
 * Assist session API — operator-side calls for the v2.64.0
 * interactive assist workflow.  The agent-side (X-API-Key) surface
 * lives at /agent/assist/* and is consumed by the agent directly,
 * not by this client.
 */
import { api, p } from './client';

export interface StartAssistResponse {
  assist_session_id: number;
  project_id: number;
  project_name: string;
  agent_id: number;
  api_key: string;
  instructions: string;
  // v2.65.0 — resolved at mint time; dialog reads this instead of
  // hardcoding "4 h" so an env override (or future ASSIST_KEY_TTL
  // bump) doesn't require a frontend change in lockstep.
  key_ttl_hours: number;
  // What the session may do beyond reading (e.g. ["write:follow","write:notes"]),
  // and how narrowly ("assigned" = only hosts assigned to the operator).
  // Echoed back so the dialog states the granted authority rather than
  // assuming its own checkbox took effect.
  capabilities: string[];
  capability_constraint: string | null;
}

export interface StartAssistRequest {
  purpose?: string;
  ttl_hours?: number;
  /**
   * Let the session write host notes and set review status, limited to hosts
   * assigned to the operator starting it. Defaults to false — assist is
   * read-only unless the operator opts in.
   */
  can_write_assigned?: boolean;
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
  // Audit: which sessions carried write authority, and how narrowly.
  capabilities: string[];
  capability_constraint: string | null;
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

export const listAssistSessions = async (): Promise<AssistSessionRow[]> => {
  const res = await api.get<AssistSessionRow[]>(`${p()}/assist/sessions`);
  return res.data;
};
