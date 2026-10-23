/**
 * An import's result is in the Scans page's ONE import history (5.418.0;
 * owner decision 2026-10-22, G15): a status filter of it (`/scans?status=…`)
 * or one upload (`?job_id=` / `?error_id=`).  Every in-app link is built by
 * `importResultsPath`; `/parse-errors`, `/ingestion-results` (App.tsx) and the
 * old `/scans?view=results` (pages/ScansHub) only redirect there, through the
 * same function, for bookmarks and old notification links.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

import { describe, it, expect } from 'vitest';

import { IMPORT_RESULTS_VIEW, importResultsPath } from '../../utils/importResultsPath';

describe('importResultsPath', () => {
  it('is the history, with the filters it was given', () => {
    expect(IMPORT_RESULTS_VIEW).toBe('results');
    expect(importResultsPath()).toBe('/scans');
    expect(importResultsPath({ status: 'needs_attention' })).toBe('/scans?status=needs_attention');
    expect(importResultsPath({ job_id: 484 })).toBe('/scans?job_id=484');
    expect(importResultsPath({ status: 'failed', search: 'dmz sweep & more' }))
      .toBe('/scans?status=failed&search=dmz+sweep+%26+more');
  });

  it('leaves out a value that is not there, and never names a view', () => {
    expect(importResultsPath({ job_id: null, error_id: undefined, status: '' })).toBe('/scans');
    expect(importResultsPath({ view: 'results', status: 'failed' })).toBe('/scans?status=failed');
  });

  // What the redirects hand it: the old address's own query.  The old view's
  // sort and page have no meaning in the history (it is in upload order and
  // loads more), so they are not carried; every filter is.
  it('keeps every filter of an old address, and drops its view, sort and page', () => {
    expect(importResultsPath(new URLSearchParams(''))).toBe('/scans');
    expect(importResultsPath(new URLSearchParams('?view=results&status=failed&page=2&sort=status&dir=asc&search=dmz')))
      .toBe('/scans?status=failed&search=dmz');
    expect(importResultsPath(new URLSearchParams('view=results&job_id=484'))).toBe('/scans?job_id=484');
    expect(importResultsPath(new URLSearchParams('error_id=55'))).toBe('/scans?error_id=55');
  });
});

describe('in-app links to an import\'s result', () => {
  const SRC = join(__dirname, '..', '..');
  const files = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === 'tests' || name === 'node_modules' ? [] : files(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
  /** A line that is only a comment (the helper and the redirect explain themselves). */
  const COMMENT = /^\s*(\/\/|\/\*|\*)/;
  const OLD_PATH = /['"`]\/(parse-errors|ingestion-results)\b/;
  const OLD_VIEW = /view=results/;
  const REDIRECT_ROUTE = /<Route path="\/(parse-errors|ingestion-results)" element=\{<RedirectToImportResults \/>\} \/>/;

  const lines = () => files(SRC)
    .filter((path) => !relative(SRC, path).startsWith(join('services', 'api')))
    .flatMap((path) => readFileSync(path, 'utf8').split('\n')
      .filter((line) => !COMMENT.test(line))
      .map((line) => ({ file: relative(SRC, path), line })));

  // A link to `/parse-errors…` still works — through a redirect, which is for
  // bookmarks.  The API's own `/parse-errors/…` routes are requests, not
  // links, and live under services/api.
  it('no page or component names the old paths — only the two redirect routes do', () => {
    const named = lines().filter(({ line }) => OLD_PATH.test(line));
    expect(named.filter(({ line }) => !REDIRECT_ROUTE.test(line)).map((n) => n.file)).toEqual([]);
    expect(named.filter(({ line }) => REDIRECT_ROUTE.test(line)).map((n) => n.file)).toEqual(['App.tsx', 'App.tsx']);
  });

  it('nothing builds the old `?view=results` address any more', () => {
    expect(lines().filter(({ line }) => OLD_VIEW.test(line)).map((n) => n.file)).toEqual([]);
  });
});
