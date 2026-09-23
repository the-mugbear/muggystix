/**
 * Posture overview (5.254.0) — the conclusion, the four-measure strip, the
 * ranked "Where to focus" comparison and the decisions list, rendered from one
 * realistic response. Worst-case strings included: nothing may be hover-only.
 */
import { render, screen, within, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
  p: () => '/api/v1/projects/1',
  setCurrentProjectId: vi.fn(),
  getCurrentProjectId: () => 1,
}));
const getPostureMock = vi.fn();
// The barrel, as the page imports it; the link builder is the real one.
vi.mock('../../services/api', async () => {
  const insights = await vi.importActual<typeof import('../../services/api/insights')>('../../services/api/insights');
  return {
    getPosture: (...a: unknown[]) => getPostureMock(...a),
    gridCellHostsHref: insights.gridCellHostsHref,
    downloadSystemicReport: vi.fn(),
  };
});
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P' } }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import SecurityPosture from '../../pages/SecurityPosture';
import { TooltipProvider } from '../../components/ui/tooltip';

const LONG = 'Branch-East-'.repeat(18);   // ~216 chars: must truncate, never widen the page

const cell = (segment: string, site: string | null, affected: number, assessed: number, in_scope: number, eligible = in_scope) => ({
  segment, affected, assessed, in_scope, eligible, eligible_assessed: assessed, unassessed: assessed === 0,
  value: assessed ? affected / assessed : 0, numerator: affected, denominator: assessed,
  drilldown_filter: { conditions: ['eol_os'], site },
});

const response = {
  label: 'action_required',
  conclusion: { text: '3 critical/high findings active', tone: 'negative' },
  reasons: [
    { text: '3 critical/high findings active', severity: 'critical' },
    { text: 'Only 40% of hosts reviewed', severity: 'medium' },
  ],
  headline: {
    active_exposure: { active_findings: 7, by_severity: { critical: 1, high: 2, medium: 3, low: 1, info: 0 } },
    review_coverage: { reviewed: 48, total: 120, pct: 40, validated_hosts: 5 },
    ownership: { owned: 5, unowned: 2, total: 7, pct: 71 },
    systemic: { adopted: true, blind_spot_count: 1, condition_count: 4 },
    detected_exposure: { vuln_count: 431 },
    open_questions: { needs_evidence_hosts: 6 },
  },
  evidence: { scan_count: 9, scan_staleness_days: 2 },
  priorities: [
    { kind: 'exposure', tier: 'action', title: '3 critical/high findings active', blast_radius: '3 of 7 active findings · 1 critical',
      action: 'Confirm the evidence and how each is reported', severity: 'critical', owner: null, link: '/findings?status=active', score: 93 },
    { kind: 'ownership', tier: 'work', title: '2 active findings unassigned (1 critical/high)', blast_radius: '2 of 7 active findings',
      action: 'Assign an analyst', severity: 'high', owner: null, link: '/findings?status=active&owner=unowned', score: 61 },
  ],
  decisions: { pending_approvals: 2, blocked_sessions: 0 },
  sites: { adopted: true, items: [] },
  systemic: { adopted: true, estate: { hosts_in_scope: 111, subnets: 4, sites: 3, blind_spot_count: 1 }, conditions: [], blind_spots: [] },
  disposition: { by_status: { open: 4, confirmed: 3, false_positive: 2 }, by_status_severity: {}, active_total: 7, scanner_active: 4, non_scanner_active: 3 },
  heatmap: {
    segments: [
      { key: '1', label: LONG, in_scope: 36, assessed: 36 },
      { key: '2', label: 'Core', in_scope: 170, assessed: 170 },
      { key: '3', label: 'North', in_scope: 25, assessed: 25 },
    ],
    rows: [{
      family: 'lifecycle_patching', family_label: 'Lifecycle & patching', conditions: ['eol_os'],
      evidence_domain: 'os_detection', evidence_domain_label: 'OS identification', affected_total: 36,
      cells: [cell('2', 'Core', 16, 160, 170), cell('1', LONG, 18, 30, 36), cell('3', 'North', 2, 2, 25)],
    }],
  },
};

const renderPage = async () => {
  render(<MemoryRouter><TooltipProvider><SecurityPosture /></TooltipProvider></MemoryRouter>);
  await screen.findByText('Where to focus');
};

