/**
 * Walkthrough 2026-10-14 (a global admin populating a report), as the report
 * WORKSPACE has it since 5.394.0:
 *   - ONE "Before issuing" checklist, on the Review section, grouped, each
 *     group with its count, every line an action; the action bar's readiness
 *     line says how much is left and opens it;
 *   - a missing detail opens its section on Details with the caret in its
 *     field — a followed detail too (no "Override" link to land on any more);
 *   - the template is chosen on Details beside the title, and saved with
 *     Save draft like any other edit (it used to save at once);
 *   - a finished HTML preview opens in a new tab, the file still a press away.
 */
import type { ReactNode } from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// The real router (setupTests stands one in): the page reads its id, its
// section and the address's hash from it.
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
  listReportTextImages: vi.fn(() => Promise.resolve([])),
  uploadReportTextImage: vi.fn(),
  downloadClientReportFile: vi.fn(),
  downloadClientReportScope: vi.fn(),
  listProjectMembers: vi.fn(),
  getProjectReportTeam: vi.fn(),
  getReportPreview: vi.fn(),
  downloadReportPreview: vi.fn(),
  fetchReportPreviewBlob: vi.fn(),
  draftReportWithAI: vi.fn(),
  listLLMProviders: vi.fn(),
  getClientReportFollowers: vi.fn(),
  promoteClientReportDetails: vi.fn(),
}));
const confirmMock = vi.hoisted(() => vi.fn());
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
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
  summary: fullSummary, can_edit: true, can_issue: true, can_discard: true, ...over,
});

const renderDetail = (url = '/reports/5') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
    </MemoryRouter>,
  );
const REVIEW = '/reports/5?section=review';
const ISSUE = '/reports/5?section=issue';

