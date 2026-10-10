/**
 * Server state — the ONE way the app reads from and writes to the API
 * (5.351.0).  Reads are `useQuery` / `useInfiniteQuery`, writes are
 * `useMutation`, both from `@tanstack/react-query`; an API function is called
 * only inside a `queryFn` or a `mutationFn` (a lint rule).  This module holds
 * what the whole app shares: the client and its defaults, polling, and the
 * wording of a failure.
 *
 * Conventions (UI_STYLE_GUIDE §48):
 *   - **Key = the API function's name, then its arguments**:
 *     `queryKey: ['listProposals', projectId, { status, kind }]`.  No registry
 *     of keys: the name is the identity, so after a write
 *     `invalidateReads(queryClient, 'listProposals')` reaches every list of
 *     proposals on screen.
 *   - **The project is an argument** (5.353.0).  A project-scoped API function
 *     takes `projectId` first (`hooks/useProjectId`, read while rendering), so
 *     the key names it like any other argument and one project's rows cannot
 *     answer another's question — by the key itself, where it can be read.
 *     There is no hidden partition of the cache, no "current project" read
 *     when a request is built, and nothing that is not one project's needs a
 *     marker: `['getProjects']`.  (Before 5.353.0 the project was mixed into
 *     the key's hash from a module variable; three defects came from identity
 *     nobody could see at the call.)
 *   - **Another user's data is never in the cache**: it is cleared when the
 *     signed-in user changes (AuthContext).
 *   - **Nothing is kept once nothing shows it** (`gcTime: 0`): a page that is
 *     opened reads from the server, as it always did.  A query that should be
 *     remembered says so itself (`staleTime` + `gcTime`, e.g. project members).
 *   - **No automatic retry, no refetch on focus**: a failure is said, with
 *     Retry (`refetch`).  A list that must stay current polls (`pollEvery`).
 */
import { useRef } from 'react';
import { QueryCache, QueryClient, type Query } from '@tanstack/react-query';

import { formatApiError } from '../utils/apiErrors';
import logger from '../utils/logger';

/**
 * THE log line for a read that failed (owner decision 2026-10-10): which
 * read, and with what — one place, so no `queryFn` wraps its request in a
 * `try` to write its own.  The failure is said on screen by whoever shows the
 * read; this is for the browser console, when someone is asked to look.
 * Arguments are not logged (a filter can hold a host name), a cancelled
 * request is not a failure, and a re-read failing again is logged again.
 */
function logFailedRead(error: unknown, query: Query<unknown, unknown, unknown>): void {
  const failure = error as { name?: string; code?: string; message?: string; response?: { status?: number } } | null;
  if (failure?.name === 'CanceledError' || failure?.name === 'CancelledError' || failure?.code === 'ERR_CANCELED') return;
  logger.warn('READ', `${String(query.queryKey[0])} failed`, {
    status: failure?.response?.status ?? null,
    message: failure?.message ?? String(error),
  });
}

export function createQueryClient({ logFailures = false }: { logFailures?: boolean } = {}): QueryClient {
  return new QueryClient({
    // Off by default: a test's client would fill the run's output with the
    // failures its cases cause on purpose.
    queryCache: logFailures ? new QueryCache({ onError: logFailedRead }) : undefined,
    defaultOptions: {
      queries: {
        retry: false,
        // A failed read stays failed until the reader retries: a second
        // component that reads the same thing a moment later must not quietly
        // ask again and hide the failure the first one is showing.
        retryOnMount: false,
        staleTime: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        // BlueStick runs on isolated networks: a browser that believes it is
        // offline must still ask the server on the LAN.
        networkMode: 'always',
      },
      mutations: { retry: false, networkMode: 'always' },
    },
  });
}

/** The app's client.  Tests get a fresh one per render (setupTests). */
export const queryClient = createQueryClient({ logFailures: true });

/**
 * Options for a query that must stay current: re-read every `ms` while the
 * tab is visible, once at once when the tab becomes visible again, and half
 * as often while the server is failing.  `null` does not poll.
 *
 *   useQuery({ queryKey: ['getProposalSummary', projectId], queryFn: …, ...pollEvery(60_000) })
 */
export type PollInterval =
  | number | null | undefined
  /** From the query's own state — e.g. faster while something is live, `null` to stop when a job is finished. */
  | ((query: Query<any, any, any, any>) => number | null | undefined);

