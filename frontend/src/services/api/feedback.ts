/**
 * Agent feedback queue (admin only) — triage of AgentFeedback rows.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';


// ---------------------------------------------------------------------------
// Agent Feedback (admin)
// ---------------------------------------------------------------------------

export interface AgentFeedbackEntry {
  id: number;
  project_id: number | null;
  agent_id: number | null;
  test_plan_id: number | null;
  execution_session_id: number | null;
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
}

export interface AgentFeedbackListParams {
  status?: string;
  source?: string;
  min_rating?: number;
  has_tool_suggestions?: boolean;
  has_api_critiques?: boolean;
  search?: string;
  /** v2.28.0 — narrow to feedback rows attributed to a specific test plan. */
  test_plan_id?: number;
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
}

export const listAgentFeedback = async (
  params: AgentFeedbackListParams = {},
): Promise<AgentFeedbackEntry[]> => {
  const response = await api.get<AgentFeedbackEntry[]>('/feedback/', { params });
  return response.data;
};

export const getAgentFeedbackStats = async (): Promise<FeedbackStats> => {
  const response = await api.get<FeedbackStats>('/feedback/stats');
  return response.data;
};

export const updateAgentFeedback = async (
  id: number,
  body: { status?: string; reviewer_notes?: string },
): Promise<AgentFeedbackEntry> => {
  const response = await api.patch<AgentFeedbackEntry>(`/feedback/${id}`, body);
  return response.data;
};
