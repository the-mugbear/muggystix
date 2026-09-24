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
    user_id: 7, username: 'ana', full_name: 'Ana Tester', is_active: true, projects_tested: 2,
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

    expect(screen.getByText('12 / 30')).toBeInTheDocument();           // hosts taken into review
    // No cards: the measures are one strip, the rest are sections.
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    const severity = screen.getByRole('table', { name: /by severity/ });
    expect(within(severity).getByRole('img', { name: 'High: 20 judged, 70 not yet judged of 90 scanner observations' })).toBeInTheDocument();
    expect(within(severity).getByText('50%')).toBeInTheDocument();      // high defect rate
    // 5.270.1 — the scanner-observation total has its own column beside Findings.
    const highRow = within(severity).getByText('High').closest('tr')!;
    expect(within(highRow).getAllByRole('cell')[2]).toHaveTextContent(/^90$/);
    expect(within(severity).getAllByRole('columnheader')[2]).toHaveTextContent('Scanner observations');
    // Growth: the readout shows the latest bucket until the pointer moves, in
    // the one date format (v5.294.0 — it printed "2026-08-30").
    const lastDay = new Date(2026, 7, 30).toLocaleDateString(undefined, { dateStyle: 'medium' });
    expect(screen.getByText(lastDay, { selector: '#growth-readout span' })).toBeInTheDocument();
    expect(screen.queryByText(/2026-08-30/, { selector: '#growth-readout span' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: /^Recorded hosts \(cumulative\): 28/ })).toHaveLength(1);
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