beforeEach(() => {
  vi.clearAllMocks();
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
  it('is one section on Review, a group per kind of gap with its count; the action bar says how much is left', async () => {
    renderDetail(REVIEW);
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
    // The strip does not repeat it: three figures, none of them what the checklist counts.
    const strip = screen.getByTestId('report-measures');
    expect(within(strip).queryByText(/Missing report text/)).toBeNull();
    expect(within(strip).getByText('Findings in the report')).toBeInTheDocument();
    // The readiness line on the action bar counts what the groups count
    // (third review 2026-10-15, R3: it said "4 warnings" — list lengths —
    // over groups adding to 9): 5 + 12 to fix; 1 + 4 + 1 + 3 warnings.
    expect(screen.getByTestId('report-readiness')).toHaveTextContent('17 to fix · 9 warnings');
    expect(screen.getByRole('tab', { name: /Review/ })).toHaveAttribute('aria-selected', 'true');
  });

  // Proposal §4.2 (Review): what refuses the issue apart from TODOs and
  // warnings — and no new blocking rule.
  it('says what blocks issuing apart from what prints as TODO; nothing blocks a draft with only TODOs', async () => {
    renderDetail(REVIEW);
    expect(await screen.findByTestId('review-blockers')).toHaveTextContent('Nothing blocks issuing.');
    fireEvent.click(screen.getByRole('tab', { name: 'Details' }));
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('tab', { name: /Review/ }));
    expect(screen.getByTestId('review-blockers')).toHaveTextContent('Unsaved changes: save the draft first.');
  });

  it('a required template file missing blocks issuing, said on Review', async () => {
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'],
        assets: [{ id: 'logo', label: 'Logo', path: 'branding/logo.png', present: false, required: true,
          description: '', note: '', formats: ['html'] }] },
    ]);
    renderDetail(REVIEW);
    await waitFor(() => expect(screen.getByTestId('review-blockers')).toHaveTextContent('The template needs Logo'));
  });

  it('the header’s Preview asks for the template’s own format — Word for a Word-only template', async () => {
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'pentest', title: 'Penetration test report', description: '', formats: ['docx'] },
    ]);
    mocked.previewClientReport.mockResolvedValue({ id: 1, status: 'queued', format: 'docx' });
    renderDetail();
    await screen.findByTestId('report-readiness');
    await waitFor(() => expect(mocked.listReportTemplates).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(mocked.previewClientReport).toHaveBeenCalledWith(1, 5, 'docx'));
  });

  it('the readiness line opens Review from anywhere', async () => {
    renderDetail();
    fireEvent.click(await screen.findByTestId('report-readiness'));
    expect(await screen.findByTestId('report-checklist')).toBeInTheDocument();
  });

  it('every line is an action: findings name the report, proposals / images / notes go to where they are decided', async () => {
    renderDetail(REVIEW);
    const checklist = await screen.findByTestId('report-checklist');
    expect(within(screen.getByTestId('checklist-missing-text')).getByRole('link', { name: 'Finding 1' }))
      .toHaveAttribute('href', '/findings/101?edit=report-text&report=5');
    expect(within(checklist).getByRole('link', { name: 'Finding 20' })).toHaveAttribute('href', '/findings/120#proposals');
    expect(within(checklist).getByRole('link', { name: 'Finding 21' }).getAttribute('href')).toMatch(/^\/findings\/121#/);
    expect(within(checklist).getByRole('link', { name: 'Finding 23' })).toHaveAttribute('href', '/findings/123?edit=report-text');
    expect(within(screen.getByTestId('report-evidence-notes')).getByText('Second note.')).toBeInTheDocument();
    expect(within(checklist).getByRole('link', { name: 'project dates' })).toHaveAttribute('href', '/project-settings');
    expect(within(checklist).getByRole('link', { name: 'Set it in System settings › Users' }))
      .toHaveAttribute('href', '/system-settings?tab=users');
  });

  it('a long group shows ten lines, then all of them on request', async () => {
    renderDetail(REVIEW);
    const group = await screen.findByTestId('checklist-missing-text');
    expect(within(group).getAllByRole('listitem')).toHaveLength(10);
    fireEvent.click(within(group).getByRole('button', { name: 'Show all 12' }));
    expect(within(group).getAllByRole('listitem')).toHaveLength(12);
    fireEvent.click(within(group).getByRole('button', { name: 'Show fewer' }));
    expect(within(group).getAllByRole('listitem')).toHaveLength(10);
  });

  it('a missing detail opens its section on Details and puts the caret in its field', async () => {
    renderDetail(REVIEW);
    const group = await screen.findByTestId('checklist-details');

    fireEvent.click(within(group).getByRole('button', { name: 'Go to client in Details' }));
    await waitFor(() => expect(screen.getByLabelText('Client')).toHaveFocus());
    expect(screen.getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true');
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: /Review/ }));
    fireEvent.click(within(await screen.findByTestId('checklist-details')).getByRole('button', { name: 'Go to executive summary in Details' }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Executive summary' })).toHaveFocus());

    // The team is a list with no field of its own: its first control.
    fireEvent.click(screen.getByRole('tab', { name: /Review/ }));
    fireEvent.click(within(await screen.findByTestId('checklist-details')).getByRole('button', { name: 'Go to assessment team in Details' }));
    await waitFor(() => {
      const team = screen.getByTestId('report-section-people');
      expect(team.contains(document.activeElement)).toBe(true);
    });
  });

  it('a detail the draft takes from the project opens as its field, holding the project’s value — nothing is overridden yet', async () => {
    const followed = { ...settings, client_name: 'Example Corp', testers: [{ name: 'Ana Lead' }] };
    mocked.getClientReport.mockResolvedValue(report({
      settings: {}, followed, effective: followed,
      followed_from: { client_name: 'project', testers: 'project', classification: null },
    }));
    renderDetail(REVIEW);
    fireEvent.click(within(await screen.findByTestId('checklist-details')).getByRole('button', { name: 'Go to client in Details' }));
    await waitFor(() => expect(screen.getByLabelText('Client')).toHaveFocus());
    expect(screen.getByLabelText('Client')).toHaveValue('Example Corp');
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('All changes saved');
  });

  it('someone who is not a global admin is told who sets a full name, with no link they cannot open', async () => {
    auth.user = { id: 3, username: 'ana', full_name: 'Ana', role: 'member' };
    renderDetail(REVIEW);
    const group = await screen.findByTestId('checklist-details');
    expect(within(group).queryByRole('link', { name: /System settings/ })).toBeNull();
    expect(group).toHaveTextContent(/each person sets their own in Profile/);
  });

  // Walkthrough 2026-10-14: an auditor's lines led to places they could not fix.
  it('someone who may not edit the draft reads what it lacks: details as text, findings open as themselves', async () => {
    mocked.getClientReport.mockResolvedValue(report({ can_edit: false, can_issue: false, can_discard: false }));
    renderDetail(REVIEW);
    const checklist = await screen.findByTestId('report-checklist');
    expect(checklist).toHaveTextContent(/whoever writes it fixes these before it is issued/);
    const details = screen.getByTestId('checklist-details');
    expect(within(details).queryByRole('link')).toBeNull();
    expect(within(details).queryByRole('button')).toBeNull();
    expect(details).toHaveTextContent('project dates');
    expect(within(screen.getByTestId('checklist-missing-text')).getByRole('link', { name: 'Finding 1' }))
      .toHaveAttribute('href', '/findings/101');
    // No save, no Edit anywhere.
    expect(screen.queryByRole('button', { name: /Save draft/ })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Details' }));
    expect(screen.queryByRole('button', { name: /^Edit / })).toBeNull();
  });

  // Review 2026-10-14, H2: a reference to an image the project no longer has
  // printed nothing, and nothing said so.
  it('names a report detail that places an image the report cannot print, and opens it', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      summary: { ...fullSummary, unresolved_images: [{ field: 'system_description', ids: [7] }] },
    }));
    renderDetail(REVIEW);
    const group = await screen.findByTestId('checklist-unresolved-images');
    expect(heading(group)).toHaveTextContent('Images the report cannot print');
    expect(group).toHaveTextContent('places image 7');
    expect(within(group).getByRole('button', { name: 'Go to System description in Details' })).toBeInTheDocument();
  });

  it('an issued report has no checklist; it opens on its files', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      status: 'issued', number: 2, render_status: 'done', can_edit: false, can_issue: false,
    }));
    renderDetail();
    await screen.findByText('Issued #2');
    expect(screen.getByRole('tab', { name: 'Preview & issue' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: /Review/ }));
    expect(screen.queryByTestId('report-checklist')).toBeNull();
    expect(screen.queryByText('Before issuing')).toBeNull();
    // What an issued report printed of the notes is still said.
    expect(screen.getByText('Notes printed in this report')).toBeInTheDocument();
  });

  // Third review 2026-10-15, R3: notes the client will read were a checklist
  // group but no warning, so the draft also said "Nothing left to fix".
  it('a draft whose only gap is notes the client will read lists them and is not "clear"', async () => {
    mocked.getClientReport.mockResolvedValue(report({ summary: {
      counts: fullSummary.counts, missing_text: [], evidence_note_findings: fullSummary.evidence_note_findings,
    } }));
    renderDetail(REVIEW);
    expect(await screen.findByTestId('report-checklist')).toBeInTheDocument();
    expect(screen.queryByTestId('review-clear')).toBeNull();
    expect(screen.getByTestId('report-readiness')).toHaveTextContent('Nothing to fix · 3 warnings');
  });

  it('a draft with nothing left has no checklist, and says so', async () => {
    mocked.getClientReport.mockResolvedValue(report({ summary: { counts: fullSummary.counts, missing_text: [] } }));
    renderDetail(REVIEW);
    expect(await screen.findByTestId('review-clear')).toHaveTextContent('Nothing left to fix before issuing.');
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

// Walkthrough 2026-10-14: one press beside Save draft threw the edits away.
describe('Discard changes', () => {
  it('asks first, naming what was changed; kept when declined, back to the last save when confirmed', async () => {
    renderDetail();
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Renamed' } });
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Discard your unsaved changes?', severity: 'danger',
      body: 'What you changed in Report goes back to the last save.',
    })));
    expect(screen.getByLabelText('Title')).toHaveValue('Renamed');

    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    await waitFor(() => expect(screen.getByTestId('report-save-status')).toHaveTextContent('All changes saved'));
    expect(screen.getByLabelText('Title')).not.toHaveValue('Renamed');
    expect(mocked.updateClientReport).not.toHaveBeenCalled();
  });
});

