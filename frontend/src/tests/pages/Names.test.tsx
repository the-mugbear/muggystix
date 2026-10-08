import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { formatTimestamp } from '../../utils/relativeTime';

vi.mock('../../services/api', () => ({
  listNames: vi.fn(),
  getNamesSummary: vi.fn(),
  getName: vi.fn(),
  importNames: vi.fn(),
  deleteName: vi.fn(),
  exportNames: vi.fn(),
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
let role = 'analyst';
const LEVEL: Record<string, number> = { admin: 100, analyst: 60, auditor: 40, viewer: 20 };
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    // A member account: the PROJECT role below decides (review 2026-10-01 R32).
    user: { id: 1, username: 'tester', role: 'member' },
    hasPermission: (r: string) => (LEVEL[role] ?? 0) >= (LEVEL[r] ?? 0),
  }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: role } }),
}));

import * as api from '../../services/api';
import Names from '../../pages/Names';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const row = {
  id: 1, fqdn: 'portal.acme.com', kind: 'fqdn', in_scope: true, first_seen: null, last_seen: null,
  current_addresses: [], previous_address_count: 0, evidence: {}, imported: true, resolved: false,
};

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Names />
    </MemoryRouter>,
  );

describe('Names page export', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    role = 'analyst';
    mocked.listNames.mockResolvedValue({ items: [row], total: 1, skip: 0, limit: 50 });
    mocked.getNamesSummary.mockResolvedValue({ total: 1, unresolved: 1, resolved: 0, in_scope: 1, wildcards: 0 });
    mocked.exportNames.mockResolvedValue(undefined);
  });

  it('exports the CURRENT filtered list (state + search) in the chosen format', async () => {
    renderAt('/names?state=in_scope&search=portal');
    await waitFor(() => expect(screen.getByText('portal.acme.com')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Export names as text' }));
    await waitFor(() => expect(mocked.exportNames).toHaveBeenCalledTimes(1));
    expect(mocked.exportNames).toHaveBeenCalledWith('txt', { search: 'portal', state: 'in_scope' });

    fireEvent.click(screen.getByRole('button', { name: 'Export names as CSV' }));
    await waitFor(() => expect(mocked.exportNames).toHaveBeenCalledTimes(2));
    expect(mocked.exportNames).toHaveBeenLastCalledWith('csv', { search: 'portal', state: 'in_scope' });
  });

  it('hides export from viewers (server enforces AUDITOR+; this is the affordance)', async () => {
    role = 'viewer';
    renderAt('/names');
    await waitFor(() => expect(screen.getByText('portal.acme.com')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Export names as text' })).toBeNull();
    expect(screen.queryByRole('button', { name: /import names/i })).toBeNull();
  });

  it('lets an auditor export but not import (the project role, not the account role)', async () => {
    role = 'auditor';
    renderAt('/names');
    await waitFor(() => expect(screen.getByText('portal.acme.com')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Export names as text' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /import names/i })).toBeNull();
  });

  // R34 — the chips used to lose their counts silently.
  it('says the counts could not be loaded, and retries', async () => {
    mocked.getNamesSummary.mockRejectedValueOnce(new Error('down'));
    renderAt('/names');
    expect(await screen.findByText('The counts could not be loaded.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText('The counts could not be loaded.')).toBeNull());
    expect(mocked.getNamesSummary).toHaveBeenCalledTimes(2);
  });

  it('surfaces an export failure as a toast (export)', async () => {
    mocked.exportNames.mockRejectedValueOnce(new Error('nope'));
    renderAt('/names');
    await waitFor(() => expect(screen.getByText('portal.acme.com')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Export names as text' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
  });
});

describe('Names page — screenshot review (v5.288.0)', () => {
  const resolved = {
    ...row,
    id: 2,
    fqdn: 'portal.example-corp.com',
    in_scope: false,
    last_seen: '2026-09-08T16:16:28Z',
    current_addresses: [
      { ip_address: '2606:2800:220:1:248:1893:25c8:1946', host_id: null, shared_with: 1, first_observed: null, last_observed: null },
      { ip_address: '203.0.113.20', host_id: 5, shared_with: 0, first_observed: null, last_observed: null },
    ],
    previous_address_count: 1,
    evidence: { A: 2, SCANNER: 3, CERT: 1 },
    resolved: true,
  };
  const wildcard = { ...row, id: 3, fqdn: '*.example-corp.com', kind: 'wildcard' };
  const short = { ...row, id: 4, fqdn: 'dc01' };

  beforeEach(() => {
    vi.clearAllMocks();
    role = 'analyst';
    mocked.listNames.mockResolvedValue({ items: [resolved, wildcard, short], total: 3, skip: 0, limit: 100 });
    mocked.getNamesSummary.mockResolvedValue({
      total: 3, unresolved: 2, resolved: 1, in_scope: 2, wildcards: 1, shared_addresses: 4, shared_names: 6,
    });
  });

  it('never truncates an address; the previous/shared chips sit on their own line', async () => {
    renderAt('/names');
    const v6 = await screen.findByText('2606:2800:220:1:248:1893:25c8:1946');
    expect(v6.closest('.truncate')).toBeNull();
    expect(screen.getByText('203.0.113.20').closest('.truncate')).toBeNull();
    const prev = screen.getByText('+1 previous');
    expect(prev.closest('li')).toBeNull();
    expect(prev.getAttribute('title')).toMatch(/previously resolved/);
  });

  it('says "Out of scope" (not "no") and highlights "In scope"', async () => {
    renderAt('/names');
    await screen.findByText('portal.example-corp.com');
    const table = screen.getByRole('table');
    expect(within(table).queryByText('no')).toBeNull();
    expect(within(table).getAllByText('Out of scope')).toHaveLength(1);
    expect(within(table).getAllByText('In scope')).toHaveLength(2);
  });

  // UX review 2026-09-24 — lists show how long ago, the exact moment on hover.
  it('shows last seen as a relative age on one line, the exact moment in the title', async () => {
    renderAt('/names');
    await screen.findByText('portal.example-corp.com');
    const iso = '2026-09-08T16:16:28Z';
    const cell = document.querySelector(`time[datetime="${new Date(iso).toISOString()}"]`) as HTMLElement;
    expect(cell).not.toBeNull();
    expect(cell.className).toMatch(/whitespace-nowrap/);
    expect(cell.getAttribute('title')).toBe(formatTimestamp(iso));
    expect(cell.textContent).not.toContain(new Date(iso).toLocaleTimeString());
  });

  it('keeps "Out of scope" on one line', async () => {
    renderAt('/names');
    await screen.findByText('portal.example-corp.com');
    expect(within(screen.getByRole('table')).getByText('Out of scope').className).toMatch(/whitespace-nowrap/);
  });

  it('explains every evidence chip and offers one legend', async () => {
    renderAt('/names');
    await screen.findByText('portal.example-corp.com');
    const scanner = screen.getByText('scanner').closest('[title]')!;
    expect(scanner.getAttribute('title')).toMatch(/scanner .*reported this name.*3 observations recorded/i);
    expect(screen.getByText('A').closest('[title]')!.getAttribute('title')).toMatch(/IPv4.*2 observations/);
    expect(screen.getByRole('button', { name: 'About the evidence chips' })).toBeInTheDocument();
    expect(screen.getByText(/the number beside one counts how many were recorded/)).toBeInTheDocument();
  });

  it('uses one word for wildcards, marks short names, and says "every name"', async () => {
    renderAt('/names');
    await screen.findByText('*.example-corp.com');
    expect(screen.queryByText('pattern')).toBeNull();
    expect(screen.getByRole('button', { name: /^Wildcard\s*1$/ })).toBeInTheDocument();
    expect(within(screen.getByRole('table')).getByText('wildcard')).toBeInTheDocument();
    expect(screen.getByText('short name')).toBeInTheDocument();
    expect(screen.getByText(/Domain names and short host names/)).toBeInTheDocument();
    expect(screen.queryByText(/Every FQDN/)).toBeNull();
  });

  it('puts the shared-address count in its chip (names, as the filter lists)', async () => {
    renderAt('/names');
    await screen.findByText('portal.example-corp.com');
    await waitFor(() => expect(screen.getByRole('button', { name: /^Shared address\s*6$/ })).toBeInTheDocument());
    expect(screen.queryByText(/4 shared addresses/)).toBeNull();
  });

  it('is a section, not a card, with no pager when everything fits and no title icon', async () => {
    renderAt('/names');
    await screen.findByText('portal.example-corp.com');
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Previous' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Next' })).toBeNull();
    expect(screen.queryByText(/Showing 1/)).toBeNull();
    expect(screen.getByRole('heading', { level: 1, name: 'Names' }).querySelector('svg')).toBeNull();
  });

  it('shows the pager once the list spans more than one page', async () => {
    mocked.listNames.mockResolvedValue({ items: [resolved], total: 250, skip: 0, limit: 100 });
    renderAt('/names');
    await screen.findByText('portal.example-corp.com');
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
    expect(screen.getByText('1–100 of 250')).toBeInTheDocument();
  });

  it('opens on the page the address names, and Next asks for the one after it', async () => {
    mocked.listNames.mockResolvedValue({ items: [resolved], total: 250, skip: 100, limit: 100 });
    renderAt('/names?page=2');
    await screen.findByText('portal.example-corp.com');
    expect(mocked.listNames).toHaveBeenCalledTimes(1);
    expect(mocked.listNames).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 100 }));
    expect(screen.getByText('101–200 of 250')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(mocked.listNames).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 200 })));
  });

  // The filter changed first and the page was put back to the first one
  // afterwards: the old page of the new filter was asked for in between.
  it('a filter chosen from a later page asks once, for the first page', async () => {
    mocked.listNames.mockResolvedValue({ items: [resolved], total: 250, skip: 100, limit: 100 });
    renderAt('/names?page=2');
    await screen.findByText('portal.example-corp.com');
    mocked.listNames.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /^Unresolved/ }));
    await waitFor(() => expect(mocked.listNames).toHaveBeenCalled());
    await screen.findByText('1–100 of 250');
    expect(mocked.listNames.mock.calls.map(([q]) => [q.state, q.skip])).toEqual([['unresolved', 0]]);
  });
});

