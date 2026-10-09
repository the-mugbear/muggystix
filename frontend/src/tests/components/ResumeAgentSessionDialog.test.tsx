/**
 * Resuming an agent session: the dialog says whether the old key still works,
 * and — when the reader asks — rotates it.  The replacement key is in the
 * answer and is shown ONCE.
 *
 * Pinned: the request (project first, then the session), the key on screen
 * once and never again after the dialog was dismissed, that the dialog cannot
 * be closed by accident while the key is on screen and nothing holding it was
 * copied, a refusal said, every read of sessions asked again, and that the
 * client holds the key nowhere once the dialog is closed.
 */
import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import type { AgentSessionRow, ResumeAgentSessionResponse } from '../../services/api';
import { AGENT_SESSION_READS } from '../../utils/agentRuns';
import { copyToClipboard } from '../../utils/clipboard';
import { formatTimestamp } from '../../utils/relativeTime';
import { heldByMutations, withClient } from '../helpers/heldByMutations';
import { readsOnScreen } from '../helpers/readsOnScreen';

const api = vi.hoisted(() => ({ resumeAgentSession: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../utils/clipboard', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }));

const project = vi.hoisted(() => ({ current: { id: 1, name: 'Demo' } as { id: number; name: string } | null }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: project.current }),
}));

import ResumeAgentSessionDialog, { resumePromptLine } from '../../components/ResumeAgentSessionDialog';

const KEY = 'nm_agent_REPLACEMENT_k3y_9f2c';
const PROMPT = `You are resuming a BlueStick session.\nX-API-Key: ${KEY}`;
const FUTURE = '2099-01-01T00:00:00Z';
const LATER = '2099-02-01T00:00:00Z';
const PAST = '2000-01-01T00:00:00Z';
const LONG_PROJECT = `Engagement-${'x'.repeat(189)}`;          // 200 characters

const session = (over: Partial<AgentSessionRow> = {}): AgentSessionRow => ({
  kind: 'project', id: 42, project_id: 1, status: 'active',
  key_expires_at: FUTURE, renewable_until: LATER, ...over,
});
const rotated = (over: Partial<ResumeAgentSessionResponse> = {}): ResumeAgentSessionResponse => ({
  session_id: 42, project_id: 1, project_name: 'Demo', agent_id: 9, api_key: KEY, instructions: PROMPT,
  mcp_clients: [], mcp_url: 'https://bluestick.example/api/v1/mcp', key_ttl_hours: 24,
  key_expires_at: FUTURE, renewable_until: LATER, ...over,
});
const refused = (status: number, detail: string) => ({ response: { status, data: { detail } } });

const sessionReads = readsOnScreen(Object.fromEntries(AGENT_SESSION_READS.map((name) => [name, name])));
const onOpenChange = vi.fn();

/** The page around the dialog, as `useAgentSessionControls` holds it: a row
 *  to resume, cleared when the dialog says it closed. */
const Page: React.FC<{ row: AgentSessionRow }> = ({ row }) => {
  const [resumeRow, setResumeRow] = useState<AgentSessionRow | null>(row);
  return (
    <>
      <sessionReads.ReadsOnScreen />
      <button type="button" onClick={() => setResumeRow(row)}>Resume from the page</button>
      <ResumeAgentSessionDialog
        session={resumeRow}
        onOpenChange={(next) => {
          onOpenChange(next);
          if (!next) setResumeRow(null);
        }}
      />
    </>
  );
};

const show = (row: AgentSessionRow = session()) => {
  const client = createQueryClient();
  render(<Page row={row} />, { wrapper: withClient(client) });
  return client;
};
const rotate = () => fireEvent.click(screen.getByRole('button', { name: 'Rotate key and get the prompt' }));
const pressEscape = () => fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
const keyOnScreen = () => screen.queryAllByText((text) => text.includes(KEY)).length > 0;
/** Everything the client still holds: its mutations and its queries. */
const heldAnywhere = (client: ReturnType<typeof createQueryClient>): string =>
  heldByMutations(client) + JSON.stringify(client.getQueryCache().getAll().map((q) => q.state.data ?? null));

beforeEach(() => {
  vi.clearAllMocks();
  project.current = { id: 1, name: 'Demo' };
  window.localStorage.clear();
  api.resumeAgentSession.mockResolvedValue(rotated());
  vi.mocked(copyToClipboard).mockResolvedValue(true);
});

