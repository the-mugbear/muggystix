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
vi.mock('../../services/api', () => ({
  draftFindingText: (...a: unknown[]) => draftFindingText(...a),
  updateFinding: (...a: unknown[]) => updateFinding(...a),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import FindingReportTextCard from '../../components/FindingReportTextCard';

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
