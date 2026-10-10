/**
 * Scope — the subnet table is ONE page at a time, its number in the address,
 * under the REAL router (setupTests replaces `useNavigate` / `useLocation`
 * for every other file).
 *
 * It was a "Load more" list (200 a read), and every change to the scope
 * re-read each loaded page in turn: after 6,000 subnets one edit was thirty
 * requests.  A page costs one request, whatever page it is.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

const scopeMock = vi.fn();
const addMock = vi.fn();
const bulkApplyMock = vi.fn();
vi.mock('../../services/api', () => ({
  getDefaultScope: (...a: unknown[]) => scopeMock(...a),
  getScopeCoverage: vi.fn(async () => ({
    total_scopes: 1, total_subnets: 120, total_domains: 0, name_reachable_hosts: 0, total_hosts: 0,
    scoped_hosts: 0, out_of_scope_hosts: 0, coverage_percentage: 0, has_scope_configuration: true,
    recent_out_of_scope_hosts: [],
  })),
  uploadSubnetFile: vi.fn(),
  addScopeSubnets: (...a: unknown[]) => addMock(...a),
  updateSubnet: vi.fn(),
  deleteSubnet: vi.fn(),
  listSubnetLabels: vi.fn(async () => [
    { id: 7, project_id: 1, name: 'PCI', color: null, created_at: '2026-10-01T00:00:00Z', subnet_count: 0, host_count: 0 },
  ]),
  bulkApplySubnetLabel: (...a: unknown[]) => bulkApplyMock(...a),
  listScopeDomains: vi.fn(async () => ({ items: [], total: 0, skip: 0, limit: 100, names_in_scope_total: 0 })),
  addScopeDomains: vi.fn(),
  deleteScopeDomain: vi.fn(),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: 'analyst' } }),
}));
const toastMock = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
vi.mock('../../hooks/useMyAssistSessions', () => ({
  useMyAssistSessions: () => ({ sessions: [], loading: false, failed: false, refresh: vi.fn() }),
}));
vi.mock('../../components/ScopeExport', () => ({ default: () => null }));
vi.mock('../../components/OutOfScopeExport', () => ({ default: () => null }));
vi.mock('../../components/SiteManagerDialog', () => ({ default: () => null }));
vi.mock('../../components/SubnetLabelManager', () => ({
  SubnetLabelManagerDialog: () => null,
  SubnetLabelEditorPopover: ({ children }: { children: React.ReactNode }) => children,
  SubnetLabelChip: ({ label }: { label: { name: string } }) => <span>{label.name}</span>,
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import Scopes from '../../pages/Scopes';
import { TooltipProvider } from '../../components/ui/tooltip';

/** 120 subnets: pages of 50, 50 and 20.  Every other one is a lab (60: 50 and 10). */
const TOTAL = 120;
const all = Array.from({ length: TOTAL }, (_, i) => ({
  id: i + 1, cidr: `10.9.${i}.0/24`, description: i % 2 === 0 ? 'lab' : 'office',
  site: null, host_count: 0, labels: [], created_at: '2026-09-22T00:00:00Z',
}));
const labs = all.filter((s) => s.description === 'lab');
type Asked = { subnetsSkip: number; subnetsLimit: number; subnetsSearch: string };
const serve = async (_projectId: number, { subnetsSkip, subnetsLimit, subnetsSearch }: Asked) => {
  const matching = subnetsSearch ? all.filter((s) => s.description.includes(subnetsSearch)) : all;
  return { id: 5, subnets_total: matching.length, subnets: matching.slice(subnetsSkip, subnetsSkip + subnetsLimit) };
};
/** [skip, limit, search] of each read of the subnets — every one asked of project 1. */
const asked = () => scopeMock.mock.calls.map(([projectId, opts]) => {
  expect(projectId).toBe(1);
  const { subnetsSkip, subnetsLimit, subnetsSearch } = opts as Asked;
  return [subnetsSkip, subnetsLimit, subnetsSearch];
});

const settle = async (ms = 450) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/scopes', element: <TooltipProvider><Scopes /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
const next = () => screen.getByRole('button', { name: 'Next 50 subnets' });
/** A subnet's row, when it is on screen (its link to its hosts). */
const row = (i: number) => screen.queryByRole('link', { name: all[i].cidr });
const tick = (cidr: string) => fireEvent.click(screen.getByLabelText(`Select ${cidr} for bulk label apply`));

beforeEach(() => {
  vi.clearAllMocks();
  scopeMock.mockImplementation(serve);
});

