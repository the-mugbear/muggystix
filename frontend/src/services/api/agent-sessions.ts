/**
 * Unified agent-session timeline (v2.30.0 backend, v3 UI consumer).
 *
 * Drives the Project Activity timeline + the per-(model, tool)
 * rollup card.  See backend
 * ``app/api/v1/endpoints/agent_sessions.py``.
 */
import { api, p } from './client';


export type AgentSessionKind = 'recon' | 'plan_generation' | 'execution';

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
  test_plan_id?: number | null;
}

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

export interface ModelToolSummaryRow {
  generated_by_model: string | null;
  generated_by_tool: string | null;
  recon: number;
  plan_generation: number;
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
