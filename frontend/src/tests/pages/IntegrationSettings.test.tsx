/**
 * Scanner Integrations — who may do what (branch review 2026-10-01 S5).
 *
 * The list is account-level and open to every signed-in user, so the route is
 * `viewer` (it was the PROJECT analyst, which refused a viewer of the selected
 * project).  Adding, editing and deleting need the GLOBAL administrator; the
 * page does not render those controls for anyone else.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '../../components/ui/tooltip';

vi.mock('../../services/api', () => ({
  listIntegrations: vi.fn(),
  listIntegrationTypes: vi.fn(),
  createIntegration: vi.fn(),
  updateIntegration: vi.fn(),
  deleteIntegration: vi.fn(),
  testIntegrationConfig: vi.fn(),
}));
const account = vi.hoisted(() => ({ role: 'member' }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: account.role }, hasPermission: (r: string) => r !== 'admin' || account.role === 'admin' }),
}));
// One object, as the real context gives: the page's loader depends on it.
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));

import * as api from '../../services/api';
import IntegrationSettings from '../../pages/IntegrationSettings';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const entry = {
  id: 4, name: 'Client X Nessus', integration_type: 'nessus', project_id: null, base_url: 'https://nessus.example:8834',
  has_secret: true, has_secret2: false, extra_config: null, is_active: true,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
};

const renderPage = () => render(<TooltipProvider><IntegrationSettings /></TooltipProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  account.role = 'member';
  mocked.listIntegrations.mockResolvedValue([entry]);
  mocked.listIntegrationTypes.mockResolvedValue([{ value: 'nessus', label: 'Nessus' }]);
});

describe('Scanner Integrations — a member reads, a global admin changes', () => {
  it('shows a member the integrations without any control that changes them', async () => {
    renderPage();
    expect(await screen.findByText('Client X Nessus')).toBeInTheDocument();
    expect(screen.getByText('Secret set')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Integration/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Edit integration/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Delete integration/ })).toBeNull();
    expect(screen.getByText(/A global administrator adds and changes them/)).toBeInTheDocument();
  });

  it('an empty list does not tell a member to add one', async () => {
    mocked.listIntegrations.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('No integrations configured yet.')).toBeInTheDocument();
    expect(screen.queryByText(/Add one to make its credentials/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Add Your First Integration/ })).toBeNull();
  });

  it('shows a global administrator the controls', async () => {
    account.role = 'admin';
    renderPage();
    expect(await screen.findByText('Client X Nessus')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Integration/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit integration Client X Nessus' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete integration Client X Nessus' })).toBeInTheDocument();
  });
});

// A failed read was a toast over "No integrations configured yet." — gone in
// seconds, with nothing to press.  It is said where the integrations would be.
describe('Scanner Integrations — the list could not be read', () => {
  it('says so in place of the list, with Retry — not "none configured", and not as a toast', async () => {
    mocked.listIntegrations.mockRejectedValueOnce({
      response: { status: 503, data: { detail: 'The integration store is not answering.' } },
    });
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The integration store is not answering.');
    expect(screen.queryByText('No integrations configured yet.')).toBeNull();
    expect(toast.error).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Client X Nessus')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(mocked.listIntegrations).toHaveBeenCalledTimes(2);
    expect(toast.error).not.toHaveBeenCalled();
  });
});

// 1.13 — the edit dialog kept its own copy of the row from the moment it was
// opened: after "Remove the stored primary secret" it still offered to remove
// a secret that was gone.
describe('Scanner Integrations — the edit dialog shows the integration as it is now', () => {
  const CLEAR = 'Remove the stored primary secret';
  const CLEAR2 = 'Remove the stored secondary secret';

  beforeEach(() => {
    account.role = 'admin';
    mocked.listIntegrations.mockResolvedValue([{ ...entry, has_secret2: true }]);
    mocked.updateIntegration.mockResolvedValue(entry);
  });

  const openEdit = async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit integration Client X Nessus' }));
    return screen.findByRole('dialog');
  };

  it('stops offering to remove a stored secret once it has been removed', async () => {
    await openEdit();
    expect(screen.getByRole('button', { name: CLEAR2 })).toBeInTheDocument();
    // The server's next answer: the primary secret is gone, the second stays.
    mocked.listIntegrations.mockResolvedValue([{ ...entry, has_secret: false, has_secret2: true }]);
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));

    await waitFor(() => expect(mocked.updateIntegration).toHaveBeenCalledWith(4, { clear_secret: true }));
    await waitFor(() => expect(screen.queryByRole('button', { name: CLEAR })).toBeNull());
    expect(screen.getByRole('button', { name: CLEAR2 })).toBeInTheDocument();
    expect(screen.getByText('Edit Integration')).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toHaveValue('Client X Nessus');
  });

  it('saves the form as it stands, to the integration being edited', async () => {
    await openEdit();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Renamed' } });
    fireEvent.change(screen.getByLabelText(/Max hosts per scan/), { target: { value: '512' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateIntegration).toHaveBeenCalledWith(4, {
      name: 'Renamed', base_url: 'https://nessus.example:8834', is_active: true,
      extra_config: { max_hosts_per_scan: 512 },
    }));
    expect(mocked.createIntegration).not.toHaveBeenCalled();
  });

  // Owner decision 52: the skeleton is for the first load only.
  it('the integrations stay on screen while the list is read again', async () => {
    await openEdit();
    let answer: (value: unknown) => void = () => {};
    mocked.listIntegrations.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));
    await waitFor(() => expect(mocked.listIntegrations).toHaveBeenCalledTimes(2));

    expect(screen.getByText('https://nessus.example:8834')).toBeInTheDocument();
    expect(screen.getByText('Secret set')).toBeInTheDocument();

    answer([{ ...entry, has_secret: false }]);
    expect(await screen.findByText('No secret')).toBeInTheDocument();
  });

  it('a re-read that fails keeps the integrations and says so, with Retry', async () => {
    await openEdit();
    mocked.listIntegrations.mockRejectedValueOnce({
      response: { status: 503, data: { detail: 'The integration store is not answering.' } },
    });
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));

    const alert = await screen.findByRole('alert', { hidden: true });
    expect(alert).toHaveTextContent('The integration store is not answering.');
    expect(within(alert).getByRole('button', { name: 'Retry', hidden: true })).toBeInTheDocument();
    expect(screen.getByText('https://nessus.example:8834')).toBeInTheDocument();
  });

  // The row can go while its dialog is open: Save must not become "add".
  it('an edit never becomes an add when the integration has gone from the list', async () => {
    await openEdit();
    mocked.listIntegrations.mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));
    await waitFor(() => expect(screen.queryByRole('button', { name: CLEAR })).toBeNull());
    expect(screen.getByText('Edit Integration')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateIntegration).toHaveBeenCalledWith(4, expect.objectContaining({ name: 'Client X Nessus' })));
    expect(mocked.createIntegration).not.toHaveBeenCalled();
  });
});
