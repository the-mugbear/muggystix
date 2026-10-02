/**
 * Scanner Integrations — who may do what (branch review 2026-10-01 S5).
 *
 * The list is account-level and open to every signed-in user, so the route is
 * `viewer` (it was the PROJECT analyst, which refused a viewer of the selected
 * project).  Adding, editing and deleting need the GLOBAL administrator; the
 * page does not render those controls for anyone else.
 */
import { render, screen } from '@testing-library/react';
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
