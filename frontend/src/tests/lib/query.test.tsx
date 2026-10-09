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
import { QueryObserver, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  GLOBAL, ScopedQueryClient, createQueryClient, getQueryScope, invalidateReads, pollEvery, scopedClient,
  setQueryScope,
} from '../../lib/query';

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
  it('re-reads by API-function name — the project\'s reads and the GLOBAL ones — and nothing else', async () => {
    const client = createQueryClient();
    const listA = vi.fn().mockResolvedValue(['a']);
    const listB = vi.fn().mockResolvedValue(['b']);
    const projects = vi.fn().mockResolvedValue([]);
    const other = vi.fn().mockResolvedValue([]);
    const otherGlobal = vi.fn().mockResolvedValue([]);
    const stop = [
      new QueryObserver(client, { queryKey: ['listThings', { status: 'open' }], queryFn: listA }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: ['listThings', { status: 'closed' }], queryFn: listB }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: [GLOBAL, 'getProjects'], queryFn: projects }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: ['getThing', 7], queryFn: other }).subscribe(() => {}),
      new QueryObserver(client, { queryKey: [GLOBAL, 'getUsers'], queryFn: otherGlobal }).subscribe(() => {}),
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

describe('the cache scope (setQueryScope)', () => {
  const before = getQueryScope();
  afterEach(() => setQueryScope(before));
  // Kept, so that a second ask of the same thing would be answered from the cache.
  const kept = { staleTime: Infinity, gcTime: Infinity };

  it('the same key under two projects is two entries: one project\'s rows never answer another\'s question', async () => {
    const client = createQueryClient();
    const read = vi.fn(async () => `rows of project ${getQueryScope().projectId}`);
    setQueryScope({ userId: 1, projectId: 1 });
    expect(await client.fetchQuery({ queryKey: ['listThings'], queryFn: read, ...kept })).toBe('rows of project 1');
    expect(await client.fetchQuery({ queryKey: ['listThings'], queryFn: read, ...kept })).toBe('rows of project 1');
    expect(read).toHaveBeenCalledTimes(1);

    setQueryScope({ userId: 1, projectId: 2 });
    expect(client.getQueryData(['listThings'])).toBeUndefined();
    expect(await client.fetchQuery({ queryKey: ['listThings'], queryFn: read, ...kept })).toBe('rows of project 2');
    expect(read).toHaveBeenCalledTimes(2);

    // Back on the first project its own rows are still its own.
    setQueryScope({ userId: 1, projectId: 1 });
    expect(client.getQueryData(['listThings'])).toBe('rows of project 1');
  });

  it('a GLOBAL key is shared across projects, and not across users', async () => {
    const client = createQueryClient();
    const read = vi.fn(async () => `projects of user ${getQueryScope().userId}`);
    setQueryScope({ userId: 1, projectId: 1 });
    await client.fetchQuery({ queryKey: [GLOBAL, 'getProjects'], queryFn: read, ...kept });
    setQueryScope({ userId: 1, projectId: 2 });
    expect(client.getQueryData([GLOBAL, 'getProjects'])).toBe('projects of user 1');
    await client.fetchQuery({ queryKey: [GLOBAL, 'getProjects'], queryFn: read, ...kept });
    expect(read).toHaveBeenCalledTimes(1);

    setQueryScope({ userId: 2, projectId: 2 });
    expect(client.getQueryData([GLOBAL, 'getProjects'])).toBeUndefined();
    expect(await client.fetchQuery({ queryKey: [GLOBAL, 'getProjects'], queryFn: read, ...kept }))
      .toBe('projects of user 2');
    expect(read).toHaveBeenCalledTimes(2);
  });
});

