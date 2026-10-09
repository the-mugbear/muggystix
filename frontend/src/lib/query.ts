/**
 * Server state — the ONE way the app reads from and writes to the API
 * (5.351.0).  Reads are `useQuery` / `useInfiniteQuery`, writes are
 * `useMutation`, both from `@tanstack/react-query`; an API function is called
 * only inside a `queryFn` or a `mutationFn` (a lint rule).  This module holds
 * what the whole app shares: the client and its defaults, the cache scope,
 * polling, and the wording of a failure.
 *
 * Conventions (UI_STYLE_GUIDE §48):
 *   - **Key = the API function's name, then its arguments**:
 *     `queryKey: ['listProposals', { status, kind }]`.  No registry of keys:
 *     the name is the identity, so after a write
 *     `queryClient.invalidateQueries({ queryKey: ['listProposals'] })`
 *     reaches every list of proposals on screen.
 *   - **The cache is partitioned by signed-in user and project** (`setQueryScope`),
 *     inside the key's hash: a key never names the project, and one project's
 *     rows cannot answer another's question.  The scope is read when a key is
 *     hashed, so a WRITE to the cache that completes late must still carry the
 *     identity it started with: components get the client through
 *     `scopedClient`, which drops a data write made under another scope.
 *   - **Nothing is kept once nothing shows it** (`gcTime: 0`): a page that is
 *     opened reads from the server, as it always did.  A query that should be
 *     remembered says so itself (`staleTime` + `gcTime`, e.g. project members).
 *   - **No automatic retry, no refetch on focus**: a failure is said, with
 *     Retry (`refetch`).  A list that must stay current polls (`pollEvery`).
 */
import { createElement, useMemo, type ReactNode } from 'react';
import {
  QueryClient, QueryClientProvider, hashKey, useQueryClient, type Query, type QueryKey,
} from '@tanstack/react-query';

import { formatApiError } from '../utils/apiErrors';

let scope = '';

/** First element of a key whose data is not one project's (the project list,
 *  the installation's settings, cross-project pages): it is partitioned by
 *  user only, so a project switch does not ask for it again.
 *  `queryKey: [GLOBAL, 'getProjects']`. */
export const GLOBAL = 'global';

/** Whose data the cache holds: the signed-in user and the current project.
 *  Called where either changes (AuthContext, ProjectContext). */
export function setQueryScope(next: { userId?: number | string | null; projectId?: number | string | null }): void {
  scope = `${next.userId ?? ''}:${next.projectId ?? ''}`;
}

/** The current scope — tests and the contexts read it to change one half. */
export function getQueryScope(): { userId: string; projectId: string } {
  const [userId = '', projectId = ''] = scope.split(':');
  return { userId, projectId };
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        queryKeyHashFn: (key: QueryKey) => hashKey([key[0] === GLOBAL ? scope.split(':')[0] : scope, ...key]),
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
export const queryClient = createQueryClient();

/** Does a key written under `madeUnder` still mean the same data now? */
function sameData(madeUnder: string, key: QueryKey | undefined): boolean {
  if (madeUnder === scope) return true;
  const [user] = madeUnder.split(':');
  const [userNow] = scope.split(':');
  // Another project: only what is not one project's is still the same data.
  return user === userNow && key?.[0] === GLOBAL;
}

/**
 * The client as a component under ONE user and project sees it.
 *
 * A key does not name its project: the scope is added when the key is hashed,
 * at the moment of the call.  A read is safe with that — it is made while its
 * component is on screen.  A WRITE may come later: a save started in project
 * A can answer after the reader has switched to B, and `setQueryData` would
 * then file A's row under B's key.  This view remembers the scope it was made
 * under and drops (or, for a read of the cache, answers "nothing" to) a data
 * call whose key no longer means the same data — so an async completion keeps
 * the identity it started with.  Everything else is the client itself.
 *
 * `ScopedQueryClient` (below) gives every component under the project
 * provider this view through the library's own `useQueryClient()`.
 */
export function scopedClient(client: QueryClient, madeUnder: string = scope): QueryClient {
  const guarded: Partial<Record<keyof QueryClient, (...args: any[]) => unknown>> = {
    setQueryData: (key: QueryKey, ...rest: unknown[]) => (
      sameData(madeUnder, key) ? (client.setQueryData as any)(key, ...rest) : undefined
    ),
    getQueryData: (key: QueryKey) => (sameData(madeUnder, key) ? client.getQueryData(key) : undefined),
    getQueryState: (key: QueryKey) => (sameData(madeUnder, key) ? client.getQueryState(key) : undefined),
    // By filter: only the entries that are still the same data.
    setQueriesData: (filters: any, ...rest: unknown[]) => (client.setQueriesData as any)(
      {
        ...filters,
        predicate: (query: Query) => sameData(madeUnder, query.queryKey) && (filters?.predicate?.(query) ?? true),
      },
      ...rest,
    ),
    getQueriesData: (filters: any) => client.getQueriesData({
      ...filters,
      predicate: (query: Query) => sameData(madeUnder, query.queryKey) && (filters?.predicate?.(query) ?? true),
    }),
  };
  return new Proxy(client, {
    get(target, prop) {
      const own = guarded[prop as keyof QueryClient];
      if (own) return own;
      const value = Reflect.get(target, prop, target);
      // The client keeps private fields: its methods must run on the client.
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
}

/** Gives the components below it the client as their user and project see
 *  it (`scopedClient`).  Mounted by `ProjectProvider`, which sets the scope
 *  while rendering; a new scope is a new view. */
export function ScopedQueryClient({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const under = scope;
  const view = useMemo(() => scopedClient(client, under), [client, under]);
  return createElement(QueryClientProvider, { client: view }, children);
}

/**
 * Options for a query that must stay current: re-read every `ms` while the
 * tab is visible, once at once when the tab becomes visible again, and half
 * as often while the server is failing.  `null` does not poll.
 *
 *   useQuery({ queryKey: ['getProposalSummary'], queryFn: …, ...pollEvery(60_000) })
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
 * date — the project's and the installation's (`GLOBAL`) alike, whatever
 * their arguments.  Those on screen are read again; the rest when next shown.
 *
 *   onSuccess: () => invalidateReads(queryClient, 'listProposals', 'getProposalSummary')
 *
 * (`queryClient.invalidateQueries({ queryKey: ['getProjects'] })` alone does
 * not reach `[GLOBAL, 'getProjects']`; this does.)
 */
export function invalidateReads(client: QueryClient, ...names: string[]): Promise<void> {
  const wanted = new Set(names);
  return client.invalidateQueries({
    predicate: (query) => {
      const [first, second] = query.queryKey;
      return wanted.has(first as string) || (first === GLOBAL && wanted.has(second as string));
    },
  });
}

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
