/**
 * Walkthrough 2026-10-14 (a global admin populating a report):
 *   - ONE "Before issuing" checklist near the top, grouped, each group with
 *     its count, every line an action (it was five sections, the TODO list
 *     below the others and the details form at the bottom);
 *   - the template is chosen beside the report's kind, not in Preview;
 *   - a finished HTML preview opens in a new tab, the file still a press away.
 */
import type { ReactNode } from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// The real router (setupTests stands one in): the page reads its id and the
// address's hash from it.
vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));
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
  downloadReportJob: vi.fn(),
  fetchReportJobBlob: vi.fn(),
  draftReportWithAI: vi.fn(),
  listLLMProviders: vi.fn(),
}));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
const auth = vi.hoisted(() => ({ user: { id: 99, username: 'admin', full_name: 'Administrator', role: 'admin' } as Record<string, unknown> }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));
// setupTests renders no tooltip content; the template's (i) is read here as
// its trigger and, beside it, what it holds.
vi.mock('../../components/ui/info-tip', () => ({
  InfoTip: ({ text, label }: { text: ReactNode; label?: string }) => (
    <span data-testid="info-tip" aria-label={label} role="note">{text}</span>
  ),
}));

import * as api from '../../services/api';
import ReportDetail from '../../pages/ReportDetail';
import { focusReportDetail } from '../../components/reports/ReportChecklist';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';
import { blobFor, installBlobUrls } from '../helpers/blobUrls';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const settings = {
  client_name: null, classification: null, engagement_type: null,
  testers: [], distribution: [], system_description: null,
};
const finding = (i: number) => ({ id: 100 + i, ref: `F-${String(i).padStart(2, '0')}`, title: `Finding ${i}` });
const fullSummary = {
  counts: { critical: 1, high: 2, medium: 0, low: 0, info: 1, total: 4 },
  under_investigation: 0, images: 0, images_skipped: 0, delta: null,
  missing_details: [
    'client', 'assessment team', 'executive summary', 'project dates',
    'a full name on the account of ben (the report shows the username)',
  ],
  missing_detail_keys: ['client_name', 'testers', 'executive_summary', 'project_dates', 'account_full_name'],
  missing_text: Array.from({ length: 12 }, (_, i) => ({ ...finding(i + 1), missing: ['impact'] })),
  pending_proposals: [{ ...finding(20), count: 2 }],
  uncaptioned_images: [{ ...finding(21), count: 3 }, { ...finding(22), count: 1 }],
  internal_references: [{ ...finding(23), fields: [{ field: 'description', phrases: ['Finding #277'] }] }],
  evidence_notes: 3,
  evidence_note_findings: [{ ...finding(24), notes: ['First note.', 'Second note.'] }, { ...finding(25), notes: ['Third.'] }],
};
const report = (over: Record<string, unknown> = {}) => ({
  id: 5, project_id: 1, kind: 'full', status: 'draft', title: 'Example report', number: null,
  template: 'pentest', baseline: null, revision_of: null, superseded_by: null, settings,
  executive_summary: null, template_fingerprint: null, quarto_version: null,
  render_status: null, render_error: null, files: [], created_by_name: 'Ana', issued_by_name: null,
  created_at: '2026-09-20T00:00:00Z', updated_at: null, issued_at: null,
  summary: fullSummary, can_edit: true, can_issue: true, ...over,
});

const renderDetail = (url = '/reports/5') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  resetProjectMembersCache();
  auth.user = { id: 99, username: 'admin', full_name: 'Administrator', role: 'admin' };
  mocked.listReportTemplates.mockResolvedValue([
    { name: 'pentest', title: 'Penetration test report', description: 'Findings-first client report.', formats: ['html', 'docx'] },
    { name: 'brief', title: 'Brief report', description: 'For leadership.', formats: ['html'] },
  ]);
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.listReportTemplateProblems.mockResolvedValue([]);
  mocked.getClientReport.mockResolvedValue(report());
  Element.prototype.scrollIntoView = vi.fn();
});

const heading = (group: HTMLElement) => within(group).getByRole('heading', { level: 3 });

