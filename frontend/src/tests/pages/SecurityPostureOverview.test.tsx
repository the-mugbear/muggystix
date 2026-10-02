/**
 * Posture overview (5.254.0) — the conclusion, the four-measure strip, the
 * ranked "Where to focus" comparison and the decisions list, rendered from one
 * realistic response. Worst-case strings included: nothing may be hover-only.
 */
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
  p: () => '/api/v1/projects/1',
  setCurrentProjectId: vi.fn(),
  getCurrentProjectId: () => 1,
}));
const getPostureMock = vi.fn();
// 5.330.0 — the two sections that moved here from Operations load for
// themselves: the address terrain, and scanner observations + scope.
const getAddressTerrainMock = vi.fn();
const getDashboardStatsMock = vi.fn();
const getProjectCoverageMock = vi.fn();
// The barrel, as the page imports it; the link builder is the real one.
vi.mock('../../services/api', async () => {
  const insights = await vi.importActual<typeof import('../../services/api/insights')>('../../services/api/insights');
  return {
    getPosture: (...a: unknown[]) => getPostureMock(...a),
    getAddressTerrain: (...a: unknown[]) => getAddressTerrainMock(...a),
    getDashboardStats: (...a: unknown[]) => getDashboardStatsMock(...a),
    getProjectCoverage: (...a: unknown[]) => getProjectCoverageMock(...a),
    gridCellHostsHref: insights.gridCellHostsHref,
    downloadSystemicReport: vi.fn(),
  };
});
// The 3D scene is three.js in its own chunk: loading it is what "Show the
// map" costs, so the page can be shown not to ask for it.
const sceneLoads = vi.hoisted(() => ({ n: 0 }));
vi.mock('../../components/operations/TerrainScene', () => {
  sceneLoads.n += 1;
  return { default: () => null };
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

const LONG_CIDR = '2001:0db8:85a3:0000:0000:8a2e:0370:7334/64';
const terrain = {
  blocks: [
    { cidr: '10.0.0.0/24', hosts: 250, tested: 3, planned: 25, worked: 30, untouched: 192, critical: 15, critical_untouched: 12 },
    { cidr: LONG_CIDR, hosts: 40, tested: 0, planned: 0, worked: 0, untouched: 40, critical: 0, critical_untouched: 0 },
  ],
  total_hosts: 290, unplaced_hosts: 0, truncated: false,
};
const stats = {
  total_scans: 9, total_hosts: 142, total_ports: 0, up_hosts: 0, open_ports: 0, total_subnets: 0,
  recent_scans: [], subnet_stats: [],
  vulnerability_stats: {
    critical: 9, high: 154, medium: 20, low: 4, info: 900, hosts_with_vulnerabilities: 130,
    hosts_by_severity: { critical: 7, high: 122, medium: 15, low: 4 },
  },
};
const coverage = {
  project_id: 1, total_hosts: 142, total_scopes: 1, scopes: [],
  hosts_in_subnet_scope: 128, hosts_name_scope_only: 2, hosts_outside_scope: 12,
};

describe('SecurityPosture — overview', () => {
  beforeEach(() => {
    getPostureMock.mockReset().mockResolvedValue(response);
    getAddressTerrainMock.mockReset().mockResolvedValue(terrain);
    getDashboardStatsMock.mockReset().mockResolvedValue(stats);
    getProjectCoverageMock.mockReset().mockResolvedValue(coverage);
    try { localStorage.removeItem('nm.operations.terrainOpen'); } catch { /* no storage */ }
  });

  // 5.330.0 — project status that was on Operations.
  describe('what moved here from Operations', () => {
    const q = (el: HTMLElement) => new URL(el.getAttribute('href') ?? '', 'http://x').searchParams.get('q');

    it('the sections, in order: the terrain and the scanner/scope section after the grid, before the findings', async () => {
      await renderPage();
      await screen.findByText(/The team has reached/);
      await screen.findByText('Scope');
      const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent ?? '');
      const at = (re: RegExp) => headings.findIndex((h) => re.test(h));
      expect(at(/^Where to focus/)).toBeGreaterThanOrEqual(0);
      expect(at(/^Every family ×/)).toBeGreaterThan(at(/^Decisions for this review/));
      expect(at(/^Where the team has been/)).toBe(at(/^Every family ×/) + 1);
      expect(at(/^Scanner observations and scope/)).toBe(at(/^Where the team has been/) + 1);
      expect(at(/^Promoted findings/)).toBe(at(/^Scanner observations and scope/) + 1);
      // Sections, not cards.
      expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    });

    it('“Where the team has been”: the sentence and the hot block; the map stays closed and unfetched', async () => {
      await renderPage();
      const section = (await screen.findByText(/The team has reached/)).closest('section')!;
      expect(within(section).getByText(/The team has reached/)).toHaveTextContent('The team has reached 58 of 290 hosts (3 tested)');
      expect(q(within(section).getByRole('link', { name: /12 untouched hosts carry a critical scanner observation/ })))
        .toBe('has:untouched has:critical');
      const hot = within(section).getByRole('complementary', { name: 'Most untouched critical exposure' });
      expect(q(within(hot).getByRole('link', { name: 'Open 250 hosts' }))).toBe('subnet:"10.0.0.0/24"');
      // Closed by default, and three.js was not asked for.  (jsdom has no
      // WebGL, so the control offers the table.)
      expect(within(section).getByRole('button', { name: /Show the (map|table)/ })).toHaveAttribute('aria-expanded', 'false');
      expect(within(section).queryByRole('table')).not.toBeInTheDocument();
      expect(sceneLoads.n).toBe(0);
      expect(getAddressTerrainMock).toHaveBeenCalledTimes(1);
      // Nothing in it points back at Operations.
      for (const a of within(section).getAllByRole('link')) expect(a.getAttribute('href')).not.toMatch(/operations/);
    });

    it('a long block name truncates in the opened table and never widens the page', async () => {
      await renderPage();
      const section = (await screen.findByText(/The team has reached/)).closest('section')!;
      fireEvent.click(within(section).getByRole('button', { name: /Show the (map|table)/ }));
      const table = await within(section).findByRole('table');
      expect(table).toHaveClass('table-fixed');
      expect(within(table).getByTitle(LONG_CIDR)).toHaveClass('truncate');
    });

    it('scanner observations by severity and the three scope states, each opening its list', async () => {
      await renderPage();
      const section = (await screen.findByRole('heading', { name: 'Scanner observations and scope' })).closest('section')!;
      expect(await within(section).findByRole('heading', { name: /Scanner observations by severity/, level: 3 })).toBeInTheDocument();
      expect(within(section).getByText(/187 observations, informational excluded/)).toBeInTheDocument();
      const scopeLine = within(section).getByText('Scope').parentElement as HTMLElement;
      expect(q(within(scopeLine).getByRole('link', { name: /128 in scope subnets/ }))).toBe('scope:subnet');
      expect(q(within(scopeLine).getByRole('link', { name: /12 outside scope/ }))).toBe('scope:none');
      // It is ON Posture now: no "…are on Posture →" pointer back to itself.
      expect(within(section).queryByRole('link', { name: /Posture/ })).not.toBeInTheDocument();
    });

    it('a failed terrain or count says so and leaves the rest of the page', async () => {
      getAddressTerrainMock.mockRejectedValue(new Error('503'));
      getDashboardStatsMock.mockRejectedValue(new Error('stats down'));
      await renderPage();
      expect(await screen.findByText('The address map could not be loaded.')).toBeInTheDocument();
      expect(await screen.findByText(/Scanner observations could not be counted — this is not a clean project/)).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /^Promoted findings/ })).toBeInTheDocument();
      expect(screen.getByText('Action required')).toBeInTheDocument();
    });

    it('the page Refresh reloads both', async () => {
      await renderPage();
      await screen.findByText(/The team has reached/);
      await screen.findByText('Scope');
      fireEvent.click(screen.getByRole('button', { name: /Refresh posture/i }));
      await waitFor(() => expect(getAddressTerrainMock).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(getDashboardStatsMock).toHaveBeenCalledTimes(2));
      expect(getProjectCoverageMock).toHaveBeenCalledTimes(2);
    });
  });

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

  it('marks an unassigned finding as assessment work and carries nothing about agent runs', async () => {
    await renderPage();
    const decisions = screen.getByText('Decisions for this review').closest('section')!;
    expect(within(decisions).getByText(/Assessment work — does not change the condition/)).toBeInTheDocument();
    // 5.320.0 — there are no execution runs, so nothing here counts blocked ones.
    expect(within(decisions).queryByText(/blocked run/)).not.toBeInTheDocument();
    expect(within(decisions).queryByRole('link', { name: /Operations/ })).not.toBeInTheDocument();
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

  it("a site cell's link excludes another site's subnet nested inside it", async () => {
    const { gridCellHostsHref } = await vi.importActual<typeof import('../../services/api/insights')>('../../services/api/insights');
    const siteCell = {
      segment: '4', drilldown_filter: { conditions: ['eol_os'], site: 'Campus', exclude_subnets: ['10.9.5.0/24'] },
    };
    const url = new URL(gridCellHostsHref(['eol_os'], siteCell)!, 'http://x');
    expect(url.searchParams.get('sites')).toBe('Campus');
    expect(url.searchParams.get('subnets')).toBeNull();
    expect(url.searchParams.get('q')).toBe('has:eol AND NOT subnet:"10.9.5.0/24"');
  });

  it('truncates a 200-character site name instead of widening the page', async () => {
    await renderPage();
    const focus = screen.getByText('Where to focus').closest('section')!;
    const button = within(focus).getByRole('button', { name: LONG });
    expect(button.className).toMatch(/truncate/);
    expect(focus.querySelector('table')!.style.tableLayout).toBe('fixed');
  });
});
