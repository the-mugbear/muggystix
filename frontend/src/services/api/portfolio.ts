/**
 * Portfolio Dashboard — cross-project summary surface.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';


// ---------------------------------------------------------------------------
// Portfolio Dashboard
// ---------------------------------------------------------------------------

export interface VulnSummaryBrief {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface ProjectCard {
  id: number;
  name: string;
  slug: string;
  status: string;
  description?: string;
  host_count: number;
  up_host_count: number;
  open_port_count: number;
  scan_count: number;
  last_scan_at?: string;
  days_since_last_scan?: number;
  is_stale: boolean;
  review_progress_pct: number;
  unreviewed_hosts: number;
  vuln_summary: VulnSummaryBrief;
  /** Derived rollup: 'critical' | 'warning' | 'stale' | 'healthy'. */
  health: string;
  // P4 control-plane signals.
  attention_reasons: string[];
  pending_plan_reviews: number;
  open_tasks: number;
  active_sessions: number;
  blocked_sessions: number;
  member_count: number;
  user_role: string | null;
  // SOC-P3 governance.
  has_admin: boolean;
  admins: string[];
}

export interface PortfolioSummary {
  total_projects: number;
  active_projects: number;
  total_hosts: number;
  total_open_ports: number;
  total_scans: number;
  total_unreviewed: number;
  // P4 attention rollups.
  projects_requiring_attention: number;
  projects_with_critical: number;
  stale_projects: number;
  projects_no_data: number;
  pending_approvals_total: number;
  blocked_sessions_total: number;
  projects_without_admin: number;
}

export interface PortfolioDashboardResponse {
  summary: PortfolioSummary;
  projects: ProjectCard[];
}

export const getPortfolioDashboard = async (): Promise<PortfolioDashboardResponse> => {
  const response = await api.get('/portfolio/dashboard');
  return response.data;
};

// --- SOC-P4 cross-project team roster ---

export interface TeamMemberProject {
  project_id: number;
  project_name: string;
  role: string;
}

export interface TeamMember {
  user_id: number;
  username: string;
  full_name: string | null;
  project_count: number;
  projects: TeamMemberProject[];
  open_tasks: number;
  hosts_in_review: number;
}

export interface TeamResponse {
  members: TeamMember[];
  total_members: number;
}

export const getPortfolioTeam = async (): Promise<TeamResponse> => {
  const response = await api.get('/portfolio/team');
  return response.data;
};
