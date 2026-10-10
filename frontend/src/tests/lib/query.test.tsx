/**
 * lib/query — what the whole app shares for server state.
 *
 * `pollEvery` took over what `hooks/useVisibilityPoll` guaranteed (its test
 * file went with it): no read in a hidden tab, one at once on return, never
 * two reads of the same thing in flight, slower while the server is failing,
 * nothing after unmount.  The query key took over what `useLatestRequest`
 * guaranteed (an answer to an earlier question is never shown for a later
 * one) — the last case here.
 */
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClientProvider, QueryObserver, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient, invalidateReads, pollEvery, useFailureStreak, useLastSettled } from '../../lib/query';

const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  window.dispatchEvent(new Event('visibilitychange'));
};
const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

describe('pollEvery', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility('visible');
  });
  afterEach(() => {
    setVisibility('visible');
    vi.useRealTimers();
  });

  it('reads again at the interval while the tab is visible, and not after unmount', async () => {
    const read = vi.fn().mockResolvedValue('rows');
    const { unmount } = renderHook(() => useQuery({ queryKey: ['poll'], queryFn: read, ...pollEvery(1000) }));
    await pass(0);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(999);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1);
    expect(read).toHaveBeenCalledTimes(2);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(3);
    unmount();
    await pass(10_000);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it('does not poll for null, 0 or a negative interval', async () => {
    expect(pollEvery(null)).toEqual({});
    expect(pollEvery(undefined)).toEqual({});
    expect(pollEvery(0)).toEqual({});
    const read = vi.fn().mockResolvedValue('rows');
    renderHook(() => useQuery({ queryKey: ['poll'], queryFn: read, ...pollEvery(null) }));
    await pass(60_000);
    expect(read).toHaveBeenCalledTimes(1);
  });

  it('does not read in a hidden tab, and reads once at once on return', async () => {
    const read = vi.fn().mockResolvedValue('rows');
    renderHook(() => useQuery({ queryKey: ['poll'], queryFn: read, ...pollEvery(1000) }));
    await pass(0);
    expect(read).toHaveBeenCalledTimes(1);

    setVisibility('hidden');
    await pass(5000);
    expect(read).toHaveBeenCalledTimes(1);

    setVisibility('visible');
    await pass(0);
    expect(read).toHaveBeenCalledTimes(2);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it('never has two reads of the same thing in flight: a tick during a slow read starts nothing', async () => {
    let inFlight = 0;
    let most = 0;
    const read = vi.fn(() => {
      inFlight += 1;
      most = Math.max(most, inFlight);
      return new Promise<string>((resolve) => { setTimeout(() => { inFlight -= 1; resolve('rows'); }, 2500); });
    });
    renderHook(() => useQuery({ queryKey: ['poll'], queryFn: read, ...pollEvery(1000) }));
    // The first read takes 2.5 s; the interval's ticks at 1 s and 2 s join it.
    await pass(2400);
    expect(read).toHaveBeenCalledTimes(1);
    // Returning to the tab during it starts nothing either.
    setVisibility('hidden');
    setVisibility('visible');
    await pass(0);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(5000);
    expect(read.mock.calls.length).toBeGreaterThan(1);
    expect(most).toBe(1);
  });

  it('reads half as often while the server is failing, and at the interval again once it answers', async () => {
    const read = vi.fn().mockRejectedValue(new Error('503'));
    renderHook(() => useQuery({ queryKey: ['poll'], queryFn: read, ...pollEvery(1000) }));
    await pass(0);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(2);

    read.mockResolvedValue('rows');
    await pass(2000);
    expect(read).toHaveBeenCalledTimes(3);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(4);
  });

  it('takes the interval from the query: it stops when the function returns null', async () => {
    const read = vi.fn()
      .mockResolvedValueOnce('running')
      .mockResolvedValueOnce('running')
      .mockResolvedValue('done');
    const interval = vi.fn((query: { state: { data?: string } }) => (query.state.data === 'running' ? 1000 : null));
    renderHook(() => useQuery({ queryKey: ['job'], queryFn: read, ...pollEvery(interval) }));
    await pass(0);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(2);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(3);
    // 'done': nothing polls any more.
    await pass(60_000);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it('backs off the function form too while failing', async () => {
    const read = vi.fn().mockResolvedValueOnce('running').mockRejectedValue(new Error('503'));
    renderHook(() => useQuery({
      queryKey: ['job'], queryFn: read,
      ...pollEvery((query) => (query.state.data === 'running' ? 1000 : null)),
    }));
    await pass(0);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(2); // rejected: the answer on screen is still 'running'
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(2);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(3);
  });
});

describe('invalidateReads', () => {
  it('re-reads by API-function name — whatever the arguments, the project among them — and nothing else', async () => {
    const client = createQueryClient();
    const listA = vi.fn().mockResolvedValue(['a']);
    const listB = vi.fn().mockResolvedValue(['b']);
    const projects = vi.fn().mockResolvedValue([]);
    const other = vi.fn().mockResolvedValue([]);
    const otherGlobal = vi.fn().mockResolvedValue([]);
    const stop = [
      new QueryObserver(client, { queryKey: ['listThings', 1, { status: 'open' }], queryFn: listA }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: ['listThings', 2, { status: 'closed' }], queryFn: listB }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: ['getProjects'], queryFn: projects }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: ['getThing', 1, 7], queryFn: other }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: ['getUsers'], queryFn: otherGlobal }).subscribe(() => {}),
    ];
    // Every first read has answered (a read still in flight would be joined, not repeated).
    await waitFor(() => expect(client.isFetching()).toBe(0));
    for (const fn of [listA, listB, projects, other, otherGlobal]) expect(fn).toHaveBeenCalledTimes(1);

    await invalidateReads(client, 'listThings', 'getProjects');
    expect(listA).toHaveBeenCalledTimes(2);
    expect(listB).toHaveBeenCalledTimes(2);
    expect(projects).toHaveBeenCalledTimes(2);
    expect(other).toHaveBeenCalledTimes(1);
    expect(otherGlobal).toHaveBeenCalledTimes(1);
    stop.forEach((unsubscribe) => unsubscribe());
  });
});

