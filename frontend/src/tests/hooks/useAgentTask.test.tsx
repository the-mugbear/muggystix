/**
 * Handing a task to the operator's agent (5.322.0): copied when one of their
 * sessions is live, the Start Agent Session dialog otherwise.
 *
 * 5.328.0 — "live" is read from the one session list (the operator's active
 * project sessions, asked for by filter) and decided by the key's expiry; the
 * session is named by its one id. It read a second list whose rows carried a
 * second id and a server-derived status.
 */
import React from 'react';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { useQueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listAgentSessions: vi.fn() }));
vi.mock('../../services/api', () => api);
const copy = vi.hoisted(() => vi.fn());
vi.mock('../../utils/clipboard', () => ({ copyToClipboard: copy }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 7 } }) }));
// The project the control is shown in: the look-up names it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1 } }) }));
const can = vi.hoisted(() => ({ value: true }));
vi.mock('../../hooks/useCanStartAgentSession', () => ({ useCanStartAgentSession: () => can.value }));
vi.mock('../../components/StartAssistDialog', () => ({
  default: ({ instruction, mySessions = [] }: { instruction?: string; mySessions?: { id: number }[] }) => (
    <div data-testid="start-dialog" data-sessions={mySessions.map((s) => s.id).join(',')}>{instruction}</div>
  ),
}));

import { useAgentTask } from '../../hooks/useAgentTask';

const Harness: React.FC<{ onReady: (give: (t: string) => Promise<void>) => void }> = ({ onReady }) => {
  const task = useAgentTask();
  onReady(task.give);
  return <>{task.dialog}</>;
};

const inAnHour = () => new Date(Date.now() + 3_600_000).toISOString();
const anHourAgo = () => new Date(Date.now() - 3_600_000).toISOString();
const session = (over = {}) => ({
  kind: 'project', id: 79, project_id: 1, status: 'active', user_id: 7,
  key_expires_at: inAnHour(), ...over,
});
const listed = (...sessions: object[]) => ({ project_id: 1, sessions, total: sessions.length });

beforeEach(() => {
  vi.clearAllMocks();
  can.value = true;
  copy.mockResolvedValue(true);
});

describe('useAgentTask', () => {
  it('copies the task and says which session is live, without opening the dialog', async () => {
    api.listAgentSessions.mockResolvedValue(listed(session()));
    let give!: (t: string) => Promise<void>;
    render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('Propose tests for host 5'));
    // Asked of the server: this operator's active project sessions.
    expect(api.listAgentSessions).toHaveBeenCalledWith(
      1, { kind: 'project', status: 'active', user_id: 7 }, expect.anything(),
    );
    expect(copy).toHaveBeenCalledWith('Propose tests for host 5');
    expect(toast.success.mock.calls[0][0]).toContain('session #79');
    expect(screen.queryByTestId('start-dialog')).not.toBeInTheDocument();
  });

  // The decision is never made from a list that may be a minute old: a
  // session that ended since the last click is not "live".
  it('asks again at every click — a session ended in between opens the dialog, not a copy', async () => {
    api.listAgentSessions.mockResolvedValue(listed(session()));
    let give!: (t: string) => Promise<void>;
    render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('first task'));
    expect(copy).toHaveBeenCalledTimes(1);

    api.listAgentSessions.mockResolvedValue(listed());
    await act(() => give('second task'));
    expect(copy).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId('start-dialog')).toHaveTextContent('second task');
  });

  // 5.363.0 — the dialog's "your sessions" is the shared list, so a session
  // started or ended elsewhere reaches it (it was the click's answer, kept in
  // state, and two callbacks told the hook to ask again).
  it('the dialog is given the shared list of sessions, which follows a change made elsewhere', async () => {
    api.listAgentSessions.mockResolvedValue(listed());
    let give!: (t: string) => Promise<void>;
    let client!: ReturnType<typeof useQueryClient>;
    const Probe = () => { client = useQueryClient(); return null; };
    render(<><Probe /><Harness onReady={(g) => { give = g; }} /></>);
    await act(() => give('a task'));
    expect(await screen.findByTestId('start-dialog')).toHaveAttribute('data-sessions', '');

    // Another surface started a session and said the lists are out of date.
    api.listAgentSessions.mockResolvedValue(listed(session({ id: 91 })));
    await act(async () => { await client.invalidateQueries({ queryKey: ['listAgentSessions'] }); });
    await waitFor(() => expect(screen.getByTestId('start-dialog')).toHaveAttribute('data-sessions', '91'));
  });

  it('opens the start dialog with the task when no session of the caller is live', async () => {
    // Still stored as active, but nothing can use them: the key ran out, or
    // was revoked. Those wait on Agent Sessions to be resumed.
    api.listAgentSessions.mockResolvedValue(listed(
      session({ key_expires_at: anHourAgo() }),
      session({ id: 80, key_expires_at: null }),
    ));
    let give!: (t: string) => Promise<void>;
    render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('Propose tests for host 5'));
    expect(copy).not.toHaveBeenCalled();
    expect(screen.getByTestId('start-dialog')).toHaveTextContent('Propose tests for host 5');
  });

  it('falls back to the dialog when the copy fails or the sessions cannot be read', async () => {
    api.listAgentSessions.mockResolvedValue(listed(session()));
    copy.mockResolvedValue(false);
    let give!: (t: string) => Promise<void>;
    const { unmount } = render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('task one'));
    expect(screen.getByTestId('start-dialog')).toHaveTextContent('task one');
    expect(toast.success).not.toHaveBeenCalled();
    unmount();

    api.listAgentSessions.mockRejectedValue(new Error('down'));
    render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('task two'));
    expect(screen.getByTestId('start-dialog')).toHaveTextContent('task two');
  });

  it('reports that a viewer has nothing to offer', () => {
    can.value = false;
    expect(renderHook(() => useAgentTask()).result.current.allowed).toBe(false);
  });
});
