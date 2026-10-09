/**
 * The lint rule behind "server state is TanStack Query" (UI_STYLE_GUIDE §48):
 * an API function is called only inside a queryFn or a mutationFn.  It is the
 * door that keeps the old way of fetching from coming back, so it is tested:
 * what it must refuse, and what it must let through.
 */
import { Linter } from 'eslint';
import tseslint from 'typescript-eslint';
import { describe, expect, it } from 'vitest';

import rule from '../../../eslint-rules/api-in-query-only.mjs';

const lint = (code: string, allow: string[] = []) => new Linter({ configType: 'flat' }).verify(code, [{
  files: ['**/*.tsx'],
  languageOptions: {
    parser: tseslint.parser as never,
    parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
  },
  plugins: { bluestick: { rules: { 'api-in-query-only': rule as never } } },
  rules: { 'bluestick/api-in-query-only': ['error', { allow }] },
}], { filename: 'Page.tsx' }).map((m) => m.message.split("'")[1]);

const IMPORT = "import api, { getThing, saveThing, thingHref } from '../services/api';\n";

describe('bluestick/api-in-query-only', () => {
  it('refuses a fetch in an effect, in a handler and in a hand-made load()', () => {
    expect(lint(`${IMPORT}
      useEffect(() => { getThing(1).then(setThing); }, []);
      const onClick = async () => { await saveThing(1, body); };
      const load = () => api.get('/things');
      useEffect(load, []);
    `)).toEqual(['getThing', 'saveThing', 'api.get']);
  });

  it('accepts a call inside a queryFn or a mutationFn, however deep', () => {
    expect(lint(`${IMPORT}
      useQuery({ queryKey: ['getThing', 1], queryFn: ({ signal }) => getThing(1, signal) });
      useQueries({ queries: ids.map((id) => ({ queryKey: ['getThing', id], queryFn: () => getThing(id) })) });
      useMutation({ mutationFn: async (body) => { const saved = await saveThing(1, body); return (await api.get('/x')).data ?? saved; } });
    `)).toEqual([]);
  });

  it('accepts the list helpers\' fetcher and a function handed on as a …Fn', () => {
    expect(lint(`${IMPORT}
      useListQuery('getThing', ({ offset }) => getThing(offset), []);
      usePagedList('getThing', ({ offset }) => getThing(offset), []);
      const el = <Upload uploadFn={(file) => saveThing(1, file)} />;
      useHostWrite(1, { mutationFn: (v) => saveThing(1, v), failure: 'no' });
    `)).toEqual([]);
  });

  it('accepts a named helper only when every use of it is in the right place', () => {
    expect(lint(`${IMPORT}
      const putThing = async (body) => (await api.put('/things', body)).data;
      useMutation({ mutationFn: putThing });
      useMutation({ mutationFn: (v) => putThing({ ...v, on: true }) });
    `)).toEqual([]);
    expect(lint(`${IMPORT}
      const putThing = async (body) => (await api.put('/things', body)).data;
      useMutation({ mutationFn: putThing });
      const onClick = () => putThing(body);
    `)).toEqual(['api.put']);
    // A helper nobody uses in a query is still a bare call.
    expect(lint(`${IMPORT} function load() { return getThing(1); }`)).toEqual(['getThing']);
  });

  it('lets through what makes no request: an allowed name, a type import, another module', () => {
    expect(lint(`${IMPORT} const href = thingHref(1);`, ['thingHref'])).toEqual([]);
    expect(lint(`${IMPORT} const href = thingHref(1);`)).toEqual(['thingHref']);
    expect(lint(`import type { Thing } from '../services/api';
      import { getThing } from '../utils/things';
      getThing(1);`)).toEqual([]);
  });
});