// Code review 2026-10-09: the scope is read when a key is hashed, so a write
// that completes AFTER a project switch would file one project's row under
// the other's key.  A component's client remembers the scope it was made under.
describe('a late write keeps the identity it started with (scopedClient)', () => {
  const before = getQueryScope();
  afterEach(() => setQueryScope(before));
  const kept = { staleTime: Infinity, gcTime: Infinity };

  it('drops a data write made under another project, and answers "nothing" to a read of its cache', async () => {
    const client = createQueryClient();
    setQueryScope({ userId: 1, projectId: 1 });
    const inProjectOne = scopedClient(client);
    await client.fetchQuery({ queryKey: ['listJobs'], queryFn: async () => ['job of 1'], ...kept });

    setQueryScope({ userId: 1, projectId: 2 });
    await client.fetchQuery({ queryKey: ['listJobs'], queryFn: async () => ['job of 2'], ...kept });
    // Project 1's save answers now.
    inProjectOne.setQueryData<string[]>(['listJobs'], (prev) => ['late job of 1', ...(prev ?? [])]);
    inProjectOne.setQueriesData<string[]>({ queryKey: ['listJobs'] }, () => ['late job of 1']);
    expect(client.getQueryData(['listJobs'])).toEqual(['job of 2']);
    expect(inProjectOne.getQueryData(['listJobs'])).toBeUndefined();
    expect(inProjectOne.getQueriesData({ queryKey: ['listJobs'] })).toEqual([]);

    // Its own project is untouched, and a view made under project 2 writes as usual.
    scopedClient(client).setQueryData(['listJobs'], ['job of 2', 'another']);
    expect(client.getQueryData(['listJobs'])).toEqual(['job of 2', 'another']);
    setQueryScope({ userId: 1, projectId: 1 });
    expect(client.getQueryData(['listJobs'])).toEqual(['job of 1']);
  });

  it('still writes what is not one project\'s after a project switch — but not after another user signs in', async () => {
    const client = createQueryClient();
    setQueryScope({ userId: 1, projectId: 1 });
    const view = scopedClient(client);
    await client.fetchQuery({ queryKey: [GLOBAL, 'getProjects'], queryFn: async () => ['a'], ...kept });

    setQueryScope({ userId: 1, projectId: 2 });
    view.setQueryData([GLOBAL, 'getProjects'], ['a', 'renamed']);
    expect(client.getQueryData([GLOBAL, 'getProjects'])).toEqual(['a', 'renamed']);

    setQueryScope({ userId: 2, projectId: 2 });
    await client.fetchQuery({ queryKey: [GLOBAL, 'getProjects'], queryFn: async () => ['theirs'], ...kept });
    view.setQueryData([GLOBAL, 'getProjects'], ['user 1 again']);
    expect(client.getQueryData([GLOBAL, 'getProjects'])).toEqual(['theirs']);
  });

  it('is the client for everything else: reads, invalidation and the library\'s own hooks go through', async () => {
    const client = createQueryClient();
    setQueryScope({ userId: 1, projectId: 1 });
    const view = scopedClient(client);
    const read = vi.fn(async () => 'rows');
    expect(await view.fetchQuery({ queryKey: ['listThings'], queryFn: read, ...kept })).toBe('rows');
    expect(view.getQueryData(['listThings'])).toBe('rows');
    expect(view.getQueryCache()).toBe(client.getQueryCache());
    await invalidateReads(view, 'listThings');
    expect(client.getQueryState(['listThings'])?.isInvalidated).toBe(true);
  });

  it('a save started in one project and answered in another leaves the other project\'s list alone (the reviewed case)', async () => {
    const answer: { resolve: (job: string) => void } = { resolve: () => undefined };
    const enqueue = () => new Promise<string>((resolve) => { answer.resolve = resolve; });
    const answered = vi.fn();
    const Dialog = () => {
      const queryClient = useQueryClient();
      const jobs = useQuery({ queryKey: ['listJobs'], queryFn: async () => [`job of ${getQueryScope().projectId}`], ...kept });
      const queue = useMutation({
        mutationFn: enqueue,
        onSuccess: (job) => {
          queryClient.setQueryData<string[]>(['listJobs'], (prev) => [job, ...(prev ?? [])]);
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
    const App = ({ project }: { project: number }) => {
      setQueryScope({ userId: 1, projectId: project });   // as ProjectProvider does, while rendering
      return <ScopedQueryClient><Dialog key={project} /></ScopedQueryClient>;
    };
    const { rerender } = render(<App project={1} />);
    expect(await screen.findByText('job of 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'queue' }));

    rerender(<App project={2} />);                         // the reader switches project
    expect(await screen.findByText('job of 2')).toBeInTheDocument();
    await act(async () => { answer.resolve('queued in project 1'); });
    // The save's completion has run (it runs although its dialog is gone)…
    await waitFor(() => expect(answered).toHaveBeenCalledTimes(1));

    // …and wrote nothing into the project now on screen.
    expect(screen.queryByText('queued in project 1')).toBeNull();
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual(['job of 2']);
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
