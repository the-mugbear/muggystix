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
  findings: sev(), finding_states: { under_investigation: 0, confirmed: 0, closed: 0 },
  findings_false_positive: 0, finding_affected_targets: 0,
  observations: sev(), observations_judged: sev(), observations_unjudged: sev(),
  defect_rate: { critical: 0, high: 0, medium: 0, low: 0 }, last_scan_at: null,
  pending_plan_reviews: 0, blocked_sessions: 0, targets_added: 0, reviews_concluded: 0,
  imports: 0, contributors: 0, attention_reasons: [],
  ...over,
});

const response = {
  window: { start: '2026-08-01', end: '2026-08-30', timezone: 'UTC' },
  generated_at: new Date().toISOString(),
  severity_basis: 'current',
  growth: {
    unit: 'day',
    points: [
      { start: '2026-08-28', targets_added: 0, reviews_concluded: 1, cumulative_targets: 23 },
      { start: '2026-08-29', targets_added: 4, reviews_concluded: 0, cumulative_targets: 27 },
      { start: '2026-08-30', targets_added: 1, reviews_concluded: 2, cumulative_targets: 28 },
    ],
  },
  summary: {
    projects_total: 3, projects_in_progress: 2, projects_complete: 1,
    targets_current: 30, targets_through_end: 28, targets_added: 5,
    targets_tested: 12, targets_in_review: 2, targets_reviewed: 10,
    reviews_concluded: 4, imports: 6, contributors: 3, unattributed_events: 0,
    severity: {
      findings: sev(2, 5, 1, 0), finding_states: { under_investigation: 3, confirmed: 4, closed: 1 },
      findings_false_positive: 2, finding_affected_targets: 7,
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

    expect(screen.getByText('12 / 30')).toBeInTheDocument();           // targets tested
    // No cards: the measures are one strip, the rest are sections.
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    const severity = screen.getByRole('table', { name: /by severity/ });
    expect(within(severity).getByRole('img', { name: 'High: 20 judged, 70 not yet judged of 90 scanner observations' })).toBeInTheDocument();
    expect(within(severity).getByText('50%')).toBeInTheDocument();      // high defect rate
    // 5.270.1 — the scanner-observation total has its own column beside Findings.
    const highRow = within(severity).getByText('High').closest('tr')!;
    expect(within(highRow).getAllByRole('cell')[2]).toHaveTextContent(/^90$/);
    expect(within(severity).getAllByRole('columnheader')[2]).toHaveTextContent('Scanner observations');
    // Growth: the readout shows the latest bucket until the pointer moves.
    expect(screen.getByText(/2026-08-30/, { selector: '#growth-readout span' })).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: /^Recorded targets \(cumulative\): 28/ })).toHaveLength(1);
    expect(screen.getByText(/9 registered, 8 enabled, 1 disabled/)).toBeInTheDocument();
    // The in-progress preview excludes the completed project.
    const preview = screen.getByRole('table', { name: /in progress/ });
    expect(within(preview).queryByText('Closed')).not.toBeInTheDocument();
    expect(within(preview).getByText('No project admin')).toBeInTheDocument();
  });

  it('an attention count opens the Projects tab filtered to it', async () => {
    await renderPage();
    fireEvent.click(screen.getByText(/without a project admin/).closest('button')!);
    const table = await screen.findByRole('table', { name: 'All projects in the cohort' });
    expect(within(table).getByText('Orphaned')).toBeInTheDocument();
    expect(within(table).queryByText('Closed')).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 3 projects/)).toBeInTheDocument();
  });
});

// The projects table says what each number counts: the total scanner
// observations with their judged split, where each finding stands, and the
// share of tested hosts with a finding — no "Defect" jargon.
describe('Oversight — projects table columns', () => {
  const detailed = project({
    id: 4, name: 'Detailed',
    findings: sev(2, 3, 1, 0),
    finding_states: { under_investigation: 2, confirmed: 3, closed: 1 },
    findings_false_positive: 2,
    observations: sev(10, 20, 5, 1), observations_judged: sev(4, 12, 5, 1),
    observations_unjudged: sev(6, 8, 0, 0),
    defect_rate: { critical: 25, high: 50, medium: 0, low: 0 },
  });
  beforeEach(() => {
    dashboardMock.mockReset().mockResolvedValue({ ...response, projects: [detailed] });
  });

  const openTable = async () => {
    render(<MemoryRouter initialEntries={['/oversight?tab=projects']}><Oversight /></MemoryRouter>);
    return screen.findByRole('table', { name: 'All projects in the cohort' });
  };

  it('headers name what they count, each with an (i), and never say "defect"', async () => {
    const table = await openTable();
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent);
    expect(headers).toEqual(expect.arrayContaining([
      'Findings and their state', 'Scanner observations', 'Tested hosts with a finding',
    ]));
    expect(within(table).queryByText(/defect/i)).not.toBeInTheDocument();
    // 5.270.1 — seven columns (window and admins under the project), so the
    // last one is not pushed out of view at a normal width.
    expect(headers).toHaveLength(7);
    expect(headers).not.toContain('Window');
    expect(table.className).not.toContain('min-w-[1180px]');
    for (const label of ['findings and their state', 'scanner observations', 'tested hosts with a finding']) {
      expect(within(table).getByRole('button', { name: `About ${label}` })).toBeInTheDocument();
    }
  });

  it('a row shows every finding by state and the total scanner observations with the judged split', async () => {
    await openTable();
    const findings = screen.getByTestId('findings-cell');
    expect(findings).toHaveTextContent('6 findings');
    expect(findings).toHaveTextContent('2 under investigation');
    expect(findings).toHaveTextContent('3 confirmed');
    expect(findings).toHaveTextContent('1 closed');
    expect(findings).toHaveTextContent('+ 2 false positives, not counted');
    const observations = screen.getByTestId('observations-cell');
    expect(observations).toHaveTextContent('36 total');
    expect(observations).toHaveTextContent('22 judged · 14 not yet judged');
    expect(screen.getByText('25% critical')).toBeInTheDocument();
    expect(screen.getByText('50% high')).toBeInTheDocument();
  });
});

describe('Oversight — severity basis and growth keyboard', () => {
  beforeEach(() => { dashboardMock.mockReset().mockResolvedValue(response); });

  it('the basis toggle asks the server for figures first recorded in the period', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'First recorded in the period' }));
    await screen.findByText('Needs attention now');
    const last = dashboardMock.mock.calls[dashboardMock.mock.calls.length - 1][0];
    expect(last.severity_basis).toBe('period');
  });

  it('arrow keys move the growth readout between dates', async () => {
    await renderPage();
    const charts = screen.getByLabelText(/Target growth charts/);
    fireEvent.keyDown(charts, { key: 'ArrowLeft' });
    const readout = document.getElementById('growth-readout')!;
    expect(readout.textContent).toMatch(/2026-08-29 · 27 recorded targets · \+4 first recorded · 0 reviews concluded/);
  });
});
