/**
 * Users reported downloading several copies of a report: neither a finished
 * preview nor a file being fetched gave any sign.  A finished preview is now
 * said once and its Download is the row's main button; every report file
 * (a preview, an issued report's files, the scope file) says "Downloading…"
 * while it is fetched, takes no second press, and is marked once saved.
 */
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { focusManager } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

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
  saveReportProfile: vi.fn(),
  listReportTextImages: vi.fn(() => Promise.resolve([])),
  downloadClientReportFile: vi.fn(),
  downloadClientReportScope: vi.fn(),
  listProjectMembers: vi.fn(),
  getProjectReportTeam: vi.fn(),
  getReportJob: vi.fn(),
  getClientReportInputs: vi.fn(),
  downloadReportJob: vi.fn(),
  draftReportWithAI: vi.fn(),
  listLLMProviders: vi.fn(),
}));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 99, username: 'admin', full_name: 'Administrator', role: 'admin' } }),
}));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));

import * as api from '../../services/api';
import Reports from '../../pages/Reports';
import ReportDetail from '../../pages/ReportDetail';
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
  summary: { counts: { critical: 0, high: 1, medium: 0, low: 0, info: 0, total: 1 }, missing_text: [], delta: null },
  can_edit: true, can_issue: true, ...over,
});
const file = (format: string) => ({
  format, filename: `acme-report-3.${format}`, media_type: 'application/octet-stream', size_bytes: 10,
  sha256: (format === 'html' ? 'ab' : 'cd').repeat(32), created_at: null,
});

/** A request the test answers when it chooses. */
const held = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
};
const readyToasts = (text: string) => toastMock.success.mock.calls.filter(([message]) => message === text).length;

// Previews are on the workspace's "Preview & issue" section (5.394.0).
const renderDetail = () =>
  render(
    <MemoryRouter initialEntries={['/reports/5?section=issue']}>
      <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  resetProjectMembersCache();
  mocked.listReportTemplates.mockResolvedValue([{ name: 'pentest', title: 'Penetration test report', description: '', formats: ['html', 'docx'] }]);
  mocked.getReportProfile.mockResolvedValue({ ...settings, template: 'pentest' });
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.listReportTemplateProblems.mockResolvedValue([]);
  mocked.getClientReportInputs.mockResolvedValue({ fingerprint: null });
});

