/**
 * System settings → Report defaults: the installation's report author and
 * disclaimer.  Loaded once, only the changed field is sent, a blank field
 * removes the value, Undo puts the stored values back — and the section is
 * System settings', which a member never sees.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The real router: setupTests replaces useLocation with a fixed one.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const api = vi.hoisted(() => ({
  getReportDefaults: vi.fn(), saveReportDefaults: vi.fn(), listUsers: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
const auth = vi.hoisted(() => ({ admin: true }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'ada' }, hasPermission: () => auth.admin }),
}));
vi.mock('../../components/QueueHealthCard', () => ({ default: () => null }));
vi.mock('../../components/remediation/RemediationSettingsSection', () => ({ default: () => null }));
vi.mock('../../components/reports/ReportWritingGuidanceSection', () => ({ default: () => null }));
vi.mock('../../components/UserMembershipsDialog', () => ({ default: () => null }));

import ReportDefaultsSection, { changedDefaults } from '../../components/reports/ReportDefaultsSection';
import SystemSettings from '../../pages/SystemSettings';
import type { ReportDefaults } from '../../services/api';

const LONG = 'This report is confidential and intended solely for the client. '.repeat(40).trim();
const defaults = (over: Partial<ReportDefaults['values']> = {}, meta: Partial<ReportDefaults> = {}): ReportDefaults => ({
  values: { author_name: 'Example Security Ltd', disclaimer: null, ...over },
  labels: { author_name: 'Author', disclaimer: 'Disclaimer' },
  max_chars: { author_name: 255, disclaimer: 32768 },
  updated_at: null, updated_by: null, ...meta,
});

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
  auth.admin = true;
  api.listUsers.mockResolvedValue([]);
});

describe('ReportDefaultsSection', () => {
  it('shows the stored values, says what they are for, and offers no save until something changed', async () => {
    api.getReportDefaults.mockResolvedValue(defaults({}, { updated_at: '2026-10-12T09:00:00Z', updated_by: 'Ada Admin' }));
    render(<ReportDefaultsSection />);
    expect(await screen.findByLabelText('Author')).toHaveValue('Example Security Ltd');
    expect(screen.getByLabelText('Disclaimer')).toHaveValue('');
    expect(screen.getByText(
      "These apply to every project's reports unless a project or a report writes its own; an empty disclaimer prints the template's own.",
    )).toBeInTheDocument();
    expect(screen.getByText(/Last changed .* by Ada Admin\./)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save report defaults' })).toBeNull();
    expect(screen.getByLabelText('Author')).toHaveAttribute('maxlength', '255');
  });

  it('sends only the field that changed and shows the server’s answer', async () => {
    api.getReportDefaults.mockResolvedValue(defaults());
    api.saveReportDefaults.mockResolvedValue(defaults({ disclaimer: LONG }, { updated_at: '2026-10-13T10:00:00Z', updated_by: 'ada' }));
    render(<ReportDefaultsSection />);
    fireEvent.change(await screen.findByLabelText('Disclaimer'), { target: { value: `  ${LONG}  ` } });
    fireEvent.click(screen.getByRole('button', { name: 'Save report defaults' }));
    await waitFor(() => expect(api.saveReportDefaults).toHaveBeenCalledWith({ disclaimer: LONG }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(screen.getByLabelText('Disclaimer')).toHaveValue(LONG);
    expect(screen.queryByRole('button', { name: 'Save report defaults' })).toBeNull();
    expect(screen.getByText(/Last changed .* by ada\./)).toBeInTheDocument();
    // No second read: the answer is the record.
    expect(api.getReportDefaults).toHaveBeenCalledTimes(1);
  });

  it('an emptied field is sent as null — the value is removed', async () => {
    api.getReportDefaults.mockResolvedValue(defaults({ disclaimer: 'Ours.' }));
    api.saveReportDefaults.mockResolvedValue(defaults({ author_name: null, disclaimer: 'Ours.' }));
    render(<ReportDefaultsSection />);
    fireEvent.change(await screen.findByLabelText('Author'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save report defaults' }));
    await waitFor(() => expect(api.saveReportDefaults).toHaveBeenCalledWith({ author_name: null }));
  });

  it('Undo puts the stored values back and sends nothing', async () => {
    api.getReportDefaults.mockResolvedValue(defaults());
    render(<ReportDefaultsSection />);
    fireEvent.change(await screen.findByLabelText('Author'), { target: { value: 'Somebody else' } });
    fireEvent.change(screen.getByLabelText('Disclaimer'), { target: { value: 'Draft words' } });
    fireEvent.click(screen.getByRole('button', { name: 'Undo changes' }));
    expect(screen.getByLabelText('Author')).toHaveValue('Example Security Ltd');
    expect(screen.getByLabelText('Disclaimer')).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'Save report defaults' })).toBeNull();
    expect(api.saveReportDefaults).not.toHaveBeenCalled();
  });

  it('a save that fails keeps what was typed and says why', async () => {
    api.getReportDefaults.mockResolvedValue(defaults());
    api.saveReportDefaults.mockRejectedValue({ response: { status: 422, data: { detail: 'Author: at most 255 characters.' } } });
    render(<ReportDefaultsSection />);
    fireEvent.change(await screen.findByLabelText('Author'), { target: { value: 'New name' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save report defaults' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Author: at most 255 characters.'));
    expect(screen.getByLabelText('Author')).toHaveValue('New name');
    expect(screen.getByRole('button', { name: 'Save report defaults' })).toBeEnabled();
  });

  it('says when the defaults could not be loaded, and reads them again on Retry', async () => {
    api.getReportDefaults.mockRejectedValueOnce(new Error('HTTP 500')).mockResolvedValue(defaults());
    render(<ReportDefaultsSection />);
    expect(await screen.findByRole('alert')).toHaveTextContent('The report defaults could not be loaded.');
    expect(screen.queryByLabelText('Author')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByLabelText('Author')).toHaveValue('Example Security Ltd');
  });

  it('compares trimmed text: spaces alone are not a change', () => {
    const stored = defaults({ disclaimer: 'Ours.' });
    expect(changedDefaults(stored, { author_name: ' Example Security Ltd ', disclaimer: 'Ours.\n' })).toEqual({});
    expect(changedDefaults(stored, { author_name: 'Example Security Ltd', disclaimer: '' })).toEqual({ disclaimer: null });
  });
});

describe('Report defaults — where they are', () => {
  const show = () => render(
    <MemoryRouter initialEntries={['/system-settings?tab=report-writing']}><SystemSettings /></MemoryRouter>,
  );

  it('a global administrator finds them under System settings → Report writing', async () => {
    api.getReportDefaults.mockResolvedValue(defaults());
    show();
    expect(await screen.findByLabelText('Author')).toHaveValue('Example Security Ltd');
    expect(screen.getByText('Report defaults')).toBeVisible();
  });

  it('a member is refused the page: the section is not shown and nothing is asked for', async () => {
    auth.admin = false;
    show();
    expect(screen.getByText('Access denied. Administrator privileges required.')).toBeInTheDocument();
    expect(screen.queryByText('Report defaults')).toBeNull();
    expect(screen.queryByLabelText('Author')).toBeNull();
    expect(api.getReportDefaults).not.toHaveBeenCalled();
  });
});
