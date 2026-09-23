/**
 * Portfolio (5.257.0) — two severity representations per project: findings
 * (issues) and scanner observations nobody has judged yet.  A project is
 * "critical" on either; a dismissed observation no longer counts at all.
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const dashboardMock = vi.fn();
vi.mock('../../services/api', () => ({
  getPortfolioDashboard: (...a: unknown[]) => dashboardMock(...a),
  getPortfolioTeam: vi.fn().mockResolvedValue({ members: [], total_members: 0 }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ projects: [], selectProject: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true }),
}));

import PortfolioDashboard from '../../pages/PortfolioDashboard';
import type { ProjectCard } from '../../services/api/portfolio';

const sev = (critical = 0, high = 0, medium = 0, low = 0) => ({ critical, high, medium, low });

const card = (over: Partial<ProjectCard>): ProjectCard => ({
  id: 1, name: 'P', slug: 'p', status: 'active', host_count: 10, up_host_count: 10,
  open_port_count: 0, scan_count: 1, days_since_last_scan: 1, is_stale: false,
  review_progress_pct: 80, unreviewed_hosts: 2, hosts_tested: 8,
  findings: sev(), unjudged_observations: sev(), health: 'healthy', attention_reasons: [],
  pending_plan_reviews: 0, open_tasks: 0, active_sessions: 0, blocked_sessions: 0,
  member_count: 1, user_role: 'admin', has_admin: true, admins: ['a'],
  ...over,
});

const summary = {
  total_projects: 3, active_projects: 3, total_hosts: 30, total_open_ports: 0, total_scans: 3,
  total_unreviewed: 6, projects_requiring_attention: 2, projects_with_critical: 2,
  stale_projects: 0, projects_no_data: 0, pending_approvals_total: 0,
  blocked_sessions_total: 0, projects_without_admin: 0,
};

const projects = [
  card({ id: 1, name: 'Confirmed critical', findings: sev(1, 2), health: 'critical',
    attention_reasons: ['critical_findings'] }),
  card({ id: 2, name: 'Untriaged critical', unjudged_observations: sev(3, 0, 5), health: 'critical',
    attention_reasons: ['critical_unjudged'] }),
  card({ id: 3, name: 'All judged' }),
];

describe('Portfolio — findings and not-yet-judged observations', () => {
  beforeEach(() => {
    dashboardMock.mockReset().mockResolvedValue({ summary, projects });
  });

  it('shows findings as issues and the untriaged scanner output beside them', async () => {
    render(<MemoryRouter><PortfolioDashboard /></MemoryRouter>);
    await screen.findByText('Confirmed critical');

    expect(screen.getByText('3 issues')).toBeInTheDocument();
    expect(screen.getByText(/Not yet judged:\s*3 critical · 5 medium\s*scanner observations/)).toBeInTheDocument();
    expect(screen.getAllByText('No findings recorded.')).toHaveLength(2);
    expect(screen.getAllByText(/8 of 10 tested/)).toHaveLength(3);
  });

  it('"With critical" keeps a project whose critical is only untriaged scanner output', async () => {
    render(<MemoryRouter><PortfolioDashboard /></MemoryRouter>);
    await screen.findByText('Confirmed critical');

    const tile = screen.getByText('With critical').closest('button');
    expect(tile).not.toBeNull();
    fireEvent.click(tile!);

    expect(screen.getByText('Confirmed critical')).toBeInTheDocument();
    expect(screen.getByText('Untriaged critical')).toBeInTheDocument();
    expect(screen.queryByText('All judged')).not.toBeInTheDocument();
    // The health tooltip names which representation drove it.
    const untriaged = screen.getByText('Untriaged critical').closest('div')!.parentElement!;
    expect(within(untriaged).getByTitle('3 critical scanner observations not yet judged')).toBeInTheDocument();
  });
});