describe('SecurityPosture — overview', () => {
  beforeEach(() => { getPostureMock.mockReset().mockResolvedValue(response); });

  it('leads with the conclusion and says what it rests on', async () => {
    await renderPage();
    expect(screen.getByText('Action required')).toBeInTheDocument();
    // The conclusion is the first reason; only the OTHER reasons are listed under it.
    expect(screen.getAllByText('3 critical/high findings active').length).toBe(2); // conclusion + decisions row
    expect(screen.getByText('Only 40% of hosts reviewed')).toBeInTheDocument();
    expect(screen.getByText(/48 of 120 hosts reviewed/)).toBeInTheDocument();
    expect(screen.getByText(/111 of 120 hosts inside scoped subnets/)).toBeInTheDocument();
  });

  it('shows four measures, each opening the set it counts — and no remediation or ownership tile', async () => {
    await renderPage();
    expect(screen.getByRole('link', { name: /3 critical or high active findings/ })).toHaveAttribute('href', '/findings?status=active');
    expect(screen.getByText(/431 scanner observations/)).toBeInTheDocument();
    const needs = screen.getByRole('link', { name: /6 hosts still needing evidence/ });
    expect(new URL(needs.getAttribute('href')!, 'http://x').searchParams.get('q')).toBe('conclusion:needs_evidence');
    expect(screen.queryByText(/Remediated|Reopened|Ownership/)).toBeNull();
  });

  it('ranks the disproportionate segment first and explains the selected row in a sentence', async () => {
    await renderPage();
    const focus = screen.getByText('Where to focus').closest('section')!;
    const rows = within(focus).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.getAttribute('data-state'))).toEqual(['comparable', 'comparable', 'limited']);
    expect(within(rows[0]).getByText('18/30 · 60%')).toBeInTheDocument();
    expect(within(rows[0]).getByText('30 of 36 eligible')).toBeInTheDocument();
    expect(within(rows[0]).getByText('6 still unknown')).toBeInTheDocument();
    expect(within(rows[2]).getByText(/Limited comparison — only 2 assessed/)).toBeInTheDocument();
    // The sentence names the comparator honestly: the rest of the assessed project.
    expect(within(focus).getByText(/18 of 30 assessed hosts affected \(60%\), against 18 of 162 \(11%\) across the rest of the assessed project — 49 points higher\./)).toBeInTheDocument();

    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Core' }));
    expect(within(focus).getByText(/Core: 16 of 160 assessed hosts affected \(10%\)/)).toBeInTheDocument();
    expect(within(focus).queryByText(/points higher/)).toBeNull();
  });

  it('marks an unassigned finding as assessment work and keeps operations out of the list', async () => {
    await renderPage();
    const decisions = screen.getByText('Decisions for this review').closest('section')!;
    expect(within(decisions).getByText(/Assessment work — does not change the condition/)).toBeInTheDocument();
    expect(within(decisions).getByRole('link', { name: /2 plans to approve in Operations/ })).toHaveAttribute('href', '/operations');
  });

  // From the first real screenshot of this page: a project with no sites put
  // every host in one "Unassigned" segment — a ranking of one.
  it('calls a single segment a measurement, not a comparison', async () => {
    const only = { ...response.heatmap.rows[0], cells: [cell('unassigned', null, 4, 10, 59, 21)] };
    getPostureMock.mockResolvedValue({
      ...response,
      heatmap: { group_by: 'site', segments: [{ key: 'unassigned', label: 'Unassigned', in_scope: 59, assessed: 59 }], rows: [only] },
    });
    await renderPage();
    const focus = screen.getByText('Where to focus').closest('section')!;
    expect(within(focus).getByText(/nothing to compare it\s+with — this is a measurement, not a ranking/)).toBeInTheDocument();
    expect(within(focus).queryByText(/marks the rate across the rest/)).toBeNull();
    // The reason is the explanation — it wraps, it is never cut off.
    const reason = within(focus).getByText(/Limited comparison — 48% of eligible hosts assessed/, { selector: 'p.text-warning' });
    expect(reason.className).not.toMatch(/truncate/);
  });

  it('groups by subnet when no site is defined, and a parent subnet link excludes its nested child', async () => {
    const sub = (key: string, cidr: string, affected: number, assessed: number, exclude: string[] = []) => ({
      ...cell(key, null, affected, assessed, assessed + 2),
      drilldown_filter: { conditions: ['eol_os'], site: null, subnet: cidr, exclude_subnets: exclude },
    });
    getPostureMock.mockResolvedValue({
      ...response,
      heatmap: {
        group_by: 'subnet',
        segments: [
          { key: 'subnet:1', label: '10.7.0.0/24', in_scope: 42, assessed: 42 },
          { key: 'subnet:2', label: '10.8.0.0/24', in_scope: 32, assessed: 32 },
        ],
        rows: [{ ...response.heatmap.rows[0], cells: [sub('subnet:1', '10.7.0.0/24', 20, 40, ['10.7.0.0/28']), sub('subnet:2', '10.8.0.0/24', 3, 30)] }],
      },
    });
    await renderPage();
    const focus = screen.getByText('Where to focus').closest('section')!;
    expect(within(focus).getByRole('columnheader', { name: 'Subnet' })).toBeInTheDocument();
    expect(within(focus).getByText(/No sites are defined, so hosts are grouped by their most-specific subnet/)).toBeInTheDocument();
    const url = new URL(within(focus).getByRole('link', { name: /20 affected hosts/ }).getAttribute('href')!, 'http://x');
    expect(url.searchParams.get('subnets')).toBe('10.7.0.0/24');
    expect(url.searchParams.get('q')).toBe('has:eol AND NOT subnet:"10.7.0.0/28"');
    expect(screen.getByText(/Every family × subnet/)).toBeInTheDocument();
    // Two columns: the grid is sized to them, not stretched across the page.
    const grid = screen.getByText(/Every family × subnet/).closest('section')!.querySelector('table')!;
    // 18rem for the family column + 9rem per column (jsdom folds the calc()).
    expect(grid.style.width).toBe('min(100%, 36rem)');
  });

  it('truncates a 200-character site name instead of widening the page', async () => {
    await renderPage();
    const focus = screen.getByText('Where to focus').closest('section')!;
    const button = within(focus).getByRole('button', { name: LONG });
    expect(button.className).toMatch(/truncate/);
    expect(focus.querySelector('table')!.style.tableLayout).toBe('fixed');
  });
});
