import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

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

  it('surfaces an export failure as a toast', async () => {
    mocked.exportNames.mockRejectedValueOnce(new Error('nope'));
    renderAt('/names');
    await waitFor(() => expect(screen.getByText('portal.acme.com')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Export names as text' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
  });
});
