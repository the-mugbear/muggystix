import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  listAuditLogs: vi.fn(),
  getAuditStats: vi.fn(),
}));

import * as api from '../../services/api';
import AuditLogViewer, { AUDIT_PAGE_SIZE } from '../../components/AuditLogViewer';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const row = (over: Record<string, unknown> = {}) => ({
  id: 1,
  user_id: 3,
  user_username: 'eval-ana',
  user_full_name: 'Ana Ortiz',
  action: 'login_success',
  resource_type: null,
  resource_id: null,
  details: { method: 'totp' },
  success: true,
  error_message: null,
  ip_address: '10.0.0.5',
  user_agent: null,
  created_at: '2026-09-22T20:49:37Z',
  ...over,
});

const stats = (over: Record<string, unknown> = {}) => ({
  total_logs: 101,
  successful_logs: 99,
  failed_logs: 2,
  recent_logs_24h: 7,
  top_actions: [{ action: 'login_success', count: 90 }],
  top_users: [],
  ...over,
});

describe('AuditLogViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listAuditLogs.mockResolvedValue({ logs: [row()], total: 101, skip: 0, limit: AUDIT_PAGE_SIZE });
    mocked.getAuditStats.mockResolvedValue(stats());
  });

  // The backend field is `recent_logs_24h`; the viewer read `recent_logs` and
  // printed "…selected project. undefined in the last 24 hours."
  it('states the last-24-hours count from the field the backend returns', async () => {
    render(<AuditLogViewer />);
    expect(await screen.findByText(/7 in the last 24 hours\./)).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it('never prints "undefined" when the count is missing', async () => {
    mocked.getAuditStats.mockResolvedValue({ ...stats({ recent_logs_24h: undefined }), recent_logs: 7 });
    render(<AuditLogViewer />);
    await screen.findByText('Ana Ortiz');
    await waitFor(() => expect(mocked.getAuditStats).toHaveBeenCalled());
    expect(screen.queryByText(/undefined/)).toBeNull();
    expect(screen.queryByText(/in the last 24 hours/)).toBeNull();
  });

  it('shows who acted by name and the detail as readable text, not an id or JSON', async () => {
    mocked.listAuditLogs.mockResolvedValue({
      logs: [
        row(),
        row({ id: 2, user_id: 1, user_username: 'admin', user_full_name: null, details: { method: 'password' } }),
        row({
          id: 3, user_id: null, user_username: null, user_full_name: null, action: 'login_failed',
          success: false, details: { username: 'mallory' }, error_message: 'Invalid credentials',
        }),
      ],
      total: 3, skip: 0, limit: AUDIT_PAGE_SIZE,
    });
    render(<AuditLogViewer />);
    const table = await screen.findByRole('table');
    const rows = within(table).getAllByRole('row').slice(1);

    expect(within(rows[0]).getByText('Ana Ortiz')).toBeInTheDocument();
    expect(within(rows[0]).getByText('method: TOTP')).toBeInTheDocument();
    expect(within(rows[1]).getByText('admin')).toBeInTheDocument();
    expect(within(rows[1]).getByText('method: password')).toBeInTheDocument();
    // No actor → a dash; the failure keeps its error text.
    const userCell = (r: HTMLElement) => within(r).getAllByRole('cell')[3];
    expect(userCell(rows[2]).textContent).toBe('—');
    expect(userCell(rows[0]).textContent).toBe('Ana Ortiz');
    expect(within(rows[2]).getByText('Invalid credentials')).toBeInTheDocument();
    expect(within(rows[2]).getByText(/username: mallory/)).toBeInTheDocument();

    expect(table.textContent).not.toMatch(/\{"method"/);
    expect(within(rows[0]).queryByText('3')).toBeNull();
  });

  it('pages by 20 and keeps the total', async () => {
    render(<AuditLogViewer />);
    expect(await screen.findByText('1–20 of 101')).toBeInTheDocument();
    expect(AUDIT_PAGE_SIZE).toBe(20);
    expect(mocked.listAuditLogs).toHaveBeenCalledWith(expect.objectContaining({ skip: 0, limit: 20 }));
  });
});
