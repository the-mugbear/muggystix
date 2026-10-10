/**
 * LLM providers — the edit dialog shows the provider as the server has it now
 * (defect 1.13).  The dialog kept its own copy of the row from the moment it
 * was opened: after "Remove the stored API key" it still offered to remove a
 * key that was gone.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  listLLMProviders: vi.fn(),
  listLLMProviderTypes: vi.fn(),
  createLLMProvider: vi.fn(),
  updateLLMProvider: vi.fn(),
  deleteLLMProvider: vi.fn(),
  testLLMProvider: vi.fn(),
}));
const account = vi.hoisted(() => ({ role: 'admin' }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: account.role }, hasPermission: (r: string) => r !== 'admin' || account.role === 'admin' }),
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
  account.role = 'admin';
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

// A failed read was a toast over "No LLM providers configured yet." — gone in
// seconds, with nothing to press.  It is said where the providers would be.
describe('LLM providers — the list could not be read', () => {
  const down = { response: { status: 503, data: { detail: 'The provider store is not answering.' } } };

  it('says so in place of the list, with Retry — not "none configured", and not as a toast', async () => {
    mocked.listLLMProviders.mockRejectedValueOnce(down);
    render(<LLMSettings />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The provider store is not answering.');
    expect(screen.queryByText('No LLM providers configured yet.')).toBeNull();
    expect(screen.queryByRole('button', { name: /Add Your First Provider/ })).toBeNull();
    expect(toast.error).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Work OpenAI')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(mocked.listLLMProviders).toHaveBeenCalledTimes(2);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('keeps the providers it has when a later read fails, and says so beside them', async () => {
    await openEdit();
    mocked.listLLMProviders.mockRejectedValueOnce(down);
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('API key cleared.'));

    expect(await screen.findByText('The provider store is not answering.')).toBeInTheDocument();
    expect(screen.getByText('Work OpenAI')).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });
});

// A provider is its OWNER's (a per-user row, and the in-app drafter uses the
// signed-in user's own): every user manages theirs.  (A gate making the
// writes a global administrator's was written and taken out on 2026-10-10 —
// it would have left every non-admin with no provider to draft with.)
describe('LLM providers — every user manages their own', () => {
  it('shows the controls to an account that is not an administrator', async () => {
    account.role = 'member';
    render(<LLMSettings />);
    expect(await screen.findByText('Work OpenAI')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Provider/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit provider Work OpenAI' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete provider Work OpenAI' })).toBeInTheDocument();
  });
});

// Owner decision 52: the skeleton is for the first load only.  Any later read
// (after a save, a Retry) keeps the providers on screen.
describe('LLM providers — a re-read keeps the rows', () => {
  it('the providers stay on screen while the list is read again', async () => {
    await openEdit();
    // The re-read after "clear" does not answer yet.
    let answer: (value: unknown) => void = () => {};
    mocked.listLLMProviders.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));
    await waitFor(() => expect(mocked.listLLMProviders).toHaveBeenCalledTimes(2));

    // (The open dialog hides the page behind it from assistive technology.)
    expect(screen.getByText('gpt-4o-mini')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Test connection to Work OpenAI', hidden: true })).toBeInTheDocument();

    answer([provider({ model_id: 'gpt-4.1' })]);
    expect(await screen.findByText('gpt-4.1')).toBeInTheDocument();
  });

  it('a Retry after a failed re-read keeps the rows while it asks, and the failure line goes when it answers', async () => {
    const down = { response: { status: 503, data: { detail: 'The provider store is not answering.' } } };
    await openEdit();
    mocked.listLLMProviders.mockRejectedValueOnce(down);
    fireEvent.click(screen.getByRole('button', { name: CLEAR }));
    const alert = await screen.findByRole('alert', { hidden: true });
    expect(alert).toHaveTextContent('The provider store is not answering.');
    expect(screen.getByText('gpt-4o-mini')).toBeInTheDocument();

    let answer: (value: unknown) => void = () => {};
    mocked.listLLMProviders.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry', hidden: true }));
    await waitFor(() => expect(mocked.listLLMProviders).toHaveBeenCalledTimes(3));
    expect(screen.getByText('gpt-4o-mini')).toBeInTheDocument();

    answer([provider()]);
    await waitFor(() => expect(screen.queryByRole('alert', { hidden: true })).toBeNull());
    expect(screen.getByText('gpt-4o-mini')).toBeInTheDocument();
  });
});

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
