// ESLint — a small rule set, on purpose.  `tsc` (noUnusedLocals /
// noUnusedParameters) already covers what the usual recommended sets do for
// this codebase; these are the rules nothing else enforces:
//
//  - the Rules of Hooks (error) and effect dependencies (warning);
//  - components and pages import the API from the `services/api` barrel;
//  - one helper each for a file save and for an absolute moment.
//
// The gate runs `npm run lint -- --max-warnings 0`: a deliberate omission
// from an effect's dependencies carries a disable comment that says why.
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

import apiInQueryOnly from './eslint-rules/api-in-query-only.mjs';

// A file save goes through utils/download (`saveBlob`).
const DOWNLOAD_NAME = {
  selector: "AssignmentExpression[left.type='MemberExpression'][left.property.name='download']",
  message: 'Save a file with utils/download (saveBlob), not a hand-built link.',
};
// An absolute moment is `formatTimestamp`.  (A number's `toLocaleString()` is
// a different thing and is not matched.)
const BARE_MOMENT = {
  selector: "CallExpression[arguments.length=0][callee.property.name='toLocaleString']"
    + "[callee.object.type='NewExpression'][callee.object.callee.name='Date']",
  message: 'Format a moment with formatTimestamp (utils/relativeTime), not new Date(x).toLocaleString().',
};

export default [
  { ignores: ['build/**', 'dist/**', 'node_modules/**', 'coverage/**'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
    },
    // Registered so that the source's existing `eslint-disable` comments for
    // its rules resolve; none of its rules is switched on here.
    plugins: { 'react-hooks': reactHooks, '@typescript-eslint': tseslint.plugin },
    // Disable comments for rules this config does not run are left alone.
    linterOptions: { reportUnusedDisableDirectives: 'off' },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'no-restricted-syntax': ['error', DOWNLOAD_NAME, BARE_MOMENT],
    },
  },
  {
    // Tests mock the `services/api` barrel.  An import of one of its
    // submodules bypasses the mock and loads the real client, which cannot
    // start in jsdom.  Only the barrel (and the client's own tests) reach in.
    // A type-only import is erased and loads nothing: the client-free helpers
    // in utils/ take their types that way.
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/services/api/**', 'src/tests/**'],
    rules: {
      '@typescript-eslint/no-restricted-imports': ['error', {
        patterns: [{
          group: ['**/services/api/*'],
          message: "Import from the 'services/api' barrel, not one of its submodules.",
          allowTypeImports: true,
        }],
      }],
    },
  },
  {
    // Server state is TanStack Query (src/lib/query.ts; UI_STYLE_GUIDE §48):
    // an API function is called only inside a queryFn or a mutationFn.
    // `allow` names the barrel's exports that make no request.
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/services/api/**', 'src/tests/**'],
    plugins: { bluestick: { rules: { 'api-in-query-only': apiInQueryOnly } } },
    rules: {
      'bluestick/api-in-query-only': ['error', {
        allow: [
          // the current project (the client's own state)
          'getCurrentProjectId', 'setCurrentProjectId',
          // links to a Hosts list, built from data already in hand
          'conditionHostsHref', 'subnetHostsHref', 'gridCellHostsHref', 'familyCellHostsHref',
        ],
      }],
    },
  },
  { files: ['src/utils/download.ts'], rules: { 'no-restricted-syntax': ['error', BARE_MOMENT] } },
  { files: ['src/utils/relativeTime.ts'], rules: { 'no-restricted-syntax': ['error', DOWNLOAD_NAME] } },
  // Tests build fixtures and expected values with the primitives themselves.
  { files: ['src/tests/**'], rules: { 'no-restricted-syntax': 'off' } },
];
