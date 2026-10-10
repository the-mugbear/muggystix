/**
 * Scanner Integrations — who may do what (branch review 2026-10-01 S5).
 *
 * The list is the INSTALLATION's (one for every project, whoever configured
 * each — 5.375.0) and open to every signed-in user, so the route is `viewer`
 * (it was the PROJECT analyst, which refused a viewer of the selected
 * project).  Adding, editing and deleting need the GLOBAL administrator, on
 * any row; the page does not render those controls for anyone else.  No
 * secret is ever on the page: the server sends none.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TooltipProvider } from '../../components/ui/tooltip';
import { createQueryClient } from '../../lib/query';
import { heldByMutations, withClient } from '../helpers/heldByMutations';

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

// Configured by ANOTHER account (the signed-in one is user 1): the list is
// everyone's, and a global admin's controls are on every row.
const entry = {
  id: 4, name: 'Client X Nessus', integration_type: 'nessus', base_url: 'https://nessus.example:8834',
  has_secret: true, has_secret2: false, extra_config: null, is_active: true,
  created_by: 'someone-else',
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
  it('shows a member another person’s integration without any control that changes it', async () => {
    renderPage();
    expect(await screen.findByText('Client X Nessus')).toBeInTheDocument();
    expect(screen.getByText('Secret set')).toBeInTheDocument();
    expect(screen.getByText('Configured by someone-else')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Integration/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Edit integration/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Delete integration/ })).toBeNull();
    expect(screen.getByText(/A global administrator adds and changes them/)).toBeInTheDocument();
    // The list is asked for as the installation's: no project, no user.
    expect(mocked.listIntegrations.mock.calls[0]).toHaveLength(1);
    expect(mocked.listIntegrations.mock.calls[0][0]).toBeInstanceOf(AbortSignal);
  });

  it('an empty list does not tell a member to add one', async () => {
    mocked.listIntegrations.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('No integrations configured yet.')).toBeInTheDocument();
    expect(screen.queryByText(/Add one so that agents/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Add Your First Integration/ })).toBeNull();
  });

  it('shows a global administrator the controls on a row someone else configured', async () => {
    account.role = 'admin';
    renderPage();
    expect(await screen.findByText('Client X Nessus')).toBeInTheDocument();
    expect(screen.getByText('Configured by someone-else')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Integration/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit integration Client X Nessus' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete integration Client X Nessus' })).toBeInTheDocument();
  });

  // Sections, not cards (UI_STYLE_GUIDE §7): one table, a row per integration.
  it('lists the integrations as rows of one table — no card anywhere', async () => {
    account.role = 'admin';
    const LONG = `nessus-${'x'.repeat(200)}`;
    mocked.listIntegrations.mockResolvedValue([
      entry,
      { ...entry, id: 5, name: LONG, base_url: null, has_secret: false, has_secret2: true, is_active: false },
    ]);
    const { container } = renderPage();
    const table = await screen.findByRole('table', { name: 'Scanner integrations' });
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Scanner', 'Base URL', 'Stored secrets', 'Actions']);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('Client X Nessus')).toBeInTheDocument();
    expect(within(rows[0]).getByText('https://nessus.example:8834')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Secret set')).toBeInTheDocument();
    expect(within(rows[0]).getByRole('button', { name: 'Edit integration Client X Nessus' })).toBeInTheDocument();
    // An unbounded name truncates with its full value on the title; no URL is a dash;
    // an inactive scanner says so.
    expect(within(rows[1]).getByText(LONG)).toHaveClass('truncate');
    expect(within(rows[1]).getByText(LONG)).toHaveAttribute('title', LONG);
    expect(within(rows[1]).getByText('—')).toBeInTheDocument();
    expect(within(rows[1]).getByText('No secret')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Secondary secret')).toBeInTheDocument();
    expect(within(rows[1]).getByText('disabled')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Configured scanners\s*2/ })).toBeInTheDocument();
    // The Card primitive is `rounded-panel border bg-card … shadow-raised`.
    expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
    expect(container.querySelector('.rounded-panel.border.bg-card')).toBeNull();
  });

  it('a member’s table has no Actions column', async () => {
    renderPage();
    const table = await screen.findByRole('table', { name: 'Scanner integrations' });
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Scanner', 'Base URL', 'Stored secrets']);
  });

  it('says whose it was when the account that configured it is gone', async () => {
    mocked.listIntegrations.mockResolvedValue([{ ...entry, created_by: null }]);
    renderPage();
    expect(await screen.findByText('Configured by an account that has since been removed')).toBeInTheDocument();
  });

  it('says once what an agent does with these, and claims no approval', async () => {
    renderPage();
    await screen.findByText('Client X Nessus');
    const lead = screen.getByText(/An agent can see that a scanner is configured/);
    expect(lead).toHaveTextContent('is told to ask its operator before using it');
    expect(lead).toHaveTextContent('only when it then requests them');
    expect(lead).toHaveTextContent('every request is recorded');
    expect(lead).toHaveTextContent('for every project');
    expect(screen.getAllByText(/An agent can see/)).toHaveLength(1);
    // The server cannot see the operator's answer: the page does not say it does.
    expect(document.body.textContent).not.toMatch(/approv|enforc|verif/i);
    // The per-project model is gone from the page.
    expect(screen.queryByText('all projects')).toBeNull();
    expect(screen.queryByText(/session prompt/)).toBeNull();
  });
});

describe('Scanner Integrations — no secret is on the page or kept by it', () => {
  const ACCESS = 'zebra-access-key-3317';
  const SECRET = 'zebra-secret-key-6652';

  it('renders nothing but "a secret is set" for a stored secret, to a member and to an admin', async () => {
    for (const role of ['member', 'admin']) {
      account.role = role;
      const view = renderPage();
      await screen.findByText('Client X Nessus');
      expect(screen.getByText('Secret set')).toBeInTheDocument();
      // No reveal control and no field holding a stored value.
      expect(screen.queryByRole('button', { name: /show|reveal/i })).toBeNull();
      expect(document.querySelectorAll('input')).toHaveLength(0);
      view.unmount();
    }
  });

  it('an admin’s edit dialog opens with empty secret fields', async () => {
    account.role = 'admin';
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit integration Client X Nessus' }));
    await screen.findByRole('dialog');
    expect(screen.getByLabelText(/Access Key/)).toHaveValue('');
    expect(screen.getByLabelText(/Secret Key/)).toHaveValue('');
  });

  it('what was typed is not held once the save has settled', async () => {
    account.role = 'admin';
    mocked.createIntegration.mockResolvedValue({ ...entry, id: 5, name: 'New one' });
    const client = createQueryClient();
    render(<TooltipProvider><IntegrationSettings /></TooltipProvider>, { wrapper: withClient(client) });

    fireEvent.click(await screen.findByRole('button', { name: /Add Integration/ }));
    await screen.findByRole('dialog');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New one' } });
    fireEvent.change(screen.getByLabelText('Access Key'), { target: { value: ACCESS } });
    fireEvent.change(screen.getByLabelText('Secret Key'), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocked.createIntegration).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'New one', secret: ACCESS, secret2: SECRET }),
    ));
    // Sent with no project: the integration is the installation's.
    expect(mocked.createIntegration.mock.calls[0][0]).not.toHaveProperty('project_id');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => {
      const held = heldByMutations(client);
      expect(held).not.toContain(ACCESS);
      expect(held).not.toContain(SECRET);
    });
    expect(document.body.textContent).not.toContain(ACCESS);
  });

  it('nor once a connection test has settled', async () => {
    account.role = 'admin';
    mocked.testIntegrationConfig.mockResolvedValue({
      ok: true, integration_type: 'nessus', message: 'Authenticated.', duration_ms: 12,
    });
    const client = createQueryClient();
    render(<TooltipProvider><IntegrationSettings /></TooltipProvider>, { wrapper: withClient(client) });

    fireEvent.click(await screen.findByRole('button', { name: /Add Integration/ }));
    await screen.findByRole('dialog');
    fireEvent.change(screen.getByLabelText('Access Key'), { target: { value: ACCESS } });
    fireEvent.click(screen.getByRole('button', { name: /Test connection/ }));

    expect(await screen.findByText('Authenticated.')).toBeInTheDocument();
    await waitFor(() => expect(heldByMutations(client)).not.toContain(ACCESS));
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
