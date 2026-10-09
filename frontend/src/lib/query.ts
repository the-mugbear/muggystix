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
import { createElement, useMemo, useRef, type ReactNode } from 'react';
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

/** Is this cache ENTRY the data of `madeUnder`?  An entry does not carry its
 *  scope, but its hash was made with it: the entry is `madeUnder`'s when
 *  hashing its key under `madeUnder` gives that hash.  (A remembered entry of
 *  another project has the same key and another hash.) */
function entryOf(madeUnder: string, query: Query): boolean {
  const key = query.queryKey;
  return query.queryHash === hashKey([key[0] === GLOBAL ? madeUnder.split(':')[0] : madeUnder, ...key]);
}

/**
 * The project an operation of SEVERAL requests started in.
 *
 * An API function builds its address from the current project when it is
 * CALLED.  One request is therefore safe; a `mutationFn` that awaits one
 * request and then makes another is not — if the reader switches project in
 * between, the second goes to the other project (an export would stitch two
 * projects' pages into one file).  Such an operation takes this at its start
 * and calls it after each `await`, before it uses the answer or sends the
 * next request:
 *
 *   mutationFn: async () => {
 *     const stillHere = holdProject();
 *     const ids = await getMatchingHostIds(filters);
 *     stillHere();                       // throws ProjectChanged: nothing more is sent
 *     return bulkTagHosts(ids, body);
 *   }
 *
 * A `queryFn` does not need it when it passes its `signal`: its component
 * unmounts with the project and the read is cancelled.
 */
export class ProjectChanged extends Error {
  constructor() {
    super('The project was changed before this finished. Nothing more was sent.');
    this.name = 'ProjectChanged';
  }
}

export function holdProject(): () => void {
  const held = scope;
  return () => {
    if (scope !== held) throw new ProjectChanged();
  };
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
    // By filter: only the ENTRIES that are this view's data.  A filter matches
    // by key, and a remembered entry of another project has the same key —
    // so each entry is checked by its own hash, not by the scope of the call.
    setQueriesData: (filters: any, ...rest: unknown[]) => (client.setQueriesData as any)(
      {
        ...filters,
        predicate: (query: Query) => sameData(madeUnder, query.queryKey) && entryOf(madeUnder, query)
          && (filters?.predicate?.(query) ?? true),
      },
      ...rest,
    ),
    getQueriesData: (filters: any) => client.getQueriesData({
      ...filters,
      predicate: (query: Query) => sameData(madeUnder, query.queryKey) && entryOf(madeUnder, query)
        && (filters?.predicate?.(query) ?? true),
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
 *   const dashboard = useQuery({ queryKey: [GLOBAL, 'getOversightDashboard', query], … });
 *   const data = useLastSettled(dashboard.data, { global: true }) ?? null;
 *
 * Give it the query's `data` (or a value made from it): it returns that value
 * while there is one, and otherwise the last one it was given.  What it
 * returns may therefore belong to another key than the one being asked for —
 * the page decides what that means (dim it, make it inert, say that the read
 * failed).  `data === theQuery.data` tells the two apart.
 *
 * What is remembered is this component's and one identity's:
 *   - it is gone when the component unmounts (a page is remounted per project
 *     and per route, a record's panel per record);
 *   - it is forgotten when the cache scope changes — another project, another
 *     user — so a component that SURVIVES a project switch (the shell, a
 *     cross-project page) cannot show one project's answer under another's.
 *     `global: true` for data that is not one project's (a `GLOBAL` key):
 *     kept across projects, forgotten with the user;
 *   - `resetKey` forgets it when the value changes — for a component that
 *     stays mounted across records and is not keyed by them.
 */
export function useLastSettled<T>(
  data: T | null | undefined,
  { global: isGlobal = false, resetKey }: { global?: boolean; resetKey?: unknown } = {},
): T | undefined {
  const under = isGlobal ? scope.split(':')[0] : scope;
  const held = useRef<{ value: T; under: string; resetKey: unknown } | null>(null);
  if (held.current && (held.current.under !== under || !Object.is(held.current.resetKey, resetKey))) {
    held.current = null;
  }
  if (data != null) held.current = { value: data, under, resetKey };
  return held.current?.value;
}