// The import answers with two more scope facts the dialog never showed:
// entries WIDENED to include subdomains, and entries the scope list refused.
describe('Names — what an import did to the scope list', () => {
  const answer = (over: Record<string, unknown> = {}) => ({
    names_created: 2, names_existing: 1, wildcards: 0, observations_recorded: 3, invalid_count: 0, invalid: [],
    scope_domains_added: 1, scope_domains_updated: 0, scope_invalid: [], ...over,
  });

  beforeEach(() => {
    vi.clearAllMocks();
    role = 'analyst';
    mocked.listNames.mockResolvedValue({ items: [row], total: 1, skip: 0, limit: 50 });
    mocked.getNamesSummary.mockResolvedValue({ total: 1, unresolved: 1, resolved: 0, in_scope: 1, wildcards: 0 });
  });

  const importList = async () => {
    renderAt('/names');
    fireEvent.click(await screen.findByRole('button', { name: /Import names/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Names to import'), { target: { value: 'a.acme.com\nb.acme.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /^Import 2$/ }));
    await waitFor(() => expect(mocked.importNames).toHaveBeenCalled());
    return dialog;
  };

  it('says how many entries were widened and how many were not added, naming the refused ones', async () => {
    const LONG = `${'x'.repeat(200)}.example.com`;
    mocked.importNames.mockResolvedValue(answer({ scope_domains_updated: 3, scope_invalid: ['10.0.0.1', LONG] }));
    const dialog = await importList();
    expect(await within(dialog).findByText('3 widened to include subdomains')).toBeInTheDocument();
    expect(within(dialog).getByText('2 not added to scope')).toBeInTheDocument();
    expect(within(dialog).getByText('10.0.0.1')).toBeInTheDocument();
    const long = within(dialog).getByText(LONG);
    expect(long.className).toContain('truncate');
    expect(long).toHaveAttribute('title', LONG);
    expect(toastMock.success).toHaveBeenCalledWith(
      'Imported: 2 new, 1 already known, 1 added to scope, 3 widened to include subdomains, 2 not added to scope',
    );
  });

  it('lists at most ten refused entries and counts the rest', async () => {
    const refused = Array.from({ length: 14 }, (_, i) => `bad-${i + 1}`);
    mocked.importNames.mockResolvedValue(answer({ scope_invalid: refused }));
    const dialog = await importList();
    expect(await within(dialog).findByText('14 not added to scope')).toBeInTheDocument();
    expect(within(dialog).getByText('bad-10')).toBeInTheDocument();
    expect(within(dialog).queryByText('bad-11')).not.toBeInTheDocument();
    expect(within(dialog).getByText('+4 more')).toBeInTheDocument();
  });

  it('says neither when the import widened and refused nothing', async () => {
    mocked.importNames.mockResolvedValue(answer());
    const dialog = await importList();
    expect(await within(dialog).findByText('1 added to scope')).toBeInTheDocument();
    expect(within(dialog).queryByText(/widened|not added to scope/)).not.toBeInTheDocument();
    expect(toastMock.success).toHaveBeenCalledWith('Imported: 2 new, 1 already known, 1 added to scope');
  });
});
