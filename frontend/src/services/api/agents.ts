/**
 * Agent management (project-scoped agent CRUD + key rotation).
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api, p } from './client';


// ---------------------------------------------------------------------------
// Agent Management
// ---------------------------------------------------------------------------

export interface AgentResponse {
  id: number;
  name: string;
  project_id: number;
  owner_id: number;
  description?: string;
  is_active: boolean;
  rate_limit_rpm: number;
  created_at: string;
  updated_at?: string;
  last_activity_at?: string;
  api_key_prefix?: string;
}

export interface AgentKeyRotateResponse {
  api_key: string;
  message: string;
}

/* `createAgent` (POST /agents/) was removed here in v5.181.0 along with the
 * "Create My Agent" button, its only caller. Nothing needs it: all four agent
 * workflows auto-provision the per-user agent row when a session starts. The
 * endpoint itself is unchanged and still reachable by script — it mints an
 * unscoped key, which is deliberately a different (and broader) thing than the
 * per-session keys the UI hands out, and not something to offer behind a
 * button that reads like setup. */

export const getProjectAgents = async (): Promise<AgentResponse[]> => {
  const response = await api.get(`${p()}/agents/`);
  return response.data;
};

export const deactivateAgent = async (agentId: number): Promise<void> => {
  await api.delete(`${p()}/agents/${agentId}`);
};

export const rotateAgentKey = async (agentId: number): Promise<AgentKeyRotateResponse> => {
  const response = await api.post(`${p()}/agents/${agentId}/rotate-key`);
  return response.data;
};