describe('A draft’s previews', () => {
  it('says once that a preview is ready, however often the job is read or the page renders', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.previewClientReport.mockImplementation((_project: number, _report: number, fmt: string) =>
      Promise.resolve({ id: fmt === 'docx' ? 70 : 71, format: `report-${fmt}`, status: 'queued' }));
    // The Word job is read three times before it is done; the HTML one once.
    const reads: Record<number, number> = { 70: 0, 71: 0 };
    mocked.getReportJob.mockImplementation((_project: number, id: number) => {
      reads[id] += 1;
      const done = reads[id] >= (id === 70 ? 3 : 1);
      return Promise.resolve({ id, format: 'x', status: done ? 'completed' : 'processing' });
    });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Word' }));
    expect(await screen.findByRole('button', { name: /Rendering Word/ })).toBeDisabled();
    // Nothing is said while it renders.
    expect(toastMock.success).not.toHaveBeenCalled();

    expect(await screen.findByRole('button', { name: 'Download Word preview' }, { timeout: 9000 })).toBeEnabled();
    expect(readyToasts('The Word preview is ready.')).toBe(1);
    expect(reads[70]).toBe(3);

    // More polls and renders on the same page — another format's job.
    fireEvent.click(screen.getByRole('button', { name: 'Preview HTML' }));
    expect(await screen.findByRole('button', { name: 'Download HTML preview' }, { timeout: 7000 })).toBeEnabled();
    expect(readyToasts('The HTML preview is ready.')).toBe(1);
    expect(readyToasts('The Word preview is ready.')).toBe(1);
    expect(toastMock.success).toHaveBeenCalledTimes(2);
  }, 25000);

  it('a failed preview is not announced: its row says why', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.previewClientReport.mockResolvedValue({ id: 80, format: 'report-docx', status: 'queued' });
    mocked.getReportJob.mockResolvedValue({ id: 80, format: 'report-docx', status: 'failed', error_message: 'quarto exited 1' });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Word' }));
    expect(await screen.findByText(/Failed: quarto exited 1/, undefined, { timeout: 4000 })).toBeInTheDocument();
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Download Word preview/ })).toBeNull();
  });

  it('a preview being downloaded says so, takes no second press, leaves the other format usable, and is marked once saved', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    let nextJob = 70;
    mocked.previewClientReport.mockImplementation((_project: number, _report: number, fmt: string) =>
      Promise.resolve({ id: nextJob++, format: `report-${fmt}`, status: 'queued' }));
    mocked.getReportJob.mockImplementation((_project: number, id: number) =>
      Promise.resolve({ id, format: 'x', status: 'completed' }));
    const word = held<string>();
    mocked.downloadReportJob.mockImplementation((_project: number, id: number) =>
      (id === 70 ? word.promise : Promise.resolve(`preview-${id}.html`)));

    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Word' }));      // job 70
    await waitFor(() => expect(mocked.previewClientReport).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Preview HTML' }));              // job 71
    const wordButton = await screen.findByRole('button', { name: 'Download Word preview' }, { timeout: 4000 });
    const htmlButton = await screen.findByRole('button', { name: 'Download HTML preview' }, { timeout: 4000 });

    fireEvent.click(wordButton);
    await waitFor(() => expect(wordButton).toHaveTextContent('Downloading…'));
    expect(wordButton).toBeDisabled();
    fireEvent.click(wordButton);
    expect(mocked.downloadReportJob).toHaveBeenCalledTimes(1);
    expect(mocked.downloadReportJob).toHaveBeenCalledWith(1, 70);

    // The other format is not held up by it.
    expect(htmlButton).toBeEnabled();
    fireEvent.click(htmlButton);
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Saved preview-71.html to your downloads.'));
    expect(screen.getAllByText('Downloaded')).toHaveLength(1);
    expect(wordButton).toHaveTextContent('Downloading…');

    word.resolve('example-report-preview.docx');
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Saved example-report-preview.docx to your downloads.'));
    expect(screen.getAllByText('Downloaded')).toHaveLength(2);
    expect(wordButton).toBeEnabled();
    expect(wordButton).toHaveTextContent('Download Word preview');

    // A new Word preview is a new file: its button starts unmarked.
    fireEvent.click(screen.getByRole('button', { name: 'Preview Word' }));              // job 72
    await waitFor(() => expect(screen.getAllByText('Downloaded')).toHaveLength(1));
    const again = await screen.findByRole('button', { name: 'Download Word preview' }, { timeout: 4000 });
    expect(again).not.toHaveAttribute('data-downloaded');
    expect(screen.getAllByText('Downloaded')).toHaveLength(1);
    // …and is announced as its own job.
    expect(readyToasts('The Word preview is ready.')).toBe(2);
    expect(readyToasts('The HTML preview is ready.')).toBe(1);
  }, 20000);
});

// The proposal's "Preview freshness": a finished preview against what it
// would render now — current, out of date, or not verified; never "current"
// unless both fingerprints are known and equal.
describe('Whether a preview is current', () => {
  const finishedWith = (fingerprint: string | null) => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.previewClientReport.mockResolvedValue({ id: 71, format: 'report-html', status: 'queued' });
    mocked.getReportJob.mockResolvedValue({ id: 71, format: 'report-html', status: 'completed', inputs_fingerprint: fingerprint });
  };
  const freshness = () => screen.findByTestId('preview-freshness-html');
  // The job is polled every two seconds: its first reading is the POST's.
  const freshnessIs = (state: string) => waitFor(
    () => expect(screen.getByTestId('preview-freshness-html')).toHaveAttribute('data-freshness', state),
    { timeout: 5000 },
  );

  it('is current while nothing changed, and out of date once something did — read again on return to the window', async () => {
    finishedWith('f1');
    mocked.getClientReportInputs.mockResolvedValue({ fingerprint: 'f1' });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview HTML' }));
    await freshnessIs('current');
    expect(await freshness()).toHaveTextContent('HTML preview: current — nothing it was built from has changed.');

    // A finding changed in another tab: the reader comes back.
    mocked.getClientReportInputs.mockResolvedValue({ fingerprint: 'f2' });
    act(() => { focusManager.setFocused(false); });
    act(() => { focusManager.setFocused(true); });
    await freshnessIs('out-of-date');
    expect(await freshness()).toHaveTextContent('Preview again to see them.');
    act(() => { focusManager.setFocused(undefined); });
  });

  it('a check that failed says so, with a way to check again — never current', async () => {
    finishedWith('f1');
    mocked.getClientReportInputs.mockRejectedValueOnce(new Error('down')).mockResolvedValue({ fingerprint: 'f1' });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview HTML' }));
    await freshnessIs('unknown');
    expect(await freshness()).toHaveTextContent('could not verify whether this preview is current.');
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await freshnessIs('current');
  });

  it('a preview that recorded nothing (rendered before the check existed) is not called current', async () => {
    finishedWith(null);
    mocked.getClientReportInputs.mockResolvedValue({ fingerprint: 'f1' });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview HTML' }));
    await freshnessIs('unknown');
    expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull();
  });
});

