/**
 * LLM providers — the edit dialog shows the provider as the server has it now
 * (defect 1.13).  The dialog kept its own copy of the row from the moment it
 * was opened: after "Remove the stored API key" it still offered to remove a
 * key that was gone.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  listLLMProviders: vi.fn(),
  listLLMProviderTypes: vi.fn(),
  createLLMProvider: vi.fn(),
  updateLLMProvider: vi.fn(),
  deleteLLMProvider: vi.fn(),
  testLLMProvider: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));

import * as api from '../../services/api';
import LLMSettings from '../../pages/LLMSettings';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const provider = (over: Record<string, unknown> = {}) => ({
  id: 4, name: 'Work OpenAI', provider_type: 'openai', base_url: null, model_id: 'gpt-4o-mini',
  has_api_key: true, extra_config: null, is_default: false,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', ...over,
});

const CLEAR = 'Remove the stored API key';

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listLLMProviders.mockResolvedValue([provider()]);
  mocked.listLLMProviderTypes.mockResolvedValue([{ value: 'openai', label: 'OpenAI' }]);
  mocked.updateLLMProvider.mockResolvedValue(provider());
  mocked.createLLMProvider.mockResolvedValue(provider({ id: 5 }));
});

const openEdit = async () => {
  render(<LLMSettings />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit provider Work OpenAI' }));
  return screen.findByRole('dialog');
};

describe('LLM providers — the edit dialog', () => {
  it('stops offering to remove the stored key once it has been removed', async () => {
    await openEdit();
    // The server's next answer: the key is gone.
    mocked.listLLMProviders.mockResolvedValue([provider({ has_api_key: false })]);
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));

    await waitFor(() => expect(mocked.updateLLMProvider).toHaveBeenCalledWith(4, { clear_api_key: true }));
    await waitFor(() => expect(screen.queryByRole('button', { name: CLEAR })).toBeNull());
    // Still the same dialog, on the same provider, with what was typed.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Edit LLM Provider')).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toHaveValue('Work OpenAI');
  });

  it('keeps what the reader typed when the list is read again', async () => {
    await openEdit();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Renamed' } });
    mocked.listLLMProviders.mockResolvedValue([provider({ has_api_key: false })]);
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));
    await waitFor(() => expect(screen.queryByRole('button', { name: CLEAR })).toBeNull());
    expect(screen.getByLabelText('Name')).toHaveValue('Renamed');
  });

  it('saves the form as it stands, to the provider being edited', async () => {
    await openEdit();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Renamed' } });
    fireEvent.change(screen.getByLabelText('Model ID'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateLLMProvider).toHaveBeenCalledWith(4, {
      name: 'Renamed', base_url: null, model_id: null, is_default: false,
    }));
    expect(mocked.createLLMProvider).not.toHaveBeenCalled();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Provider updated.'));
  });

  // The row can go while its dialog is open (deleted from another tab): Save
  // must not turn into "add a new provider".
  it('an edit never becomes an add when the provider has gone from the list', async () => {
    await openEdit();
    mocked.listLLMProviders.mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));
    await waitFor(() => expect(mocked.listLLMProviders).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('button', { name: CLEAR })).toBeNull());
    expect(screen.getByText('Edit LLM Provider')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateLLMProvider).toHaveBeenCalledWith(4, expect.objectContaining({ name: 'Work OpenAI' })));
    expect(mocked.createLLMProvider).not.toHaveBeenCalled();
  });

  it('adds a new provider from the form', async () => {
    render(<LLMSettings />);
    fireEvent.click(await screen.findByRole('button', { name: /Add Provider/ }));
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Home Ollama' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.createLLMProvider).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Home Ollama', provider_type: 'openai', base_url: undefined, api_key: undefined,
    })));
    expect(mocked.updateLLMProvider).not.toHaveBeenCalled();
  });
});