// Review 2026-10-14, H10: only the back button asked; a checklist link left
// with the pending edits.
describe('A link out of the draft', () => {
  it('asks first when there are unsaved changes, and stays when declined', async () => {
    render(
      <MemoryRouter initialEntries={['/reports/5']}>
        <Routes>
          <Route path="/reports/:reportId" element={<ReportDetail />} />
          <Route path="/findings/:id" element={<p>The finding page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('tab', { name: /Review/ }));
    const checklist = await screen.findByTestId('report-checklist');
    const link = within(checklist).getAllByRole('link').find((a) => a.getAttribute('href')?.startsWith('/findings/'))!;

    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(link);
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
      body: 'This draft has unsaved changes. Leave anyway?',
    })));
    expect(screen.queryByText('The finding page')).toBeNull();

    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(link);
    expect(await screen.findByText('The finding page')).toBeInTheDocument();
  });
});

// Second review 2026-10-14 (nitpick): a preview is a write; for whoever may
// not edit the draft its buttons were disabled, not hidden.
it('whoever may not edit the draft is offered no Preview', async () => {
  mocked.getClientReport.mockResolvedValue(report({ can_edit: false }));
  renderDetail(ISSUE);
  await screen.findByText(/Rendered from the live findings/);
  expect(screen.queryByRole('button', { name: /^(Save & )?[Pp]review (HTML|Word)/ })).toBeNull();
});

