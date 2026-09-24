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
    user: { id: 1, username: 'tester' },
    hasPermission: (r: string) => (LEVEL[role] ?? 0) >= (LEVEL[r] ?? 0),
  }),
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
    expect(screen.getByText(/Every name this engagement knows about/)).toBeInTheDocument();
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
});
