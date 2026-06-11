import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import Hosts from '../../pages/Hosts';
import { projectScopedKey } from '../../utils/scopedStorage';

// setupTests.ts globally mocks useLocation to a fixed empty search. Override it
// here with a controllable value so we can exercise the URL-restore path.
const routerState = vi.hoisted(() => ({ search: '' }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ id: '1' }),
    useLocation: () => ({ pathname: '/hosts', search: routerState.search, hash: '', state: null }),
  };
});

// Mock every api function the page imports.  The cleanup pass added
// the three saved-views helpers (listHostFilterViews,
// createHostFilterView, deleteHostFilterView) — without them the page
// crashes on mount when it fires listHostFilterViews().
vi.mock('../../services/api', () => ({
  getHosts: vi.fn(),
  getHostFilterData: vi.fn(),
  followHost: vi.fn(),
  unfollowHost: vi.fn(),
  listHostFilterViews: vi.fn(),
  createHostFilterView: vi.fn(),
  deleteHostFilterView: vi.fn(),
  getProjectDefaultView: vi.fn(),
  promoteProjectDefaultView: vi.fn(),
  clearProjectDefaultView: vi.fn(),
  // v5.0.0 — query-UX helpers the command bar (useQueryAssist) calls.
  getHostQuerySchema: vi.fn(),
  validateHostQuery: vi.fn(),
  listHostQueryHistory: vi.fn(),
  recordHostQuery: vi.fn(),
  deleteHostQuery: vi.fn(),
  clearHostQueryHistory: vi.fn(),
  // Project-scope helpers — needed because anything that imports from
  // ``../services/api`` (now a barrel re-exporting per-domain
  // submodules) may transitively touch them.  v2.29.0.
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

// v4.51.0 — the "Critical" preset chip lives inside HostFilters now
// (it was previously in the Hosts page sticky-bar Quick views row).
// Expose a thin button stub that drives the same onFiltersChange the
// real preset handler would so the existing Critical-quick-view test
// continues to exercise the page-level integration (filter state →
// buildFilterParams → getHosts) without pulling in the real
// HostFilters' combobox/advanced-filter dependencies.
vi.mock('../../components/HostFilters', () => ({
  __esModule: true,
  default: ({ onFiltersChange }: { onFiltersChange: (next: any) => void }) => (
    <div data-testid="host-filters">
      Host Filters
      <button type="button" onClick={() => onFiltersChange({ hasCriticalVulns: true })}>
        Critical
      </button>
    </div>
  ),
}));

vi.mock('../../components/ReportsDialog', () => ({
  __esModule: true,
  default: () => null,
}));

vi.mock('../../components/ToolReadyOutput', () => ({
  __esModule: true,
  default: () => null,
}));

import * as api from '../../services/api';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const makeHost = (id: number, overrides: Record<string, any> = {}) => ({
  id,
  ip_address: `10.0.0.${id}`,
  hostname: `host-${id}.internal`,
  state: 'up',
  os_name: 'Linux',
  ports: [
    { id: id * 10 + 1, port_number: 22, protocol: 'tcp', state: 'open', service_name: 'ssh', service_product: null, service_version: null },
    { id: id * 10 + 2, port_number: 443, protocol: 'tcp', state: 'open', service_name: 'https', service_product: null, service_version: null },
  ],
  vulnerability_summary: {
    total_vulnerabilities: id % 4,
    critical: id % 3 === 0 ? 2 : 0,
    high: id % 2 === 0 ? 1 : 0,
    medium: 0,
    low: 0,
    info: 0,
  },
  follow: null,
  notes: [],
  note_count: 0,
  discoveries: [
    {
      scan_id: id,
      scan_filename: `scan-${id}.xml`,
      scan_type: 'nmap',
      tool_name: 'nmap',
      discovered_at: `2024-01-${String((id % 28) + 1).padStart(2, '0')}T00:00:00Z`,
    },
  ],
  ...overrides,
});

const desktopHosts = [
  makeHost(2, { ip_address: '10.0.0.20', hostname: 'zulu.internal', vulnerability_summary: { total_vulnerabilities: 2, critical: 3, high: 1, medium: 0, low: 0, info: 0 } }),
  makeHost(1, { ip_address: '10.0.0.5', hostname: 'alpha.internal', vulnerability_summary: { total_vulnerabilities: 0, critical: 0, high: 0, medium: 0, low: 0, info: 0 } }),
  ...Array.from({ length: 28 }, (_, index) => makeHost(index + 3)),
];

const buildHostResponse = (params: Record<string, any> = {}) => {
  let items = [...desktopHosts];

  if (params.has_critical_vulns) {
    items = items.filter((host) => (host.vulnerability_summary?.critical ?? 0) > 0);
  }

  const sortBy = params.sort_by ?? 'critical_vulns';
  if (sortBy === 'ip_address') {
    items.sort((a, b) => a.ip_address.localeCompare(b.ip_address, undefined, { numeric: true, sensitivity: 'base' }));
  } else {
    items.sort((a, b) => (b.vulnerability_summary?.critical ?? 0) - (a.vulnerability_summary?.critical ?? 0));
  }

  const skip = params.skip ?? 0;
  const limit = params.limit ?? 25;

  return {
    items: items.slice(skip, skip + limit),
    total: items.length,
    skip,
    limit,
    sort_by: sortBy,
    sort_order: params.sort_order ?? 'desc',
  };
};

const renderHosts = () =>
  render(
    <MemoryRouter>
      <Hosts />
    </MemoryRouter>
  );

describe('Hosts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getHosts.mockImplementation(async (params?: Record<string, any>) => buildHostResponse(params));
    mockedApi.getHostFilterData.mockResolvedValue({
      common_ports: [],
      services: [],
      operating_systems: [],
      subnets: [],
      scans: [],
    });
    mockedApi.followHost.mockResolvedValue({});
    mockedApi.unfollowHost.mockResolvedValue({});
    // Saved-views endpoints — return an empty list by default so the
    // saved-views chip row renders nothing and tests don't have to
    // assert against fixture views.
    mockedApi.listHostFilterViews.mockResolvedValue([]);
    mockedApi.createHostFilterView.mockResolvedValue({
      id: 1,
      name: 'fixture',
      filter_json: {},
      created_at: '2026-04-09T00:00:00Z',
      updated_at: null,
    });
    mockedApi.deleteHostFilterView.mockResolvedValue(undefined);
    // No project default by default — the auto-apply effect resolves to null.
    mockedApi.getProjectDefaultView.mockResolvedValue(null);
    mockedApi.clearProjectDefaultView.mockResolvedValue(undefined);
    // Query-UX defaults: empty schema/history, queries validate clean.
    mockedApi.getHostQuerySchema.mockResolvedValue({
      fields: [
        { name: 'port', aliases: [], value_source: 'port', trgm: false, enum_values: [] },
        { name: 'cve', aliases: [], value_source: 'free', trgm: true, enum_values: [] },
      ],
      examples: [{ label: 'Both ports', q: 'port:80 port:443' }],
    });
    mockedApi.validateHostQuery.mockResolvedValue({ valid: true, match_count: 3, leaf_count: 1 });
    mockedApi.listHostQueryHistory.mockResolvedValue([]);
    mockedApi.recordHostQuery.mockResolvedValue({ id: 1, q: 'port:443', result_count: 3, created_at: '2026-06-05T00:00:00Z' });
    mockedApi.deleteHostQuery.mockResolvedValue(undefined);
    mockedApi.clearHostQueryHistory.mockResolvedValue(undefined);
    sessionStorage.clear();
    routerState.search = '';
  });

  it('fetches hosts and filter data on mount', async () => {
    renderHosts();

    await waitFor(() => {
      expect(mockedApi.getHosts).toHaveBeenCalled();
      expect(mockedApi.getHostFilterData).toHaveBeenCalled();
      expect(mockedApi.listHostFilterViews).toHaveBeenCalled();
    });

    // getHosts now takes (params, AbortSignal) since the page added
    // request cancellation on rapid filter changes.  Match params
    // explicitly and let the signal pass through.
    expect(mockedApi.getHosts).toHaveBeenCalledWith(
      expect.objectContaining({ skip: 0, limit: 25, sort_by: 'critical_vulns', sort_order: 'desc' }),
      expect.anything(),
    );
  });

  it('applies the critical quick view and refetches with matching params', async () => {
    const user = userEvent.setup({ skipHover: true });
    renderHosts();

    await screen.findByText('Discovered Hosts');
    // v5.0.0 — the structured panel (and its Critical preset) now lives
    // behind the "Advanced filters" disclosure; expand it first.
    await user.click(screen.getByRole('button', { name: /Advanced filters/i }));
    await user.click(screen.getByRole('button', { name: 'Critical' }));

    await waitFor(() => {
      expect(mockedApi.getHosts).toHaveBeenLastCalledWith(
        expect.objectContaining({ has_critical_vulns: true, skip: 0, limit: 25 }),
        expect.anything(),
      );
    });
  });

  it('forwards a command-bar query as the q param to getHosts', async () => {
    const user = userEvent.setup({ skipHover: true });
    renderHosts();

    await screen.findByText('Discovered Hosts');
    await user.type(screen.getByLabelText('Host query'), 'port:443');

    await waitFor(
      () => {
        expect(mockedApi.getHosts).toHaveBeenLastCalledWith(
          expect.objectContaining({ q: 'port:443' }),
          expect.anything(),
        );
      },
      { timeout: 2000 },
    );
  });

  it('treats URL params as authoritative over conflicting session filters', async () => {
    // A shared ?q= link must reproduce the sender's set, not inherit the
    // recipient's stale session filter (here: a leftover critical filter).
    sessionStorage.setItem(
      projectScopedKey('hostFiltersState'),
      JSON.stringify({ filters: { hasCriticalVulns: true } }),
    );
    routerState.search = '?q=port%3A443';

    renderHosts();

    const lastCall = () => {
      const calls = mockedApi.getHosts.mock.calls;
      return calls[calls.length - 1]?.[0] as Record<string, any> | undefined;
    };
    await waitFor(() => {
      expect(lastCall()?.q).toBe('port:443');
    });
    const params = lastCall() as Record<string, any>;
    expect(params.q).toBe('port:443');
    // The recipient's seeded session critical-filter must NOT bleed into the shared link.
    expect(params.has_critical_vulns).toBeUndefined();
    // Finding 2: the DSL query is part of the active-filter model — a removable
    // chip is rendered (so the summary/empty-state/save-view treat it as a filter).
    expect(await screen.findByText('Query: port:443')).toBeInTheDocument();
  });

  it('scopes facet (dropdown option) requests to the active filters', async () => {
    // Finding 1: facet requests must carry the same filter context as the table
    // so options/counts agree. The initial (deferred) facet fetch goes through
    // buildFacetParams(), which includes URL-restored filters.
    routerState.search = '?has_critical_vulns=true';
    renderHosts();
    await waitFor(() => expect(mockedApi.getHostFilterData).toHaveBeenCalled());
    const calls = mockedApi.getHostFilterData.mock.calls;
    const lastParams = calls[calls.length - 1]?.[0] as Record<string, any> | undefined;
    expect(lastParams).toMatchObject({ has_critical_vulns: true });
  });

  it('paginates the desktop inventory table and renders the next page', async () => {
    const user = userEvent.setup({ skipHover: true });
    renderHosts();

    await screen.findByRole('table');
    let rows = within(screen.getByRole('table')).getAllByRole('row');
    // First body row should be the largest-critical host (`10.0.0.20`).
    expect(within(rows[1]).getByText('10.0.0.20')).toBeInTheDocument();

    await user.click(screen.getByLabelText('Next page'));

    await waitFor(() => {
      expect(mockedApi.getHosts).toHaveBeenLastCalledWith(
        expect.objectContaining({ skip: 25, limit: 25, sort_by: 'critical_vulns', sort_order: 'desc' }),
        expect.anything(),
      );
    });
  });

  it('renders both mobile cards and the desktop table — CSS picks the right one at runtime', async () => {
    // The page no longer reads `useMediaQuery`; the mobile-card stack
    // is `md:hidden` and the desktop DataTable is `hidden md:block`.
    // In jsdom both are present; assert each renders so we don't
    // regress to a JS-driven layout swap.
    renderHosts();

    await screen.findByText('Discovered Hosts');
    expect(screen.getByRole('table')).toBeInTheDocument();
    // Mobile cards render IP addresses inside <button> elements with
    // hosts' IPs — at least one should be present.
    expect(screen.getAllByText('10.0.0.20').length).toBeGreaterThan(0);
  });
});