export function pollEvery(interval: PollInterval) {
  if (interval == null || (typeof interval === 'number' && interval <= 0)) return {};
  return {
    refetchInterval: (query: Query<any, any, any, any>) => {
      const ms = typeof interval === 'function' ? interval(query) : interval;
      if (ms == null || ms <= 0) return false as const;
      return query.state.status === 'error' ? ms * 2 : ms;
    },
    refetchIntervalInBackground: false,
    // Once on return to the tab — but only for a poll that is still running:
    // a job that finished is not asked about again.
    refetchOnWindowFocus: (query: Query<any, any, any, any>) => {
      const ms = typeof interval === 'function' ? interval(query) : interval;
      return ms != null && ms > 0;
    },
  } as const;
}

/**
 * After a write: every read made by one of these API functions is out of
 * date, whatever its arguments (the project among them).  Those on screen are
 * read again; the rest when next shown.
 *
 *   onSuccess: () => invalidateReads(queryClient, 'listProposals', 'getProposalSummary')
 */
export function invalidateReads(client: QueryClient, ...names: string[]): Promise<void> {
  const wanted = new Set(names);
  return client.invalidateQueries({ predicate: (query) => wanted.has(query.queryKey[0] as string) });
}

/**
 * Options for a mutation that CARRIES OR RETURNS A SECRET — a password, a
 * one-time code, a freshly minted key.  The library keeps a mutation's
 * variables and answer in its cache (five minutes by default) and on the
 * observer for as long as the component is mounted; a secret must be in
 * neither once it has been used.
 *
 *   const change = useMutation({ ...SECRET_MUTATION, mutationFn: … });
 *   change.mutate(body, { onSettled: () => change.reset() });   // nothing reads it afterwards
 *
 * `gcTime: 0` drops it from the cache the moment nothing observes it;
 * `reset()` clears the observer's copy.  Where the page SHOWS the answer once
 * (a new agent key), `reset()` goes where the reader dismisses it instead.
 * A secret never goes in a query: a query can be read again.
 */
export const SECRET_MUTATION = { gcTime: 0 } as const;

/** A failed query's or mutation's message, or null when there is none. */
export function queryErrorText(error: unknown, fallback: string): string | null {
  return error ? formatApiError(error, fallback) : null;
}

/** Keep a query's answer for `ms` after it was read, mounted or not — for the
 *  few things every page asks for (project members, the installation's
 *  settings). */
export function rememberFor(ms: number) {
  return { staleTime: ms, gcTime: ms } as const;
}

/**
 * The last answer this component was given — for what must stay on screen
 * while ANOTHER key loads, or fails: the figures under a filter that is
 * changing, a dropdown's options, the rows under a search box.
 *
 *   const dashboard = useQuery({ queryKey: ['getOversightDashboard', query], … });
 *   const data = useLastSettled(dashboard.data) ?? null;
 *
 * Give it the query's `data` (or a value made from it): it returns that value
 * while there is one, and otherwise the last one it was given.  What it
 * returns may therefore belong to another key than the one being asked for —
 * the page decides what that means (dim it, make it inert, say that the read
 * failed).  `data === theQuery.data` tells the two apart.
 *
 * What is remembered is this component's:
 *   - it is gone when the component unmounts (a page is remounted per project
 *     and per route, a record's panel per record);
 *   - `resetKey` forgets it when the value changes — the PROJECT, for one
 *     project's data (`{ resetKey: projectId }`: a component that survives a
 *     project switch must not show one project's answer under another's), or
 *     the record, for a component that stays mounted across records.
 */
export function useLastSettled<T>(
  data: T | null | undefined,
  { resetKey }: { resetKey?: unknown } = {},
): T | undefined {
  const held = useRef<{ value: T; resetKey: unknown } | null>(null);
  if (held.current && !Object.is(held.current.resetKey, resetKey)) held.current = null;
  if (data != null) held.current = { value: data, resetKey };
  return held.current?.value;
}

/**
 * How many times in a row a polled read has failed since this component last
 * saw it answer — for "the figure shown may be out of date" after several
 * failures, not after one blip.  0 again on the next answer.
 *
 *   const unread = useQuery({ …, ...pollEvery(60_000) });
 *   const stale = useFailureStreak(unread) >= 3;
 *
 * (The library's own `failureCount` starts again at every attempt, and this
 * app does not retry, so it never passes 1.)
 */
export function useFailureStreak(
  query: { dataUpdatedAt: number; errorUpdateCount: number },
): number {
  const { dataUpdatedAt, errorUpdateCount } = query;
  const answered = useRef({ dataUpdatedAt, errors: errorUpdateCount });
  if (answered.current.dataUpdatedAt !== dataUpdatedAt) answered.current = { dataUpdatedAt, errors: errorUpdateCount };
  return errorUpdateCount - answered.current.errors;
}
