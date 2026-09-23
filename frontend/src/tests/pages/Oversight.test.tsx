/**
 * Oversight (5.258.0) — the administrators' programme dashboard: labelled
 * figures, both severity representations, attention drill-downs that filter
 * the Projects tab, and the tester table.
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const dashboardMock = vi.fn();
vi.mock('../../services/api', () => ({
  getOversightDashboard: (...a: unknown[]) => dashboardMock(...a),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ projects: [], selectProject: vi.fn() }),
}));

import Oversight from '../../pages/Oversight';

const sev = (critical = 0, high = 0, medium = 0, low = 0) => ({ critical, high, medium, low });
const LONG = 'engagement-with-a-very-long-name-'.repeat(6);

const project = (over: Record<string, unknown>) => ({
  id: 1, name: 'P', status: 'active', start_date: null, end_date: null, admins: ['Ada'],
  host_count: 10, hosts_tested: 4, hosts_in_review: 1, hosts_reviewed: 3,
  findings: sev(), finding_affected_targets: 0, observations_unjudged: sev(),
  defect_rate: { critical: 0, high: 0, medium: 0, low: 0 }, last_scan_at: null,
  pending_plan_reviews: 0, blocked_sessions: 0, targets_added: 0, reviews_concluded: 0,
  imports: 0, contributors: 0, attention_reasons: [],
  ...over,
});

const response = {
  window: { start: '2026-08-01', end: '2026-08-30', timezone: 'UTC' },
  generated_at: new Date().toISOString(),
  summary: {
    projects_total: 3, projects_in_progress: 2, projects_complete: 1,
    targets_current: 30, targets_through_end: 28, targets_added: 5,
    targets_tested: 12, targets_in_review: 2, targets_reviewed: 10,
    reviews_concluded: 4, imports: 6, contributors: 3, unattributed_events: 0,
    severity: {
      findings: sev(2, 5, 1, 0), finding_affected_targets: 7,
      observations: sev(40, 90, 10, 3), observations_judged: sev(30, 20, 0, 0),
      observations_unjudged: sev(10, 70, 10, 3), tested_targets: 12,
      defect_targets: sev(3, 6, 1, 0), defect_rate: { critical: 25, high: 50, medium: 8.3, low: 0 },
    },
  },
  attention: {
    critical_projects: 1, pending_approval_plans: 0, blocked_runs: 0,
    no_admin_projects: 1, quiet_projects: 0, no_inventory_projects: 0,
  },
  accounts: { total: 9, enabled: 8, disabled: 1, without_membership: 2 },
  projects: [
    project({ id: 1, name: LONG, findings: sev(2), attention_reasons: ['critical'] }),
    project({ id: 2, name: 'Orphaned', admins: [], attention_reasons: ['no_admin'] }),
    project({ id: 3, name: 'Closed', status: 'completed' }),
  ],
  testers: [{
    user_id: 7, username: 'ana', full_name: 'Ana Tester', is_active: true, active_projects: 2,
    tested: 6, in_review: 1, reviewed: 5, reviewed_in_period: 2, findings: sev(1, 2),
    open_tasks: 3, last_contribution_at: null,
    projects: [{ project_id: 1, project_name: LONG, role: null, tested: 6, in_review: 1, reviewed: 5,
      reviewed_in_period: 2, findings: sev(1, 2) }],
  }],
  project_options: [], tester_options: [],
};

const renderPage = async () => {
  render(<MemoryRouter><Oversight /></MemoryRouter>);
  await screen.findByText('Needs attention now');
};

describe('Oversight', () => {
  beforeEach(() => { dashboardMock.mockReset().mockResolvedValue(response); });

  it('labels every figure and shows both severity representations', async () => {
    await renderPage();
    // Default range is the last 30 UTC days, sent as dates.
    const q = dashboardMock.mock.calls[0][0];
    expect(q.start).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(q.end).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    expect(screen.getByText('12 of 30')).toBeInTheDocument();          // targets tested
    expect(screen.getByText('Selected period')).toBeInTheDocument();    // contributors basis
    const severity = screen.getByRole('table', { name: /by severity/ });
    expect(within(severity).getByText('Findings (issues)')).toBeInTheDocument();
    expect(within(severity).getByText('└ not yet judged')).toBeInTheDocument();
    expect(within(severity).getByText('70')).toBeInTheDocument();       // high not yet judged
    expect(within(severity).getByText('50%')).toBeInTheDocument();      // high defect rate
    expect(screen.getByText(/9 registered, 8 enabled, 1 disabled/)).toBeInTheDocument();
    // The in-progress preview excludes the completed project.
    const preview = screen.getByRole('table', { name: /in progress/ });
    expect(within(preview).queryByText('Closed')).not.toBeInTheDocument();
    expect(within(preview).getByText('No project admin')).toBeInTheDocument();
  });

  it('an attention count opens the Projects tab filtered to it', async () => {
    await renderPage();
    fireEvent.click(screen.getByText(/No project admin · projects/).closest('button')!);
    const table = await screen.findByRole('table', { name: 'All projects in the cohort' });
    expect(within(table).getByText('Orphaned')).toBeInTheDocument();
    expect(within(table).queryByText('Closed')).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 3 projects/)).toBeInTheDocument();
  });
});
