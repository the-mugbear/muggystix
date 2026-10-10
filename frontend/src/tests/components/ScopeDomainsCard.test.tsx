import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';

import * as api from '../../services/api';
import { readsOnScreen } from '../helpers/readsOnScreen';
import ScopeDomainsCard from '../../components/ScopeDomainsCard';
import { TooltipProvider } from '../../components/ui/tooltip';

const toastMock = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
const confirmMock = vi.hoisted(() => vi.fn());
vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => [null, confirmMock],
}));
vi.mock('../../services/api', () => ({
  listScopeDomains: vi.fn().mockResolvedValue({
    items: [
      { id: 1, scope_id: 1, domain: 'acme.com', include_subdomains: true, description: null, name_count: 12 },
      { id: 2, scope_id: 1, domain: 'portal.acme.com', include_subdomains: false, description: null, name_count: 0 },
    ],
    total: 2,
    skip: 0,
    limit: 100,
    names_in_scope_total: 12,
  }),
  addScopeDomains: vi.fn(),
  deleteScopeDomain: vi.fn(),
}));

const renderCard = (canEdit = true) =>
  render(
    <TooltipProvider>
      <ScopeDomainsCard scopeId={1} canEdit={canEdit} />
    </TooltipProvider>,
  );

beforeEach(() => {
  confirmMock.mockReset();
  confirmMock.mockResolvedValue(false);
});

// Owner decision 42: removal is in the row's "⋯" menu, as the subnet table's
// delete on the same page — never an icon on the row (style guide §47).
describe('ScopeDomainsCard removing', () => {
  const deleteMock = () => (api as unknown as { deleteScopeDomain: ReturnType<typeof vi.fn> }).deleteScopeDomain;
  const openMenu = (domain: string) => {
    const actions = screen.getByRole('button', { name: `Actions for ${domain}` });
    // Radix opens a menu on pointer-down, or from the keyboard.
    actions.focus();
    fireEvent.keyDown(actions, { key: 'Enter' });
    return screen.findByRole('menuitem', { name: /Remove from scope…/ });
  };

  it('a row has no remove icon; its menu holds "Remove from scope…", which asks first', async () => {
    deleteMock().mockClear();
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Remove .* from scope/ })).toBeNull();

    fireEvent.click(await openMenu('acme.com'));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1));
    expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Remove domain from scope',
      body: expect.stringContaining('acme.com and all subdomains will no longer be in scope'),
      confirmLabel: 'Remove',
    }));
    // Not confirmed: nothing is sent.
    expect(deleteMock()).not.toHaveBeenCalled();
  });

  it('confirmed: that entry is removed and it is said', async () => {
    deleteMock().mockClear();
    deleteMock().mockResolvedValue(undefined);
    confirmMock.mockResolvedValue(true);
    renderCard();
    await waitFor(() => expect(screen.getByText('portal.acme.com')).toBeInTheDocument());
    fireEvent.click(await openMenu('portal.acme.com'));
    await waitFor(() => expect(deleteMock()).toHaveBeenCalledWith(1, 1, 2));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Removed portal.acme.com from scope'));
  });

  it('a reader gets no row menu', async () => {
    renderCard(false);
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Actions for/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Remove/ })).toBeNull();
  });
});

describe('ScopeDomainsCard tooltips', () => {
  it('explains every derived presentation with its own (i)', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());

    // One distinct accessible name per concept — a screen-reader user can
    // tell "names covered" from "match" without opening each.
    for (const label of ['About domain scope', 'About include subdomains', 'About match', 'About names covered', 'About names in scope']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('shows the deduplicated names-in-scope total next to the entry count', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());
    // 12 + 0 per row would also be 12 here; the point is the field is the
    // server's deduplicated figure, not a client-side sum.
    expect(screen.getByText('12 names in scope')).toBeInTheDocument();
  });

  // Radix tooltips do not open under jsdom (no other test opens one either),
  // so the copy itself is not asserted here — only that each concept has its
  // own trigger and that the presentation the copy describes is on screen.
  it('renders the exact-vs-subdomain presentation the tips describe', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());
    expect(screen.getByText('name + subdomains')).toBeInTheDocument();
    expect(screen.getByText('exact name')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
  });
});

