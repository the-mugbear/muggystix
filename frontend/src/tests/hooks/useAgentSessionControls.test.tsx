import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, fireEvent, waitFor } from '@testing-library/react';

import React from 'react';

import { readsOnScreen } from '../helpers/readsOnScreen';
import { useAgentSessionControls } from '../../hooks/useAgentSessionControls';
import type { AgentSessionRow } from '../../services/api';

const endAgentSession = vi.fn();
vi.mock('../../services/api', () => ({
  endAgentSession: (...args: unknown[]) => endAgentSession(...args),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
// The project the controls are shown in (the rows' own): End names it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1 } }) }));

type Deferred = { promise: Promise<void>; resolve: () => void };
const deferred = (): Deferred => {
  let resolve!: () => void;
  const promise = new Promise<void>((res) => { resolve = res; });
  return { promise, resolve };
};

const row = (id: number): AgentSessionRow => ({
  kind: 'project', id, project_id: 1, status: 'active', can_end: true,
});

// A block, not an expression: `mockReset()` returns the mock, and a function
// returned from `beforeEach` is run by vitest as the test's teardown.
beforeEach(() => { endAgentSession.mockReset(); });

// N5 — ending A then B before A returns must keep both in flight: a single
// `endingId` re-enabled A when B started and A's finally cleared B's spinner.
describe('useAgentSessionControls', () => {
  it('tracks every End in flight, clearing each only when it settles', async () => {
    const a = deferred();
    const b = deferred();
    endAgentSession.mockImplementation((_projectId: number, id: number) => (id === 1 ? a.promise : b.promise));
    const { result } = renderHook(() => useAgentSessionControls());
    const { rerender } = render(<>{result.current.dialogs}</>);

    const confirmEnd = async () => {
      rerender(<>{result.current.dialogs}</>);
      fireEvent.click(await screen.findByRole('button', { name: /^end session$/i }));
    };

    let pa!: Promise<boolean>;
    act(() => { pa = result.current.requestEnd(row(1)); });
    await confirmEnd();
    await waitFor(() => expect(result.current.isEnding(1)).toBe(true));

    let pb!: Promise<boolean>;
    act(() => { pb = result.current.requestEnd(row(2)); });
    await confirmEnd();
    await waitFor(() => expect(result.current.isEnding(2)).toBe(true));
    expect(result.current.isEnding(1)).toBe(true);

    await act(async () => { a.resolve(); await pa; });
    expect(result.current.isEnding(1)).toBe(false);
    expect(result.current.isEnding(2)).toBe(true);

    await act(async () => { b.resolve(); await pb; });
    expect(result.current.isEnding(2)).toBe(false);
  });

  // 5.351.0 — the hook took an `onChanged` its callers used to re-read their
  // lists.  An End says itself which reads are out of date: the lists of
  // sessions, the session's own page and notes, the counts over sessions.
  it('an End reads again every read of sessions that is on screen, and says it ended', async () => {
    endAgentSession.mockResolvedValue(undefined);
    const names = {
      listAgentSessions: 'lists', getAgentSession: 'the session', getAgentSessionNotes: 'its notes',
      getAgentSessionSummary: 'the summary', getAgentActivitySummary: 'the activity summary',
    };
    const { reread, ReadsOnScreen } = readsOnScreen(names);
    let controls!: ReturnType<typeof useAgentSessionControls>;
    const Page: React.FC = () => {
      controls = useAgentSessionControls();
      return <><ReadsOnScreen />{controls.dialogs}</>;
    };
    render(<Page />);

    let ended!: Promise<boolean>;
    act(() => { ended = controls.requestEnd(row(1)); });
    fireEvent.click(await screen.findByRole('button', { name: /^end session$/i }));
    await expect(ended).resolves.toBe(true);
    await waitFor(() => expect(reread).toHaveBeenCalledTimes(5));
    expect(reread.mock.calls.map(([what]) => what).sort()).toEqual(Object.values(names).sort());
  });

  it.each([
    ['is not confirmed', /cancel/i, 0],
    ['fails', /^end session$/i, 1],
  ] as const)('an End that %s reads nothing again and says so', async (_what, button, asked) => {
    endAgentSession.mockRejectedValue(new Error('boom'));
    const { reread, ReadsOnScreen } = readsOnScreen({ listAgentSessions: 'lists' });
    let controls!: ReturnType<typeof useAgentSessionControls>;
    const Page: React.FC = () => {
      controls = useAgentSessionControls();
      return <><ReadsOnScreen />{controls.dialogs}</>;
    };
    render(<Page />);

    let ended!: Promise<boolean>;
    act(() => { ended = controls.requestEnd(row(1)); });
    fireEvent.click(await screen.findByRole('button', { name: button }));
    let answer: boolean | undefined;
    await act(async () => { answer = await ended; });
    expect(answer).toBe(false);
    expect(endAgentSession).toHaveBeenCalledTimes(asked);
    expect(reread).not.toHaveBeenCalled();
  });
});
