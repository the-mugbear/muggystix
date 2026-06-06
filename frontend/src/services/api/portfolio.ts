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
}

export interface PortfolioSummary {
  total_projects: number;
  active_projects: number;
  total_hosts: number;
  total_open_ports: number;
  total_scans: number;
  total_unreviewed: number;
}

export interface PortfolioDashboardResponse {
  summary: PortfolioSummary;
  projects: ProjectCard[];
}

export const getPortfolioDashboard = async (): Promise<PortfolioDashboardResponse> => {
  const response = await api.get('/portfolio/dashboard');
  return response.data;
};
