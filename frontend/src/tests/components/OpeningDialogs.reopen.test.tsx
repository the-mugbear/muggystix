/**
 * Review 2026-10-23, R-FC2 — dialogs whose every opening starts clean are
 * keyed by their opening (`useOpeningKey`), not reset by an effect on `open`;
 * what a dialog keeps across openings on purpose is held outside its body.
 * (`GeneratedTextDialog` is pinned by `ExportDialogs.reopen.test.tsx`,
 * `FormatRetryDialog` by its own "each opening starts narrowed again".)
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listLLMProviders: vi.fn(), draftReportWithAI: vi.fn() }));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import { MemoryRouter } from 'react-router-dom';
import ConfirmDialog from '../../components/ConfirmDialog';
import AiDraftReportDialog from '../../components/AiDraftReportDialog';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ConfirmDialog — each opening starts clean', () => {
  const dialog = (open: boolean, onConfirm = vi.fn()) => (
    <ConfirmDialog
      open={open} title="Delete project?" resourceName="prod" confirmTypedName
      confirmLabel="Delete" onConfirm={onConfirm} onClose={vi.fn()}
    />
  );

  it('the typed name and a refusal are gone at the next opening', async () => {
    const refuse = vi.fn().mockRejectedValue({ response: { data: { detail: 'Not yours to delete.' } } });
    const { rerender } = render(dialog(true, refuse));
    fireEvent.change(screen.getByLabelText('Type prod to confirm'), { target: { value: 'prod' } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Not yours to delete.')).toBeInTheDocument();

    rerender(dialog(false, refuse));
    rerender(dialog(true, refuse));
    expect(screen.getByLabelText('Type prod to confirm')).toHaveValue('');
    expect(screen.queryByText('Not yours to delete.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
  });
});

describe('AiDraftReportDialog — a new opening has no draft, but keeps the steer', () => {
  const dialog = (open: boolean) => (
    <MemoryRouter><AiDraftReportDialog reportId={3} open={open} onClose={vi.fn()} /></MemoryRouter>
  );

  it('drops the draft and keeps the audience and instructions written last time', async () => {
    api.listLLMProviders.mockResolvedValue([{ id: 1, name: 'Local', is_default: true, model_id: 'm' }]);
    api.draftReportWithAI.mockResolvedValue({
      content: '# The draft', finding_total: 2, provider_type: 'openai', model_id: 'm',
    });
    const { rerender } = render(dialog(true));
    const audience = await screen.findByLabelText('Audience (optional)');
    fireEvent.change(audience, { target: { value: 'The CISO' } });
    fireEvent.change(screen.getByLabelText('Instructions (optional)'), { target: { value: 'Lead with risk.' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /Generate draft/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /Generate draft/ }));
    expect(await screen.findByLabelText('Drafted report (editable)')).toHaveValue('# The draft');
    expect(api.draftReportWithAI).toHaveBeenCalledWith(
      1, expect.objectContaining({ audience: 'The CISO', instructions: 'Lead with risk.' }), expect.anything(),
    );

    rerender(dialog(false));
    rerender(dialog(true));
    expect(await screen.findByLabelText('Audience (optional)')).toHaveValue('The CISO');
    expect(screen.getByLabelText('Instructions (optional)')).toHaveValue('Lead with risk.');
    expect(screen.queryByLabelText('Drafted report (editable)')).toBeNull();
    expect(screen.getByRole('button', { name: /Generate draft/ })).toBeInTheDocument();
  });
});
