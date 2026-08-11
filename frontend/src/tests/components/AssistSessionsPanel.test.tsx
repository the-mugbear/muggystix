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
  key_expires_at: new Date(Date.now() + 3 * 3_600_000).toISOString(),
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

  it('shows how long the key has left, since that decides end-or-lapse', () => {
    // Pin the clock. Computing the expiry from a live Date.now() made this
    // flaky: the few ms between building the timestamp and rendering pushed
    // the remaining time just under the boundary, so "3h 20m" intermittently
    // rendered as "3h 19m".
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-10T12:00:00Z'));
    try {
      renderPanel([session({ key_expires_at: '2026-08-10T15:20:00Z' })]);
      expect(screen.getByText(/expires in 3h 20m/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('treats a session with no live key as dead, not as expiring soon', () => {
    // status still reads 'active' here — the key is what actually grants
    // access, so "No live key" must not be rendered as "expires in 0 min".
    renderPanel([session({ key_expires_at: null })]);
    expect(screen.getByText('No live key')).toBeInTheDocument();
    expect(screen.queryByText(/expires in/)).toBeNull();
  });

  it('marks an already-expired key rather than showing negative time', () => {
    renderPanel([session({ key_expires_at: new Date(Date.now() - 60_000).toISOString() })]);
    expect(screen.getByText('key expired')).toBeInTheDocument();
  });
});
