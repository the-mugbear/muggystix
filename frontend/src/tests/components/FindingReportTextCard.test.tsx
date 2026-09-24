/**
 * Report text — AI drafting of the empty sections (review 2026-09-23
 * B-Ops-5): a suggestion fills only the empty required boxes, is marked as
 * an AI draft, and nothing is saved until the author saves.
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
  it('fills only the empty required sections, marks them, and saves nothing by itself', async () => {
    draftFindingText.mockResolvedValue({
      suggestions: { impact: 'Downgrade attacks.', recommendation: 'Disable TLS 1.0.' },
      provider_id: 1, provider_type: 'openai', model_id: 'm',
    });
    render(<FindingReportTextCard finding={finding} canEdit onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));

    await waitFor(() => expect(screen.getByLabelText('Impact')).toHaveValue('Downgrade attacks.'));
    expect(draftFindingText).toHaveBeenCalledWith(42, ['impact', 'recommendation']);
    expect(screen.getByLabelText('Recommendation')).toHaveValue('Disable TLS 1.0.');
    expect(screen.getByLabelText('Description')).toHaveValue('TLS 1.0 is accepted.');
    expect(screen.getAllByText(/Drafted by AI/)).toHaveLength(2);
    expect(updateFinding).not.toHaveBeenCalled();
    // Every required box is filled: nothing left to draft.
    expect(screen.queryByRole('button', { name: /Draft empty sections/ })).not.toBeInTheDocument();
  });

  it('says why a draft failed and keeps the editor open', async () => {
    draftFindingText.mockRejectedValue(new Error('No LLM provider is configured.'));
    render(<FindingReportTextCard finding={finding} canEdit onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Draft empty sections/ }));
    expect(await screen.findByText(/No LLM provider is configured|Could not draft/)).toBeInTheDocument();
    expect(screen.getByLabelText('Impact')).toHaveValue('');
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
