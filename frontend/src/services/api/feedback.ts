/**
 * Agent feedback queue (admin only) — triage of AgentFeedback rows.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';
import type { Paginated } from './shared';


// ---------------------------------------------------------------------------
// Agent Feedback (admin)
// ---------------------------------------------------------------------------

export interface AgentFeedbackEntry {
  id: number;
  project_id: number | null;
  agent_id: number | null;
  source: string;
  prompt_version: string | null;
  overall_rating: number | null;
  api_critiques: Array<Record<string, any>> | null;
  tool_suggestions: Array<Record<string, any>> | null;
  friction_notes: string | null;
  agent_metrics: Record<string, any> | null;
  status: string;
  reviewed_by_id: number | null;
  reviewed_at: string | null;
  reviewer_notes: string | null;
  created_at: string;
  /** v2.428.2 — who and where: the session (its page, with its API calls, is
   *  `/agent-sessions/{agent_session_id}`), whether that page exists (false
   *  for a recon / plan / execution row from before the unified session), and
   *  its call count. */
  agent_session_id?: number | null;
  session_has_page?: boolean | null;
  session_api_calls?: number | null;
  project_name?: string | null;
  agent_name?: string | null;
  /** v2.428.5 — the MCP client the session connected with (its `initialize`). */
  client_name?: string | null;
}

export interface AgentFeedbackListParams {
  status?: string;
  source?: string;
  min_rating?: number;
  has_tool_suggestions?: boolean;
  has_api_critiques?: boolean;
  search?: string;
  project_id?: number;
  skip?: number;
  limit?: number;
}

export interface FeedbackStats {
  total: number;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  by_prompt_version: Record<string, number>;
  avg_rating: number | null;
  top_tool_suggestions: Array<{ name: string; count: number; categories: string[] }>;
  with_api_critiques?: number;
  with_tool_suggestions?: number;
}

export const listAgentFeedback = async (
  params: AgentFeedbackListParams = {},
  signal?: AbortSignal,
): Promise<Paginated<AgentFeedbackEntry>> => {
  // v2.428.2 — the standard Paginated envelope (was a bare array).
  const response = await api.get<Paginated<AgentFeedbackEntry>>('/feedback/', { params, signal });
  return response.data;
};

export const getAgentFeedbackStats = async (signal?: AbortSignal): Promise<FeedbackStats> => {
  const response = await api.get<FeedbackStats>('/feedback/stats', { signal });
  return response.data;
};

export const updateAgentFeedback = async (
  id: number,
  body: { status?: string; reviewer_notes?: string },
): Promise<AgentFeedbackEntry> => {
  const response = await api.patch<AgentFeedbackEntry>(`/feedback/${id}`, body);
  return response.data;
};
