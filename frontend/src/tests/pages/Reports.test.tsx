import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateSpy };
});
vi.mock('../../services/api', () => ({
  listClientReports: vi.fn(),
  getClientReport: vi.fn(),
  createClientReport: vi.fn(),
  updateClientReport: vi.fn(),
  deleteClientReport: vi.fn(),
  previewClientReport: vi.fn(),
  issueClientReport: vi.fn(),
  rerenderClientReport: vi.fn(),
  reviseClientReport: vi.fn(),
  listReportTemplates: vi.fn(),
  getReportProfile: vi.fn(),
  saveReportProfile: vi.fn(),
  downloadClientReportFile: vi.fn(),
  listProjectMembers: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
  draftReportWithAI: vi.fn(),
  listLLMProviders: vi.fn(),
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
// Poll once when enabled — enough to follow a job or a render in a test.
vi.mock('../../hooks/useVisibilityPoll', async () => {
  const react = await vi.importActual<typeof import('react')>('react');
  return {
    useVisibilityPoll: (cb: () => void, _ms: number, enabled = true) => {
      react.useEffect(() => { if (enabled) void cb(); }, [enabled]); // eslint-disable-line react-hooks/exhaustive-deps
    },
  };
});

import * as api from '../../services/api';
import Reports from '../../pages/Reports';
import ReportDetail from '../../pages/ReportDetail';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const settings = {
  client_name: 'Example Corp', classification: null, engagement_type: null,
  testers: [], distribution: [], system_description: null,
};

const report = (over: Record<string, unknown> = {}) => ({
  id: 5, project_id: 1, kind: 'full', status: 'draft', title: 'Example report', number: null,
  template: 'pentest', baseline: null, revision_of: null, superseded_by: null, settings,
  executive_summary: null, template_fingerprint: null, quarto_version: null,
  render_status: null, render_error: null, files: [], created_by_name: 'Ana', issued_by_name: null,
  created_at: '2026-09-20T00:00:00Z', updated_at: null, issued_at: null,
  summary: {
    counts: { critical: 1, high: 2, medium: 0, low: 0, info: 1, total: 4 },
    under_investigation: 3, images: 2, images_skipped: 0,
    missing_text: [{ id: 42, ref: 'F-02', title: 'Weak TLS', missing: ['impact', 'recommendation'] }],
    delta: null,
  },
  can_edit: true, can_issue: true, ...over,
});

const pdf = { format: 'pdf', filename: 'x-report-01.pdf', media_type: 'application/pdf', size_bytes: 10, sha256: 'ab'.repeat(32), created_at: null };

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listReportTemplates.mockResolvedValue([{ name: 'pentest', title: 'Penetration test report', description: '', formats: ['html', 'docx', 'pdf'] }]);
  mocked.getReportProfile.mockResolvedValue({ ...settings, template: 'pentest' });
  mocked.listProjectMembers.mockResolvedValue([]);
});

const renderList = () => render(<MemoryRouter><Reports /></MemoryRouter>);
const renderDetail = () =>
  render(
    <MemoryRouter initialEntries={['/reports/5']}>
      <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
    </MemoryRouter>,
  );

describe('Reports list', () => {
  it('says what was issued and offers an addendum to the latest', async () => {
    mocked.listClientReports.mockResolvedValue({
      items: [
        report({ id: 9, title: 'Draft two' }),
        report({ id: 7, status: 'issued', number: 2, issued_at: '2026-09-21T00:00:00Z', files: [pdf], render_status: 'done' }),
        report({ id: 6, status: 'superseded', number: 1, issued_at: '2026-09-19T00:00:00Z' }),
      ],
      latest_issued_id: 7, can_create: true, can_issue: false,
    });
    renderList();
    expect(await screen.findByText(/the latest is/)).toHaveTextContent('1 issued report; the latest is #2');
    expect(screen.getByText('Superseded')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download x-report-01.pdf' })).toBeInTheDocument();

    mocked.createClientReport.mockResolvedValue(report({ id: 11, kind: 'addendum' }));
    fireEvent.click(screen.getByRole('button', { name: /New addendum to #2/ }));
    await waitFor(() => expect(mocked.createClientReport).toHaveBeenCalledWith({ kind: 'addendum' }));
    expect(navigateSpy).toHaveBeenCalledWith('/reports/11');
  });

  it('offers no addendum before anything is issued, and nothing to an auditor', async () => {
    mocked.listClientReports.mockResolvedValue({ items: [], latest_issued_id: null, can_create: true, can_issue: false });
    const { unmount } = renderList();
    expect(await screen.findByText('No report has been issued for this project yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New addendum/ })).toBeDisabled();
    unmount();

    mocked.listClientReports.mockResolvedValue({ items: [], latest_issued_id: null, can_create: false, can_issue: false });
    renderList();
    await screen.findByText('No report has been issued for this project yet.');
    expect(screen.queryByRole('button', { name: /New report/ })).not.toBeInTheDocument();
  });
});

describe('Report detail — draft', () => {
  it('shows what the draft contains and what is missing', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    expect(await screen.findByText('Before issuing')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Weak TLS' })).toHaveAttribute('href', '/findings/42');
    expect(screen.getByText('missing impact, recommendation')).toBeInTheDocument();
    expect(screen.getByText('Still under investigation')).toBeInTheDocument();
  });

  it('previews on the worker and offers the file when it is ready', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.previewClientReport.mockResolvedValue({ id: 70, format: 'report-pdf', status: 'queued' });
    mocked.getReportJob.mockResolvedValue({ id: 70, format: 'report-pdf', status: 'completed' });
    mocked.downloadReportJob.mockResolvedValue({ truncated: false });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview PDF' }));
    await waitFor(() => expect(mocked.previewClientReport).toHaveBeenCalledWith(5, 'pdf'));
    fireEvent.click(await screen.findByRole('button', { name: 'Download the PDF preview' }));
    expect(mocked.downloadReportJob).toHaveBeenCalledWith(70);
  });

  it('saves the details and will not issue with unsaved changes', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.updateClientReport.mockResolvedValue(report({ executive_summary: 'Two criticals.' }));
    renderDetail();
    fireEvent.change(await screen.findByLabelText('Executive summary'), { target: { value: 'Two criticals.' } });
    expect(screen.getByRole('button', { name: /Issue report/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(5, expect.objectContaining({
      executive_summary: 'Two criticals.', title: 'Example report',
    })));
    await waitFor(() => expect(screen.getByRole('button', { name: /Issue report/ })).toBeEnabled());
  });

  it('issues only after confirmation', async () => {
    const issuedReport = report({ status: 'issued', number: 3, render_status: 'pending', can_edit: false, can_issue: false });
    // First load: the draft; afterwards (the render poll): the issued report.
    mocked.getClientReport.mockResolvedValueOnce(report()).mockResolvedValue(issuedReport);
    mocked.issueClientReport.mockResolvedValue(issuedReport);
    renderDetail();
    const issue = await screen.findByRole('button', { name: /Issue report/ });
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(issue);
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(mocked.issueClientReport).not.toHaveBeenCalled();
    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(issue);
    await waitFor(() => expect(mocked.issueClientReport).toHaveBeenCalledWith(5));
    expect(await screen.findByText('Issued #3')).toBeInTheDocument();
  });
});

describe('Report detail — issued', () => {
  it('lists the files with their checksums and offers a revision', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 2, files: [pdf], render_status: 'done', issued_by_name: 'Admin',
      issued_at: '2026-09-21T00:00:00Z', template_fingerprint: 'f'.repeat(64), can_edit: false, can_issue: false,
    }));
    mocked.reviseClientReport.mockResolvedValue(report({ id: 12 }));
    renderDetail();
    expect(await screen.findByText('ab'.repeat(32))).toBeInTheDocument();
    expect(screen.getByLabelText('Title')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Revise' }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/reports/12'));
  });

  it('shows a failed render and renders again', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 2, render_status: 'failed', render_error: 'Quarto failed', can_edit: false, can_issue: false,
    }));
    mocked.rerenderClientReport.mockResolvedValue(report({ status: 'issued', number: 2, render_status: 'pending' }));
    renderDetail();
    expect(await screen.findByText('Quarto failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Render again/ }));
    await waitFor(() => expect(mocked.rerenderClientReport).toHaveBeenCalledWith(5));
  });
});

// Keep React referenced for the JSX transform in older setups.
void React;