// 5.353.0 — the project is an ARGUMENT, so it is in the key.  These cases were
// first guaranteed by a hidden partition of the cache (a scope mixed into the
// key's hash) and a client proxy that dropped late writes; three reviews found
// holes in that.  With the project in the key they hold by construction — the
// tests stay, to say so.
describe('the project is in the key', () => {
  // Kept, so that a second ask of the same thing would be answered from the cache.
  const kept = { staleTime: Infinity, gcTime: Infinity };

  it('the same read for two projects is two entries: one project\'s rows never answer another\'s question', async () => {
    const client = createQueryClient();
    const read = vi.fn(async (project: number) => `rows of project ${project}`);
    const ask = (project: number) => client.fetchQuery({ queryKey: ['listThings', project], queryFn: () => read(project), ...kept });
    expect(await ask(1)).toBe('rows of project 1');
    expect(await ask(1)).toBe('rows of project 1');
    expect(read).toHaveBeenCalledTimes(1);

    expect(client.getQueryData(['listThings', 2])).toBeUndefined();
    expect(await ask(2)).toBe('rows of project 2');
    expect(read).toHaveBeenCalledTimes(2);
    // The first project's rows are still its own.
    expect(client.getQueryData(['listThings', 1])).toBe('rows of project 1');
  });

  it('a bulk read or write under one project\'s key reaches only that project\'s entries', async () => {
    const client = createQueryClient();
    await client.fetchQuery({ queryKey: ['listJobs', 1, 'open'], queryFn: async () => ['job of 1'], ...kept });
    await client.fetchQuery({ queryKey: ['listJobs', 2, 'open'], queryFn: async () => ['job of 2'], ...kept });

    expect(client.getQueriesData({ queryKey: ['listJobs', 2] })).toEqual([[['listJobs', 2, 'open'], ['job of 2']]]);
    const seen = vi.fn((prev: string[] | undefined) => [...(prev ?? []), 'added']);
    client.setQueriesData<string[]>({ queryKey: ['listJobs', 2] }, seen);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(['listJobs', 2, 'open'])).toEqual(['job of 2', 'added']);
    expect(client.getQueryData(['listJobs', 1, 'open'])).toEqual(['job of 1']);
  });

  it('a save started in one project and answered in another writes its OWN project\'s list, not the one on screen (the reviewed case)', async () => {
    const client = createQueryClient();
    const answer: { resolve: (job: string) => void } = { resolve: () => undefined };
    const enqueue = () => new Promise<string>((resolve) => { answer.resolve = resolve; });
    const answered = vi.fn();
    const Dialog = ({ project }: { project: number }) => {
      const queryClient = useQueryClient();
      const jobs = useQuery({ queryKey: ['listJobs', project], queryFn: async () => [`job of ${project}`], ...kept });
      const queue = useMutation({
        mutationFn: enqueue,
        onSuccess: (job) => {
          // `project` is the one this dialog rendered with — the save's own.
          queryClient.setQueryData<string[]>(['listJobs', project], (prev) => [job, ...(prev ?? [])]);
          answered();
        },
      });
      return (
        <div>
          <button type="button" onClick={() => queue.mutate()}>queue</button>
          <ul>{(jobs.data ?? []).map((j) => <li key={j}>{j}</li>)}</ul>
        </div>
      );
    };
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { rerender } = render(<Dialog key={1} project={1} />, { wrapper });
    expect(await screen.findByText('job of 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'queue' }));

    rerender(<Dialog key={2} project={2} />);              // the reader switches project
    expect(await screen.findByText('job of 2')).toBeInTheDocument();
    await act(async () => { answer.resolve('queued in project 1'); });
    // The save's completion has run (it runs although its dialog is gone)…
    await waitFor(() => expect(answered).toHaveBeenCalledTimes(1));

    // …wrote nothing into the project now on screen…
    expect(screen.queryByText('queued in project 1')).toBeNull();
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual(['job of 2']);
    // …and is in the list of the project it was made in.
    expect(client.getQueryData(['listJobs', 1])).toEqual(['queued in project 1', 'job of 1']);
  });
});

// What seven pages each kept in a ref of their own (Hosts' rows and facets,
// Oversight's figures, the scope under a search…): the last answer stays on
// screen while another key loads or fails.
describe('useLastSettled — the last answer this component was given', () => {
  /** A read per filter, answered by hand. */
  const reader = () => {
    const answers: Record<string, { resolve: (value: string) => void; reject: (error: unknown) => void }> = {};
    const read = (filter: string) => new Promise<string>((resolve, reject) => { answers[filter] = { resolve, reject }; });
    return { answers, read };
  };

  it('stays while another key loads, and when that read fails; the next answer replaces it', async () => {
    const { answers, read } = reader();
    const { result, rerender } = renderHook(
      ({ filter }: { filter: string }) => {
        const query = useQuery({ queryKey: ['listThings', filter], queryFn: () => read(filter) });
        // Read here: a query tells its component only of what it reads.
        return { query: { data: query.data, isError: query.isError }, shown: useLastSettled(query.data) };
      },
      { initialProps: { filter: 'a' } },
    );
    // Nothing was ever given: nothing is made up.
    expect(result.current.shown).toBeUndefined();
    await waitFor(() => expect(answers.a).toBeDefined());
    await act(async () => { answers.a.resolve('rows for a'); });
    await waitFor(() => expect(result.current.shown).toBe('rows for a'));

    rerender({ filter: 'b' });
    // The query itself has nothing for "b" — the page still has what it showed.
    expect(result.current.query.data).toBeUndefined();
    expect(result.current.shown).toBe('rows for a');
    await waitFor(() => expect(answers.b).toBeDefined());
    await act(async () => { answers.b.reject(new Error('down')); });
    await waitFor(() => expect(result.current.query.isError).toBe(true));
    expect(result.current.shown).toBe('rows for a');
    // …and the page can tell it is not the answer to what is asked now.
    expect(result.current.shown).not.toBe(result.current.query.data);

    rerender({ filter: 'c' });
    await waitFor(() => expect(answers.c).toBeDefined());
    await act(async () => { answers.c.resolve('rows for c'); });
    await waitFor(() => expect(result.current.shown).toBe('rows for c'));
  });

  it('treats null like undefined — "nothing yet" — and keeps a falsy answer', () => {
    const { result, rerender } = renderHook(
      ({ data }: { data: number | null | undefined }) => useLastSettled(data),
      { initialProps: { data: 0 as number | null | undefined } },
    );
    expect(result.current).toBe(0);
    rerender({ data: null });
    expect(result.current).toBe(0);
    rerender({ data: undefined });
    expect(result.current).toBe(0);
  });

  it('is the component\'s own: a remount starts with nothing', () => {
    const first = renderHook(
      ({ data }: { data?: string }) => useLastSettled(data),
      { initialProps: { data: 'rows' as string | undefined } },
    );
    first.rerender({ data: undefined });
    expect(first.result.current).toBe('rows');
    first.unmount();
    const second = renderHook(({ data }: { data?: string }) => useLastSettled(data), { initialProps: {} });
    expect(second.result.current).toBeUndefined();
  });

  it('forgets with the project when the project is its resetKey: a component that survives a switch shows nothing of the other project', () => {
    const { result, rerender } = renderHook(
      ({ data, project }: { data?: string; project: number }) => useLastSettled(data, { resetKey: project }),
      { initialProps: { data: 'rows of project 1' as string | undefined, project: 1 } },
    );
    expect(result.current).toBe('rows of project 1');

    // The reader switches project; the new project's read has not answered.
    rerender({ data: undefined, project: 2 });
    expect(result.current).toBeUndefined();
    rerender({ data: 'rows of project 2', project: 2 });
    expect(result.current).toBe('rows of project 2');

    // Back on the first project nothing of the second is shown, and nothing
    // old is brought back either: the query is asked.
    rerender({ data: undefined, project: 1 });
    expect(result.current).toBeUndefined();
  });

  it('resetKey: forgotten when the record changes under a component that stays mounted', () => {
    const { result, rerender } = renderHook(
      ({ data, host }: { data?: string; host: number }) => useLastSettled(data, { resetKey: host }),
      { initialProps: { data: 'entries of host 1' as string | undefined, host: 1 } },
    );
    // Same record, another page of it: kept.
    rerender({ data: undefined, host: 1 });
    expect(result.current).toBe('entries of host 1');
    rerender({ data: undefined, host: 2 });
    expect(result.current).toBeUndefined();
  });
});

describe('a failed read is logged in one place (the app client)', () => {
  const run = async (client: ReturnType<typeof createQueryClient>, queryFn: () => Promise<unknown>) => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => useQuery({ queryKey: ['listThings', 7, { search: 'dc01.corp.example' }], queryFn }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isFetching).toBe(false));
    return result;
  };

  it('says which read and with what status — not its arguments; again when a re-read fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    try {
      const down = Object.assign(new Error('Request failed with status code 503'), { response: { status: 503 } });
      const result = await run(createQueryClient({ logFailures: true }), () => Promise.reject(down));
      expect(warn).toHaveBeenCalledTimes(1);
      const [line, details] = warn.mock.calls[0];
      expect(line).toContain('[READ] listThings failed');
      expect(details).toEqual({ status: 503, message: 'Request failed with status code 503' });
      expect(JSON.stringify(warn.mock.calls)).not.toContain('dc01.corp.example');

      await act(async () => { await result.current.refetch(); });
      await waitFor(() => expect(warn).toHaveBeenCalledTimes(2));
    } finally {
      warn.mockRestore();
    }
  });

  it('a cancelled request is not a failure, and a client made without the option logs nothing', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    try {
      const cancelled = Object.assign(new Error('canceled'), { name: 'CanceledError', code: 'ERR_CANCELED' });
      await run(createQueryClient({ logFailures: true }), () => Promise.reject(cancelled));
      await run(createQueryClient(), () => Promise.reject(new Error('down')));
      expect(warn).not.toHaveBeenCalled();
    } finally {
      warn.mockRestore();
    }
  });
});

