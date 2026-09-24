import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  // The bulk bar (shown once a row is checked) loads its tag / member lists.
  listHostTags: vi.fn(async () => []),
  listProjectMembers: vi.fn(async () => []),
  getMatchingHostIds: vi.fn(),
  bulkTagHosts: vi.fn(),
  bulkAssignHosts: vi.fn(),
  bulkUnassignHosts: vi.fn(),
  bulkFollowHosts: vi.fn(),
  // Project-scope helpers — needed because anything that imports from
  // ``../services/api`` (now a barrel re-exporting per-domain
  // submodules) may transitively touch them.  v2.29.0.
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

// One shared toast object (the global mock hands out fresh spies per call), so
// a test can read what was announced and press a toast's action.
const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), dismiss: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', async () => ({
  ...(await vi.importActual<typeof import('../../contexts/ToastContext')>('../../contexts/ToastContext')),
  useToast: () => toastMock,
}));

// The inspector's body fetches a whole host; the page tests only need to know
// WHICH host is open.
vi.mock('../../components/HostInspector', () => ({
  __esModule: true,
  default: ({ hostId }: { hostId: number }) => <div data-testid="host-inspector">host {hostId}</div>,
}));

vi.mock('../../components/ReportsDialog', () => ({
  __esModule: true,
  default: ({ open }: { open: boolean }) => <div data-testid="reports-dialog" data-open={String(open)} />,
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

  // A "service ftp" filter listed hosts whose rows never said FTP: the
  // Exposure chips are the risk-ranked services, which need not include it.
  it('names the matching endpoint on each row only while a service filter is applied', async () => {
    const ftpHosts = [
      makeHost(1, {
        ports: [
          { id: 11, port_number: 21, protocol: 'tcp', state: 'open', service_name: 'ftp', service_product: null, service_version: null },
          { id: 12, port_number: 23, protocol: 'tcp', state: 'open', service_name: 'telnet', service_product: null, service_version: null },
        ],
      }),
    ];
    mockedApi.getHosts.mockImplementation(async () => ({ items: ftpHosts, total: 1, skip: 0, limit: 25 }));
    routerState.search = '?services=ftp';
    const { unmount } = renderHosts();
    const match = await screen.findByTestId('endpoint-match');
    expect(match).toHaveTextContent('ftp 21/tcp');
    expect(match).not.toHaveTextContent('telnet');
    unmount();

    // The page restores its last filters from session storage; start clean.
    sessionStorage.clear();
    routerState.search = '';
    renderHosts();
    await screen.findByText('10.0.0.1');
    expect(screen.queryByTestId('endpoint-match')).toBeNull();
  });

  it('explains the state dot beside each IP', async () => {
    renderHosts();
    await screen.findAllByText('10.0.0.5');
    expect(screen.getAllByRole('img', { name: /^State: up/ }).length).toBeGreaterThan(0);
    expect(screen.getByTestId('hosts-legend')).toHaveTextContent(/state unknown/);
  });

  it('keeps both exports secondary and the query placeholder short', async () => {
    renderHosts();
    await screen.findAllByText('10.0.0.5');
    for (const name of [/Export targets/, /Export hosts/]) {
      expect(screen.getByRole('button', { name })).toHaveClass('border');
    }
    const placeholder = screen.getByRole('combobox', { name: 'Host query' }).getAttribute('placeholder') ?? '';
    expect(placeholder.length).toBeLessThanOrEqual(60);
  });

  it('opens the export tray when the URL carries ?reports=1 (report-finished deep link)', async () => {
    routerState.search = '?reports=1&job=7';
    renderHosts();
    await waitFor(() => expect(screen.getByTestId('reports-dialog')).toHaveAttribute('data-open', 'true'));
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

  // 5.250.0 — "+ Add filter": catalog → one field's editor → Apply.  Nothing is
  // requested until the condition is applied, and two severities are ONE reload.
  it('adds a condition through the filter catalog and refetches once it is applied', async () => {
    const user = userEvent.setup({ skipHover: true });
    renderHosts();

    await screen.findByRole('heading', { level: 1, name: 'Hosts' });
    await user.click(screen.getByRole('button', { name: /Add filter/i }));
    await user.click(await screen.findByRole('button', { name: /Scanner severity/ }));
    const callsBefore = mockedApi.getHosts.mock.calls.length;
    await user.click(screen.getByRole('checkbox', { name: 'Critical' }));
    await user.click(screen.getByRole('checkbox', { name: 'High' }));
    expect(mockedApi.getHosts.mock.calls.length).toBe(callsBefore);
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));

    await waitFor(() => {
      expect(mockedApi.getHosts).toHaveBeenLastCalledWith(
        expect.objectContaining({ has_critical_vulns: true, has_high_vulns: true, skip: 0, limit: 25 }),
        expect.anything(),
      );
    });
    expect(mockedApi.getHosts.mock.calls.length).toBe(callsBefore + 1);
    // One chip for the one condition, and its label reopens the same editor.
    await user.click(screen.getByRole('button', { name: 'Scanner severity: Critical or High' }));
    expect(await screen.findByRole('checkbox', { name: 'Critical' })).toBeChecked();
  });

  // 5.249.0 — the whole-view presets live in the View picker and REPLACE the
  // applied filters, exactly as a saved view does.
  it('a built-in view replaces the applied filters and names itself in the picker', async () => {
    const user = userEvent.setup({ skipHover: true });
    routerState.search = '?ports=8080';
    renderHosts();

    await screen.findByRole('heading', { level: 1, name: 'Hosts' });
    await user.click(await screen.findByRole('button', { name: /^View: Custom filters/ }));
    await user.click(await screen.findByRole('menuitem', { name: /Critical observations/ }));

    await waitFor(() => {
      const calls = mockedApi.getHosts.mock.calls;
      const lastParams = calls[calls.length - 1][0];
      expect(lastParams).toMatchObject({ has_critical_vulns: true });
      expect(lastParams.ports).toBeUndefined();
    });
    expect(screen.getByRole('button', { name: 'View: Critical observations' })).toBeInTheDocument();
  });

  // The project default is usually a colleague's view — not in THIS user's
  // saved list — so once the filters were cleared nothing led back to it.
  it('the project default stays reachable after the filters are cleared', async () => {
    const user = userEvent.setup({ skipHover: true });
    mockedApi.getProjectDefaultView.mockResolvedValue({
      id: 9, name: 'Web tier', filter_json: { filters: { ports: ['443'] } },
      is_project_default: true, created_at: '2026-09-01T00:00:00Z', updated_at: null,
    });
    const lastParams = () => {
      const calls = mockedApi.getHosts.mock.calls;
      return calls[calls.length - 1][0];
    };
    renderHosts();

    await screen.findByText(/Project default view applied/);
    await waitFor(() => expect(lastParams()).toMatchObject({ ports: '443' }));

    await user.click(screen.getByRole('button', { name: 'Show all hosts' }));
    await waitFor(() => expect(lastParams().ports).toBeUndefined());
    expect(screen.queryByText(/Project default view applied/)).not.toBeInTheDocument();

    // The way back: the picker offers it, marked…
    await user.click(screen.getByRole('button', { name: /^View: All hosts/ }));
    expect(await screen.findByRole('menuitem', { name: /Web tier.*project default/ })).toBeInTheDocument();
    await user.keyboard('{Escape}');

    // …and one click beside the conditions restores it, the clearing being undone for the session.
    await user.click(await screen.findByRole('button', { name: 'Back to default view' }));
    await waitFor(() => expect(lastParams()).toMatchObject({ ports: '443' }));
    expect(await screen.findByText(/Project default view applied/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back to default view' })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(projectScopedKey('projectDefaultDismissed'))).toBeNull();
  });

  it('forwards a command-bar query as the q param to getHosts', async () => {
    const user = userEvent.setup({ skipHover: true });
    renderHosts();

    await screen.findByRole('heading', { level: 1, name: 'Hosts' });
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

  it('renders the inventory table (desktop-only product — no mobile card stack)', async () => {
    // The mobile-card layout was deleted; the table is the sole renderer and
    // narrow widths scroll horizontally.  Assert the table exists and host
    // data reaches it.
    renderHosts();

    await screen.findByRole('heading', { level: 1, name: 'Hosts' });
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getAllByText('10.0.0.20').length).toBeGreaterThan(0);
  });

  it('renders the host identity as a link to the standalone host route', async () => {
    // The identity is an <a href="/hosts/:id">, not a <button>, so
    // cmd/ctrl/middle-click open a new tab the way operators expect in a
    // triage list.  A plain click is intercepted and opens the side sheet
    // instead (see useHostColumns) — that path is covered by the
    // open-inspector assertions elsewhere in this file.
    renderHosts();

    await screen.findByRole('heading', { level: 1, name: 'Hosts' });
    const opener = screen.getAllByRole('link', { name: /Open host inspector for 10\.0\.0\.20/ })[0];
    expect(opener).toHaveAttribute('href', expect.stringContaining('/hosts/'));
  });

  it('has no per-row expand control', async () => {
    // The expandable sub-row was removed in v4.46.0 — it predated the side
    // sheet, duplicated most of the collapsed row, and gave every row a
    // third competing affordance.  Guard against it creeping back.
    renderHosts();

    await screen.findByRole('heading', { level: 1, name: 'Hosts' });
    expect(screen.queryByRole('button', { name: /Expand host details/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Collapse host details/i })).toBeNull();
  });

  // ── 5.251.0 — investigation flow ─────────────────────────────────────────
  const applyCriticalFilter = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole('button', { name: /Add filter/i }));
    await user.click(await screen.findByRole('button', { name: /Scanner severity/ }));
    await user.click(screen.getByRole('checkbox', { name: 'Critical' }));
    await user.click(screen.getByRole('button', { name: 'Apply condition' }));
  };

  it('removing a condition is immediate and offers Undo, which restores it', async () => {
    const user = userEvent.setup({ skipHover: true });
    routerState.search = '?ports=8080&sites=East';
    renderHosts();

    await user.click(await screen.findByRole('button', { name: 'Clear filter: Endpoint: port 8080' }));
    await waitFor(() => expect(screen.queryByText('Endpoint: port 8080')).not.toBeInTheDocument());
    expect(screen.getByText('Site: East')).toBeInTheDocument();

    const [message, options] = toastMock.info.mock.calls[toastMock.info.mock.calls.length - 1];
    expect(message).toBe('Removed: Endpoint: port 8080');
    expect(options.action.label).toBe('Undo');
    act(() => options.action.onClick());
    expect(await screen.findByText('Endpoint: port 8080')).toBeInTheDocument();
  });

  it('keeps the open host when a filter takes it out of the rows shown, says so, and Next starts from the top', async () => {
    const user = userEvent.setup({ skipHover: true });
    renderHosts();

    // alpha (host 1) has no critical observation — the filter below drops it.
    // The table re-renders once when the facet data lands, and a node found
    // before that is detached by the time it is clicked (a browser user cannot
    // click a detached node).  Query and click together until it takes.
    await waitFor(() => {
      fireEvent.click(screen.getByRole('link', { name: /Open host inspector for 10\.0\.0\.5 \(alpha\.internal\)/ }));
      expect(screen.getByTestId('host-inspector')).toBeInTheDocument();
    });
    expect(await screen.findByTestId('host-inspector')).toHaveTextContent('host 1');
    expect(screen.getByText(/of 30 in this queue/)).toBeInTheDocument();

    await applyCriticalFilter(user);

    expect(await screen.findByText('outside the rows shown')).toBeInTheDocument();
    expect(screen.getByTestId('host-inspector')).toHaveTextContent('host 1'); // never switched silently
    expect(screen.getByRole('button', { name: 'Previous host (k)' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Next host (j)' }));
    await waitFor(() => expect(screen.getByTestId('host-inspector')).toHaveTextContent('host 2'));
    expect(screen.getByText(/^1 of \d+ in this queue/)).toBeInTheDocument();
  });

  it('says that a filter change cleared the selection instead of dropping it silently', async () => {
    const user = userEvent.setup({ skipHover: true });
    renderHosts();

    // Same settle-then-click as above: select once the row is the live one.
    await waitFor(() => {
      const box = screen.getByRole('checkbox', { name: 'Select 10.0.0.3' });
      if (box.getAttribute('aria-checked') !== 'true') fireEvent.click(box);
      expect(screen.getByText('1 selected')).toBeInTheDocument();
    });
    await applyCriticalFilter(user);

    await waitFor(() =>
      expect(toastMock.info).toHaveBeenCalledWith(
        expect.stringMatching(/^Selection cleared \(1 host\)/),
        expect.anything(),
      ),
    );
  });
});

// v5.270.0 — the table states things quietly: review state as text (the
// action on hover), the test-workflow state as a word, one attention line with
// the other reasons spelled out, "N open ports", and no card chrome.
describe('Hosts — streamlined table', () => {
  const rows = [
    makeHost(41, {
      ip_address: '10.9.0.41',
      follow: { status: 'in_review' },
      test_plan_entry_count: 2,
      conflict_count: 1,
      vulnerability_summary: { total_vulnerabilities: 3, critical: 2, high: 1, medium: 0, low: 0, info: 0 },
    }),
    makeHost(42, {
      ip_address: '10.9.0.42',
      follow: { status: 'reviewed' },
      test_execution_count: 3,
      vulnerability_summary: { total_vulnerabilities: 0, critical: 0, high: 0, medium: 0, low: 0, info: 0 },
    }),
    makeHost(43, {
      ip_address: '10.9.0.43',
      ports: [{ id: 431, port_number: 445, protocol: 'tcp', state: 'open', service_name: null }],
      vulnerability_summary: { total_vulnerabilities: 0, critical: 0, high: 0, medium: 0, low: 0, info: 0 },
    }),
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getHosts.mockResolvedValue({ items: rows, total: 3, skip: 0, limit: 25, sort_by: 'critical_vulns', sort_order: 'desc' });
    mockedApi.getHostFilterData.mockResolvedValue({ common_ports: [], services: [], operating_systems: [], subnets: [], scans: [] });
    mockedApi.listHostFilterViews.mockResolvedValue([]);
    mockedApi.getProjectDefaultView.mockResolvedValue(null);
    mockedApi.getHostQuerySchema.mockResolvedValue({ fields: [], examples: [] });
    mockedApi.validateHostQuery.mockResolvedValue({ valid: true, match_count: 3, leaf_count: 1 });
    mockedApi.listHostQueryHistory.mockResolvedValue([]);
    sessionStorage.clear();
    routerState.search = '';
  });

  it('states review as text, with the change action as a quiet control', async () => {
    const { container } = renderHosts();
    await screen.findByText('10.9.0.41');
    const states = [...container.querySelectorAll('[data-review-state]')].map((el) => el.textContent);
    expect(states).toEqual(['In review', 'Reviewed', 'Not started']);
    // No filled "Review" chip per row: the menu trigger is a labelled text control.
    expect(screen.getByRole('button', { name: 'Change review for 10.9.0.43' })).toHaveClass('opacity-0');
  });

  it('names the test-workflow state instead of colouring the row border', async () => {
    const { container } = renderHosts();
    await screen.findByText('10.9.0.41');
    expect(screen.getByText('Planned')).toHaveAttribute('title', '2 tests approved but not yet executed');
    expect(screen.getByText('Tested')).toBeInTheDocument();
    expect(container.querySelector('tr.border-l-warning, tr.border-l-info')).toBeNull();
  });

  it('one attention line, the other reasons spelled out rather than "+N"', async () => {
    renderHosts();
    await screen.findByText('10.9.0.41');
    expect(screen.getByText('2 critical')).toBeInTheDocument();
    expect(screen.getByText('1 conflict · 1 high')).toBeInTheDocument();
    expect(screen.queryByText('+2')).toBeNull();
  });

  it('counts open ports in words, explains a guessed service once, and has no card', async () => {
    const { container } = renderHosts();
    await screen.findByText('10.9.0.41');
    expect(screen.getAllByText((_, el) => el?.tagName === 'DIV' && el.textContent === '2 open ports').length).toBeGreaterThan(0);
    expect(screen.getByText(/guessed from its port number/)).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Review status filter' })).toBeInTheDocument();
    expect(container.querySelector('.rounded-panel.border.bg-card')).toBeNull();
  });
});
