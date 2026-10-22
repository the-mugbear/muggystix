/**
 * The lint rule behind "a queryFn passes the query's signal" (frontend.md,
 * "Fetching lists and details"): a read the query has given up on is
 * cancelled only when the API function was handed the query's signal.  What
 * it must refuse, and what it must let through.
 */
import { Linter } from 'eslint';
import tseslint from 'typescript-eslint';
import { describe, expect, it } from 'vitest';

import rule from '../../../eslint-rules/query-fn-signal.mjs';

const lint = (code: string, options: { allow?: string[]; noSignal?: string[] } = {}) =>
  new Linter({ configType: 'flat' }).verify(code, [{
    files: ['**/*.tsx'],
    languageOptions: {
      parser: tseslint.parser as never,
      parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
    },
    plugins: { bluestick: { rules: { 'query-fn-signal': rule as never } } },
    rules: { 'bluestick/query-fn-signal': ['error', options] },
  }], { filename: 'Page.tsx' }).map((m) => m.message.split("'")[1]);

const IMPORT = "import api, { getThing, getThings, download, thingHref } from '../services/api';\n";

describe('bluestick/query-fn-signal', () => {
  it('refuses a queryFn that drops the signal, or never takes it', () => {
    expect(lint(`${IMPORT}
      useQuery({ queryKey: ['getThing', 1], queryFn: () => getThing(1) });
      useQuery({ queryKey: ['getThing', 2], queryFn: ({ signal }) => getThing(2) });
      useQuery({ queryKey: ['x'], queryFn: async () => (await api.get('/x')).data });
      useQueries({ queries: ids.map((id) => ({ queryKey: ['getThing', id], queryFn: () => getThing(id) })) });
      queryClient.fetchQuery({ queryKey: ['getThing', 3], queryFn: (ctx) => getThing(3) });
    `)).toEqual(['getThing', 'getThing', 'api.get', 'getThing', 'getThing']);
  });

  it('refuses a list fetcher that drops the signal it is given', () => {
    expect(lint(`${IMPORT}
      useListQuery('getThings', ({ offset, limit }) => getThings({ offset, limit }), []);
      usePagedList('getThings', ({ offset }) => getThings(offset), []);
    `)).toEqual(['getThings', 'getThings']);
  });

  it('accepts the signal however it is handed on', () => {
    expect(lint(`${IMPORT}
      useQuery({ queryKey: ['getThing', 1], queryFn: ({ signal }) => getThing(1, signal) });
      useQuery({ queryKey: ['getThing', 2], queryFn: ({ signal: s }) => getThing(2, { signal: s }) });
      useQuery({ queryKey: ['getThing', 3], queryFn: (ctx) => getThing(3, ctx.signal) });
      useQuery({ queryKey: ['getThing', 4], queryFn: ({ signal }) => getThing(4, wide ? { all: true, signal } : { signal }) });
      useQuery({ queryKey: ['x'], queryFn: async ({ signal }) => (await api.get('/x', { signal })).data });
      useQuery({ queryKey: ['getThings'], queryFn: ({ signal }) => Promise.all(ids.map((id) => getThing(id, signal))) });
      useListQuery('getThings', ({ offset, limit, signal }) => getThings({ offset, limit }, signal), []);
      usePagedList('getThings', async (req) => getThings(1, req), []);
      useJobPoll({ queryKey: ['getThing', 5], job, queryFn: (job, signal) => getThing(job.id, signal) });
    `)).toEqual([]);
  });

  it('does not hold a job poll\'s job for the signal', () => {
    expect(lint(`${IMPORT}
      useJobPoll({ queryKey: ['getThing', 5], job, queryFn: (job, signal) => getThing(job) });
    `)).toEqual(['getThing']);
  });

  it('judges only queryFns: a mutation, an allowed name and a no-signal request pass', () => {
    expect(lint(`${IMPORT}
      useMutation({ mutationFn: () => getThing(1) });
      useQuery({ queryKey: ['a'], queryFn: ({ signal }) => getThing(thingHref(1), signal) });
      useQuery({ queryKey: ['b'], queryFn: () => download(1) });
    `, { allow: ['thingHref'], noSignal: ['download'] })).toEqual([]);
  });

  it('leaves a function that is not imported from the API barrel alone', () => {
    expect(lint(`import { getThing } from '../utils/things';
      useQuery({ queryKey: ['getThing', 1], queryFn: () => getThing(1) });
    `)).toEqual([]);
  });
});