// Second review 2026-10-14, H6: "Draft with AI…" → Configure called
// navigate() and left the pending edits behind with no word.
describe('Configure from "Draft with AI…"', () => {
  it('asks first when there are unsaved changes', async () => {
    mocked.listLLMProviders.mockResolvedValue([]);
    render(
      <MemoryRouter initialEntries={['/reports/5']}>
        <Routes>
          <Route path="/reports/:reportId" element={<ReportDetail />} />
          <Route path="/llm-settings" element={<p>The providers page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: /^Edit (Executive summary|Summary)/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Draft with AI/ }));
    const configure = await screen.findByRole('link', { name: /Configure/ });
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(configure);
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
      body: 'This draft has unsaved changes. Leave anyway?',
    })));
    expect(screen.queryByText('The providers page')).toBeNull();
  });
});

describe('The template', () => {
  it('is chosen on Details beside the title, and saved with Save draft like any other edit', async () => {
    mocked.updateClientReport.mockResolvedValue(report({ template: 'brief' }));
    renderDetail();
    const picker = await screen.findByTestId('report-template-picker');
    // What the template is for is on the (i) beside the picker.
    const tip = within(picker).getByRole('note', { name: 'About this template' });
    expect(within(tip).getByTestId('template-description')).toHaveTextContent('Findings-first client report.');

    fireEvent.keyDown(within(picker).getByRole('combobox', { name: 'Template' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('option', { name: 'Brief report' }));
    // Not saved at once (it was until 5.394.0): one more pending edit.
    expect(mocked.updateClientReport).not.toHaveBeenCalled();
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('Unsaved changes — Report');
    await waitFor(() => expect(screen.getByTestId('template-description')).toHaveTextContent('For leadership.'));

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, {
      template: 'brief', settings: {},
    }));
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
    expect(within(tip).queryByTestId('template-description')).toBeNull();
  });

  it('a missing REQUIRED template file blocks preview and issue: said on the page, never only in the tip', async () => {
    mocked.listReportTemplates.mockResolvedValue([
      { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'],
        assets: [{ id: 'logo', label: 'Logo', path: 'branding/logo.png', present: false, required: true,
          description: 'The client’s logo.', note: '', formats: ['html'] }] },
    ]);
    renderDetail();
    const block = await screen.findByTestId('report-section-report');
    const warning = await within(block).findByText(/preview and issue are unavailable until it is installed/);
    expect(warning.closest('[role="note"]')).toBeNull();
    expect(screen.queryByTestId('template-files')).toBeNull();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
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
    mocked.getReportPreview.mockResolvedValue({ id: 71, format: 'report-html', status: 'completed' });
  });
  afterEach(() => { window.open = originalOpen; });

  const finishedHtml = async () => {
    renderDetail(ISSUE);
    fireEvent.click(await screen.findByRole('button', { name: 'Preview HTML' }));
    return screen.findByRole('button', { name: 'Open HTML preview' }, { timeout: 4000 });
  };

  it('opens a tab in the click itself, then shows the fetched file in it; the download stays', async () => {
    const tab = fakeTab();
    open = vi.fn(() => tab);
    window.open = open as unknown as typeof window.open;
    let answer!: (v: { blob: Blob; filename: string }) => void;
    mocked.fetchReportPreviewBlob.mockImplementation(() => new Promise((res) => { answer = res; }));

    const button = await finishedHtml();
    expect(screen.getByRole('button', { name: 'Download HTML preview' })).toBeInTheDocument();
    fireEvent.click(button);
    expect(open).toHaveBeenCalledWith('', '_blank');
    expect(tab.document.body.textContent).toBe('Opening the preview…');
    await waitFor(() => expect(mocked.fetchReportPreviewBlob).toHaveBeenCalledWith(1, 5, 71));
    expect(tab.location.href).toBe('');

    answer({ blob: blobFor('blob:report'), filename: 'example-preview.html' });
    await waitFor(() => expect(tab.location.href).toMatch(/^blob:/));
    const makes = (URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock.calls;
    const made = makes[makes.length - 1][0] as Blob;
    expect(made.type).toBe('text/html');
    expect(tab.opener).toBeNull();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(mocked.downloadReportPreview).not.toHaveBeenCalled();
    expect(toastMock.warning).not.toHaveBeenCalled();
  });

  it('saves the file instead, and says so, when the browser blocks the tab', async () => {
    open = vi.fn(() => null);
    window.open = open as unknown as typeof window.open;
    mocked.fetchReportPreviewBlob.mockResolvedValue({ blob: blobFor('blob:report'), filename: 'example-preview.html' });
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
    mocked.fetchReportPreviewBlob.mockRejectedValue(new Error('gone'));
    fireEvent.click(await finishedHtml());
    await waitFor(() => expect(tab.close).toHaveBeenCalled());
    expect(toastMock.error).toHaveBeenCalled();
  });

  it('Word stays a download', async () => {
    mocked.previewClientReport.mockResolvedValue({ id: 70, format: 'report-docx', status: 'queued' });
    mocked.getReportPreview.mockResolvedValue({ id: 70, format: 'report-docx', status: 'completed' });
    renderDetail(ISSUE);
    fireEvent.click(await screen.findByRole('button', { name: 'Preview Word' }));
    expect(await screen.findByRole('button', { name: 'Download Word preview' }, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Open Word/ })).toBeNull();
  });

  it('with unsaved changes, Preview saves them first — and a failed save previews nothing', async () => {
    mocked.updateClientReport.mockRejectedValue(new Error('down'));
    renderDetail();
    fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & preview' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalled());
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(mocked.previewClientReport).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Title')).toHaveValue('Renamed');            // the edit is kept
  });
});