describe('Scope — the page of subnets lives in the address (real router)', () => {
  it('a reload on page 3 shows page 3, and asks for it alone', async () => {
    const router = open('/scopes?page=3');
    expect(await screen.findByText('101–120 of 120')).toBeInTheDocument();
    expect(screen.getByText('page 3 of 3')).toBeInTheDocument();
    expect(row(100)).toBeInTheDocument();
    expect(row(0)).toBeNull();
    expect(asked()).toEqual([[100, 50, '']]);
    await settle();
    expect(router.state.location.search).toBe('?page=3');
    expect(asked()).toEqual([[100, 50, '']]);
  });

  it('Next writes the address (replace) and shows that page only — no "Load more"', async () => {
    const router = open('/scopes');
    expect(await screen.findByText('1–50 of 120')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull();

    fireEvent.click(next());
    expect(await screen.findByText('51–100 of 120')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=2');
    expect(router.state.historyAction).toBe('REPLACE');
    // The rows of the page before are not kept under the new ones.
    await waitFor(() => expect(row(50)).toBeInTheDocument());
    expect(row(0)).toBeNull();
    expect(asked()).toEqual([[0, 50, ''], [50, 50, '']]);
  });

  // The regression this list was moved for: a change re-read every page the
  // reader had loaded.
  it('a change to the scope re-reads the ONE page on screen, however far the reader went', async () => {
    open('/scopes');
    await screen.findByText('1–50 of 120');
    fireEvent.click(next());
    await waitFor(() => expect(row(50)).toBeInTheDocument());
    fireEvent.click(next());
    await waitFor(() => expect(row(100)).toBeInTheDocument());
    expect(asked()).toHaveLength(3);
    scopeMock.mockClear();

    addMock.mockResolvedValue([]);
    fireEvent.change(screen.getByLabelText('CIDR or IP'), { target: { value: '10.8.8.0/24' } });
    fireEvent.click(screen.getAllByRole('button', { name: /^Add$/ })[0]);
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Added 10.8.8.0/24.'));
    // The write names the project and no scope.
    expect(addMock).toHaveBeenCalledWith(1, [{ cidr: '10.8.8.0/24', description: undefined }]);
    await waitFor(() => expect(asked()).toEqual([[100, 50, '']]));
    await settle(100);
    expect(asked()).toEqual([[100, 50, '']]);
  });

  it('a search starts from its first page, in one request, and takes the page out of the address', async () => {
    const router = open('/scopes?page=2');
    await screen.findByText('51–100 of 120');
    await waitFor(() => expect(row(50)).toBeInTheDocument());
    scopeMock.mockClear();

    fireEvent.change(screen.getByLabelText('Search subnets by CIDR or description'), { target: { value: 'lab' } });
    expect(await screen.findByText('1–50 of 60')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?subnet_q=lab');
    await settle(100);
    expect(asked()).toEqual([[0, 50, 'lab']]);

    fireEvent.click(next());
    expect(await screen.findByText('51–60 of 60')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?subnet_q=lab&page=2');
  });

  it('a page past the end steps back to the last page that exists', async () => {
    const router = open('/scopes?page=40');
    expect(await screen.findByText('101–120 of 120')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=3');
    expect(screen.queryByText(/No entries in this project's scope yet/)).toBeNull();
  });
});

// The ticked subnets are kept from page to page of a search — as they were
// kept while more rows were loaded — and the bar says how many are not on
// screen, because "Apply label…" reaches those too.  They never outlive the
// search they were ticked under.
describe('Scope — the selection for "Apply label…" across pages', () => {
  const bar = () => screen.getByText(/subnets? selected/);
  const header = () => screen.getByLabelText('Select all subnets on this page for bulk label apply');

  it('spans the pages of a search, says what is not on this page, and applies to all of it', async () => {
    open('/scopes?subnet_q=lab');
    await screen.findByText('1–50 of 60');
    tick(labs[0].cidr);
    expect(bar()).toHaveTextContent(/^1 subnet selected$/);

    fireEvent.click(next());
    await screen.findByText('51–60 of 60');
    await screen.findByLabelText(`Select ${labs[50].cidr} for bulk label apply`);
    // Nothing on this page is ticked; the one from the first page still is.
    expect(header()).toHaveAttribute('aria-checked', 'false');
    expect(bar()).toHaveTextContent('1 subnet selected · 1 not on this page');

    // The header box is THIS page's 10 rows; the earlier tick stays.
    fireEvent.click(header());
    expect(header()).toHaveAttribute('aria-checked', 'true');
    expect(bar()).toHaveTextContent('11 subnets selected · 1 not on this page');

    bulkApplyMock.mockResolvedValue({});
    const apply = screen.getByRole('button', { name: /Apply label…/ });
    apply.focus();
    fireEvent.keyDown(apply, { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'PCI' }));
    await waitFor(() => expect(bulkApplyMock).toHaveBeenCalledTimes(1));
    const [projectId, labelId, ids] = bulkApplyMock.mock.calls[0] as [number, number, number[]];
    expect([projectId, labelId]).toEqual([1, 7]);
    expect([...ids].sort((a, b) => a - b)).toEqual([labs[0].id, ...labs.slice(50).map((s) => s.id)]);
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Applied "PCI" to 11 subnets.'));
    // Applied: nothing is left ticked.
    await waitFor(() => expect(screen.queryByText(/subnets? selected/)).toBeNull());
  });

  it('un-ticking the header box clears this page only', async () => {
    open('/scopes?subnet_q=lab');
    await screen.findByText('1–50 of 60');
    tick(labs[0].cidr);
    fireEvent.click(next());
    await screen.findByLabelText(`Select ${labs[50].cidr} for bulk label apply`);
    fireEvent.click(header());
    expect(header()).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(header());
    expect(header()).toHaveAttribute('aria-checked', 'false');
    expect(bar()).toHaveTextContent('1 subnet selected · 1 not on this page');
  });

  it('a new search starts with nothing ticked: a bulk action never reaches a row the search hides', async () => {
    open('/scopes');
    await screen.findByText('1–50 of 120');
    // An office subnet, which the search for "lab" does not list.
    tick(all[1].cidr);
    expect(bar()).toHaveTextContent(/^1 subnet selected$/);

    fireEvent.change(screen.getByLabelText('Search subnets by CIDR or description'), { target: { value: 'lab' } });
    await screen.findByText('1–50 of 60');
    expect(screen.queryByText(/subnets? selected/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Apply label…/ })).toBeNull();
    expect(header()).toHaveAttribute('aria-checked', 'false');
  });
});
