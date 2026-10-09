/**
 * The agent API-call audit feed: one row per inbound /agent/* request an
 * agent made, read per session on the Agent Session page.
 *
 * 5.320.0 — moved out of services/api/test-plans.ts when test plans were
 * removed; the per-plan feed went with them.  services/api.ts re-exports
 * everything from here so consumers keep importing from ``../services/api``.
 */
import { api, projectPath } from './client';

/** One captured agent → BlueStick request. */
export interface AgentApiCallRow {
  id: number;
  created_at: string;
  agent_id: number;
  /** Who engaged this agent — joined from Agent.owner; null if deleted. */
  agent_name?: string | null;
  owner_id?: number | null;
  owner_username?: string | null;
  api_key_prefix?: string | null;
  source_ip?: string | null;
  method: string;
  path: string;
  path_template?: string | null;
  path_params?: Record<string, unknown> | null;
  query_params?: Record<string, unknown> | null;
  request_body_summary?: Record<string, unknown> | null;
  status_code: number;
  response_bytes?: number | null;
  duration_ms: number;
  scope_id?: number | null;
  referenced_host_ids?: number[] | null;
  referenced_entry_ids?: number[] | null;
  referenced_target_ips?: string[] | null;
}

export interface AgentApiCallListResponse {
  total: number;
  items: AgentApiCallRow[];
}

export interface AgentActivityFilters {
  method?: string;
  status_min?: number;
  status_max?: number;
  host_id?: number;
  target_ip?: string;
  /** Only calls made by agents the current user owns. */
  mine?: boolean;
  limit?: number;
  offset?: number;
}

/** The audit feed for one agent session (v5.173.0), by the session id
 *  (5.328.0 — it was keyed by a second id, the session's detail row). */
export const getAgentSessionApiActivity = async (
  projectId: number,
  sessionId: number,
  filters: AgentActivityFilters = {},
  signal?: AbortSignal,
): Promise<AgentApiCallListResponse> => {
  const response = await api.get<AgentApiCallListResponse>(
    `${projectPath(projectId)}/agent-sessions/${sessionId}/api-activity`,
    { params: filters, signal },
  );
  return response.data;
};
