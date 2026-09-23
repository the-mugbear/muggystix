/**
 * Report text — AI drafting of the empty sections (review 2026-09-23
 * B-Ops-5): a suggestion fills only the empty required boxes, is marked as
 * an AI draft, and nothing is saved until the author saves.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