describe('The draft’s checklist', () => {
  it('is one section with a group per kind of gap, each with its count, above Preview and the details', async () => {
    renderDetail();
    const checklist = await screen.findByTestId('report-checklist');
    expect(within(checklist).getByRole('heading', { level: 2, name: /Before issuing/ })).toBeInTheDocument();
    const groups = within(checklist).getAllByRole('group');
    expect(groups.map((g) => g.getAttribute('data-testid'))).toEqual([
      'checklist-details', 'checklist-missing-text', 'checklist-proposals',
      'checklist-uncaptioned', 'checklist-references', 'checklist-notes',
    ]);
    expect(heading(groups[0])).toHaveTextContent(/^TODOReport details to fill5$/);
    expect(heading(groups[1])).toHaveTextContent(/^TODOFindings missing report text12$/);
    expect(heading(groups[2])).toHaveTextContent(/^Findings with proposals to decide1$/);
    expect(heading(groups[3])).toHaveTextContent(/^Images without a caption4$/);       // 3 + 1 images
    expect(heading(groups[4])).toHaveTextContent(/^Text the reader cannot follow1$/);
    expect(heading(groups[5])).toHaveTextContent(/^Notes the client will read3$/);     // 2 + 1 notes

    // The five sections it replaced are gone.
    for (const old of ['Proposals to review', 'Report details still empty']) {
      expect(screen.queryByText(new RegExp(old))).toBeNull();
    }
    // Near the top: before Preview and before the details form.
    const preview = screen.getByRole('heading', { level: 2, name: /Preview/ });
    const details = screen.getByRole('heading', { level: 2, name: /Report details/ });
    // eslint-disable-next-line no-bitwise
    expect(checklist.compareDocumentPosition(preview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // eslint-disable-next-line no-bitwise
    expect(checklist.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The strip does not repeat it (owner, 2026-10-14): three figures, none
    // of them what the checklist counts.
    const strip = screen.getByTestId('report-measures');
    expect(within(strip).queryByText(/Missing report text/)).toBeNull();
    expect(within(strip).queryByText(/Before issuing/)).toBeNull();
    expect(within(strip).getByText('Findings in the report')).toBeInTheDocument();
    expect(within(strip).getByText('Still under investigation')).toBeInTheDocument();
    expect(within(strip).getByText('Evidence images')).toBeInTheDocument();
  });

  it('every line is an action: findings name the report, proposals / images / notes go to where they are decided', async () => {
    renderDetail();
    const checklist = await screen.findByTestId('report-checklist');
    expect(within(screen.getByTestId('checklist-missing-text')).getByRole('link', { name: 'Finding 1' }))
      .toHaveAttribute('href', '/findings/101?edit=report-text&report=5');
    expect(within(checklist).getByRole('link', { name: 'Finding 20' })).toHaveAttribute('href', '/findings/120#proposals');
    expect(within(checklist).getByRole('link', { name: 'Finding 21' }).getAttribute('href')).toMatch(/^\/findings\/121#/);
    expect(within(checklist).getByRole('link', { name: 'Finding 23' })).toHaveAttribute('href', '/findings/123?edit=report-text');
    expect(within(screen.getByTestId('report-evidence-notes')).getByText('Second note.')).toBeInTheDocument();
    expect(within(checklist).getByRole('link', { name: 'project dates' })).toHaveAttribute('href', '/project-settings');
    // A global admin is sent where a full name is set.
    expect(within(checklist).getByRole('link', { name: 'Set it in System settings › Users' }))
      .toHaveAttribute('href', '/system-settings?tab=users');
  });

  it('a long group shows ten lines, then all of them on request', async () => {
    renderDetail();
    const group = await screen.findByTestId('checklist-missing-text');
    expect(within(group).getAllByRole('listitem')).toHaveLength(10);
    fireEvent.click(within(group).getByRole('button', { name: 'Show all 12' }));
    expect(within(group).getAllByRole('listitem')).toHaveLength(12);
    fireEvent.click(within(group).getByRole('button', { name: 'Show fewer' }));
    expect(within(group).getAllByRole('listitem')).toHaveLength(10);
  });

  it('a missing detail goes to its field in Report details and puts the caret there', async () => {
    renderDetail();
    const group = await screen.findByTestId('checklist-details');
    await screen.findByLabelText('Client');

    fireEvent.click(within(group).getByRole('button', { name: 'Go to client in Report details' }));
    expect(screen.getByLabelText('Client')).toHaveFocus();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();

    fireEvent.click(within(group).getByRole('button', { name: 'Go to executive summary in Report details' }));
    expect(screen.getByLabelText('Executive summary')).toHaveFocus();

    // The team is a list with no field of its own: its first control.
    fireEvent.click(within(group).getByRole('button', { name: 'Go to assessment team in Report details' }));
    const team = screen.getByRole('group', { name: 'Assessment team' });
    expect(team.contains(document.activeElement)).toBe(true);
  });

  it('a missing detail the draft takes from the project goes to its first action', async () => {
    // Owner, 2026-10-14: a followed detail is the project's, shown as text —
    // the caret goes to its first action: "Override for this report" when the
    // project holds a value, "Set for the project" when it holds none.
    const followed = { ...settings, client_name: 'Example Corp', testers: [{ name: 'Ana Lead' }] };
    mocked.getClientReport.mockResolvedValue(report({
      settings: {}, followed, effective: followed,
      followed_from: { client_name: 'project', testers: 'project', classification: null },
    }));
    renderDetail();
    const group = await screen.findByTestId('checklist-details');
    expect(await screen.findByRole('group', { name: 'Client' })).toHaveTextContent('Example Corp');
    expect(screen.queryByRole('textbox', { name: 'Client' })).toBeNull();          // no field: text

    fireEvent.click(within(group).getByRole('button', { name: 'Go to client in Report details' }));
    expect(screen.getByRole('button', { name: 'Override Client for this report' })).toHaveFocus();
    // The visible action, never the "⋯" menu beside it.
    expect(screen.getByRole('button', { name: 'More actions for Client' })).not.toHaveFocus();
    fireEvent.click(within(group).getByRole('button', { name: 'Go to assessment team in Report details' }));
    expect(screen.getByRole('button', { name: 'Override Assessment team for this report' })).toHaveFocus();
    // Empty in the project: "Set for the project" first.
    expect(focusReportDetail(screen.getByRole('group', { name: 'Classification' }).closest('form'), 'classification'))
      .toBe(true);
    expect(screen.getByRole('button', { name: 'Set Classification for the project' })).toHaveFocus();
  });

  it('someone who is not a global admin is told who sets a full name, with no link they cannot open', async () => {
    auth.user = { id: 3, username: 'ana', full_name: 'Ana', role: 'member' };
    renderDetail();
    const group = await screen.findByTestId('checklist-details');
    expect(within(group).queryByRole('link', { name: /System settings/ })).toBeNull();
    expect(group).toHaveTextContent(/each person sets their own in Profile/);
  });

  it('an issued report has no checklist', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 2, render_status: 'done', can_edit: false, can_issue: false,
    }));
    renderDetail();
    await screen.findByText('Issued #2');
    expect(screen.queryByTestId('report-checklist')).toBeNull();
    expect(screen.queryByText('Before issuing')).toBeNull();
    // What an issued report printed of the notes is still said.
    expect(screen.getByText('Notes printed in this report')).toBeInTheDocument();
  });

  it('a draft with nothing left has no checklist', async () => {
    mocked.getClientReport.mockResolvedValue(report({ summary: { counts: fullSummary.counts, missing_text: [] } }));
    renderDetail();
    await screen.findByRole('button', { name: /Issue report/ });
    expect(screen.queryByTestId('report-checklist')).toBeNull();
  });

  it('"Back to the report" from a finding lands on the checklist', async () => {
    renderDetail('/reports/5#report-checklist');
    const checklist = await screen.findByTestId('report-checklist');
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
    const scrolled = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.contexts;
    expect(scrolled).toContain(checklist);
  });
});