describe('An issued report’s files', () => {
  const issued = report({
    id: 7, status: 'issued', number: 3, files: [file('html'), file('docx')], render_status: 'done',
    issued_at: '2026-09-21T00:00:00Z', issued_by_name: 'Admin', can_edit: false, can_issue: false,
  });

  it('on the Reports list: each file shows its own download, and is marked once saved', async () => {
    mocked.listClientReports.mockResolvedValue({ items: [issued], latest_issued_id: 7, can_create: true, can_issue: false });
    const html = held<void>();
    mocked.downloadClientReportFile.mockImplementation((_project: number, _id: number, f: { format: string }) =>
      (f.format === 'html' ? html.promise : Promise.resolve()));
    render(<MemoryRouter><Reports /></MemoryRouter>);

    const htmlButton = await screen.findByRole('button', { name: 'Download acme-report-3.html' });
    const wordButton = screen.getByRole('button', { name: 'Download acme-report-3.docx' });
    fireEvent.click(htmlButton);
    await waitFor(() => expect(htmlButton).toHaveTextContent('Downloading…'));
    expect(htmlButton).toBeDisabled();
    fireEvent.click(htmlButton);
    expect(mocked.downloadClientReportFile).toHaveBeenCalledTimes(1);
    expect(mocked.downloadClientReportFile).toHaveBeenCalledWith(1, 7, expect.objectContaining({ format: 'html' }));

    expect(wordButton).toBeEnabled();
    fireEvent.click(wordButton);
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Saved acme-report-3.docx to your downloads.'));
    expect(wordButton).toHaveAttribute('data-downloaded');
    expect(wordButton).toHaveAccessibleDescription('Downloaded during this visit');
    expect(htmlButton).not.toHaveAttribute('data-downloaded');

    html.resolve();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Saved acme-report-3.html to your downloads.'));
    expect(htmlButton).toHaveAttribute('data-downloaded');
    expect(htmlButton).toHaveTextContent('HTML');
    expect(htmlButton).toBeEnabled();
  });

  it('on the report’s page: the same buttons', async () => {
    mocked.getClientReport.mockResolvedValue(issued);
    mocked.downloadClientReportFile.mockResolvedValue(undefined);
    render(
      <MemoryRouter initialEntries={['/reports/7']}>
        <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Download acme-report-3.docx' }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Saved acme-report-3.docx to your downloads.'));
    expect(screen.getByRole('button', { name: 'Download acme-report-3.docx' })).toHaveAttribute('data-downloaded');
  });

  it('the scope file: downloading, then saved under its name', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      id: 7, status: 'issued', number: 3, render_status: 'done', can_edit: false, can_issue: false,
      summary: {
        counts: { critical: 0, high: 0, medium: 0, low: 0, info: 0, total: 0 },
        scope_external: {
          networks: 4212, domains: 3, inline_max: 25, domains_inline_max: 25,
          file: { name: 'scope-acme-report-3.csv', sha256: 'ef'.repeat(32), bytes: 90000 },
        },
      },
    }));
    const scope = held<void>();
    mocked.downloadClientReportScope.mockReturnValue(scope.promise);
    // The scope file is said on the workspace's Review section (5.394.0).
    render(
      <MemoryRouter initialEntries={['/reports/7?section=review']}>
        <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
      </MemoryRouter>,
    );
    const button = await screen.findByRole('button', { name: 'Download scope file (CSV)' });
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveTextContent('Downloading…'));
    fireEvent.click(button);
    expect(mocked.downloadClientReportScope).toHaveBeenCalledTimes(1);
    expect(mocked.downloadClientReportScope).toHaveBeenCalledWith(1, 7, 'scope-acme-report-3.csv');
    scope.resolve();
    expect(await screen.findByText('Downloaded')).toBeInTheDocument();
    expect(toastMock.success).toHaveBeenCalledWith('Saved scope-acme-report-3.csv to your downloads.');
  });
});
