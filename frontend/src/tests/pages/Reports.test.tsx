/**
 * The Reports pages: the list (/reports), the report workspace
 * (/reports/:id), and the templates (/reports/templates).  The report
 * workspace (5.394.0; owner 2026-10-14) moved things:
 *   - the list holds drafts and issued reports, "New report" opens a small
 *     dialog (title, template; a draft in progress is named, several are
 *     counted with "View drafts"), and the defaults and templates have pages
 *     of their own (ReportDefaults.test; the templates' cases are below);
 *   - a draft's page is Details / Review / Preview & issue (`?section=`),
 *     with an action bar; the template saves with Save draft.
 *
 * Removed on purpose with what they guarded: the defaults' read view on the
 * list (`profileWritten`: the defaults page shows the values themselves);
 * discarding a draft from the continue-or-start dialog (a draft is discarded
 * on its own page, Preview & issue); saving a template change at once.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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
  listReportTemplateProblems: vi.fn(),
  uploadReportTemplateAsset: vi.fn(),
  removeReportTemplateAsset: vi.fn(),
  fetchReportTemplateAssetPreview: vi.fn(() => Promise.reject(new Error('no preview in tests'))),
  getReportProfile: vi.fn(),
  // The project's own report images (the System description's): none here.
  listReportTextImages: vi.fn(() => Promise.resolve([])),
  uploadReportTextImage: vi.fn(),
  downloadClientReportFile: vi.fn(),
  listProjectMembers: vi.fn(),
  getProjectReportTeam: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
  draftReportWithAI: vi.fn(),
  listLLMProviders: vi.fn(),
  getClientReportFollowers: vi.fn(),
  promoteClientReportDetails: vi.fn(),
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
// A global admin by default (server paths shown); tests switch to a member.
const auth = vi.hoisted(() => ({ user: { id: 99, username: 'admin', full_name: 'Administrator', role: 'admin' } as Record<string, unknown> }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));
// setupTests renders no tooltip content; a template's description and files
// line are on its (i), read here beside it.
vi.mock('../../components/ui/info-tip', () => ({
  InfoTip: ({ text, label }: { text: React.ReactNode; label?: string }) => (
    <span data-testid="info-tip" aria-label={label} role="note">{text}</span>
  ),
}));

import * as api from '../../services/api';
import Reports from '../../pages/Reports';
import ReportDetail from '../../pages/ReportDetail';
import ReportTemplates from '../../pages/ReportTemplates';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

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
  can_edit: true, can_issue: true, can_discard: true, ...over,
});

const pdf = { format: 'pdf', filename: 'x-report-01.pdf', media_type: 'application/pdf', size_bytes: 10, sha256: 'ab'.repeat(32), created_at: null };

beforeEach(() => {
  vi.clearAllMocks();
  resetProjectMembersCache();
  auth.user = { id: 99, username: 'admin', full_name: 'Administrator', role: 'admin' };
  mocked.listReportTemplates.mockResolvedValue([{ name: 'pentest', title: 'Penetration test report', description: '', formats: ['html', 'docx'] }]);
  mocked.getReportProfile.mockResolvedValue({ ...settings, template: 'pentest' });
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.listReportTemplateProblems.mockResolvedValue([]);
});

const renderList = () => render(<MemoryRouter><Reports /></MemoryRouter>);
const renderTemplates = () => render(<MemoryRouter><ReportTemplates /></MemoryRouter>);
const renderDetail = (search = '') =>
  render(
    <MemoryRouter initialEntries={[`/reports/5${search}`]}>
      <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
    </MemoryRouter>,
  );
const tab = (name: RegExp | string) => fireEvent.click(screen.getByRole('tab', { name }));
const section = (id: string) => within(screen.getByTestId(`report-section-${id}`));

describe('Reports list', () => {
  it('says what was issued, links the defaults and templates, and starts an addendum to the latest', async () => {
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
    expect(screen.getByText('Latest')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download x-report-01.pdf' })).toBeInTheDocument();
    const links = screen.getByTestId('reports-config-links');
    expect(within(links).getByRole('link', { name: 'Report defaults' })).toHaveAttribute('href', '/reports/defaults');
    expect(within(links).getByRole('link', { name: 'Templates' })).toHaveAttribute('href', '/reports/templates');

    mocked.createClientReport.mockResolvedValue(report({ id: 11, kind: 'addendum' }));
    fireEvent.click(screen.getByRole('button', { name: /New addendum to #2/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start the addendum' }));
    await waitFor(() => expect(mocked.createClientReport).toHaveBeenCalledWith(1, { kind: 'addendum', template: 'pentest' }));
    expect(navigateSpy).toHaveBeenCalledWith('/reports/11');
  });

  it('marks only the newest issued report; an older one that was not revised is a dash', async () => {
    mocked.listClientReports.mockResolvedValue({
      items: [
        report({ id: 8, status: 'issued', number: 3, issued_at: '2026-09-22T00:00:00Z', render_status: 'done' }),
        report({ id: 7, status: 'issued', number: 2, issued_at: '2026-09-21T00:00:00Z', render_status: 'done' }),
      ],
      latest_issued_id: 8, can_create: true, can_issue: false,
    });
    renderList();
    const table = await screen.findByRole('table', { name: 'Issued reports' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('Latest');
    expect(rows[1]).not.toHaveTextContent('Latest');
    expect(rows[1]).toHaveTextContent('—');
    expect(within(table).queryByText('Current')).toBeNull();
  });

  it('reads the list again while a report renders, and stops once its files are there', async () => {
    const issued = (render_status: string, files: unknown[] = []) => ({
      items: [report({ id: 7, status: 'issued', number: 2, issued_at: '2026-09-21T00:00:00Z', render_status, files })],
      latest_issued_id: 7, can_create: true, can_issue: true,
    });
    mocked.listClientReports
      .mockResolvedValueOnce(issued('pending'))
      .mockResolvedValue(issued('done', [pdf]));
    renderList();
    expect(await screen.findByText('Rendering files…')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Download x-report-01.pdf' }, { timeout: 5000 })).toBeInTheDocument();
    const reads = mocked.listClientReports.mock.calls.length;
    await new Promise((r) => setTimeout(r, 3500));
    expect(mocked.listClientReports.mock.calls.length).toBe(reads);
  }, 12000);

  it('tells drafts with the same title apart by start time, overrides and template, under their number', async () => {
    const title = 'Demo — security assessment report';
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html', 'docx'] },
      { name: 'brief', title: 'Executive brief', description: '', formats: ['html', 'docx'] },
    ]);
    mocked.listClientReports.mockResolvedValue({
      items: [
        report({ id: 12, title, created_at: '2026-09-22T09:00:00Z', settings: {} }),
        report({ id: 13, title, created_at: '2026-09-22T15:30:00Z', template: 'brief', settings: { classification: 'Secret' } }),
      ],
      latest_issued_id: null, can_create: true, can_issue: false,
    });
    renderList();
    const a = await screen.findByTestId('draft-meta-12');
    const b = screen.getByTestId('draft-meta-13');
    expect(a).toHaveTextContent(/^started .+ · follows the project’s details$/);
    await waitFor(() => expect(b).toHaveTextContent(/^started .+ · overrides 1 detail: classification · template Executive brief$/));
    expect(screen.getByTestId('draft-number-12')).toHaveTextContent(/^#12$/);
    expect(a).not.toHaveClass('truncate');
    expect(a).toHaveAttribute('title', a.textContent!);
    expect(mocked.getClientReport).not.toHaveBeenCalled();
  });

  it('renames a draft in place and flags drafts that share a title', async () => {
    const title = 'Demo — security assessment report';
    mocked.listClientReports.mockResolvedValue({
      items: [report({ id: 12, title }), report({ id: 13, title })],
      latest_issued_id: null, can_create: true, can_issue: false,
    });
    renderList();
    await screen.findByTestId('draft-meta-12');
    expect(screen.getAllByText('(same title)')).toHaveLength(2);
    mocked.updateClientReport.mockResolvedValue(report({ id: 12, title: 'Internal network — round 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rename draft #12' }));
    fireEvent.change(screen.getByLabelText('New title for draft #12'), { target: { value: 'Internal network — round 2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 12, { title: 'Internal network — round 2' }));
    expect(await screen.findByRole('link', { name: 'Internal network — round 2' })).toBeInTheDocument();
    expect(screen.queryByText('(same title)')).not.toBeInTheDocument();
  });

  // Walkthrough 2026-10-14: "New report" made a third draft under one title;
  // the report workspace keeps the guard, as one line in a light dialog.
  describe('New report', () => {
    const issued7 = report({ id: 7, status: 'issued', number: 2, issued_at: '2026-09-21T00:00:00Z', render_status: 'done' });
    const list = (items: unknown[]) => ({ items, latest_issued_id: 7, can_create: true, can_issue: true });

    it('with no draft in progress, starts one with the title and template chosen', async () => {
      mocked.listReportTemplates.mockResolvedValue([
        { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'], kind: 'client' },
        { name: 'brief', title: 'Executive brief', description: '', formats: ['html'], kind: 'client' },
      ]);
      mocked.listClientReports.mockResolvedValue(list([issued7]));
      mocked.createClientReport.mockResolvedValue(report({ id: 31 }));
      renderList();
      fireEvent.click(await screen.findByRole('button', { name: /^New report/ }));
      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).queryByTestId('new-report-in-progress')).toBeNull();
      fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: '  Round 2  ' } });
      fireEvent.keyDown(within(dialog).getByRole('combobox', { name: 'Template' }), { key: 'Enter' });
      fireEvent.click(await screen.findByRole('option', { name: 'Executive brief' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Start the draft' }));
      await waitFor(() => expect(mocked.createClientReport).toHaveBeenCalledWith(1, { kind: 'full', title: 'Round 2', template: 'brief' }));
      expect(navigateSpy).toHaveBeenCalledWith('/reports/31');
    });

    // Second review 2026-10-14 (nitpick): a failed read of the project's
    // default sent the first template in its place, silently.
    it('when the project’s default cannot be read, sends no template and says so', async () => {
      mocked.getReportProfile.mockRejectedValue(new Error('down'));
      mocked.listReportTemplates.mockResolvedValue([
        { name: 'brief', title: 'Executive brief', description: '', formats: ['html'], kind: 'client' },
        { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'], kind: 'client' },
      ]);
      mocked.listClientReports.mockResolvedValue(list([issued7]));
      mocked.createClientReport.mockResolvedValue(report({ id: 32 }));
      renderList();
      fireEvent.click(await screen.findByRole('button', { name: /^New report/ }));
      const dialog = await screen.findByRole('dialog');
      expect(await within(dialog).findByTestId('new-report-default-unknown')).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Start the draft' }));
      await waitFor(() => expect(mocked.createClientReport).toHaveBeenCalledWith(1, { kind: 'full' }));
    });

    it('names the one draft in progress, and continues it instead', async () => {
      mocked.listClientReports.mockResolvedValue(list([
        report({ id: 23, title: 'Latest draft', updated_at: '2026-10-13T08:00:00Z' }),
        report({ id: 30, kind: 'addendum', baseline: { id: 7, number: 2 }, title: 'An addendum' }),
        issued7,
      ]));
      renderList();
      fireEvent.click(await screen.findByRole('button', { name: /^New report/ }));
      const note = await screen.findByTestId('new-report-in-progress');
      expect(note).toHaveTextContent('#23 Latest draft');                     // the addendum is not a report's draft
      fireEvent.click(within(note).getByRole('button', { name: 'Continue it' }));
      expect(navigateSpy).toHaveBeenCalledWith('/reports/23');
      expect(mocked.createClientReport).not.toHaveBeenCalled();
    });

    it('counts several drafts in progress, with "View drafts"', async () => {
      mocked.listClientReports.mockResolvedValue(list([report({ id: 21 }), report({ id: 22 }), issued7]));
      Element.prototype.scrollIntoView = vi.fn();
      renderList();
      fireEvent.click(await screen.findByRole('button', { name: /^New report/ }));
      const note = await screen.findByTestId('new-report-in-progress');
      expect(note).toHaveTextContent('2 draft reports are in progress.');
      fireEvent.click(within(note).getByRole('button', { name: 'View drafts' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    });

    it('an addendum names only drafts of an addendum to the same report', async () => {
      mocked.listClientReports.mockResolvedValue(list([
        report({ id: 23 }),
        report({ id: 30, kind: 'addendum', baseline: { id: 7, number: 2 }, title: 'Addendum draft' }),
        report({ id: 29, kind: 'addendum', baseline: { id: 6, number: 1 }, title: 'To an older report' }),
        issued7,
      ]));
      renderList();
      fireEvent.click(await screen.findByRole('button', { name: /New addendum to #2/ }));
      const note = await screen.findByTestId('new-report-in-progress');
      expect(note).toHaveTextContent('#30 Addendum draft');
    });
  });

  it('offers no addendum before anything is issued, and nothing to an auditor', async () => {
    mocked.listClientReports.mockResolvedValue({ items: [], latest_issued_id: null, can_create: true, can_issue: false });
    const { unmount } = renderList();
    expect(await screen.findByText('No report has been issued for this project yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New addendum/ })).toBeDisabled();
    expect(screen.getByText(/Issue a report first/)).toBeInTheDocument();
    unmount();

    mocked.listClientReports.mockResolvedValue({ items: [], latest_issued_id: null, can_create: false, can_issue: false });
    renderList();
    await screen.findByText('No report has been issued for this project yet.');
    expect(screen.queryByRole('button', { name: /New report/ })).not.toBeInTheDocument();
  });
});

describe('Report detail — draft', () => {
  it('shows what the draft contains and what is missing, on Review', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail('?section=review');
    expect(await screen.findByText('Before issuing')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Weak TLS' })).toHaveAttribute('href', '/findings/42?edit=report-text&report=5');
    expect(screen.getByText('missing impact, recommendation')).toBeInTheDocument();
    expect(screen.getByText('Still under investigation')).toBeInTheDocument();
  });

  it('previews on the worker and offers the file when it is ready', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.previewClientReport.mockResolvedValue({ id: 70, format: 'report-docx', status: 'queued' });
    mocked.getReportJob.mockResolvedValue({ id: 70, format: 'report-docx', status: 'completed' });
    mocked.downloadReportJob.mockResolvedValue('example-report-preview.docx');
    renderDetail('?section=issue');
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Word' }));
    await waitFor(() => expect(mocked.previewClientReport).toHaveBeenCalledWith(1, 5, 'docx'));
    fireEvent.click(await screen.findByRole('button', { name: 'Download Word preview' }, { timeout: 4000 }));
    await waitFor(() => expect(mocked.downloadReportJob).toHaveBeenCalledWith(1, 70));
  });

  it('the action bar’s Preview renders HTML and goes to Preview & issue', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.previewClientReport.mockResolvedValue({ id: 72, format: 'report-html', status: 'queued' });
    mocked.getReportJob.mockResolvedValue({ id: 72, format: 'report-html', status: 'queued' });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(mocked.previewClientReport).toHaveBeenCalledWith(1, 5, 'html'));
    expect(screen.getByRole('tab', { name: 'Preview & issue' })).toHaveAttribute('aria-selected', 'true');
  });

  // 5.352.0 (owner) — a failed status poll is said, and the job is followed on.
  it('says the status may be stale when a preview job cannot be re-read, and carries on', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.previewClientReport.mockResolvedValue({ id: 71, format: 'report-docx', status: 'queued' });
    mocked.getReportJob
      .mockRejectedValueOnce(Object.assign(new Error('boom'), {
        isAxiosError: true, response: { status: 503, data: { detail: 'The server is busy.' } },
      }))
      .mockResolvedValue({ id: 71, format: 'report-docx', status: 'completed' });
    renderDetail('?section=issue');
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Word' }));
    const stale = await screen.findByText(/Status may be stale/, undefined, { timeout: 4000 });
    expect(stale).toHaveTextContent('Status may be stale — The server is busy.');
    expect(screen.getByRole('button', { name: /Rendering Word/ })).toBeDisabled();
    expect(await screen.findByRole('button', { name: 'Download Word preview' }, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.queryByText(/Status may be stale/)).toBeNull();
  }, 15000);

  it('offers no PDF preview and says where a PDF comes from', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail('?section=issue');
    expect(await screen.findByRole('button', { name: 'Preview Word' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /PDF/ })).not.toBeInTheDocument();
    expect(screen.getByText(/For a PDF, open the Word report and export it/)).toBeInTheDocument();
  });

  // Review 2026-09-23 B-UI-7: Back discarded unsaved work silently.
  it('asks before leaving with unsaved changes, and not otherwise', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    const back = await screen.findByRole('button', { name: /Reports/ });
    navigateSpy.mockClear(); confirmMock.mockClear();
    fireEvent.click(back);
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/reports'));
    expect(confirmMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Unsaved title' } });
    navigateSpy.mockClear();
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(back);
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('saves the draft from the action bar, and will not issue with unsaved changes', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.updateClientReport.mockResolvedValue(report({ title: 'Round 2' }));
    renderDetail();
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Round 2' } });
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('Unsaved changes — Report');
    tab('Preview & issue');
    expect(screen.getByRole('button', { name: /Issue report/ })).toBeDisabled();
    expect(screen.getByText('Save your changes before issuing.')).toBeInTheDocument();
    expect(screen.getByTestId('preview-pending')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save & preview Word' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    // A title-only save sends no detail: a stored override someone else
    // changed meanwhile is never reverted to this page's copy (review
    // 2026-10-14, H1).
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, { title: 'Round 2', settings: {} }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Issue report/ })).toBeEnabled());
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('All changes saved');
  });

  // Review 2026-10-10 H10 — the draft is asked for THIS report.
  it('"Draft with AI" names the report it drafts the summary of', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.listLLMProviders.mockResolvedValue([{ id: 3, name: 'local', provider_type: 'ollama', model_id: 'm', is_default: true }]);
    mocked.draftReportWithAI.mockResolvedValue({
      content: 'Two criticals.', provider_id: 3, provider_type: 'ollama', model_id: 'm',
      finding_total: 4, severity_counts: { critical: 1 }, usage: null,
    });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Executive summary' }));
    fireEvent.click(screen.getByRole('button', { name: /Draft with AI/ }));
    const generate = await screen.findByRole('button', { name: 'Generate draft' });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await waitFor(() => expect(mocked.draftReportWithAI).toHaveBeenCalledWith(
      1, expect.objectContaining({ report_id: 5, provider_id: 3 }), expect.anything(),
    ));
  });

  it('issues only after confirmation', async () => {
    const issuedReport = report({ status: 'issued', number: 3, render_status: 'pending', can_edit: false, can_issue: false });
    mocked.getClientReport.mockResolvedValueOnce(report()).mockResolvedValue(issuedReport);
    mocked.issueClientReport.mockResolvedValue(issuedReport);
    renderDetail('?section=issue');
    const issue = await screen.findByRole('button', { name: /Issue report/ });
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(issue);
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(mocked.issueClientReport).not.toHaveBeenCalled();
    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(issue);
    await waitFor(() => expect(mocked.issueClientReport).toHaveBeenCalledWith(1, 5));
    expect(await screen.findByText('Issued #3')).toBeInTheDocument();
  });

  it('says once, when the render it watched ends, that the files are ready — and offers them', async () => {
    const pending = report({ status: 'issued', number: 3, render_status: 'pending', can_edit: false, can_issue: false });
    const done = report({ ...pending, render_status: 'done', files: [pdf] });
    mocked.getClientReport.mockResolvedValueOnce(pending).mockResolvedValue(done);
    renderDetail();
    expect(await screen.findByText('Rendering files…')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Download x-report-01.pdf' }, { timeout: 5000 })).toBeInTheDocument();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith(
      'Report #3 is issued and its files are ready to download.', { id: 'report-files-ready-5' },
    ));
    expect(toastMock.success.mock.calls.filter((c) => String(c[0]).includes('files are ready'))).toHaveLength(1);
  }, 12000);

  it('says nothing about files for a report opened after they were rendered', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 3, render_status: 'done', files: [pdf], can_edit: false, can_issue: false,
    }));
    renderDetail();
    expect(await screen.findByRole('button', { name: 'Download x-report-01.pdf' })).toBeInTheDocument();
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('says before issuing how many notes go to the client with their images, and that it issues the data as it stands', async () => {
    const base = report();
    mocked.getClientReport.mockResolvedValue(report({ summary: { ...base.summary, missing_text: [], evidence_notes: 2 } }));
    renderDetail('?section=review');
    const sentence = '2 notes are printed as written before the images attached to them, under Evidence.';
    expect(await screen.findByTestId('report-evidence-notes')).toHaveTextContent(sentence);
    tab('Preview & issue');
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: /Issue report/ }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    const dialog = render(<div>{confirmMock.mock.calls[0][0].body}</div>);
    expect(dialog.container).toHaveTextContent(sentence);
    expect(dialog.container).toHaveTextContent('It is built from the findings and details as they stand at this moment, not from an earlier preview.');
  });

  it('the issue dialog names the template the report is issued with', async () => {
    const base = report();
    mocked.getClientReport.mockResolvedValue(report({ summary: { ...base.summary, missing_text: [] } }));
    renderDetail('?section=issue');
    const issue = await screen.findByRole('button', { name: /Issue report/ });
    await waitFor(() => expect(mocked.listReportTemplates).toHaveBeenCalled());
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(issue);
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    const dialog = render(<div>{confirmMock.mock.calls[0][0].body}</div>);
    expect(dialog.getByTestId('issue-template')).toHaveTextContent(/^It is issued with the .+ template/);
  });

  // Owner, 2026-10-14: every detail follows the project unless the report
  // overrides it.  The page saves the override map and the keys edited.
  it('a draft follows the project’s details; one is customised, another set back, and both are sent as edited', async () => {
    const followed = { ...settings, author_name: 'Example Security Ltd', classification: 'Confidential' };
    mocked.getClientReport.mockResolvedValue(report({
      settings: { client_name: 'Ours Ltd' }, followed, effective: { ...followed, client_name: 'Ours Ltd' },
      followed_from: { client_name: 'project', author_name: 'installation', classification: 'project' },
    }));
    const renamed = { ...followed, classification: 'Public' };
    mocked.updateClientReport.mockResolvedValue(report({
      settings: { author_name: 'Red Team Ltd' }, followed: renamed, effective: { ...renamed, author_name: 'Red Team Ltd' },
      followed_from: { client_name: 'project', author_name: 'installation', classification: 'project' },
    }));
    renderDetail();
    await screen.findByTestId('report-section-engagement');
    expect(await section('engagement').findByText('Example Security Ltd')).toBeInTheDocument();
    expect(section('engagement').getByText('Ours Ltd')).toBeInTheDocument();
    expect(section('engagement').getByTestId('report-section-engagement-status')).toHaveTextContent('1 customised for this report');

    fireEvent.click(screen.getByRole('button', { name: 'Use the project’s Client' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Client & engagement' }));
    fireEvent.change(screen.getByLabelText('Author'), { target: { value: 'Red Team Ltd' } });
    fireEvent.click(section('engagement').getByRole('button', { name: 'Done' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, {
      settings: { client_name: null, author_name: 'Red Team Ltd' },
    }));
    // The answer: the project changed meanwhile; a followed detail shows it.
    await waitFor(() => expect(section('engagement').getByText('Public')).toBeInTheDocument());
    expect(section('engagement').getByText('Red Team Ltd')).toBeInTheDocument();
  });

  it('the executive summary follows the project’s, and is customised for this report on request', async () => {
    const followed = { ...settings, executive_summary: 'The project’s summary.' };
    mocked.getClientReport.mockResolvedValue(report({
      settings: {}, followed, effective: followed, followed_from: { executive_summary: 'project' },
    }));
    mocked.updateClientReport.mockResolvedValue(report());
    renderDetail();
    await screen.findByTestId('report-section-summary');
    expect(await section('summary').findByText('The project’s summary.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Executive summary' }));
    const box = screen.getByRole('textbox', { name: 'Executive summary' });
    expect(box).toHaveValue('The project’s summary.');
    fireEvent.change(box, { target: { value: 'Ours.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, {
      settings: { executive_summary: 'Ours.' },
    }));
  });

  it('an addendum’s summary of changes is its own: a field, never the project’s', async () => {
    const followed = { ...settings, executive_summary: null };
    mocked.getClientReport.mockResolvedValue(report({
      kind: 'addendum', baseline: { id: 1, number: 1 }, settings: {}, followed, effective: followed,
      followed_from: { executive_summary: null },
    }));
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Summary of changes' }));
    expect(screen.getByRole('textbox', { name: 'Summary of changes' })).toHaveValue('');
    expect(screen.queryByTestId('report-section-summary-status')).toBeNull();
    expect(screen.queryByRole('button', { name: /Use the project’s Summary of changes/ })).toBeNull();
  });
});

describe('Report detail — issued', () => {
  it('opens on its files with their checksums, and offers a revision', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 2, files: [pdf], render_status: 'done', issued_by_name: 'Admin',
      issued_at: '2026-09-21T00:00:00Z', template_fingerprint: 'f'.repeat(64), can_edit: false, can_issue: false,
    }));
    mocked.reviseClientReport.mockResolvedValue(report({ id: 12 }));
    renderDetail();
    expect(await screen.findByText('ab'.repeat(32))).toBeInTheDocument();
    expect(screen.queryByTestId('report-action-bar')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Revise' }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/reports/12'));
  });

  it('v5.290.0 — shows the details as issued, with no add or remove control at all', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 1, files: [pdf], render_status: 'done', issued_at: '2026-09-21T00:00:00Z',
      can_edit: false, can_issue: false,
      effective: {
        ...settings,
        testers: [{ user_id: 1, name: 'Ana', role: 'Lead', email: null }],
        distribution: [{ name: 'CISO', email: null }],
      },
    }));
    renderDetail('?section=details');
    expect(await screen.findByText('Ana')).toBeInTheDocument();
    expect(screen.getByText('CISO')).toBeInTheDocument();
    for (const name of [/Add the project's members/, /Someone else/, /Add recipient/, /Add yourself/, /^Remove /, /^Edit /]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('shows a failed render and renders again', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 2, render_status: 'failed', render_error: 'Quarto failed', can_edit: false, can_issue: false,
    }));
    mocked.rerenderClientReport.mockResolvedValue(report({ status: 'issued', number: 2, render_status: 'pending' }));
    renderDetail();
    expect(await screen.findByText('Quarto failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Render again/ }));
    await waitFor(() => expect(mocked.rerenderClientReport).toHaveBeenCalledWith(1, 5));
  });
});

describe('Report detail — layout', () => {
  const missing = (n: number) => Array.from({ length: n }, (_, i) => ({
    id: 100 + i, ref: `F-${String(i + 1).padStart(2, '0')}`, title: `Finding ${i + 1}`, missing: ['impact'],
  }));

  it('lists the first ten findings missing text, then all of them on request', async () => {
    mocked.getClientReport.mockResolvedValue(report({ summary: { ...report().summary, missing_text: missing(23) } }));
    renderDetail('?section=review');
    const group = await screen.findByTestId('checklist-missing-text');
    expect(within(group).getByRole('heading', { name: /Findings missing report text/ })).toHaveTextContent(/23$/);
    expect(screen.getByRole('link', { name: 'Finding 10' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Finding 11' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show all 23' }));
    expect(screen.getByRole('link', { name: 'Finding 23' })).toBeInTheDocument();
  });

  it('does not lead with Issue while parts still print as TODO', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    const { unmount } = renderDetail('?section=issue');
    expect(await screen.findByRole('button', { name: /Issue report/ })).toBeEnabled();
    expect(screen.getByText(/Parts of this report still print as TODO/)).toBeInTheDocument();
    unmount();

    mocked.getClientReport.mockResolvedValue(report({ summary: { ...report().summary, missing_text: [], missing_details: [] } }));
    renderDetail('?section=issue');
    await screen.findByRole('button', { name: /Issue report/ });
    expect(screen.queryByText(/Parts of this report still print as TODO/)).not.toBeInTheDocument();
  });

  it('says what the chosen template is for when there is a choice', async () => {
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'pentest', title: 'Penetration test report', description: 'Findings-first client report.', formats: ['html', 'docx'] },
      { name: 'executive-brief', title: 'Executive brief', description: 'A short report for leadership.', formats: ['docx', 'html'] },
    ]);
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    expect(await screen.findByTestId('template-description')).toHaveTextContent('Findings-first client report.');
    expect(screen.getByTestId('template-description').closest('[role="note"]')).toHaveAccessibleName('About this template');
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Template' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('option', { name: 'Executive brief' }));
    await waitFor(() => expect(screen.getByTestId('template-description')).toHaveTextContent('A short report for leadership.'));
  });

  it('offers a global admin who is not a member to add themself to the team', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.updateClientReport.mockResolvedValue(report());
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Team & distribution' }));
    expect(screen.getByText(/Nobody is a member of this project yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Add yourself/ }));
    expect(screen.getByDisplayValue('Administrator')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add yourself/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, expect.objectContaining({
      settings: expect.objectContaining({ testers: [{ user_id: 99, name: 'Administrator', role: null, email: null }] }),
    })));
  });

  it('describes evidence images as opt-in', async () => {
    mocked.getClientReport.mockResolvedValue(report({ summary: { ...report().summary, images: 0 } }));
    renderDetail('?section=review');
    expect(await screen.findByText(/Tick “In report” on a finding’s image to include it/)).toBeInTheDocument();
  });
});

describe('Report detail — TODOs and the team (v5.263.0)', () => {
  it('lists the empty report details as TODOs before issuing', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      summary: { ...report().summary, missing_details: ['executive summary', 'project dates'] },
    }));
    renderDetail('?section=review');
    const todo = await screen.findByTestId('checklist-details');
    expect(within(todo).getByRole('heading', { name: /Report details to fill/ })).toHaveTextContent(/TODO.*2$/);
    // A summary with no keys (frozen before them) is read by its labels.
    expect(within(todo).getByRole('button', { name: 'Go to executive summary in Details' })).toBeInTheDocument();
    expect(within(todo).getByRole('link', { name: 'project dates' })).toHaveAttribute('href', '/project-settings');
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
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Team & distribution' }));
    await screen.findByDisplayValue('Ana (edited)');
    fireEvent.click(screen.getByRole('button', { name: /Add the project's members/ }));
    await waitFor(() => expect(screen.getByDisplayValue('Ben')).toBeInTheDocument());
    expect(screen.queryByDisplayValue('Ana')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, expect.objectContaining({
      settings: expect.objectContaining({ testers: [
        { user_id: 1, name: 'Ana (edited)', role: 'Lead', email: null },
        { user_id: 2, name: 'Ben', role: 'Tester', email: 'ben@example.com' },
      ] }),
    })));
  });
});

// Keep React referenced for the JSX transform in older setups.
void React;

describe('Template images', () => {
  const asset = (over: Record<string, unknown>) => ({
    id: 'logo', path: 'img/logo.png', label: 'Company logo', description: 'Above the title',
    note: '', required: false, formats: ['html'], present: false, ...over,
  });
  const withAssets = (assets: unknown[]) => mocked.listReportTemplates.mockResolvedValue([
    { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html', 'docx'], assets },
  ]);

  it('on a draft: lists each image with where it goes, and a missing required one blocks preview and issue', async () => {
    withAssets([
      asset({ id: 'cover', path: 'img/cover.png', label: 'Cover art', required: true, description: 'Page one' }),
      asset({ present: true, note: 'A Word header belongs in reference.docx.' }),
    ]);
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    expect(await screen.findByText('Missing · required')).toBeInTheDocument();
    expect(screen.getByText('Installed on server')).toBeInTheDocument();
    expect(screen.getByText(/1 required missing — preview and issue are unavailable/)).toBeInTheDocument();
    expect(screen.getByText('report-templates/pentest/img/cover.png')).toBeInTheDocument();
    expect(screen.getByText('A Word header belongs in reference.docx.')).toBeInTheDocument();
    const bar = screen.getByRole('button', { name: 'Preview' });
    expect(bar).toBeDisabled();
    expect(bar).toHaveAttribute('title', expect.stringContaining('Cover art'));
    tab('Preview & issue');
    expect(screen.getByRole('button', { name: 'Preview Word' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Issue report/ })).toBeDisabled();
  });

  it('a Word styles file that replaces the shipped one is never a gap', async () => {
    withAssets([asset({
      id: 'reference-docx', path: 'branding/reference.docx', label: 'Word styles (reference.docx)',
      replaces: 'reference.docx', formats: ['docx'],
    })]);
    mocked.getClientReport.mockResolvedValue(report());
    const { unmount } = renderDetail();
    expect(await screen.findByText(/not installed, the shipped file is used/)).toBeInTheDocument();
    expect(screen.queryByText('Not installed · shipped used')).not.toBeInTheDocument();
    tab('Preview & issue');
    expect(screen.getByRole('button', { name: /Issue report/ })).toBeEnabled();
    unmount();

    renderTemplates();
    expect(await screen.findByText('Not installed · shipped used')).toBeInTheDocument();
    expect(screen.getByText(/When installed, used in place of the template/)).toBeInTheDocument();
    expect(screen.getByText(/HTML, Word$/).textContent).not.toMatch(/missing|not installed/);
  });

  it('an optional image that is missing never blocks, and is not called missing', async () => {
    withAssets([asset({})]);
    mocked.getClientReport.mockResolvedValue(report());
    const { unmount } = renderDetail('?section=issue');
    expect(await screen.findByRole('button', { name: 'Preview Word' })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Issue report/ })).toBeEnabled();
    tab('Details');
    expect(await screen.findByText(/Company logo: not installed \(optional, left out\)/)).toBeInTheDocument();
    unmount();

    renderTemplates();
    expect((await screen.findByText(/1 optional not installed/)).textContent).not.toMatch(/missing/);
    expect(screen.getByText('Not installed · optional')).toBeInTheDocument();
    expect(screen.queryByText('Missing · optional')).not.toBeInTheDocument();
  });

  it('says so when a template uses no images of its own', async () => {
    renderTemplates();
    expect(await screen.findByText(/uses no images of its own/)).toBeInTheDocument();
  });

  it('lists every installed template on the templates page, the project’s default marked', async () => {
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'brief', title: 'Brief report', description: 'Two pages.', formats: ['html'] },
      { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html', 'docx'], assets: [asset({ required: true })] },
    ]);
    renderTemplates();
    expect(await screen.findByText('2 installed')).toBeInTheDocument();
    expect(screen.getByText('Project default')).toBeInTheDocument();
    expect(screen.getByText('Brief report')).toBeInTheDocument();
    expect(screen.getByText('Missing · required')).toBeInTheDocument();
    expect(screen.getByText('report-templates/pentest/img/logo.png')).toHaveAttribute(
      'title', 'report-templates/pentest/img/logo.png',
    );
    expect(screen.getByText('Add a template')).toBeInTheDocument();
    expect(screen.getByText(/is the starter: the smallest/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Report defaults' })).toHaveAttribute('href', '/reports/defaults');
  });

  it('tells an admin which template folders are not offered, and why', async () => {
    mocked.listReportTemplateProblems.mockResolvedValue([
      { name: 'My Template', error: 'The folder name must be lower-case letters, digits, …' },
      { name: 'draft-brief', error: 'The folder has no template.json.' },
    ]);
    renderTemplates();
    expect(await screen.findByText(/2 folders are/)).toBeInTheDocument();
    expect(screen.getByText('draft-brief')).toBeInTheDocument();
    expect(screen.getByText(/The folder has no template\.json\./)).toBeInTheDocument();
  });

  it('shows server paths and install steps only to a global admin', async () => {
    auth.user = { id: 7, username: 'ana', full_name: 'Ana', role: 'member' };
    withAssets([asset({ required: true })]);
    renderTemplates();
    expect(await screen.findByText('Missing · required')).toBeInTheDocument();
    expect(screen.queryByText('report-templates/pentest/img/logo.png')).not.toBeInTheDocument();
    expect(screen.queryByText('Add a template')).not.toBeInTheDocument();
    expect(mocked.listReportTemplateProblems).not.toHaveBeenCalled();
    expect(screen.getByText(/A global administrator uploads these files or installs them on the server/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Upload' })).not.toBeInTheDocument();
  });

  // v5.311.0 — uploading a template's own files, with the template's guidance.
  const logo = (over: Record<string, unknown> = {}) => asset({
    kind: 'png', uploadable: true, max_bytes: 2097152, min_width: 400, min_height: 100, aspect: '3.4:1',
    installed: false, source: null, upload: null, ...over,
  });
  const templatesPage = async () => {
    renderTemplates();
    return screen.findByTestId('asset-guidance-logo');
  };

  it('says what each file must be, from the template', async () => {
    withAssets([
      logo(),
      asset({ id: 'styles', label: 'Word styles', kind: 'docx', uploadable: true, max_bytes: 10485760 }),
      asset({ id: 'icon', label: 'Icon', kind: 'svg', uploadable: false }),
    ]);
    expect((await templatesPage()).textContent).toBe('PNG · at least 400 × 100 px · shaped 3.4:1 · up to 2 MB');
    expect(screen.getByTestId('asset-guidance-styles').textContent).toBe('Word document (.docx) · up to 10 MB');
    expect(screen.getByTestId('asset-guidance-icon').textContent).toBe('SVG — installed on the server by an administrator');
    expect(screen.getAllByRole('button', { name: 'Upload' })).toHaveLength(2);
  });

  it('refuses a file of the wrong type or size before sending it', async () => {
    withAssets([logo()]);
    await templatesPage();
    const input = screen.getByLabelText('Upload Company logo') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['x'], 'photo.jpg', { type: 'image/jpeg' })] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('Company logo must be a PNG; “photo.jpg” is not.');
    const big = new File([new Uint8Array(3 * 1048576)], 'huge.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [big] } });
    expect(await screen.findByText(/“huge.png” is 3 MB; Company logo can be at most 2 MB/)).toBeInTheDocument();
    expect(mocked.uploadReportTemplateAsset).not.toHaveBeenCalled();
  });

  it('uploads a file, shows it as uploaded with the server’s warning, and removes it', async () => {
    withAssets([logo()]);
    const uploaded = logo({
      present: true, source: 'uploaded',
      upload: { width: 500, height: 500, size: 4096, uploaded_by: 'admin', original_filename: 'square.png' },
    });
    mocked.uploadReportTemplateAsset.mockResolvedValue({
      template: { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'], assets: [uploaded] },
      warnings: ['The image is 500 × 500 px; this place is shaped 3.4:1.'],
    });
    await templatesPage();
    const file = new File(['png'], 'square.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('Upload Company logo'), { target: { files: [file] } });
    await waitFor(() => expect(mocked.uploadReportTemplateAsset).toHaveBeenCalledWith(1, 'pentest', 'logo', file));
    expect(await screen.findByText('Uploaded')).toBeInTheDocument();
    expect(screen.getByText(/this place is shaped 3.4:1/)).toBeInTheDocument();
    expect(screen.getByText(/Uploaded by admin/).textContent).toMatch(/500 × 500 px, 4 KB · square.png/);
    expect(screen.getByRole('button', { name: 'Replace' })).toBeInTheDocument();

    mocked.removeReportTemplateAsset.mockResolvedValue({
      template: { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'], assets: [logo()] },
      warnings: [],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove upload' }));
    await waitFor(() => expect(mocked.removeReportTemplateAsset).toHaveBeenCalledWith(1, 'pentest', 'logo'));
    expect(await screen.findByText('Not installed · optional')).toBeInTheDocument();
  });

  it('a template opened for a missing required file stays open once it is uploaded (review 2026-10-14)', async () => {
    withAssets([logo({ required: true })]);
    const uploaded = logo({
      required: true, present: true, source: 'uploaded',
      upload: { width: 500, height: 500, size: 4096, uploaded_by: 'admin', original_filename: 'logo.png' },
    });
    const done = { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'], assets: [uploaded] };
    mocked.uploadReportTemplateAsset.mockResolvedValue({ template: done, warnings: [] });
    await templatesPage();
    const details = screen.getByTestId('asset-guidance-logo').closest('details')!;
    expect(details).toHaveAttribute('open');
    mocked.listReportTemplates.mockResolvedValue([done]);
    fireEvent.change(screen.getByLabelText('Upload Company logo'), {
      target: { files: [new File(['png'], 'logo.png', { type: 'image/png' })] },
    });
    expect(await screen.findByText('Uploaded')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('asset-guidance-logo').closest('details')).toHaveAttribute('open'));
  });

  it('shows the server’s reason when it refuses an upload', async () => {
    withAssets([logo()]);
    mocked.uploadReportTemplateAsset.mockRejectedValue({
      response: { data: { detail: 'The image is 300 × 80 px; Company logo needs at least 400 × 100 px to print sharply.' } },
    });
    await templatesPage();
    fireEvent.change(screen.getByLabelText('Upload Company logo'), {
      target: { files: [new File(['png'], 'small.png', { type: 'image/png' })] },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('needs at least 400 × 100 px');
  });
});