describe('The template', () => {
  it('is chosen under the title, not in Preview, and saved at once', async () => {
    mocked.updateClientReport.mockResolvedValue(report({ template: 'brief' }));
    renderDetail();
    const picker = await screen.findByTestId('report-template-picker');
    expect(picker.closest('header')).not.toBeNull();
    const preview = screen.getByRole('heading', { level: 2, name: /Preview/ }).closest('section')!;
    expect(within(preview).queryByRole('combobox', { name: 'Template' })).toBeNull();
    // What the template is for is on the (i) beside the picker, not a
    // paragraph in the header (owner, 2026-10-14).
    const tip = within(picker).getByRole('note', { name: 'About this template' });
    expect(within(tip).getByTestId('template-description')).toHaveTextContent('Findings-first client report.');
    expect(screen.getAllByTestId('template-description')).toHaveLength(1);

    fireEvent.keyDown(within(picker).getByRole('combobox', { name: 'Template' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('option', { name: 'Brief report' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, { template: 'brief' }));
    await waitFor(() => expect(screen.getByTestId('template-description')).toHaveTextContent('For leadership.'));
  });

  it('the files line and “the only template” are in the tip; a missing required file is said on the page', async () => {
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'pentest', title: 'Penetration test report', description: 'Findings-first client report.', formats: ['html'],
        assets: [{ id: 'logo', label: 'Logo', path: 'branding/logo.png', present: false, required: false,
          description: 'The client’s logo.', note: '', formats: ['html'] }] },
    ]);
    renderDetail();
    const picker = await screen.findByTestId('report-template-picker');
    const tip = await within(picker).findByRole('note', { name: 'About this template' });
    expect(tip).toHaveTextContent('The only template installed.');
    expect(within(tip).getByTestId('template-files')).toHaveTextContent('Logo: not installed (optional, left out)');
    // One template: no description to choose by.
    expect(within(tip).queryByTestId('template-description')).toBeNull();
  });

  it('a missing REQUIRED template file blocks preview and issue: said on the page, never only in the tip', async () => {
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'],
        assets: [{ id: 'logo', label: 'Logo', path: 'branding/logo.png', present: false, required: true,
          description: 'The client’s logo.', note: '', formats: ['html'] }] },
    ]);
    renderDetail();
    const picker = await screen.findByTestId('report-template-picker');
    const warning = await within(picker).findByText(/preview and issue are unavailable until it is installed/);
    expect(warning.closest('[role="note"]')).toBeNull();
    expect(screen.queryByTestId('template-files')).toBeNull();
  });

  it('someone who may not edit the draft reads its template, with no chooser', async () => {
    mocked.getClientReport.mockResolvedValue(report({ can_edit: false, can_issue: false }));
    renderDetail();
    const picker = await screen.findByTestId('report-template-picker');
    expect(within(picker).queryByRole('combobox')).toBeNull();
    await within(picker).findByText('Penetration test report');
  });
});

