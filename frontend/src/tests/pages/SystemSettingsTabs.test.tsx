/**
 * System settings is tabs (5.350.0): one job at a time, the tab in the
 * address, worker health above them, and the two settings forms kept mounted
 * so unsaved text survives a look at another tab.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SystemSettings from '../../pages/SystemSettings';

// The real router: setupTests replaces useLocation with a fixed one.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const { listUsers } = vi.hoisted(() => ({ listUsers: vi.fn() }));
vi.mock('../../services/api', () => ({
  listUsers,
  registerUser: vi.fn(),
  updateUserAccount: vi.fn(),
  deleteUser: vi.fn(),
  resetUserPassword: vi.fn(),
  resetUserTwoFactor: vi.fn(),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'ada' }, hasPermission: () => true }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../components/QueueHealthCard', () => ({ default: () => <div>worker health</div> }));
vi.mock('../../components/remediation/RemediationSettingsSection', () => ({
  default: () => <input aria-label="remediation form" />,
}));
vi.mock('../../components/reports/ReportWritingGuidanceSection', () => ({
  default: () => <input aria-label="guidance form" />,
}));
vi.mock('../../components/UserMembershipsDialog', () => ({ default: () => null }));

const Where = () => <span data-testid="where">{useLocation().search}</span>;
const show = (path = '/system-settings') => render(
  <MemoryRouter initialEntries={[path]}><SystemSettings /><Where /></MemoryRouter>,
);

beforeEach(() => {
  listUsers.mockReset();
  listUsers.mockResolvedValue([]);
});

describe('System settings tabs', () => {
  it('opens on Users with worker health above the tabs', async () => {
    show();
    expect(screen.getByText('worker health')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Users' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('Role reference')).toBeInTheDocument();
    // The audit log is its own Administration page (/audit-log), not a tab.
    expect(screen.queryByRole('tab', { name: /audit/i })).toBeNull();
    expect(screen.getAllByRole('tab')).toHaveLength(3);
    expect(screen.getByLabelText('guidance form')).not.toBeVisible();
  });

  it('opens the tab the address names, and an unknown one is Users', () => {
    show('/system-settings?tab=report-writing');
    expect(screen.getByRole('tab', { name: 'Report writing' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('guidance form')).toBeVisible();
    expect(screen.queryByText('Role reference')).toBeNull();
  });

  it('an unknown tab is Users', () => {
    show('/system-settings?tab=nope');
    expect(screen.getByRole('tab', { name: 'Users' })).toHaveAttribute('aria-selected', 'true');
  });

  it('choosing a tab writes it to the address, and a form keeps what was typed', () => {
    show('/system-settings?tab=remediation');
    fireEvent.change(screen.getByLabelText('remediation form'), { target: { value: '45' } });
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Report writing' }), { button: 0 });
    expect(screen.getByTestId('where')).toHaveTextContent('?tab=report-writing');
    expect(screen.getByLabelText('guidance form')).toBeVisible();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Remediation' }), { button: 0 });
    expect(screen.getByLabelText('remediation form')).toHaveValue('45');
    // Users is the page itself: no `tab` in the address.
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Users' }), { button: 0 });
    expect(screen.getByTestId('where')).toHaveTextContent(/^$/);
  });
});
