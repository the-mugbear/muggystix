/**
 * /reports/defaults (the report workspace, owner 2026-10-14): the project's
 * report defaults on the same sections as a draft's details, with ONE save
 * that sends only what changed — a stale read never overwrites another
 * person's change to a detail this page did not touch.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getReportProfile = vi.fn();
const patchReportProfile = vi.fn();
const getProposalSummary = vi.fn();
const listProposals = vi.fn();
const acceptProposal = vi.fn();
vi.mock('../../services/api', () => ({
  getProposalSummary: (...a: unknown[]) => getProposalSummary(...a),
  listProposals: (...a: unknown[]) => listProposals(...a),
  acceptProposal: (...a: unknown[]) => acceptProposal(...a),
  rejectProposal: vi.fn(),
  getReportProfile: (...a: unknown[]) => getReportProfile(...a),
  patchReportProfile: (...a: unknown[]) => patchReportProfile(...a),
  listReportTemplates: vi.fn().mockResolvedValue([
    { name: 'pentest', title: 'Penetration test report', formats: ['html', 'docx'], kind: 'client' },
    { name: 'executive-brief', title: 'Executive brief', formats: ['html', 'docx'], kind: 'client' },
  ]),
  listReportTextImages: vi.fn().mockResolvedValue([]),
  uploadReportTextImage: vi.fn(),
  getProjectReportTeam: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../hooks/useProjectRole', () => ({ useProjectRole: () => ({ canWrite: true, canExport: true }) }));
vi.mock('../../hooks/useProjectMembers', () => ({ useProjectRoster: () => ({ members: [], status: 'ready', retry: vi.fn() }) }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, username: 'ana', role: 'member' } }) }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import ReportDefaults from '../../pages/ReportDefaults';

const profile = {
  executive_summary: null, client_name: 'Example Corp', classification: 'Confidential', engagement_type: null,
  testers: [{ user_id: 1, name: 'Ana', role: 'Lead', email: null }], distribution: [], system_description: null,
  applications: null, thick_clients: null, other_targets: null, author_name: null, disclaimer: null,
  template: 'pentest', testers_from_project: true, inherited: { author_name: 'Installation Ltd', disclaimer: null },
};

const noProposals = {
  pending: 0, by_kind: {}, pending_mine: 0, by_kind_mine: {}, viewer_is_project_admin: false,
};

const draft = (id: number, field: string, value: string, model: string) => ({
  id, kind: 'report_detail', status: 'pending', source: 'agent', finding_id: null, vulnerability_id: null,
  finding_host_id: null, field, payload: { value, base_value: null }, current_value: null, base_value: null,
  base_recorded: true, changed_since_proposed: false, other_pending: 0,
  target: { finding_title: null, observation_title: null, host_id: null, host_ip: null },
  rationale: 'From the findings.', evidence_ids: [], agent_session_id: 88, proposed_by: 'Ana', agent_model: model,
  agent_client: null, prompt_version: null, created_at: `2026-11-17T10:0${id}:00Z`, decided_by: null,
  decided_at: null, decision_note: null, result_finding_id: null, error: null,
});

beforeEach(() => {
  getProposalSummary.mockReset().mockResolvedValue(noProposals);
  listProposals.mockReset().mockResolvedValue({ total: 0, items: [], has_more: false });
  acceptProposal.mockReset();
  getReportProfile.mockReset().mockResolvedValue(profile);
  patchReportProfile.mockReset().mockImplementation(async (_p, body) => ({ ...profile, ...body, testers_from_project: true }));
});

const show = () => render(<MemoryRouter><ReportDefaults /></MemoryRouter>);

describe('Report defaults page', () => {
  // Owner, 2026-11-17: "99% of users will use the Penetration test report" —
  // the template choice is the LAST section, after the text people write.
  it('puts the default template last, after the disclaimer', async () => {
    show();
    const template = await screen.findByTestId('defaults-section-template');
    expect(within(template).getByRole('heading', { name: 'Template' })).toBeInTheDocument();
    expect(within(template).getByText('Default template')).toBeInTheDocument();
    const disclaimer = screen.getByTestId('defaults-section-disclaimer');
    const summary = screen.getByTestId('defaults-section-summary');
    // DOCUMENT_POSITION_FOLLOWING: the second node comes after the first.
    expect(disclaimer.compareDocumentPosition(template) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(summary.compareDocumentPosition(template) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  // 5.430.0 — text an agent proposed for a default is not in the defaults
  // until accepted: the page whose text it would replace says it is waiting.
  it('says how many proposals for these defaults wait, and opens exactly those', async () => {
    getProposalSummary.mockResolvedValue({ ...noProposals, pending: 6, by_kind: { report_detail: 2, finding_text: 4 } });
    show();
    const link = await screen.findByRole('link', { name: '2 proposals for these defaults are waiting for review' });
    expect(link).toHaveAttribute('href', '/proposals?kind=report_detail&scope=all');
  });

  it('says nothing of proposals when none is waiting', async () => {
    show();
    await screen.findByRole('heading', { name: 'Report defaults' });
    await waitFor(() => expect(getProposalSummary).toHaveBeenCalled());
    expect(screen.queryByTestId('proposed-report-text-line')).not.toBeInTheDocument();
  });

  // 5.431.0 — a detail's drafts are reviewed where the detail is, together:
  // on the Proposals page two drafts of one detail were two separate rows.
  it('shows each detail’s waiting drafts under it, side by side with the default, and accepts one there', async () => {
    listProposals.mockResolvedValue({ total: 3, has_more: false, items: [
      draft(1, 'executive_summary', 'Two critical issues were found.', 'model-a'),
      draft(2, 'executive_summary', 'A shorter summary.', 'model-b'),
      draft(3, 'disclaimer', 'Point in time only.', 'model-a'),
    ] });
    acceptProposal.mockResolvedValue({ ...draft(2, 'executive_summary', 'A shorter summary.', 'model-b'), status: 'accepted' });
    show();
    const summary = await screen.findByTestId('drafts-executive_summary');
    expect(listProposals).toHaveBeenCalledWith(
      1, { kind: 'report_detail', status: 'pending', limit: 200 }, expect.any(AbortSignal));
    expect(summary).toHaveTextContent('2 drafts to review');
    expect(within(summary).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Draft A· model-a', 'Draft B· model-b']);
    expect(within(summary).getByTestId('compare-current')).toHaveTextContent('Now in the report defaults');
    expect(within(summary).getByTestId('compare-proposed')).toHaveTextContent('Two critical issues were found.');
    expect(screen.getByTestId('drafts-disclaimer')).toHaveTextContent('1 draft to review');
    // A detail nobody proposed for has none.
    expect(screen.queryByTestId('drafts-system_description')).toBeNull();
    // 5.431.1 — the current value is shown ONCE (§43): a detail under review
    // says it in the comparison only, never "Not written" above "Nothing yet".
    const read = screen.getByTestId('defaults-summary-read');
    expect(within(read).queryByText('Not written')).toBeNull();
    expect(within(read).getAllByText('Nothing yet — accepting fills this default.')).toHaveLength(1);
    // One with no draft still reads as before.
    expect(within(screen.getByTestId('defaults-system-read')).getByText('Not written')).toBeInTheDocument();

    fireEvent.click(within(summary).getByRole('tab', { name: /Draft B/ }));
    expect(within(summary).getByTestId('compare-proposed')).toHaveTextContent('A shorter summary.');
    fireEvent.click(within(summary).getByRole('button', { name: /^Accept$/ }));
    await waitFor(() => expect(acceptProposal).toHaveBeenCalledWith(1, 2, expect.anything()));
    // The defaults and the drafts are read again.
    await waitFor(() => expect(getReportProfile.mock.calls.length).toBeGreaterThan(1));
    await waitFor(() => expect(listProposals.mock.calls.length).toBeGreaterThan(1));
  });

  it('a written default under review prints its text once, beside the draft, with room above it for the save bar', async () => {
    getReportProfile.mockResolvedValue({ ...profile, system_description: 'The network as it stands.' });
    listProposals.mockResolvedValue({ total: 1, has_more: false, items: [
      { ...draft(4, 'system_description', 'The network, reworded.', 'model-a'), current_value: 'The network as it stands.' },
    ] });
    show();
    const drafts = await screen.findByTestId('drafts-system_description');
    expect(screen.getAllByText('The network as it stands.')).toHaveLength(1);
    expect(within(drafts).getByTestId('compare-current')).toHaveTextContent('The network as it stands.');
    // A link to these drafts lands below the page's sticky save bar.
    expect(drafts.style.scrollMarginTop).toContain('6.5rem');
  });

  // Browser pass 2026-11-17: with drafts waiting, a person's UNSAVED edit of
  // the detail vanished from the read view (only the comparison showed), and
  // once a draft was accepted under it the page said the unsaved text "was
  // accepted from" the proposal.
  it('an unsaved edit of a detail stays visible above its drafts, and is never said to come from a proposal', async () => {
    getReportProfile.mockResolvedValue({
      ...profile, executive_summary: 'By the agent.',
      from_proposals: { executive_summary: {
        proposal_id: 77, agent_session_id: 165, agent_model: 'model-a', accepted_by: 'Ana',
        accepted_at: '2026-11-17T10:00:00Z', edited_on_accept: false, changed_since: false,
      } },
    });
    listProposals.mockResolvedValue({ total: 1, has_more: false, items: [
      { ...draft(5, 'executive_summary', 'Another draft.', 'model-b'), current_value: 'By the agent.' },
    ] });
    show();
    await screen.findByTestId('drafts-executive_summary');
    expect(screen.getByTestId('defaults-executive_summary-source')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Edit Executive summary' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Executive summary' }), { target: { value: 'A person’s own words.' } });
    fireEvent.click(within(screen.getByTestId('defaults-section-summary')).getByRole('button', { name: 'Done' }));

    const read = screen.getByTestId('defaults-summary-read');
    expect(within(read).getByText('A person’s own words.')).toBeInTheDocument();      // the unsaved edit, shown
    expect(within(read).getByTestId('compare-current')).toHaveTextContent('By the agent.');   // the drafts' base
    expect(screen.queryByTestId('defaults-executive_summary-source')).toBeNull();
    expect(screen.getByTestId('defaults-action-bar')).toHaveTextContent('Unsaved changes');
  });

  // 5.432.0 — browser pass 2026-11-17: with all six written defaults
  // accepted from one session, the same sentence sat under every detail.
  it('says once which details one agent session wrote; a detail of its own source keeps its line', async () => {
    const from = (proposal_id: number, agent_session_id: number, changed_since = false) => ({
      proposal_id, agent_session_id, agent_model: 'model-a', accepted_by: 'Ana',
      accepted_at: '2026-11-17T10:00:00Z', edited_on_accept: false, changed_since,
    });
    getReportProfile.mockResolvedValue({
      ...profile, executive_summary: 'A.', system_description: 'B.', disclaimer: 'C.', applications: 'D.',
      from_proposals: {
        executive_summary: from(88, 168), system_description: from(89, 168),
        disclaimer: from(93, 168, true),          // edited since: its own line
        applications: from(70, 150),              // another session, alone: its own line
      },
    });
    show();
    const shared = await screen.findByTestId('defaults-sources-168');
    expect(shared).toHaveTextContent('Executive summary and System description hold text accepted from agent session #168’s proposals (model-a), not edited since.');
    expect(screen.queryByTestId('defaults-executive_summary-source')).toBeNull();
    expect(screen.queryByTestId('defaults-system_description-source')).toBeNull();
    expect(screen.getByTestId('defaults-disclaimer-source')).toHaveTextContent(/^Edited since it was accepted/);
    expect(screen.getByTestId('defaults-applications-source')).toHaveTextContent(/agent session #150’s proposal #70/);
    expect(screen.queryByTestId('defaults-sources-150')).toBeNull();
  });

  // 5.432.0 — a draft accepted a moment ago on this very page was replaced
  // by the Save that followed, with no word of it.
  it('says, while an edit is unsaved, that saving replaces text accepted from a proposal', async () => {
    getReportProfile.mockResolvedValue({
      ...profile, executive_summary: 'By the agent.',
      from_proposals: { executive_summary: {
        proposal_id: 77, agent_session_id: 165, agent_model: 'model-a', accepted_by: 'Ana',
        accepted_at: '2026-11-17T10:00:00Z', edited_on_accept: false, changed_since: false,
      } },
    });
    show();
    await screen.findByTestId('defaults-executive_summary-source');
    expect(screen.queryByTestId('defaults-executive_summary-replaces')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Executive summary' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Executive summary' }), { target: { value: 'My own words.' } });
    expect(screen.getByTestId('defaults-executive_summary-replaces'))
      .toHaveTextContent('Unsaved: saving replaces the text accepted from an agent’s proposal #77 (accepted by Ana).');
    fireEvent.click(within(screen.getByTestId('defaults-section-summary')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('defaults-executive_summary-replaces')).toBeNull();
  });

  // 5.431.0 — the defaults keep no history: the page says which text came
  // from an accepted proposal, and when a person has edited it since.
  it('says which default’s text came from an accepted agent proposal, and when it was edited since', async () => {
    getReportProfile.mockResolvedValue({
      ...profile, executive_summary: 'By the agent.', system_description: 'Edited by a person.',
      from_proposals: {
        executive_summary: {
          proposal_id: 77, agent_session_id: 165, agent_model: 'model-a', accepted_by: 'Ana',
          accepted_at: '2026-11-17T10:00:00Z', edited_on_accept: false, changed_since: false,
        },
        system_description: {
          proposal_id: 79, agent_session_id: 165, agent_model: null, accepted_by: 'Ben',
          accepted_at: '2026-11-17T11:00:00Z', edited_on_accept: true, changed_since: true,
        },
      },
    });
    show();
    const first = await screen.findByTestId('defaults-executive_summary-source');
    expect(first).toHaveTextContent(/^Text accepted from agent session #165’s proposal #77 \(model-a\) · accepted by Ana/);
    expect(within(first).getByRole('link', { name: 'agent session #165' })).toHaveAttribute('href', '/agent-sessions/165');
    expect(screen.getByTestId('defaults-system_description-source'))
      .toHaveTextContent(/^Edited since it was accepted from agent session #165’s proposal #79 · accepted by Ben/);
    expect(screen.queryByTestId('defaults-disclaimer-source')).toBeNull();
  });

  it('says where an empty value comes from, and saves only what changed', async () => {
    show();
    expect(await screen.findByText(/Empty: reports use the installation’s — “Installation Ltd”/)).toBeInTheDocument();
    expect(screen.getByText('The project’s analysts and admins, until a team is saved.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save defaults' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Edit Client & engagement' }));
    fireEvent.change(screen.getByLabelText('Client'), { target: { value: 'New Corp' } });
    fireEvent.change(screen.getByLabelText('Classification'), { target: { value: '' } });
    fireEvent.click(within(screen.getByTestId('defaults-section-engagement')).getByRole('button', { name: 'Done' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save defaults' }));

    await waitFor(() => expect(patchReportProfile).toHaveBeenCalledWith(1, { client_name: 'New Corp', classification: null }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save defaults' })).toBeDisabled());
    expect(screen.getByText('All changes saved')).toBeInTheDocument();
  });

  it('an empty disclaimer with the installation’s shows it on request, and offers a copy to edit as the project’s', async () => {
    getReportProfile.mockResolvedValue({ ...profile, inherited: { author_name: null, disclaimer: 'The **installation’s** terms.' } });
    show();
    const note = await screen.findByTestId('defaults-disclaimer-inherited');
    expect(within(note).getByText('installation’s')).toBeInTheDocument();          // rendered Markdown
    fireEvent.click(within(note).getByRole('button', { name: 'Copy it here to edit' }));
    expect(screen.getByRole('textbox', { name: 'Disclaimer' })).toHaveValue('The **installation’s** terms.');
    expect(screen.getByRole('button', { name: 'Save defaults' })).toBeEnabled();
  });

  it('a follow status is a draft\'s: the defaults page shows none', async () => {
    show();
    await screen.findByTestId('defaults-section-engagement');
    expect(screen.queryByText('Uses the project’s defaults')).toBeNull();
  });
});