describe('Open HTML preview', () => {
  let open: ReturnType<typeof vi.fn>;
  const originalOpen = window.open;
  const fakeTab = () => ({
    closed: false, opener: {} as unknown, close: vi.fn(), location: { href: '' },
    document: { title: '', body: { textContent: '' } },
  });

  beforeEach(() => {
    installBlobUrls();
    URL.revokeObjectURL = vi.fn();
    mocked.previewClientReport.mockResolvedValue({ id: 71, format: 'report-html', status: 'queued' });
    mocked.getReportJob.mockResolvedValue({ id: 71, format: 'report-html', status: 'completed' });
  });
  afterEach(() => { window.open = originalOpen; });

  const finishedHtml = async () => {
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview HTML' }));
    return screen.findByRole('button', { name: 'Open HTML preview' }, { timeout: 4000 });
  };

  it('opens a tab in the click itself, then shows the fetched file in it; the download stays', async () => {
    const tab = fakeTab();
    open = vi.fn(() => tab);
    window.open = open as unknown as typeof window.open;
    let answer!: (v: { blob: Blob; filename: string }) => void;
    mocked.fetchReportJobBlob.mockImplementation(() => new Promise((res) => { answer = res; }));

    const button = await finishedHtml();
    expect(screen.getByRole('button', { name: 'Download HTML preview' })).toBeInTheDocument();
    fireEvent.click(button);
    // Synchronously, before the bytes arrive: a popup blocker allows nothing later.
    expect(open).toHaveBeenCalledWith('', '_blank');
    expect(tab.document.body.textContent).toBe('Opening the preview…');
    await waitFor(() => expect(mocked.fetchReportJobBlob).toHaveBeenCalledWith(1, 71));
    expect(tab.location.href).toBe('');

    answer({ blob: blobFor('blob:report'), filename: 'example-preview.html' });
    await waitFor(() => expect(tab.location.href).toMatch(/^blob:/));
    // Typed as HTML, so the tab renders it rather than offering it as a file.
    const makes = (URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock.calls;
    const made = makes[makes.length - 1][0] as Blob;
    expect(made.type).toBe('text/html');
    expect(tab.opener).toBeNull();                       // the report cannot reach this page
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();  // revoked later, once the tab has read it
    expect(mocked.downloadReportJob).not.toHaveBeenCalled();
    expect(toastMock.warning).not.toHaveBeenCalled();
  });

  it('saves the file instead, and says so, when the browser blocks the tab', async () => {
    open = vi.fn(() => null);
    window.open = open as unknown as typeof window.open;
    mocked.fetchReportJobBlob.mockResolvedValue({ blob: blobFor('blob:report'), filename: 'example-preview.html' });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    fireEvent.click(await finishedHtml());
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledWith(
      'The browser did not open a new tab, so the preview was saved to your downloads as example-preview.html.',
    ));
    expect(click).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:report');
    click.mockRestore();
  });

  it('closes the blank tab when the file cannot be fetched', async () => {
    const tab = fakeTab();
    window.open = vi.fn(() => tab) as unknown as typeof window.open;
    mocked.fetchReportJobBlob.mockRejectedValue(new Error('gone'));
    fireEvent.click(await finishedHtml());
    await waitFor(() => expect(tab.close).toHaveBeenCalled());
    expect(toastMock.error).toHaveBeenCalled();
  });

  it('Word stays a download', async () => {
    mocked.previewClientReport.mockResolvedValue({ id: 70, format: 'report-docx', status: 'queued' });
    mocked.getReportJob.mockResolvedValue({ id: 70, format: 'report-docx', status: 'completed' });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Word' }));
    expect(await screen.findByRole('button', { name: 'Download Word preview' }, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Open Word/ })).toBeNull();
  });
});
