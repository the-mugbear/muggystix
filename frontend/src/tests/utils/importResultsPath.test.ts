/**
 * Ingestion results is a view of the Scans page (`/scans?view=results`).  Every
 * in-app link is built by `importResultsPath`; `/parse-errors` and
 * `/ingestion-results` only redirect there (App.tsx, through the same
 * function), for bookmarks and old notification links.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

import { describe, it, expect } from 'vitest';

import { IMPORT_RESULTS_VIEW, importResultsPath } from '../../utils/importResultsPath';

describe('importResultsPath', () => {
  it('names the view once, then the filters it was given', () => {
    expect(IMPORT_RESULTS_VIEW).toBe('results');
    expect(importResultsPath()).toBe('/scans?view=results');
    expect(importResultsPath({ status: 'needs_attention' })).toBe('/scans?view=results&status=needs_attention');
    expect(importResultsPath({ job_id: 484 })).toBe('/scans?view=results&job_id=484');
    expect(importResultsPath({ status: 'failed', search: 'dmz sweep & more' }))
      .toBe('/scans?view=results&status=failed&search=dmz+sweep+%26+more');
  });

  it('leaves out a value that is not there, and never takes the view from its caller', () => {
    expect(importResultsPath({ job_id: null, error_id: undefined, status: '' })).toBe('/scans?view=results');
    expect(importResultsPath({ view: 'scans', status: 'failed' })).toBe('/scans?view=results&status=failed');
  });

  // What the two redirect routes hand it: the old address's own query.
  it('keeps every filter of an old address, whatever view it named', () => {
    expect(importResultsPath(new URLSearchParams(''))).toBe('/scans?view=results');
    expect(importResultsPath(new URLSearchParams('?status=failed&page=2')))
      .toBe('/scans?view=results&status=failed&page=2');
    expect(importResultsPath(new URLSearchParams('view=scans&job_id=484'))).toBe('/scans?view=results&job_id=484');
  });
});

describe('in-app links to Ingestion results', () => {
  const SRC = join(__dirname, '..', '..');
  const files = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === 'tests' || name === 'node_modules' ? [] : files(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
  /** A line that is only a comment (the helper and the redirect explain themselves). */
  const COMMENT = /^\s*(\/\/|\/\*|\*)/;
  const OLD_PATH =/['"`]\/(parse-errors|ingestion-results)\b/;
  const REDIRECT_ROUTE = /<Route path="\/(parse-errors|ingestion-results)" element=\{<RedirectToImportResults \/>\} \/>/;

  // A link to `/parse-errors…` still works — through a redirect, which is for
  // bookmarks.  The API's own `/parse-errors/…` routes are requests, not
  // links, and live under services/api.
  it('no page or component names the old paths — only the two redirect routes do', () => {
    const named = files(SRC)
      .filter((path) => !relative(SRC, path).startsWith(join('services', 'api')))
      .flatMap((path) => readFileSync(path, 'utf8').split('\n')
        .filter((line) => OLD_PATH.test(line) && !COMMENT.test(line))
        .map((line) => ({ file: relative(SRC, path), line })));
    expect(named.filter(({ line }) => !REDIRECT_ROUTE.test(line)).map((n) => n.file)).toEqual([]);
    // …and both redirects are still there.
    expect(named.filter(({ line }) => REDIRECT_ROUTE.test(line)).map((n) => n.file)).toEqual(['App.tsx', 'App.tsx']);
  });
});