describe('Oversight — wording a reader can reconcile', () => {
  beforeEach(() => { dashboardMock.mockReset().mockResolvedValue(response); });

  it('every attention count agrees with its noun, and the phrases are complete', async () => {
    dashboardMock.mockReset().mockResolvedValue({
      ...response,
      attention: {
        critical_projects: 1, pending_approval_plans: 1, blocked_runs: 2,
        no_admin_projects: 0, quiet_projects: 1, no_inventory_projects: 3,
      },
    });
    await renderPage();
    const text = (code: string) => screen.getByTestId(`attention-${code}`).textContent;
    expect(text('critical')).toBe('1 project with a critical finding or critical scanner output not yet judged');
    expect(text('pending_review')).toBe('1 plan awaiting approval');
    expect(text('blocked_session')).toBe('2 runs blocked');
    expect(text('no_admin')).toBe('0 projects without a project admin');
    // Project activity, never the age of evidence.
    expect(text('quiet')).toBe('1 active project with no import in 14 days');
    expect(text('no_data')).toBe('3 projects with no inventory');
    expect(screen.queryByText(/quiet/i)).not.toBeInTheDocument();
  });

  it('measure captions wrap instead of being cut off, and agree with their numbers', async () => {
    dashboardMock.mockReset().mockResolvedValue({
      ...response,
      summary: { ...response.summary, reviews_concluded: 1, imports: 1, unattributed_events: 1 },
    });
    await renderPage();
    const tested = screen.getByText(/1 review concluded in the period/);
    expect(tested.className).not.toContain('truncate');
    const contributors = screen.getByText(/1 scan imported · 1 action with no recorded author · 1 tester/);
    expect(contributors.className).not.toContain('truncate');
  });

  it('labels hosts taken into review and contributors so neither is mistaken for another count', async () => {
    await renderPage();
    // v5.294.0 — one vocabulary with Posture and Portfolio: hosts in review or
    // reviewed are "taken into review", never "tested" (a recorded test result
    // elsewhere), and the unit is the host, not the "target". The label was
    // "Targets tested (in review or reviewed)" and was cut off.
    // The measure's label (the projects preview has a column of the same name).
    const label = screen.getAllByText('Taken into review').find((el) => el.closest('p')?.className.includes('text-caption'));
    expect(label).toBeDefined();
    expect(label).toHaveClass('truncate'); // PostureMeasure's rule — short enough now to fit
    expect(screen.getByText(/12 of 30 hosts taken into review \(40%\)/)).toBeInTheDocument();
    expect(screen.getByText('Recorded hosts')).toBeInTheDocument();
    expect(screen.queryByText(/targets? tested/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\btargets?\b/i)).not.toBeInTheDocument();
    // 3 contributors vs 1 tester: the page says they are different sets.
    expect(screen.getByText('Contributors in the period')).toBeInTheDocument();
    expect(screen.getByText(/6 scans imported · 1 tester \(hosts in review or reviewed\)/)).toBeInTheDocument();
    expect(screen.getByText(/not the same set as Contributors in the period/)).toBeInTheDocument();
  });

  it('sits on the standard page gutter and prints the period in the one date format', async () => {
    await renderPage();
    // p-md alone put the title 8px left of every other page's.
    expect(screen.getByRole('heading', { level: 1, name: 'Oversight' }).closest('.space-y-md')).toHaveClass('md:p-lg');
    expect(screen.queryByText(/Period: \d{4}-\d{2}-\d{2}/)).not.toBeInTheDocument();
  });

  it('the in-progress preview says how many it shows when it leaves projects out', async () => {
    const many = Array.from({ length: 6 }, (_, i) => project({ id: 10 + i, name: `Proj ${i}` }));
    dashboardMock.mockReset().mockResolvedValue({
      ...response, projects: [...many, project({ id: 99, name: 'Closed', status: 'completed' })],
    });
    await renderPage();
    expect(screen.getByTestId('in-progress-shown')).toHaveTextContent('5 of 6 shown');
    const preview = screen.getByRole('table', { name: /in progress/ });
    expect(within(preview).getAllByRole('row')).toHaveLength(6); // header + 5
  });

  it('no "N of M shown" when the preview holds everything', async () => {
    await renderPage();
    expect(screen.queryByTestId('in-progress-shown')).not.toBeInTheDocument();
    expect(screen.queryByTestId('testers-shown')).not.toBeInTheDocument();
  });

  it('the tester preview says how many it shows, and sits in no card', async () => {
    const tester = response.testers[0];
    const testers = Array.from({ length: 7 }, (_, i) => ({ ...tester, user_id: 100 + i, username: `t${i}`, full_name: null }));
    dashboardMock.mockReset().mockResolvedValue({ ...response, testers });
    await renderPage();
    expect(screen.getByTestId('testers-shown')).toHaveTextContent('5 of 7 shown');
    const box = screen.getAllByTestId('testers-table')[0];
    expect(box.className).not.toContain('rounded-panel');
    // No full name: the username stands in.
    expect(within(box).getByText('t0')).toBeInTheDocument();
  });

  it('shows people by full name', async () => {
    await renderPage();
    const box = screen.getAllByTestId('testers-table')[0];
    expect(within(box).getByText('Ana Tester')).toBeInTheDocument();
    expect(within(box).queryByText('ana')).not.toBeInTheDocument();
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
      'Taken into review', 'Findings and their state', 'Scanner observations', 'Taken into review, with a finding',
    ]));
    expect(within(table).queryByText(/defect/i)).not.toBeInTheDocument();
    // 5.270.1 — seven columns (window and admins under the project), so the
    // last one is not pushed out of view at a normal width. v5.294.0 — and no
    // minimum width at all: 960px scrolled 28px at a 1246px viewport.
    expect(headers).toHaveLength(7);
    expect(headers).not.toContain('Window');
    expect(table.className).not.toMatch(/min-w-/);
    expect(table.closest('.overflow-x-auto')).toBeNull();
    for (const label of ['findings and their state', 'scanner observations', 'taken into review, with a finding']) {
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
    const charts = screen.getByLabelText(/Host growth charts/);
    fireEvent.keyDown(charts, { key: 'ArrowLeft' });
    const readout = document.getElementById('growth-readout')!;
    const day = new Date(2026, 7, 29).toLocaleDateString(undefined, { dateStyle: 'medium' });
    expect(readout.textContent).toContain(`${day} · 27 recorded hosts · +4 first recorded · 0 reviews concluded`);
  });

  // v5.273.0 — the notebook's metrics, copied for an email or a chat.
  it('copies a summary of the figures on the page', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Copy summary/ }));
    const dialog = await screen.findByRole('dialog');
    const text = (within(dialog).getByLabelText('Summary to copy') as HTMLTextAreaElement).value;
    expect(text).toMatch(/Projects: 3 \(2 in progress, 1 complete\)/);
    // v5.294.0 — the page's words: hosts, and "taken into review", not "tested".
    expect(text).toMatch(/Hosts: 30 recorded; 12 taken into review \(40%\) — 2 in review, 10 reviewed/);
    expect(text).toMatch(/Findings: 8 \(critical 2, high 5, medium 1, low 0\) on 7 hosts/);
    // v5.289.0 — reads naturally, and says nothing when there are none.
    expect(text).toMatch(/closed \(2 false positives excluded\)/);
    expect(text).not.toMatch(/not counted/);
    expect(text).toMatch(/Defect rate \(hosts taken into review with a finding, of 12\): critical 25%, high 50%, medium 8.3%, low 0%/);
    expect(text).toMatch(/Scanner observations not yet judged: critical 10, high 70 \(of 143 observations\)/);
    // In-progress projects first; the completed one last.
    expect(text.indexOf('Orphaned (in progress)')).toBeLessThan(text.indexOf('Closed (complete)'));
    expect(text).toMatch(/Ana Tester: 6 hosts taken into review, 5 reviewed \(2 in the period\) across 2 projects/);
    expect(text).not.toMatch(/\btargets?\b/i);
    expect(text).not.toMatch(/\btested\b/);

    fireEvent.click(within(dialog).getByRole('checkbox', { name: /Per tester/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Copy' }));
    await within(dialog).findByRole('button', { name: 'Copied' });
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).not.toMatch(/Per tester/);
    expect(copied).toMatch(/Per project \(3\)/);
  });

  it('Markdown puts the per-project figures in a table that a name cannot break', async () => {
    const { buildOversightSummary } = await import('../../utils/oversightSummary');
    const data = { ...response, projects: [project({ name: 'A | B', findings: sev(1, 2, 3, 4) })] };
    const md = buildOversightSummary(data as never, {
      format: 'markdown', includeProjects: true, includeTesters: false, periodLabel: 'all time', filterLabels: ['Tester: Ana'],
    });
    expect(md).toMatch(/^\*\*Security testing update — all time\*\*/);
    expect(md).toMatch(/Tester: Ana · figures as of/);
    expect(md).toContain('| A \\| B | in progress | 4 of 10 (40%) | C 1 / H 2 / M 3 / L 4 | 0% / 0% |');
  });

  it('no false positives: the findings line ends at the states, and one is singular', async () => {
    const { buildOversightSummary } = await import('../../utils/oversightSummary');
    const opts = { format: 'text' as const, includeProjects: false, includeTesters: true, periodLabel: 'all time', filterLabels: [] };
    const withFp = (fp: number) => ({
      ...response, summary: { ...response.summary, severity: { ...response.summary.severity, findings_false_positive: fp } },
    });
    const none = buildOversightSummary(withFp(0) as never, opts);
    expect(none).not.toMatch(/\d+ false positives?/);
    expect(none).toMatch(/\d+ closed\n/);
    expect(buildOversightSummary(withFp(1) as never, opts)).toMatch(/closed \(1 false positive excluded\)/);
    // The tester's project count is where they test, never a 0 beside reviews.
    expect(none).toMatch(/Ana Tester: .* across 2 projects/);
  });
});
