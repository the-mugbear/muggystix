import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, fireEvent, waitFor } from '@testing-library/react';

import { useAgentSessionControls } from '../../hooks/useAgentSessionControls';
import type { AgentSessionRow } from '../../services/api';

const endAgentSession = vi.fn();
vi.mock('../../services/api', () => ({
  endAgentSession: (...args: unknown[]) => endAgentSession(...args),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

type Deferred = { promise: Promise<void>; resolve: () => void };
const deferred = (): Deferred => {
  let resolve!: () => void;
  const promise = new Promise<void>((res) => { resolve = res; });
  return { promise, resolve };
};

const row = (id: number): AgentSessionRow => ({
  kind: 'project', id, project_id: 1, status: 'active', can_end: true,
});

beforeEach(() => endAgentSession.mockReset());

// N5 — ending A then B before A returns must keep both in flight: a single
// `endingId` re-enabled A when B started and A's finally cleared B's spinner.
describe('useAgentSessionControls', () => {
  it('tracks every End in flight, clearing each only when it settles', async () => {
    const a = deferred();
    const b = deferred();
    endAgentSession.mockImplementation((id: number) => (id === 1 ? a.promise : b.promise));
    const { result } = renderHook(() => useAgentSessionControls(vi.fn()));
    const { rerender } = render(<>{result.current.dialogs}</>);

    const confirmEnd = async () => {
      rerender(<>{result.current.dialogs}</>);
      fireEvent.click(await screen.findByRole('button', { name: /^end session$/i }));
    };

    let pa!: Promise<void>;
    act(() => { pa = result.current.requestEnd(row(1)); });
    await confirmEnd();
    await waitFor(() => expect(result.current.isEnding(1)).toBe(true));

    let pb!: Promise<void>;
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
});