describe('useFailureStreak — failures in a row since the last answer', () => {
  it('counts each failed re-read, and starts again at the next answer', async () => {
    let fail = false;
    const read = vi.fn(() => (fail ? Promise.reject(new Error('down')) : Promise.resolve('3 unread')));
    const { result } = renderHook(() => {
      const query = useQuery({ queryKey: ['getCount'], queryFn: read });
      return { streak: useFailureStreak(query), data: query.data, refetch: query.refetch };
    });
    await waitFor(() => expect(result.current.data).toBe('3 unread'));
    expect(result.current.streak).toBe(0);

    fail = true;
    for (const expected of [1, 2, 3]) {
      await act(async () => { await result.current.refetch(); });
      // (The component hears of it a tick after the read settles.)
      await waitFor(() => expect(result.current.streak).toBe(expected));
      // The last answer stays: it is what "may be out of date".
      expect(result.current.data).toBe('3 unread');
    }

    fail = false;
    await act(async () => { await result.current.refetch(); });
    await waitFor(() => expect(result.current.streak).toBe(0));

    // One blip after an answer is one, not four.
    fail = true;
    await act(async () => { await result.current.refetch(); });
    await waitFor(() => expect(result.current.streak).toBe(1));
    expect(read).toHaveBeenCalledTimes(6);
  });
});

describe('an answer belongs to its question (what useLatestRequest guaranteed)', () => {
  it('a slow answer to an earlier key is never shown for a later one', async () => {
    const answers: Record<string, (value: string) => void> = {};
    const read = vi.fn((filter: string) => new Promise<string>((resolve) => { answers[filter] = resolve; }));
    const { result, rerender } = renderHook(
      ({ filter }: { filter: string }) => useQuery({ queryKey: ['listThings', filter], queryFn: () => read(filter) }),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(answers.a).toBeDefined());
    rerender({ filter: 'b' });
    await waitFor(() => expect(answers.b).toBeDefined());
    await act(async () => { answers.b('rows for b'); });
    await waitFor(() => expect(result.current.data).toBe('rows for b'));
    // The first question's answer arrives late: it is not the page's.
    await act(async () => { answers.a('rows for a'); });
    expect(result.current.data).toBe('rows for b');
    expect(result.current.isError).toBe(false);
  });
});
