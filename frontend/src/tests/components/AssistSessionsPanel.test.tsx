import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import AssistSessionsPanel from '../../components/AssistSessionsPanel';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { AssistSessionRow } from '../../services/api';

const endAssistSession = vi.fn();
vi.mock('../../services/api', () => ({
  endAssistSession: (...args: unknown[]) => endAssistSession(...args),
}));

const success = vi.fn();
const error = vi.fn();
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success, error, info: vi.fn(), warning: vi.fn() }),
}));

const session = (over: Partial<AssistSessionRow> = {}): AssistSessionRow => ({
  id: 12,
  project_id: 1,
  purpose: 'Looking for FTP exposure',
  status: 'active',
  started_by_id: 7,
  started_by_username: 'alice',
  started_at: new Date(Date.now() - 40 * 60_000).toISOString(),
  ended_at: null,
  last_activity_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  environment_probed: true,
  capabilities: [],
  capability_constraint: null,
  ...over,
});

const renderPanel = (sessions: AssistSessionRow[], onChanged = vi.fn()) =>
  render(
    <TooltipProvider>
      <AssistSessionsPanel sessions={sessions} onChanged={onChanged} />
    </TooltipProvider>,
  );

beforeEach(() => {
  endAssistSession.mockReset().mockResolvedValue(undefined);
  success.mockReset();
  error.mockReset();
});

describe('AssistSessionsPanel', () => {
  it('renders nothing when the operator has no active session', () => {
    // The common case. A permanent "no active sessions" panel is noise.
    const { container } = renderPanel([]);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the live key and how long it has been idle', () => {
    renderPanel([session()]);
    expect(screen.getByText(/1 active assist session/i)).toBeInTheDocument();
    expect(screen.getByText('#12')).toBeInTheDocument();
    expect(screen.getByText(/Started 40 minutes ago/)).toBeInTheDocument();
    expect(screen.getByText(/last used 5 minutes ago/)).toBeInTheDocument();
  });

  it('distinguishes a write-capable session from a read-only one', () => {
    renderPanel([session({ capabilities: ['write:notes'] })]);
    expect(screen.getByText('Can write')).toBeInTheDocument();
    expect(screen.queryByText('Read-only')).toBeNull();
  });

  it('flags a session whose agent never connected', () => {
    // Key minted, prompt never pasted — different from merely idle, and the
    // operator usually wants to end it.
    renderPanel([session({ environment_probed: false, last_activity_at: null })]);
    expect(screen.getByText('Not yet connected')).toBeInTheDocument();
    expect(screen.getByText(/not used yet/)).toBeInTheDocument();
  });

  it('ends a session only after confirmation, then refreshes', async () => {
    const onChanged = vi.fn();
    renderPanel([session()], onChanged);

    fireEvent.click(screen.getByRole('button', { name: /end assist session 12/i }));
    // Revoking a key mid-conversation is disruptive enough to confirm.
    fireEvent.click(await screen.findByRole('button', { name: /^end session$/i }));

    await waitFor(() => expect(endAssistSession).toHaveBeenCalledWith(12));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(success).toHaveBeenCalled();
  });

  it('does not end the session when the confirmation is dismissed', async () => {
    renderPanel([session()]);
    fireEvent.click(screen.getByRole('button', { name: /end assist session 12/i }));
    fireEvent.click(await screen.findByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(endAssistSession).not.toHaveBeenCalled());
  });

  it('surfaces a failure instead of silently appearing to succeed', async () => {
    endAssistSession.mockRejectedValue(new Error('boom'));
    const onChanged = vi.fn();
    renderPanel([session()], onChanged);

    fireEvent.click(screen.getByRole('button', { name: /end assist session 12/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^end session$/i }));

    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(success).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('lists every active session, not just the first', () => {
    renderPanel([session({ id: 12 }), session({ id: 13, purpose: null })]);
    expect(screen.getByText(/2 active assist sessions/i)).toBeInTheDocument();
    expect(screen.getByText('#12')).toBeInTheDocument();
    expect(screen.getByText('#13')).toBeInTheDocument();
  });
});