describe('ScopeDomainsCard adding (v5.290.0)', () => {
  const addMock = () => (api as unknown as { addScopeDomains: ReturnType<typeof vi.fn> }).addScopeDomains;

  it('names the "Include subdomains" checkbox', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());
    const box = screen.getByRole('checkbox', { name: 'Include subdomains' });
    fireEvent.click(box);
    expect(box).toHaveAttribute('aria-checked', 'true');
  });

  it('explains rejected entries under the field and keeps them to correct', async () => {
    addMock().mockResolvedValue({
      domains: [], total: 2, added: 1, updated: 0, names_in_scope_total: 12,
      invalid: ["'10.0.0.1': is an IP address, not a name"],
    });
    renderCard();
    await waitFor(() => expect(screen.getByText('*.acme.com')).toBeInTheDocument());
    const input = screen.getByLabelText(/^Domain/);
    fireEvent.change(input, { target: { value: 'new.acme.com 10.0.0.1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("Not added — '10.0.0.1': is an IP address, not a name");
    expect(input).toHaveValue('new.acme.com 10.0.0.1');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(toastMock.success).toHaveBeenCalledWith('1 added');
    expect(toastMock.warning).not.toHaveBeenCalled();
  });
});

// Plan B21 (2026-10-10).  The add's answer carries the scope's domains as
// they now stand (the first page, the entry count, the names in scope).  The
// reader sees THAT list — and where the hosts stand against the scope is read
// again, because name-reachable hosts move between states.
describe('ScopeDomainsCard — after an add the list is the server’s', () => {
  const listMock = () => (api as unknown as { listScopeDomains: ReturnType<typeof vi.fn> }).listScopeDomains;
  const addMock = () => (api as unknown as { addScopeDomains: ReturnType<typeof vi.fn> }).addScopeDomains;
  const entry = (id: number, domain: string, name_count = 0, include_subdomains = false) => ({
    id, scope_id: 1, domain, include_subdomains, description: null, created_at: null, name_count,
  });
  const before = [entry(1, 'acme.com', 12, true), entry(2, 'portal.acme.com')];
  const page = (items: unknown[], total: number, names: number, skip = 0) => ({
    items, total, skip, limit: 100, has_more: skip + items.length < total, names_in_scope_total: names,
  });
  const { reread, ReadsOnScreen } = readsOnScreen({ getScopeCoverage: 'coverage', getDefaultScope: 'subnets' });
  const renderWithPage = () => render(
    <TooltipProvider><ReadsOnScreen /><ScopeDomainsCard scopeId={1} /></TooltipProvider>,
  );
  let original: unknown;
  beforeEach(() => {
    original = listMock().getMockImplementation();
    listMock().mockClear();
    addMock().mockReset();
    toastMock.success.mockClear();
    reread.mockClear();
  });
  afterEach(() => { listMock().mockImplementation(original as never); });

  it('shows the entries, the entry count and the names in scope the server answered with', async () => {
    listMock().mockResolvedValueOnce(page(before, 2, 12));
    const after = [before[0], entry(3, 'new.acme.com', 8), before[1]];
    // Any later read of the list would say the same.
    listMock().mockResolvedValue(page(after, 3, 20));
    addMock().mockResolvedValue({ added: 1, updated: 0, invalid: [], domains: after, total: 3, names_in_scope_total: 20 });
    renderWithPage();
    await screen.findByText('portal.acme.com');
    expect(screen.getByText('2 entries')).toBeInTheDocument();

    const input = screen.getByLabelText(/^Domain/);
    fireEvent.change(input, { target: { value: 'new.acme.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(addMock()).toHaveBeenCalledWith(1, 1, [{ domain: 'new.acme.com', include_subdomains: false }]));

    expect(await screen.findByText('new.acme.com')).toBeInTheDocument();
    expect(screen.getByText('3 entries')).toBeInTheDocument();
    expect(screen.getByText('20 names in scope')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
    await waitFor(() => expect(input).toHaveValue(''));
    expect(toastMock.success).toHaveBeenCalledWith('1 added');
    await waitFor(() => expect(reread).toHaveBeenCalledWith('coverage'));
    // The answer was the list: it is not asked for a second time, and the
    // scope's subnets — which a domain does not change — are not read again.
    expect(listMock()).toHaveBeenCalledTimes(1);
    expect(reread).not.toHaveBeenCalledWith('subnets');
  });

  it('shows the server’s list, not the one the page had: an entry someone else added meanwhile is there too', async () => {
    listMock().mockResolvedValueOnce(page(before, 2, 12));
    const after = [before[0], entry(9, 'elsewhere.acme.com'), entry(3, 'new.acme.com'), before[1]];
    addMock().mockResolvedValue({ added: 1, updated: 0, invalid: [], domains: after, total: 4, names_in_scope_total: 12 });
    renderWithPage();
    await screen.findByText('portal.acme.com');
    fireEvent.change(screen.getByLabelText(/^Domain/), { target: { value: 'new.acme.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByText('elsewhere.acme.com')).toBeInTheDocument();
    expect(screen.getByText('4 entries')).toBeInTheDocument();
    expect(listMock()).toHaveBeenCalledTimes(1);
  });

  // A reader who loaded more than the first page keeps what they loaded: the
  // answer is only the first page, so the list is read again, page by page.
  it('with more than one page loaded, the list is read again and nothing the reader loaded is cut', async () => {
    const many = Array.from({ length: 150 }, (_, i) => entry(i + 1, `d${String(i).padStart(3, '0')}.acme.com`));
    const serve = (rows: unknown[]) => async (_p: number, _s: number, opts: { skip?: number } = {}) =>
      page(rows.slice(opts.skip ?? 0, (opts.skip ?? 0) + 100), rows.length, 0, opts.skip ?? 0);
    listMock().mockImplementation(serve(many));
    renderWithPage();
    await screen.findByText('d000.acme.com');
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    await screen.findByText('d149.acme.com');
    const readsBefore = listMock().mock.calls.length;

    const grown = [...many, entry(151, 'zz-new.acme.com')];
    listMock().mockImplementation(serve(grown));
    addMock().mockResolvedValue({
      added: 1, updated: 0, invalid: [], domains: grown.slice(0, 100), total: 151, names_in_scope_total: 0,
    });
    fireEvent.change(screen.getByLabelText(/^Domain/), { target: { value: 'zz-new.acme.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByText('zz-new.acme.com')).toBeInTheDocument();
    expect(screen.getByText('d149.acme.com')).toBeInTheDocument();
    expect(screen.getByText('151 entries')).toBeInTheDocument();
    expect(listMock().mock.calls.length).toBe(readsBefore + 2);
    expect(reread).not.toHaveBeenCalledWith('subnets');
  });

  it('a removal reads the list and the coverage again, and not the scope’s subnets', async () => {
    const deleteMock = (api as unknown as { deleteScopeDomain: ReturnType<typeof vi.fn> }).deleteScopeDomain;
    deleteMock.mockReset();
    deleteMock.mockResolvedValue(undefined);
    confirmMock.mockResolvedValue(true);
    listMock().mockResolvedValueOnce(page(before, 2, 12));
    listMock().mockResolvedValue(page([before[0]], 1, 12));
    renderWithPage();
    await screen.findByText('portal.acme.com');
    const actions = screen.getByRole('button', { name: 'Actions for portal.acme.com' });
    actions.focus();
    fireEvent.keyDown(actions, { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: /Remove from scope…/ }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith(1, 1, 2));
    await waitFor(() => expect(screen.queryByText('portal.acme.com')).toBeNull());
    expect(screen.getByText('1 entry')).toBeInTheDocument();
    await waitFor(() => expect(reread).toHaveBeenCalledWith('coverage'));
    expect(reread).not.toHaveBeenCalledWith('subnets');
  });
});
