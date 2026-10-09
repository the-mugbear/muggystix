/**
 * The Hosts list's state is the address — under the REAL router (the other
 * Hosts tests stand an address in for it): what the page writes is what it
 * reads back, a restored session is put into the address before anything is
 * fetched, and a navigation to another /hosts address is a new list.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// setupTests replaces useNavigate / useLocation for every file; this one
// needs the router's own.
vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({
  getHosts: vi.fn(),
  getHostFilterData: vi.fn(),
  followHost: vi.fn(),
  unfollowHost: vi.fn(),
  listHostFilterViews: vi.fn(async () => []),
  createHostFilterView: vi.fn(),
  deleteHostFilterView: vi.fn(),
  getProjectDefaultView: vi.fn(async () => null),
  promoteProjectDefaultView: vi.fn(),
  clearProjectDefaultView: vi.fn(),
  getHostQuerySchema: vi.fn(async () => ({ fields: [], examples: [] })),
  validateHostQuery: vi.fn(async () => ({ valid: true, match_count: 3, leaf_count: 1 })),
  listHostQueryHistory: vi.fn(async () => []),
  recordHostQuery: vi.fn(async () => ({ id: 1, q: 'x', result_count: 0, created_at: '2026-06-05T00:00:00Z' })),
  deleteHostQuery: vi.fn(),
  clearHostQueryHistory: vi.fn(),
  suggestHostQueryValues: vi.fn(async (field: string) => ({ field, supported: false, values: [] })),
  listHostTags: vi.fn(async () => []),
  listProjectMembers: vi.fn(async () => []),
  getMatchingHostIds: vi.fn(),
  bulkTagHosts: vi.fn(),
  bulkAssignHosts: vi.fn(),
  bulkUnassignHosts: vi.fn(),
  bulkFollowHosts: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));
vi.mock('../../components/HostInspector', () => ({ __esModule: true, default: () => null }));
vi.mock('../../components/InventoryDownloadDialog', () => ({ __esModule: true, default: () => null }));
vi.mock('../../components/ToolReadyOutput', () => ({ __esModule: true, default: () => null }));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import Hosts from '../../pages/Hosts';
import { projectScopedKey } from '../../utils/scopedStorage';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const host = (id: number) => ({
  id, ip_address: `10.0.0.${id}`, hostname: `host-${id}.internal`, state: 'up', os_name: 'Linux',
  ports: [], vulnerability_summary: { total_vulnerabilities: 0, critical: 0, high: 0, medium: 0, low: 0, info: 0 },
  follow: null, notes: [], note_count: 0, discoveries: [],
});

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/hosts', element: <Hosts /> }, { path: '/elsewhere', element: <p>elsewhere</p> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
/** The list's own table.  The page skeleton — what is on screen until the
 *  first read is in — is a table too, and the rows arrive a tick after the
 *  request resolves (they used to land before the first look). */
const findHostsTable = () => waitFor(() => {
  expect(screen.queryByText('Loading table…')).toBeNull();
  return screen.getByRole('table');
});
const asked =() => mocked.getHosts.mock.calls.map(([params]) => params as Record<string, unknown>);
const last = () => asked()[asked().length - 1];

describe('Hosts — the list is its address (real router)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    mocked.getHosts.mockImplementation(async (params: { skip?: number }) => ({
      items: Array.from({ length: 25 }, (_, i) => host((params.skip ?? 0) + i + 1)), total: 120,
      skip: params.skip ?? 0, limit: 25,
    }));
    mocked.getHostFilterData.mockResolvedValue({
      common_ports: [], services: [], operating_systems: [], subnets: [], scans: [],
    });
  });

  it('reads conditions, sort and page from the address it was opened at, and asks once', async () => {
    const router = open('/hosts?has_critical_vulns=true&ports=22,443&sort_by=ip_address&page=3');
    await findHostsTable();
    expect(last()).toMatchObject({
      has_critical_vulns: true, ports: '22,443', sort_by: 'ip_address', sort_order: 'asc', skip: 50,
    });
    expect(asked()).toHaveLength(1);
    // Opening it changed nothing in the address.
    expect(router.state.location.search).toBe('?has_critical_vulns=true&ports=22,443&sort_by=ip_address&page=3');
  });

  it('a page change replaces the address, and the list read back from it is the one asked for', async () => {
    const user = userEvent.setup({ skipHover: true });
    const router = open('/hosts?has_critical_vulns=true');
    await findHostsTable();
    await user.click(screen.getByLabelText('Next page'));
    await waitFor(() => expect(last()).toMatchObject({ has_critical_vulns: true, skip: 25 }));
    expect(router.state.location.search).toContain('has_critical_vulns=true');
    expect(router.state.location.search).toContain('page=2');
    expect(router.state.historyAction).toBe('REPLACE');
    // One request per state: the write did not echo back as another change.
    expect(asked().map((p) => p.skip)).toEqual([0, 25]);
  });

  it('a restored session goes into the address first: the unfiltered list is never asked for', async () => {
    sessionStorage.setItem(
      projectScopedKey('hostFiltersState'),
      JSON.stringify({ filters: { hasCriticalVulns: true, services: ['ssh'] }, followFilter: 'all', onlyWithNotes: false }),
    );
    const router = open('/hosts');
    await screen.findByTestId('hosts-restored-notice');
    expect(router.state.location.search).toContain('has_critical_vulns=true');
    expect(router.state.location.search).toContain('services=ssh');
    expect(asked().length).toBeGreaterThan(0);
    for (const params of asked()) expect(params).toMatchObject({ has_critical_vulns: true, services: 'ssh' });
  });

  it('a navigation to another /hosts address is a new list; Back is the one before', async () => {
    const router = open('/hosts?has_critical_vulns=true&page=2');
    await findHostsTable();
    await act(async () => { await router.navigate('/hosts?q=port%3A22'); });
    await waitFor(() => expect(last()).toMatchObject({ q: 'port:22', skip: 0 }));
    expect(last().has_critical_vulns).toBeUndefined();
    // Nothing puts the previous filters back over it.
    await new Promise((resolve) => setTimeout(resolve, 450));
    expect(router.state.location.search).toBe('?q=port%3A22');

    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(last()).toMatchObject({ has_critical_vulns: true, skip: 25 }));
    expect(last().q).toBeUndefined();
  });
});
