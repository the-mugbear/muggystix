/**
 * Start Agent Session: the key is in the answer and is shown ONCE.
 *
 * The start dialog and the resume dialog share how a key is handed over
 * (`hooks/useKeyHandoff`, plan B2b): it cannot be dismissed while the request
 * is out, nor by accident while the key is on screen and nothing holding it
 * was copied; closing drops the key from the client; the next opening starts
 * clean.  `ResumeAgentSessionDialog.test.tsx` pins this for the resume
 * dialog; this pins it for the start dialog, with what only it has — the
 * page is told which session was started, after the dialog closed, and a
 * live session's "copy the task" step is forgotten with the opening.
 */
import React, { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import type { AgentSessionRow, StartAssistResponse } from '../../services/api';
import { copyToClipboard } from '../../utils/clipboard';
import { heldByMutations, withClient } from '../helpers/heldByMutations';
import { readsOnScreen } from '../helpers/readsOnScreen';

const api = vi.hoisted(() => ({ startAssistSession: vi.fn() }));
vi.mock('../../services/api', () => ({
  ...api,
  endAgentSession: vi.fn(),
  getMcpTools: vi.fn(() => new Promise(() => {})),
}));
vi.mock('../../utils/clipboard', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { StartAssistDialog } from '../../components/StartAssistDialog';
import { TooltipProvider } from '../../components/ui/tooltip';

const KEY = 'nm_agent_STARTED_k3y_71ab';
const started = (): StartAssistResponse => ({
  agent_session_id: 21, project_id: 1, project_name: 'engagement', agent_id: 9, api_key: KEY,
  instructions: `prompt text\nX-API-Key: ${KEY}`, mcp_url: 'https://bluestick.example/api/v1/mcp',
  mcp_clients: [], key_ttl_hours: 24,
} as StartAssistResponse);
const refused = (status: number, detail: string) => ({ response: { status, data: { detail } } });

const sessionReads = readsOnScreen({ listAgentSessions: 'sessions' });
const onOpenChange = vi.fn();
const onSessionStarted = vi.fn();

/** The page around the dialog: a button that opens it, closed when it says so. */
const Page: React.FC<{ instruction?: string; mySessions?: AgentSessionRow[] }> = (props) => {
  const [open, setOpen] = useState(true);
  return (
    <>
      <sessionReads.ReadsOnScreen />
      <button type="button" onClick={() => setOpen(true)}>Start from the page</button>
      <StartAssistDialog
        open={open}
        onOpenChange={(next) => {
          onOpenChange(next);
          setOpen(next);
        }}
        onSessionStarted={onSessionStarted}
        {...props}
      />
    </>
  );
};

const show = (props: React.ComponentProps<typeof Page> = {}) => {
  const client = createQueryClient();
  const Client = withClient(client);
  render(<Page {...props} />, {
    wrapper: ({ children }) => <Client><MemoryRouter><TooltipProvider>{children}</TooltipProvider></MemoryRouter></Client>,
  });
  return client;
};
const start = () => fireEvent.click(screen.getByRole('button', { name: 'Start session' }));
const pressEscape = () => fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
const keyOnScreen = () => screen.queryAllByText((text) => text.includes(KEY)).length > 0;
/** Everything the client still holds: its mutations and its queries. */
const heldAnywhere = (client: ReturnType<typeof createQueryClient>): string =>
  heldByMutations(client) + JSON.stringify(client.getQueryCache().getAll().map((q) => q.state.data ?? null));

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  api.startAssistSession.mockResolvedValue(started());
  vi.mocked(copyToClipboard).mockResolvedValue(true);
});

