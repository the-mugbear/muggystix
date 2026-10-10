/**
 * Report text — AI drafting of the empty sections (review 2026-09-23
 * B-Ops-5).  Since v5.316.0 a draft is a set of PROPOSALS reviewed in the
 * finding's Proposals section, like an agent's: it never fills the editor
 * and saves nothing; any analyst may draft, only the author may edit.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const draftFindingText = vi.fn();
const updateFinding = vi.fn();
const acceptProposal = vi.fn();
vi.mock('../../services/api', () => ({
  draftFindingText: (...a: unknown[]) => draftFindingText(...a),
  updateFinding: (...a: unknown[]) => updateFinding(...a),
  acceptProposal: (...a: unknown[]) => acceptProposal(...a),
  rejectProposal: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { MemoryRouter } from 'react-router-dom';
import { readsOnScreen } from '../helpers/readsOnScreen';
import FindingReportTextCard from '../../components/FindingReportTextCard';
import type { Proposal } from '../../services/api';

const finding = {
  id: 42, title: 'Weak TLS',
  report_text: {
    description: 'TLS 1.0 is accepted.', impact: null, recommendation: null, references: null,
    steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
  },
} as never;

// 5.351.0 — a draft no longer calls the page's `onDrafted` (which bumped a
// key to re-read the page's proposals): it says the proposals and their
// count are out of date, and what shows them reads again.  These stand in
// for the finding page's pending proposals and the top bar's count.
const { reread, ReadsOnScreen } = readsOnScreen({ listProposals: 'proposals', getProposalSummary: 'count' });
const renderDrafting = () => render(
  <><ReadsOnScreen /><FindingReportTextCard finding={finding} canEdit /></>,
);

beforeEach(() => {
  draftFindingText.mockReset();
  updateFinding.mockReset();
  reread.mockClear();
});

describe('FindingReportTextCard — drafting', () => {
  it('drafts only the empty required sections as proposals and fills nothing in', async () => {
    draftFindingText.mockResolvedValue({
      proposals: [{ id: 1, field: 'impact' }, { id: 2, field: 'recommendation' }],
      provider_id: 1, provider_type: 'openai', model_id: 'm',
    });
    renderDrafting();
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));

    // The page's proposals and the top bar's count are read again, once each.
    await waitFor(() => expect(reread).toHaveBeenCalledWith('proposals'));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('count'));
    expect(reread).toHaveBeenCalledTimes(2);
    expect(draftFindingText).toHaveBeenCalledWith(1, 42,['impact', 'recommendation']);
    // No editor was opened and nothing was saved: the drafts wait as proposals.
    expect(screen.queryByLabelText('Impact')).not.toBeInTheDocument();
    expect(updateFinding).not.toHaveBeenCalled();
  });

  // 5.334.4 — "not enough information" is an answer: the section gets no
  // proposal, and the reason is shown beside it, never written into it.
  it('shows the sections the draft declined, with what each needs, and proposes none for them', async () => {
    draftFindingText.mockResolvedValue({
      proposals: [{ id: 1, field: 'impact' }],
      declined: { recommendation: 'No product or version is recorded for the affected service.' },
      provider_id: 1, provider_type: 'openai', model_id: 'm',
    });
    renderDrafting();
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));
    expect(await screen.findByTestId('declined-recommendation')).toHaveTextContent(
      'Not drafted — not enough information: No product or version is recorded for the affected service.',
    );
    // One section was drafted: the proposals are read again.
    await waitFor(() => expect(reread).toHaveBeenCalledWith('proposals'));
    expect(updateFinding).not.toHaveBeenCalled();
  });

  it('a draft that declines every section proposes nothing and says why', async () => {
    draftFindingText.mockResolvedValue({
      proposals: [], declined: { impact: 'Nothing shows who reaches the service.', recommendation: 'No version.' },
      provider_id: 1, provider_type: 'openai', model_id: 'm',
    });
    renderDrafting();
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));
    expect(await screen.findByTestId('declined-impact')).toHaveTextContent(/Nothing shows who reaches the service/);
    expect(screen.getByTestId('declined-recommendation')).toBeInTheDocument();
    // Nothing was proposed: nothing is read again.
    expect(reread).not.toHaveBeenCalled();
  });

  it('says why a draft failed', async () => {
    draftFindingText.mockRejectedValue(new Error('No LLM provider is configured.'));
    renderDrafting();
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));
    expect(await screen.findByText(/No LLM provider is configured|Could not draft/)).toBeInTheDocument();
    expect(reread).not.toHaveBeenCalled();
  });

  it('lets an analyst who is not the author draft, but not edit', () => {
    render(<FindingReportTextCard finding={finding} canEdit={false} canPropose />);
    expect(screen.getByRole('button', { name: /Draft empty sections/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it('opens in the editor when asked, and offers nothing to someone who may not edit', () => {
    const { unmount } = render(<FindingReportTextCard finding={finding} canEdit startEditing />);
    expect(screen.getByLabelText('Impact')).toBeInTheDocument();
    unmount();
    render(<FindingReportTextCard finding={finding} canEdit={false} startEditing />);
    expect(screen.queryByLabelText('Impact')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Draft empty sections/ })).not.toBeInTheDocument();
  });
});

describe('FindingReportTextCard — v5.290.0 the written text is shown rendered, safely', () => {
  const withText = (description: string) => ({
    id: 42, title: 'Weak TLS',
    report_text: {
      description, impact: null, recommendation: null, references: null,
      steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
    },
  }) as never;

  it('renders **bold** as bold instead of printing the asterisks', () => {
    render(<FindingReportTextCard finding={withText('Seen on **filesrv-01** only.')} canEdit />);
    const dd = screen.getByTestId('report-text-description');
    expect(dd.querySelector('strong')?.textContent).toBe('filesrv-01');
    expect(dd.textContent).not.toContain('**');
  });

  it('never turns raw HTML or an image into an element', () => {
    const { container } = render(
      <FindingReportTextCard
        finding={withText('<img src=x onerror="alert(1)"> and ![shot](https://evil.example/x.png)')}
        canEdit      />,
    );
    expect(container.querySelector('img')).toBeNull();
    const dd = screen.getByTestId('report-text-description');
    // The HTML stays text; the image is its alt text.
    expect(dd.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(dd.textContent).toContain('shot');
  });

  it('keeps the editor a plain textarea holding the Markdown as written', () => {
    render(<FindingReportTextCard finding={withText('Seen on **filesrv-01**.')} canEdit startEditing />);
    expect(screen.getByLabelText('Description')).toHaveValue('Seen on **filesrv-01**.');
  });

  // 5.293.0 — Markdown help while editing.
  const toolbar = () => within(screen.getByRole('toolbar', { name: 'Description formatting' }));

  it('inserts a table on lines of its own and previews it as the report prints it', () => {
    render(<FindingReportTextCard finding={withText('Before.')} canEdit startEditing />);
    const box = screen.getByLabelText('Description') as HTMLTextAreaElement;
    box.setSelectionRange(7, 7);
    fireEvent.click(toolbar().getByRole('button', { name: 'Table' }));
    expect(box.value).toBe('Before.\n\n| Column | Column |\n| ------ | ------ |\n| Value  | Value  |');

    fireEvent.click(toolbar().getByRole('button', { name: 'Preview' }));
    const preview = screen.getByTestId('rt-description-preview');
    expect(preview.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(preview.querySelector('th')?.textContent).toBe('Column');
    fireEvent.click(toolbar().getByRole('button', { name: 'Write' }));
    expect(screen.getByLabelText('Description')).toHaveValue(box.value);
  });

  it('warns about a table straight after text, and adds the blank line', () => {
    render(<FindingReportTextCard finding={withText('Totals:\n| A | B |\n| - | - |\n| 1 | 2 |')} canEdit startEditing />);
    expect(screen.getByText(/The table on line 2 follows a line of text/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add the blank line' }));
    expect(screen.getByLabelText('Description')).toHaveValue('Totals:\n\n| A | B |\n| - | - |\n| 1 | 2 |');
    expect(screen.queryByText(/follows a line of text/)).not.toBeInTheDocument();
  });

  // Found in the Chrome pass: a padded small button squeezed each icon to
  // 4px wide, so the toolbar showed dots.
  it('draws the toolbar as icon buttons with no side padding', () => {
    render(<FindingReportTextCard finding={withText('x')} canEdit startEditing />);
    const bold = toolbar().getByRole('button', { name: 'Bold' });
    expect(bold.className).toContain('size-7');
    expect(bold.className).not.toMatch(/\bpx-/);
    expect(bold.className).not.toMatch(/\b[hw]-10\b/);
    expect(bold.querySelector('svg')?.getAttribute('class')).toContain('shrink-0');
  });

  it('makes the selection bold with Ctrl+B', () => {
    render(<FindingReportTextCard finding={withText('Seen on filesrv-01.')} canEdit startEditing />);
    const box = screen.getByLabelText('Description') as HTMLTextAreaElement;
    box.setSelectionRange(8, 18);
    fireEvent.keyDown(box, { key: 'b', ctrlKey: true });
    expect(box.value).toBe('Seen on **filesrv-01**.');
  });
});

describe('FindingReportTextCard — 5.317.0 work on this with your agent', () => {
  const agentButton = <button type="button">Work on this with your agent</button>;

  it('offers the agent task to anyone who may propose, and hides it while editing', () => {
    render(<FindingReportTextCard finding={finding} canEdit={false} canPropose agentAction={agentButton} />);
    expect(screen.getByRole('button', { name: /Work on this with your agent/ })).toBeInTheDocument();
  });

  it('is not offered to someone who may not propose', () => {
    render(<FindingReportTextCard finding={finding} canEdit={false} canPropose={false} agentAction={agentButton} />);
    expect(screen.queryByRole('button', { name: /Work on this with your agent/ })).not.toBeInTheDocument();
  });

  it('the task names the finding, asks for proposals, and lists what is empty', async () => {
    const { agentInstruction } = await import('../../utils/agentRuns');
    const task = agentInstruction.reviewFinding(42, ['impact', 'recommendation']);
    expect(task).toMatch(/finding #42/);
    expect(task).toMatch(/propose_finding_text/);
    expect(task).toMatch(/Do not change the finding directly/);
    // 5.318.0 — the rewrite itself, not a critique: the text replaces the section.
    expect(task).toMatch(/complete new section/);
    expect(task).toMatch(/replaces the section/);
    expect(task).toMatch(/rationale/);
    expect(task).not.toMatch(/propose improvements/);
    // 5.334.4 — "not enough to write this" is an answer: no guesses, no placeholders.
    expect(task).toMatch(/do not propose it or fill it with a guess or placeholder/);
    expect(task).toMatch(/tell me what is missing/);
    expect(task).toMatch(/Still empty: impact, recommendation\./);
    expect(agentInstruction.reviewFinding(42)).not.toMatch(/Still empty/);
  });
});

// 5.323.0 — a first-time user promoted a finding and landed on a page of
// "Not written yet" behind an Edit button. A finding with nothing written
// opens ready to write; an empty section of a partly written one is itself
// the way in.
describe('FindingReportTextCard — a new finding is ready to write', () => {
  const blank = {
    id: 43, title: 'New',
    report_text: {
      description: null, impact: '  ', recommendation: null, references: null,
      steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
    },
  } as never;

  it('opens in the editor when nothing the report needs is written', () => {
    render(<FindingReportTextCard finding={blank} canEdit />);
    expect(screen.getByLabelText('Description')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save report text' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
    // Drafting stays on offer while the editor is open.
    expect(screen.getByRole('button', { name: /Draft empty sections/ })).toBeInTheDocument();
  });

  it('stays read-only for someone who may not edit it', () => {
    render(<FindingReportTextCard finding={blank} canEdit={false} canPropose={false} />);
    expect(screen.queryByLabelText('Description')).not.toBeInTheDocument();
    expect(screen.getAllByText('Not written yet').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Write it/ })).not.toBeInTheDocument();
  });

  it('a partly written finding reads first, and an empty section opens the editor there', async () => {
    render(<FindingReportTextCard finding={finding} canEdit />);
    expect(screen.queryByLabelText('Impact')).not.toBeInTheDocument();
    expect(screen.getByText('TLS 1.0 is accepted.')).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId('report-text-impact')).getByRole('button', { name: /Not written yet\. Write it/ }));
    const impact = await screen.findByLabelText('Impact');
    await waitFor(() => expect(impact).toHaveFocus());
  });

  it('Cancel returns to reading', () => {
    render(<FindingReportTextCard finding={blank} canEdit />);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('Description')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Edit$/ })).toBeInTheDocument();
  });
});

// Walkthrough 2026-10-02 — drafts are reviewed in the section they change.
describe('FindingReportTextCard — drafts waiting (5.334.0)', () => {
  const draft = (id: number, field: string, value: string, over: Partial<Proposal> = {}): Proposal => ({
    id, kind: 'finding_text', status: 'pending', source: 'agent', finding_id: 42, vulnerability_id: null,
    finding_host_id: null, field, payload: { value }, current_value: null, base_value: null, base_recorded: true,
    changed_since_proposed: false,
    target: { finding_title: 'Weak TLS', observation_title: null, host_id: null, host_ip: null },
    rationale: null, evidence_ids: [], agent_session_id: 9, proposed_by: 'Ana', agent_model: 'model-a',
    agent_client: null, prompt_version: null, created_at: null, decided_by: null, decided_at: null,
    decision_note: null, result_finding_id: null, error: null, ...over,
  });
  const empty = {
    id: 42, title: 'Weak TLS',
    report_text: {
      description: null, impact: null, recommendation: null, references: null,
      steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
    },
  } as never;
  const renderCard = (props: Record<string, unknown>) => render(
    <MemoryRouter><FindingReportTextCard finding={finding} canEdit canDecide {...props} /></MemoryRouter>,
  );

  it('a section with drafts shows them beside its current text, not “Not written yet”', () => {
    renderCard({
      drafts: new Map([
        ['impact', [draft(1, 'impact', 'Relay to the file share.')]],
        ['description', [draft(2, 'description', 'TLS 1.0 and 1.1 are accepted.', { current_value: 'TLS 1.0 is accepted.' })]],
      ]),
    });
    const impact = screen.getByTestId('report-text-impact');
    expect(within(impact).queryByText(/Not written yet/)).toBeNull();
    // An empty section is laid out like a written one (the user, 2026-10-07:
    // Description read side by side and the empty sections did not).
    expect(within(impact).getByTestId('compare-current')).toHaveTextContent(/Nothing yet — accepting fills this section/);
    expect(within(impact).getByTestId('compare-proposed')).toHaveTextContent('Relay to the file share.');
    expect(within(impact).queryByRole('button', { name: 'Changes' })).toBeNull();
    const desc = screen.getByTestId('report-text-description');
    expect(within(desc).getByTestId('compare-current')).toHaveTextContent('TLS 1.0 is accepted.');
    expect(within(desc).getByTestId('compare-proposed')).toHaveTextContent('TLS 1.0 and 1.1 are accepted.');
    // The words the draft changes, marked.
    fireEvent.click(within(desc).getByRole('button', { name: 'Changes' }));
    const changes = within(desc).getByTestId('text-changes');
    expect(changes.querySelector('ins')).toHaveTextContent(/and 1\.1 are/);
    expect(changes.querySelector('del')).toHaveTextContent(/is/);
    expect(screen.getByText(/2 drafts are waiting for review in the sections below/)).toBeInTheDocument();
  });

  it('drafts from one model are told apart by their opening words (browser pass 5.334.1)', () => {
    renderCard({
      drafts: new Map([['impact', [
        draft(1, 'impact', 'Apply the vendor (synthetic): fix and verify it.'),
        draft(2, 'impact', 'Restrict access, then patch.'),
      ]]]),
    });
    expect(screen.getByRole('tab', { name: /Draft A · “Apply the vendor synthetic: fix and…”/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Draft B · “Restrict access, then patch\.…”/ })).toBeInTheDocument();
  });

  it('says when the section changed after the draft was written, with the text it was written against', () => {
    renderCard({
      drafts: new Map([['description', [draft(2, 'description', 'TLS 1.0 and 1.1 are accepted.', {
        current_value: 'TLS 1.0 is accepted.', base_value: 'TLS is weak.', changed_since_proposed: true,
      })]]]),
    });
    const desc = screen.getByTestId('report-text-description');
    expect(within(desc).getByRole('note')).toHaveTextContent(/changed after the draft was written/);
    expect(within(desc).getByText('TLS is weak.')).toBeInTheDocument();
  });

  it('“Draft empty sections” leaves a section that already has a draft alone', async () => {
    draftFindingText.mockResolvedValue({ proposals: [{ id: 3, field: 'recommendation' }] });
    renderCard({ drafts: new Map([['impact', [draft(1, 'impact', 'Relay.')]]]) });
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));
    await waitFor(() => expect(draftFindingText).toHaveBeenCalledWith(1, 42,['recommendation']));
  });

  it('no draft button when every empty section already has a draft', () => {
    renderCard({
      drafts: new Map([
        ['impact', [draft(1, 'impact', 'Relay.')]], ['recommendation', [draft(2, 'recommendation', 'Fix.')]],
      ]),
    });
    expect(screen.queryByRole('button', { name: /Draft empty sections/ })).toBeNull();
  });

  it('the editor a new finding opens by itself closes when drafts arrive, so the drafts show', () => {
    const { rerender } = render(
      <MemoryRouter><FindingReportTextCard finding={empty} canEdit canDecide /></MemoryRouter>,
    );
    expect(screen.getByLabelText('Description')).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <FindingReportTextCard finding={empty} canEdit canDecide
          drafts={new Map([['description', [draft(1, 'description', 'TLS 1.0 is accepted.')]]])} />
      </MemoryRouter>,
    );
    expect(screen.queryByLabelText('Description')).toBeNull();
    expect(screen.getByTestId('drafts-description')).toBeInTheDocument();
  });

  it('editing by hand says a section has drafts waiting that a later accept replaces', () => {
    renderCard({ drafts: new Map([['impact', [draft(1, 'impact', 'Relay.')]]]) });
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    expect(screen.getByText(/A draft is waiting for this section/)).toBeInTheDocument();
  });

  it('accepting a draft hands the decided proposal back', async () => {
    const onProposalDecided = vi.fn();
    acceptProposal.mockResolvedValue({ ...draft(1, 'impact', 'Relay.'), status: 'accepted' });
    renderCard({ drafts: new Map([['impact', [draft(1, 'impact', 'Relay.')]]]), onProposalDecided });
    fireEvent.click(within(screen.getByTestId('drafts-impact')).getByRole('button', { name: /^Accept$/ }));
    // …with the promise of the re-read the decision asked for (5.351.0: the
    // page no longer re-reads from this callback; it waits on that to say
    // when the finding could not be read again).
    await waitFor(() => expect(onProposalDecided).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'accepted' }), expect.any(Promise),
    ));
  });
});

// Unsaved report text asks before it is dropped: the editor kept its draft in
// state with no guard, so a reload, a tab close or Cancel lost it silently.
describe('FindingReportTextCard — unsaved text is guarded', () => {
  const written = {
    id: 42, title: 'Weak TLS',
    report_text: {
      description: 'TLS 1.0 is accepted.', impact: 'Downgrade.', recommendation: 'Disable it.', references: null,
      steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
    },
  } as never;

  const unload = () => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  };

  it('asks before a reload or a tab close only while the editor holds a change', () => {
    const onDirtyChange = vi.fn();
    render(<FindingReportTextCard finding={written} canEdit onDirtyChange={onDirtyChange} />);
    expect(unload()).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    // Open but untouched: nothing to lose.
    expect(unload()).toBe(false);
    fireEvent.change(screen.getByLabelText('Impact'), { target: { value: 'Downgrade to a broken cipher.' } });
    expect(unload()).toBe(true);
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    // Put back as it was: clean again.
    fireEvent.change(screen.getByLabelText('Impact'), { target: { value: 'Downgrade.' } });
    expect(unload()).toBe(false);
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it('Cancel with a change asks first, keeps the text on "no" and drops it on Discard', async () => {
    render(<FindingReportTextCard finding={written} canEdit />);
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText('Impact'), { target: { value: 'Rewritten.' } });

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    const ask = await screen.findByRole('dialog');
    expect(within(ask).getByText('Discard unsaved work?')).toBeInTheDocument();
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Impact')).toHaveValue('Rewritten.');

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(screen.queryByLabelText('Impact')).not.toBeInTheDocument());
    expect(updateFinding).not.toHaveBeenCalled();
    expect(unload()).toBe(false);
  });

  it('a saved editor leaves nothing to guard', async () => {
    updateFinding.mockResolvedValue(written);
    render(<FindingReportTextCard finding={written} canEdit />);
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText('Impact'), { target: { value: 'Rewritten.' } });
    fireEvent.click(screen.getByRole('button', { name: /Save report text/ }));
    await waitFor(() => expect(screen.queryByLabelText('Impact')).not.toBeInTheDocument());
    expect(unload()).toBe(false);
  });
});
