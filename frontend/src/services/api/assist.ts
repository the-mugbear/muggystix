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
}

export interface StartAssistRequest {
  purpose?: string;
  ttl_hours?: number;
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