describe('StartAssistDialog — before a session is started', () => {
  it('Escape and Cancel dismiss it while there is no key to lose, and no session is reported', () => {
    show();
    pressEscape();
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Start from the page' }));
    onOpenChange.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onSessionStarted).not.toHaveBeenCalled();
    expect(api.startAssistSession).not.toHaveBeenCalled();
  });

  it('while the request is out: nothing can be sent twice and the dialog stays', async () => {
    let answer!: (value: StartAssistResponse) => void;
    api.startAssistSession.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    show();
    start();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start session' })).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    start();
    pressEscape();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(api.startAssistSession).toHaveBeenCalledTimes(1);
    await act(async () => { answer(started()); });
    expect(await screen.findByTestId('agent-key')).toHaveTextContent(KEY);
  });

  it('a refusal is said in the dialog, shows no key, re-reads nothing, and is not carried into the next opening', async () => {
    api.startAssistSession.mockRejectedValueOnce(refused(403, 'Agent sessions need the analyst role.'));
    show();
    start();
    expect(await screen.findByText('Agent sessions need the analyst role.')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-key')).toBeNull();
    expect(sessionReads.reread).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start from the page' }));
    await screen.findByRole('dialog');
    expect(screen.queryByText('Agent sessions need the analyst role.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Start session' })).toBeEnabled();
  });

  it('with a live session: "Start another session" and "Copied" are forgotten with the opening', async () => {
    const live = {
      id: 86, kind: 'project', status: 'active', can_end: true, can_resume: true,
      key_expires_at: new Date(Date.now() + 3 * 3_600_000).toISOString(),
    } as unknown as AgentSessionRow;
    show({ instruction: 'Propose tests for host 12.', mySessions: [live] });
    fireEvent.click(screen.getByRole('button', { name: 'Copy task' }));
    expect(await screen.findByRole('button', { name: /Copied — paste it to your agent/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Start another session' }));
    expect(screen.getByRole('button', { name: 'Start session' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start from the page' }));
    await screen.findByRole('dialog');
    expect(screen.getByRole('button', { name: 'Copy task' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start session' })).toBeNull();
  });
});

describe('StartAssistDialog — the key is shown once', () => {
  it('asks for a session of THIS project once, shows the key, and every read of sessions is asked again', async () => {
    show();
    start();
    expect(await screen.findByTestId('agent-key')).toHaveTextContent(KEY);
    expect(api.startAssistSession).toHaveBeenCalledTimes(1);
    expect(api.startAssistSession).toHaveBeenCalledWith(1, {});
    await waitFor(() => expect(sessionReads.reread).toHaveBeenCalledWith('sessions'));
    // Not yet: the page hears of the session when the dialog closes.
    expect(onSessionStarted).not.toHaveBeenCalled();
  });

  it('cannot be closed by accident while nothing holding the key was copied', async () => {
    show();
    start();
    await screen.findByTestId('agent-key');
    // No corner X, and Escape does nothing.
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    pressEscape();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    // Done says why, once; the second press is the reader's decision.
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was copied');
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole('button', { name: 'Close anyway' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onSessionStarted).toHaveBeenCalledWith(21);
  });

  it('once the prompt (which holds the key) was copied, Escape closes — and the page is told the session, after the close', async () => {
    show();
    start();
    await screen.findByTestId('agent-key');
    fireEvent.click(screen.getByRole('button', { name: 'Copy the agent prompt' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument());
    pressEscape();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onSessionStarted).toHaveBeenCalledTimes(1);
    expect(onSessionStarted).toHaveBeenCalledWith(21);
    expect(onOpenChange.mock.invocationCallOrder[0]).toBeLessThan(onSessionStarted.mock.invocationCallOrder[0]);
  });

  it('a copy that did not reach the clipboard does not count', async () => {
    vi.mocked(copyToClipboard).mockResolvedValue(false);
    show();
    start();
    await screen.findByTestId('agent-key');
    fireEvent.click(screen.getByRole('button', { name: 'Copy the agent prompt' }));
    await act(async () => { await Promise.resolve(); });
    pressEscape();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('dismissed and opened again: no key, no second session, and the client holds the key nowhere', async () => {
    const client = show();
    start();
    await screen.findByTestId('agent-key');
    expect(heldAnywhere(client)).toContain(KEY);   // (the probe sees it while it is shown)
    fireEvent.click(screen.getByRole('button', { name: 'Copy the agent prompt' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    expect(keyOnScreen()).toBe(false);
    await waitFor(() => expect(heldAnywhere(client)).not.toContain(KEY));

    fireEvent.click(screen.getByRole('button', { name: 'Start from the page' }));
    expect(await screen.findByRole('dialog', { name: 'Start Agent Session' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start session' })).toBeEnabled();
    expect(screen.queryByTestId('agent-key')).toBeNull();
    expect(keyOnScreen()).toBe(false);
    expect(api.startAssistSession).toHaveBeenCalledTimes(1);
    expect(heldAnywhere(client)).not.toContain(KEY);
    // The warning of the previous opening is not remembered either.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('"Close anyway" drops the key just the same, and the next key is not "already copied"', async () => {
    const client = show();
    start();
    await screen.findByTestId('agent-key');
    fireEvent.click(screen.getByRole('button', { name: 'Copy the agent prompt' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(heldAnywhere(client)).not.toContain(KEY));

    // A second session: its key has not been copied, whatever happened to the first.
    fireEvent.click(screen.getByRole('button', { name: 'Start from the page' }));
    await screen.findByRole('dialog');
    start();
    await screen.findByTestId('agent-key');
    pressEscape();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Close anyway' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(heldAnywhere(client)).not.toContain(KEY));
    expect(onSessionStarted).toHaveBeenCalledTimes(2);
  });
});
