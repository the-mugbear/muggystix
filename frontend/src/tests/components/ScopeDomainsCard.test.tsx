import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, it, expect, vi } from 'vitest';

import * as api from '../../services/api';
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