describe('ResumeAgentSessionDialog — before anything is rotated', () => {
  it('a live key: says until when it works and can be renewed, gives the line for the agent, and asks nothing', () => {
    show();
    const dialog = screen.getByRole('dialog', { name: 'Resume agent session #42' });
    expect(dialog).toHaveTextContent(`The key is valid until ${formatTimestamp(FUTURE)}`);
    expect(dialog).toHaveTextContent(`the agent can renew it until ${formatTimestamp(LATER)}`);
    expect(screen.getByText(resumePromptLine(42))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Rotate key and get the prompt' })).toBeEnabled();
    expect(api.resumeAgentSession).not.toHaveBeenCalled();
    expect(sessionReads.reread).not.toHaveBeenCalled();
  });

  it('an expired key inside the session’s lifetime: the agent renews it itself, the line is still offered', () => {
    show(session({ key_expires_at: PAST }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(`The key expired at ${formatTimestamp(PAST)}`);
    expect(dialog).toHaveTextContent(`renewed until ${formatTimestamp(LATER)}`);
    expect(screen.getByText(resumePromptLine(42))).toBeInTheDocument();
  });

  it('a revoked key (none on the row): that path is closed, no line to paste, rotating is offered', () => {
    show(session({ key_expires_at: null }));
    expect(screen.getByText(/The previous key was revoked, so this path is closed/)).toBeInTheDocument();
    expect(screen.queryByText(resumePromptLine(42))).toBeNull();
    expect(screen.getByRole('button', { name: 'Rotate key and get the prompt' })).toBeEnabled();
  });

  it('past its maximum lifetime: says so and offers no rotation', () => {
    show(session({ key_expires_at: PAST, renewable_until: PAST }));
    expect(screen.getByText(/past its maximum lifetime and can no longer be renewed or resumed/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rotate key and get the prompt' })).toBeNull();
    expect(screen.queryByText(resumePromptLine(42))).toBeNull();
    fireEvent.click(within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Close' })[0]);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(api.resumeAgentSession).not.toHaveBeenCalled();
  });

  it('with no session there is no dialog', () => {
    const client = createQueryClient();
    render(<ResumeAgentSessionDialog session={null} onOpenChange={onOpenChange} />, { wrapper: withClient(client) });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Escape and Close dismiss it while there is no key to lose', async () => {
    show();
    pressEscape();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(api.resumeAgentSession).not.toHaveBeenCalled();
  });
});

describe('ResumeAgentSessionDialog — rotating the key', () => {
  it('asks for THIS session of THIS project, once, and shows the key, the prompt and what was revoked', async () => {
    api.resumeAgentSession.mockResolvedValue(rotated({ project_name: LONG_PROJECT }));
    show();
    rotate();

    expect(await screen.findByTestId('agent-key')).toHaveTextContent(KEY);
    expect(api.resumeAgentSession).toHaveBeenCalledTimes(1);
    expect(api.resumeAgentSession).toHaveBeenCalledWith(1, 42);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(`Key rotated on agent session #42 for project ${LONG_PROJECT}. The previous key is revoked.`);
    expect(dialog).toHaveTextContent('You are resuming a BlueStick session.');
    expect(dialog).toHaveTextContent(`Valid 24 h; renewable until ${formatTimestamp(LATER)}.`);
    // The first step is gone: one thing to do now.
    expect(screen.queryByRole('button', { name: 'Rotate key and get the prompt' })).toBeNull();
    expect(screen.queryByText(resumePromptLine(42))).toBeNull();
  });

  it('names the project the dialog was showing', async () => {
    project.current = { id: 7, name: 'Other' };
    show(session({ id: 5, project_id: 7 }));
    rotate();
    await screen.findByTestId('agent-key');
    expect(api.resumeAgentSession).toHaveBeenCalledWith(7, 5);
  });

  it('every read of sessions is asked again', async () => {
    show();
    rotate();
    await screen.findByTestId('agent-key');
    await waitFor(() => {
      for (const name of AGENT_SESSION_READS) expect(sessionReads.reread).toHaveBeenCalledWith(name);
    });
    expect(sessionReads.reread).toHaveBeenCalledTimes(AGENT_SESSION_READS.length);
  });

  it('while the request is out: nothing can be sent twice and the dialog stays', async () => {
    let answer: (value: ResumeAgentSessionResponse) => void = () => {};
    api.resumeAgentSession.mockReturnValue(new Promise<ResumeAgentSessionResponse>((resolve) => { answer = resolve; }));
    show();
    rotate();

    const button = screen.getByRole('button', { name: 'Rotate key and get the prompt' });
    await waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);
    pressEscape();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    answer(rotated());
    expect(await screen.findByTestId('agent-key')).toHaveTextContent(KEY);
    expect(api.resumeAgentSession).toHaveBeenCalledTimes(1);
  });

  it('a refusal is said in the dialog, shows no key, re-reads nothing, and can be tried again', async () => {
    api.resumeAgentSession.mockRejectedValueOnce(refused(403, 'Only the operator who started this session can resume it.'));
    show();
    rotate();

    expect(await screen.findByText('Only the operator who started this session can resume it.')).toBeInTheDocument();
    expect(keyOnScreen()).toBe(false);
    expect(screen.queryByText(/Key rotated/)).toBeNull();
    expect(sessionReads.reread).not.toHaveBeenCalled();
    // Still on the first step, with what it said.
    expect(screen.getByText(resumePromptLine(42))).toBeInTheDocument();

    rotate();
    expect(await screen.findByTestId('agent-key')).toHaveTextContent(KEY);
    expect(screen.queryByText('Only the operator who started this session can resume it.')).toBeNull();
    expect(api.resumeAgentSession).toHaveBeenCalledTimes(2);
    expect(api.resumeAgentSession).toHaveBeenLastCalledWith(1, 42);
  });

  it('a refusal is not carried into the next opening', async () => {
    api.resumeAgentSession.mockRejectedValueOnce(refused(409, 'This session is not active.'));
    show();
    rotate();
    expect(await screen.findByText('This session is not active.')).toBeInTheDocument();

    fireEvent.click(within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Close' })[0]);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Resume from the page' }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText('This session is not active.')).toBeNull();
  });
});

describe('ResumeAgentSessionDialog — the key is shown once', () => {
  it('cannot be closed by accident while nothing holding the key was copied', async () => {
    show();
    rotate();
    await screen.findByTestId('agent-key');

    // No corner X, and Escape does nothing.
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    pressEscape();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(keyOnScreen()).toBe(true);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    // Done says what would be lost before it closes anything.
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Nothing was copied — the key will not be shown again.');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(keyOnScreen()).toBe(true);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    fireEvent.click(screen.getByRole('button', { name: 'Close anyway' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('once the prompt (which holds the key) was copied, Done closes at once', async () => {
    show();
    rotate();
    await screen.findByTestId('agent-key');

    fireEvent.click(screen.getByRole('button', { name: 'Copy the agent prompt' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument());
    expect(copyToClipboard).toHaveBeenCalledWith(PROMPT);

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('copying the key on its own counts too, and Escape then closes', async () => {
    show();
    rotate();
    await screen.findByTestId('agent-key');

    fireEvent.click(screen.getByRole('button', { name: 'Copy agent API key' }));
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(KEY));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument());
    pressEscape();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('a copy that did not reach the clipboard does not count', async () => {
    vi.mocked(copyToClipboard).mockResolvedValue(false);
    show();
    rotate();
    await screen.findByTestId('agent-key');

    fireEvent.click(screen.getByRole('button', { name: 'Copy the agent prompt' }));
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(PROMPT));
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was copied');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    pressEscape();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('dismissed and opened again: no key, no second rotation, and the client holds the key nowhere', async () => {
    const client = show();
    rotate();
    await screen.findByTestId('agent-key');
    fireEvent.click(screen.getByRole('button', { name: 'Copy the agent prompt' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    expect(keyOnScreen()).toBe(false);
    await waitFor(() => expect(heldAnywhere(client)).not.toContain(KEY));

    fireEvent.click(screen.getByRole('button', { name: 'Resume from the page' }));
    expect(await screen.findByRole('dialog', { name: 'Resume agent session #42' })).toBeInTheDocument();
    // Back at the first step: the key is not there to be read a second time.
    expect(screen.getByRole('button', { name: 'Rotate key and get the prompt' })).toBeEnabled();
    expect(screen.queryByTestId('agent-key')).toBeNull();
    expect(screen.queryByText(/Key rotated/)).toBeNull();
    expect(keyOnScreen()).toBe(false);
    expect(api.resumeAgentSession).toHaveBeenCalledTimes(1);
    expect(heldAnywhere(client)).not.toContain(KEY);
    // The warning of the previous opening is not remembered either.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('"Close anyway" drops the key just the same', async () => {
    const client = show();
    rotate();
    await screen.findByTestId('agent-key');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close anyway' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    await waitFor(() => expect(heldAnywhere(client)).not.toContain(KEY));
    fireEvent.click(screen.getByRole('button', { name: 'Resume from the page' }));
    await screen.findByRole('dialog');
    expect(keyOnScreen()).toBe(false);
    expect(screen.getByRole('button', { name: 'Rotate key and get the prompt' })).toBeInTheDocument();
  });
});
