/**
 * Handing a task to the operator's agent (5.322.0): copied when one of their
 * sessions is live, the Start Agent Session dialog otherwise.
 */
import React from 'react';
import { act, render, renderHook, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listAssistSessions: vi.fn() }));
vi.mock('../../services/api', () => api);
const copy = vi.hoisted(() => vi.fn());
vi.mock('../../utils/clipboard', () => ({ copyToClipboard: copy }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 7 } }) }));
const can = vi.hoisted(() => ({ value: true }));
vi.mock('../../hooks/useCanStartAgentSession', () => ({ useCanStartAgentSession: () => can.value }));
vi.mock('../../components/StartAssistDialog', () => ({
  default: ({ instruction }: { instruction?: string }) => <div data-testid="start-dialog">{instruction}</div>,
}));

import { useAgentTask } from '../../hooks/useAgentTask';

const Harness: React.FC<{ onReady: (give: (t: string) => Promise<void>) => void }> = ({ onReady }) => {
  const task = useAgentTask();
  onReady(task.give);
  return <>{task.dialog}</>;
};

const session = (over = {}) => ({ id: 3, agent_session_id: 79, status: 'active', started_by_id: 7, ...over });

beforeEach(() => {
  vi.clearAllMocks();
  can.value = true;
  copy.mockResolvedValue(true);
});

describe('useAgentTask', () => {
  it('copies the task and says which session is live, without opening the dialog', async () => {
    api.listAssistSessions.mockResolvedValue([session()]);
    let give!: (t: string) => Promise<void>;
    render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('Propose tests for host 5'));
    expect(copy).toHaveBeenCalledWith('Propose tests for host 5');
    expect(toast.success.mock.calls[0][0]).toContain('session #79');
    expect(screen.queryByTestId('start-dialog')).not.toBeInTheDocument();
  });

  it('opens the start dialog with the task when no session of the caller is live', async () => {
    api.listAssistSessions.mockResolvedValue([session({ started_by_id: 8 }), session({ status: 'ended' })]);
    let give!: (t: string) => Promise<void>;
    render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('Propose tests for host 5'));
    expect(copy).not.toHaveBeenCalled();
    expect(screen.getByTestId('start-dialog')).toHaveTextContent('Propose tests for host 5');
  });

  it('falls back to the dialog when the copy fails or the sessions cannot be read', async () => {
    api.listAssistSessions.mockResolvedValue([session()]);
    copy.mockResolvedValue(false);
    let give!: (t: string) => Promise<void>;
    const { unmount } = render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('task one'));
    expect(screen.getByTestId('start-dialog')).toHaveTextContent('task one');
    expect(toast.success).not.toHaveBeenCalled();
    unmount();

    api.listAssistSessions.mockRejectedValue(new Error('down'));
    render(<Harness onReady={(g) => { give = g; }} />);
    await act(() => give('task two'));
    expect(screen.getByTestId('start-dialog')).toHaveTextContent('task two');
  });

  it('reports that a viewer has nothing to offer', () => {
    can.value = false;
    expect(renderHook(() => useAgentTask()).result.current.allowed).toBe(false);
  });
});
