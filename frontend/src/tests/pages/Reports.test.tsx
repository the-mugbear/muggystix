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
  getProjectReportTeam: vi.fn(),
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
    // Opens the finding's report text in the editor, where it can be drafted.
    expect(screen.getByRole('link', { name: 'Weak TLS' })).toHaveAttribute('href', '/findings/42?edit=report-text');
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

  // Review 2026-09-23 B-UI-7: Back discarded an unsaved narrative silently.
  it('asks before leaving with unsaved changes, and not otherwise', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    const back = await screen.findByRole('button', { name: /Reports/ });

    navigateSpy.mockClear(); confirmMock.mockClear();
    fireEvent.click(back);
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/reports'));
    expect(confirmMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Executive summary'), { target: { value: 'Unsaved words.' } });
    navigateSpy.mockClear();
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(back);
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(navigateSpy).not.toHaveBeenCalled();
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

describe('Report detail — unsaved changes (v5.261.1)', () => {
  it('says why preview and issue are unavailable, where they are', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    fireEvent.change(await screen.findByLabelText('Client'), { target: { value: 'Other Corp' } });
    expect(screen.getByText('Save your changes below before issuing.')).toBeInTheDocument();
    expect(screen.getByText(/save them to preview them/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview PDF' })).toBeDisabled();
    expect(screen.getByText(/no members to pick from/)).toBeInTheDocument();
  });
});

describe('Report detail — TODOs and the team (v5.263.0)', () => {
  it('lists the empty report details as TODOs before issuing', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      summary: { ...report().summary, missing_details: ['executive summary', 'project dates'] },
    }));
    renderDetail();
    expect(await screen.findByText(/Report details still empty/)).toHaveTextContent(
      'Report details still empty: executive summary, project dates (project dates are set in Project settings).',
    );
    expect(screen.getByRole('link', { name: 'Project settings' })).toHaveAttribute('href', '/project-settings');
  });

  it("adds the project's members to the team once each", async () => {
    mocked.getClientReport.mockResolvedValue(report({
      settings: { ...settings, testers: [{ user_id: 1, name: 'Ana (edited)', role: 'Lead', email: null }] },
    }));
    mocked.listProjectMembers.mockResolvedValue([
      { id: 1, project_id: 1, user_id: 1, username: 'ana', full_name: 'Ana', role: 'admin', created_at: '' },
      { id: 2, project_id: 1, user_id: 2, username: 'ben', full_name: 'Ben', role: 'analyst', created_at: '' },
    ]);
    mocked.getProjectReportTeam.mockResolvedValue([
      { user_id: 1, name: 'Ana', role: 'Engagement lead', email: 'ana@example.com' },
      { user_id: 2, name: 'Ben', role: 'Tester', email: 'ben@example.com' },
    ]);
    mocked.updateClientReport.mockResolvedValue(report());
    renderDetail();
    await screen.findByDisplayValue('Ana (edited)');
    fireEvent.click(screen.getByRole('button', { name: /Add the project's members/ }));
    // Ben is added; Ana keeps what was written for her.
    await waitFor(() => expect(screen.getByDisplayValue('Ben')).toBeInTheDocument());
    expect(screen.queryByDisplayValue('Ana')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(5, expect.objectContaining({
      settings: expect.objectContaining({ testers: [
        { user_id: 1, name: 'Ana (edited)', role: 'Lead', email: null },
        { user_id: 2, name: 'Ben', role: 'Tester', email: 'ben@example.com' },
      ] }),
    })));
  });
});

describe('Template images', () => {
  const asset = (over: Record<string, unknown>) => ({
    id: 'logo', path: 'img/logo.png', label: 'Company logo', description: 'Above the title',
    note: '', required: false, formats: ['html', 'pdf'], present: false, ...over,
  });
  const withAssets = (assets: unknown[]) => mocked.listReportTemplates.mockResolvedValue([
    { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html', 'docx', 'pdf'], assets },
  ]);

  it('lists each image with where it goes, and a missing required one blocks preview and issue', async () => {
    withAssets([
      asset({ id: 'cover', path: 'img/cover.png', label: 'Cover art', required: true, description: 'Page one' }),
      asset({ present: true, note: 'A Word header belongs in reference.docx.' }),
    ]);
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    expect(await screen.findByText('Missing · required')).toBeInTheDocument();
    expect(screen.getByText('Installed')).toBeInTheDocument();
    expect(screen.getByText('1 missing')).toBeInTheDocument();
    expect(screen.getByText('report-templates/pentest/img/cover.png')).toBeInTheDocument();
    expect(screen.getByText('A Word header belongs in reference.docx.')).toBeInTheDocument();
    expect(screen.getAllByText('Used in HTML, PDF')).toHaveLength(2);

    const previewPdf = screen.getByRole('button', { name: 'Preview PDF' });
    expect(previewPdf).toBeDisabled();
    expect(previewPdf).toHaveAttribute('title', expect.stringContaining('Cover art'));
    expect(screen.getByRole('button', { name: /Issue report/ })).toBeDisabled();
    expect(screen.getByText(/A required template image is not installed/)).toBeInTheDocument();
  });

  it('an optional image that is missing never blocks', async () => {
    withAssets([asset({})]);
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    expect(await screen.findByText('Missing · optional')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview PDF' })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Issue report/ })).toBeEnabled();
  });

  it('says so when a template uses no images of its own', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    expect(await screen.findByText(/uses no images of its own/)).toBeInTheDocument();
  });

  it("shows the default template's images on the Reports page", async () => {
    withAssets([asset({ required: true })]);
    mocked.listClientReports.mockResolvedValue({ items: [], latest_issued_id: null, can_create: true, can_issue: true });
    renderList();
    expect(await screen.findByText('Missing · required')).toBeInTheDocument();
    expect(screen.getByText('report-templates/pentest/img/logo.png')).toHaveAttribute(
      'title', 'report-templates/pentest/img/logo.png',
    );
  });
});
