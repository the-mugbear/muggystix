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
import FindingReportTextCard from '../../components/FindingReportTextCard';
import type { Proposal } from '../../services/api';

const finding = {
  id: 42, title: 'Weak TLS',
  report_text: {
    description: 'TLS 1.0 is accepted.', impact: null, recommendation: null, references: null,
    steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false,
  },
} as never;

beforeEach(() => {
  draftFindingText.mockReset();
  updateFinding.mockReset();
});

describe('FindingReportTextCard — drafting', () => {
  it('drafts only the empty required sections as proposals and fills nothing in', async () => {
    draftFindingText.mockResolvedValue({
      proposals: [{ id: 1, field: 'impact' }, { id: 2, field: 'recommendation' }],
      provider_id: 1, provider_type: 'openai', model_id: 'm',
    });
    const onDrafted = vi.fn();
    render(<FindingReportTextCard finding={finding} canEdit onSaved={vi.fn()} onDrafted={onDrafted} />);
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));

    await waitFor(() => expect(onDrafted).toHaveBeenCalledTimes(1));
    expect(draftFindingText).toHaveBeenCalledWith(42, ['impact', 'recommendation']);
    // No editor was opened and nothing was saved: the drafts wait as proposals.
    expect(screen.queryByLabelText('Impact')).not.toBeInTheDocument();
    expect(updateFinding).not.toHaveBeenCalled();
  });

  it('says why a draft failed', async () => {
    draftFindingText.mockRejectedValue(new Error('No LLM provider is configured.'));
    const onDrafted = vi.fn();
    render(<FindingReportTextCard finding={finding} canEdit onSaved={vi.fn()} onDrafted={onDrafted} />);
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));
    expect(await screen.findByText(/No LLM provider is configured|Could not draft/)).toBeInTheDocument();
    expect(onDrafted).not.toHaveBeenCalled();
  });

  it('lets an analyst who is not the author draft, but not edit', () => {
    render(<FindingReportTextCard finding={finding} canEdit={false} canPropose onSaved={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Draft empty sections/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it('opens in the editor when asked, and offers nothing to someone who may not edit', () => {
    const { unmount } = render(<FindingReportTextCard finding={finding} canEdit onSaved={vi.fn()} startEditing />);
    expect(screen.getByLabelText('Impact')).toBeInTheDocument();
    unmount();
    render(<FindingReportTextCard finding={finding} canEdit={false} onSaved={vi.fn()} startEditing />);
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
    render(<FindingReportTextCard finding={withText('Seen on **filesrv-01** only.')} canEdit onSaved={vi.fn()} />);
    const dd = screen.getByTestId('report-text-description');
    expect(dd.querySelector('strong')?.textContent).toBe('filesrv-01');
    expect(dd.textContent).not.toContain('**');
  });

  it('never turns raw HTML or an image into an element', () => {
    const { container } = render(
      <FindingReportTextCard
        finding={withText('<img src=x onerror="alert(1)"> and ![shot](https://evil.example/x.png)')}
        canEdit onSaved={vi.fn()}
      />,
    );
    expect(container.querySelector('img')).toBeNull();
    const dd = screen.getByTestId('report-text-description');
    // The HTML stays text; the image is its alt text.
    expect(dd.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(dd.textContent).toContain('shot');
  });

  it('keeps the editor a plain textarea holding the Markdown as written', () => {
    render(<FindingReportTextCard finding={withText('Seen on **filesrv-01**.')} canEdit onSaved={vi.fn()} startEditing />);
    expect(screen.getByLabelText('Description')).toHaveValue('Seen on **filesrv-01**.');
  });

  // 5.293.0 — Markdown help while editing.
  const toolbar = () => within(screen.getByRole('toolbar', { name: 'Description formatting' }));

  it('inserts a table on lines of its own and previews it as the report prints it', () => {
    render(<FindingReportTextCard finding={withText('Before.')} canEdit onSaved={vi.fn()} startEditing />);
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
    render(<FindingReportTextCard finding={withText('Totals:\n| A | B |\n| - | - |\n| 1 | 2 |')} canEdit onSaved={vi.fn()} startEditing />);
    expect(screen.getByText(/The table on line 2 follows a line of text/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add the blank line' }));
    expect(screen.getByLabelText('Description')).toHaveValue('Totals:\n\n| A | B |\n| - | - |\n| 1 | 2 |');
    expect(screen.queryByText(/follows a line of text/)).not.toBeInTheDocument();
  });

  // Found in the Chrome pass: a padded small button squeezed each icon to
  // 4px wide, so the toolbar showed dots.
  it('draws the toolbar as icon buttons with no side padding', () => {
    render(<FindingReportTextCard finding={withText('x')} canEdit onSaved={vi.fn()} startEditing />);
    const bold = toolbar().getByRole('button', { name: 'Bold' });
    expect(bold.className).toContain('size-7');
    expect(bold.className).not.toMatch(/\bpx-/);
    expect(bold.className).not.toMatch(/\b[hw]-10\b/);
    expect(bold.querySelector('svg')?.getAttribute('class')).toContain('shrink-0');
  });

  it('makes the selection bold with Ctrl+B', () => {
    render(<FindingReportTextCard finding={withText('Seen on filesrv-01.')} canEdit onSaved={vi.fn()} startEditing />);
    const box = screen.getByLabelText('Description') as HTMLTextAreaElement;
    box.setSelectionRange(8, 18);
    fireEvent.keyDown(box, { key: 'b', ctrlKey: true });
    expect(box.value).toBe('Seen on **filesrv-01**.');
  });
});

describe('FindingReportTextCard — 5.317.0 work on this with your agent', () => {
  const agentButton = <button type="button">Work on this with your agent</button>;

  it('offers the agent task to anyone who may propose, and hides it while editing', () => {
    render(<FindingReportTextCard finding={finding} canEdit={false} canPropose onSaved={vi.fn()} agentAction={agentButton} />);
    expect(screen.getByRole('button', { name: /Work on this with your agent/ })).toBeInTheDocument();
  });

  it('is not offered to someone who may not propose', () => {
    render(<FindingReportTextCard finding={finding} canEdit={false} canPropose={false} onSaved={vi.fn()} agentAction={agentButton} />);
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
    render(<FindingReportTextCard finding={blank} canEdit onSaved={vi.fn()} />);
    expect(screen.getByLabelText('Description')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save report text' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
    // Drafting stays on offer while the editor is open.
    expect(screen.getByRole('button', { name: /Draft empty sections/ })).toBeInTheDocument();
  });

  it('stays read-only for someone who may not edit it', () => {
    render(<FindingReportTextCard finding={blank} canEdit={false} canPropose={false} onSaved={vi.fn()} />);
    expect(screen.queryByLabelText('Description')).not.toBeInTheDocument();
    expect(screen.getAllByText('Not written yet').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Write it/ })).not.toBeInTheDocument();
  });

  it('a partly written finding reads first, and an empty section opens the editor there', async () => {
    render(<FindingReportTextCard finding={finding} canEdit onSaved={vi.fn()} />);
    expect(screen.queryByLabelText('Impact')).not.toBeInTheDocument();
    expect(screen.getByText('TLS 1.0 is accepted.')).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId('report-text-impact')).getByRole('button', { name: /Not written yet\. Write it/ }));
    const impact = await screen.findByLabelText('Impact');
    await waitFor(() => expect(impact).toHaveFocus());
  });

  it('Cancel returns to reading', () => {
    render(<FindingReportTextCard finding={blank} canEdit onSaved={vi.fn()} />);
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
    <MemoryRouter><FindingReportTextCard finding={finding} canEdit onSaved={vi.fn()} canDecide {...props} /></MemoryRouter>,
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
    expect(within(impact).getByText(/the section is empty now, so accepting fills it/)).toBeInTheDocument();
    expect(within(impact).getByText('Relay to the file share.')).toBeInTheDocument();
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
        draft(1, 'impact', 'Apply the vendor fix and verify it.'),
        draft(2, 'impact', 'Restrict access, then patch.'),
      ]]]),
    });
    expect(screen.getByRole('tab', { name: /Draft A · “Apply the vendor fix and verify…”/ })).toBeInTheDocument();
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
    renderCard({ drafts: new Map([['impact', [draft(1, 'impact', 'Relay.')]]]), onDrafted: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));
    await waitFor(() => expect(draftFindingText).toHaveBeenCalledWith(42, ['recommendation']));
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
      <MemoryRouter><FindingReportTextCard finding={empty} canEdit onSaved={vi.fn()} canDecide /></MemoryRouter>,
    );
    expect(screen.getByLabelText('Description')).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <FindingReportTextCard finding={empty} canEdit onSaved={vi.fn()} canDecide
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
    await waitFor(() => expect(onProposalDecided).toHaveBeenCalledWith(expect.objectContaining({ status: 'accepted' })));
  });
});
