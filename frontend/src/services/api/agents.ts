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

export interface AgentCreateResponse extends AgentResponse {
  api_key: string;
}

export interface AgentKeyRotateResponse {
  api_key: string;
  message: string;
}

export const createAgent = async (data: {
  name: string;
  description?: string;
  rate_limit_rpm?: number;
}): Promise<AgentCreateResponse> => {
  const response = await api.post(`${p()}/agents/`, data);
  return response.data;
};

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
