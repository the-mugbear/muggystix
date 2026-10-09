/**
 * System settings → Report writing guidance: one box per section, only the
 * changed boxes are sent, an edited box offers its default back, and the
 * rules that always apply are shown, not edited.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ReportWritingGuidanceSection, { changedSections } from '../../components/reports/ReportWritingGuidanceSection';
import type { ReportWritingGuidance } from '../../services/api';

const { getReportWritingGuidance, updateReportWritingGuidance } = vi.hoisted(() => ({
  getReportWritingGuidance: vi.fn(), updateReportWritingGuidance: vi.fn(),
}));
vi.mock('../../services/api', () => ({ getReportWritingGuidance, updateReportWritingGuidance }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

const LONG = 'Lead-with-the-business-consequence-'.repeat(8);
const guidance = (over: Record<string, string> = {}): ReportWritingGuidance => ({
  sections: [
    ['general', 'Every section', 'Plain, factual prose.'],
    ['description', 'Description', 'What the issue is.'],
    ['impact', 'Impact', 'What an attacker gains.'],
    ['recommendation', 'Recommendation', 'What to change.'],
    ['steps_to_reproduce', 'Steps to reproduce', 'The steps, in order.'],
    ['references', 'References', 'One per line.'],
  ].map(([key, label, def]) => ({
    key, label, default: def, text: over[key] ?? def, is_default: !(key in over),
  })),
  fixed_rules: ['Ground every statement in the supplied data.', 'Declining a section is allowed.'],
  drafter_prompt: 'You are drafting ONE finding…',
  max_chars: 4000,
  updated_at: null,
  updated_by: null,
});

beforeEach(() => {
  [getReportWritingGuidance, updateReportWritingGuidance].forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
});

describe('ReportWritingGuidanceSection', () => {
  it('shows a box per section and sends only the one that changed', async () => {
    getReportWritingGuidance.mockResolvedValue(guidance());
    updateReportWritingGuidance.mockResolvedValue(guidance({ impact: LONG }));
    render(<ReportWritingGuidanceSection />);

    const impact = await screen.findByLabelText('Impact');
    expect(screen.getByLabelText('Every section')).toHaveValue('Plain, factual prose.');
    expect(screen.getAllByText('Default')).toHaveLength(6);
    expect(screen.queryByRole('button', { name: 'Save guidance' })).toBeNull();

    fireEvent.change(impact, { target: { value: `  ${LONG}  ` } });
    fireEvent.click(screen.getByRole('button', { name: 'Save guidance' }));
    await waitFor(() => expect(updateReportWritingGuidance).toHaveBeenCalledWith({ impact: LONG }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Save guidance' })).toBeNull();
    expect(screen.getByLabelText('Impact')).toHaveValue(LONG);
  });

  it('offers the default back on an edited box, and an emptied box is sent blank', async () => {
    getReportWritingGuidance.mockResolvedValue(guidance({ impact: 'Ours.', references: 'CVE first.' }));
    updateReportWritingGuidance.mockResolvedValue(guidance());
    render(<ReportWritingGuidanceSection />);

    await screen.findByLabelText('Impact');
    const resets = screen.getAllByRole('button', { name: 'Reset to default' });
    expect(resets).toHaveLength(2);
    fireEvent.click(resets[0]);
    expect(screen.getByLabelText('Impact')).toHaveValue('What an attacker gains.');
    fireEvent.change(screen.getByLabelText('References'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save guidance' }));
    await waitFor(() => expect(updateReportWritingGuidance).toHaveBeenCalledWith({
      impact: 'What an attacker gains.', references: '',
    }));
  });

  it('shows the rules that always apply and the prompt, neither as an input', async () => {
    getReportWritingGuidance.mockResolvedValue(guidance());
    render(<ReportWritingGuidanceSection />);
    expect(await screen.findByText('Ground every statement in the supplied data.')).toBeInTheDocument();
    expect(screen.getByTestId('ss-guidance-prompt')).toHaveTextContent('You are drafting ONE finding…');
    expect(screen.getAllByRole('textbox')).toHaveLength(6);
  });

  it('says a failed load, with Retry, and a failed save keeps what was typed', async () => {
    getReportWritingGuidance.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(guidance());
    updateReportWritingGuidance.mockRejectedValue(new Error('boom'));
    render(<ReportWritingGuidanceSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    fireEvent.change(await screen.findByLabelText('Impact'), { target: { value: 'Typed.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save guidance' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByLabelText('Impact')).toHaveValue('Typed.');
  });

  it('changedSections ignores surrounding whitespace', () => {
    const g = guidance();
    expect(changedSections(g, { ...Object.fromEntries(g.sections.map((s) => [s.key, s.text])), impact: ' What an attacker gains. ' }))
      .toEqual({});
  });
});
