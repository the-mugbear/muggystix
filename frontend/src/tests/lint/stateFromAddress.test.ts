/**
 * The lint entry behind "a filter has one owner, the address"
 * (UI_STYLE_GUIDE §39): state may not be seeded from the search params.  It
 * is what keeps the Back / same-page-link defect of 5.354.0 from returning,
 * so it is tested: what it must refuse, and what it must let through.
 */
import { Linter } from 'eslint';
import tseslint from 'typescript-eslint';
import { describe, expect, it } from 'vitest';

import { STATE_FROM_ADDRESS } from '../../../eslint-rules/state-from-address.mjs';

const lint = (code: string) => new Linter({ configType: 'flat' }).verify(code, [{
  files: ['**/*.tsx'],
  languageOptions: {
    parser: tseslint.parser as never,
    parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
  },
  rules: { 'no-restricted-syntax': ['error', STATE_FROM_ADDRESS] },
}], { filename: 'Page.tsx' }).map((m) => m.line);

describe('state seeded from the address', () => {
  it('refuses useState seeded from the params — directly, cast, defaulted, or from an initialiser', () => {
    expect(lint(`
      const [a] = useState(searchParams.get('search') ?? '');
      const [b] = useState<Filter>((searchParams.get('state') as Filter) || 'all');
      const [c] = useState(() => urlParams.get('tool') || '');
      const [d] = useState(() => {
        const raw = params.get('days');
        return raw ? parseInt(raw, 10) : null;
      });
      const [e] = useState(searchParams.getAll('tag'));
      const [f] = useState(urlParams.has('batch_files'));
    `)).toEqual([2, 3, 4, 6, 9, 10]);
  });

  it('lets through a filter read during render, the search-box hook, and state that has nothing to do with the address', () => {
    expect(lint(`
      const status = searchParams.get('status') ?? 'active';
      const page = Math.max(0, Number(searchParams.get('page')) || 0);
      const search = useUrlSearchDraft('search');
      const [open, setOpen] = useState(false);
      const [draft, setDraft] = useState(() => initialDraft(row));
      const [picked, setPicked] = useState(new Map().get('x'));
      const options = useMemo(() => build(searchParams.get('owner')), [searchParams]);
    `)).toEqual([]);
  });
});
