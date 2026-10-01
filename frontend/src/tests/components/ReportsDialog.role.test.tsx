/**
 * Review 2026-10-01 R32 — a report job's Retry / Cancel / Dismiss are a
 * project analyst's, or the person's who asked for the job.  A viewer or
 * auditor does not get them on someone else's job.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';

const role = vi.hoisted(() => ({ value: 'auditor' as string }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 7, username: 'aud', role: 'member' }, hasPermission: () => true }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: role.value } }),
}));
vi.mock('../../hooks/useVisibilityPoll', () => ({ useVisibilityPoll: vi.fn() }));
vi.mock('../../services/api', () => ({
  generateHostsReport: vi.fn(),
  enqueueReportJob: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
  listReportJobs: vi.fn(),
  getReportLimits: vi.fn().mockResolvedValue({ in_memory_host_cap: 2000, streamed_host_cap: 50000, per_format: {} }),
  dismissReportJob: vi.fn(),
  retryReportJob: vi.fn(),
  cancelReportJob: vi.fn(),
}));

import ReportsDialog from '../../components/ReportsDialog';
import * as api from '../../services/api';

const render = (ui: ReactElement) => rtlRender(ui, { wrapper: MemoryRouter });
const job = (id: number, status: string, requestedBy: number | null | undefined) => ({
  id, project_id: 1, format: 'html', report_type: 'comprehensive', status, truncated: false,
  created_at: '2026-10-01T00:00:00Z', ...(requestedBy === undefined ? {} : { requested_by_id: requestedBy }),
});
const jobs = (requestedBy: number | null | undefined) =>
  (api.listReportJobs as ReturnType<typeof vi.fn>).mockResolvedValue([
    job(1, 'failed', requestedBy), job(2, 'queued', requestedBy), job(3, 'completed', requestedBy),
  ]);
const open = async () => {
  render(<ReportsDialog open onClose={vi.fn()} filters={{}} totalHosts={10} />);
  await screen.findByText('Recent reports');
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Download' })).toHaveLength(1));
};

beforeEach(() => { role.value = 'auditor'; });

describe('ReportsDialog — whose report job it is', () => {
  it('hides Retry, Cancel and Dismiss from an auditor on a job someone else requested', async () => {
    jobs(99);
    await open();
    expect(screen.queryByRole('button', { name: /Retry report job/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Cancel report job/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Dismiss report job/ })).toBeNull();
    // Reading the result is still theirs.
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });

  it('keeps them on the jobs the auditor requested', async () => {
    jobs(7);
    await open();
    expect(screen.getByRole('button', { name: 'Retry report job 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel report job 2' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Dismiss report job/ })).toHaveLength(3);
  });

  it('shows them to an analyst on anyone’s job', async () => {
    role.value = 'analyst';
    jobs(99);
    await open();
    expect(screen.getByRole('button', { name: 'Retry report job 1' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Dismiss report job/ })).toHaveLength(3);
  });

  it('treats a viewer as it does an auditor: not on someone else’s job, yes on their own', async () => {
    role.value = 'viewer';
    jobs(99);
    await open();
    expect(screen.queryByRole('button', { name: /(Retry|Cancel|Dismiss) report job/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });

  it('keeps a viewer’s own job theirs', async () => {
    role.value = 'viewer';
    jobs(7);
    await open();
    expect(screen.getByRole('button', { name: 'Retry report job 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel report job 2' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Dismiss report job/ })).toHaveLength(3);
  });

  it('decides per job in a mixed list', async () => {
    (api.listReportJobs as ReturnType<typeof vi.fn>).mockResolvedValue([
      job(1, 'failed', 99), job(2, 'queued', 7), job(3, 'completed', 99),
    ]);
    await open();
    expect(screen.queryByRole('button', { name: 'Retry report job 1' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Cancel report job 2' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Dismiss report job/ })).toHaveLength(1);
  });

  // A server that does not send `requested_by_id` (older build), or sends
  // null, cannot be second-guessed: every control stays and the server decides.
  it.each([[undefined], [null]])(
    'leaves the decision to the server when the job does not say who asked for it (%s)',
    async (requestedBy) => {
      jobs(requestedBy);
      await open();
      expect(screen.getByRole('button', { name: 'Retry report job 1' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Cancel report job 2' })).toBeInTheDocument();
      expect(screen.getAllByRole('button', { name: /Dismiss report job/ })).toHaveLength(3);
    },
  );
});
