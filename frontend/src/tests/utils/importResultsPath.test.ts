/**
 * An import's result is in the Scans page's ONE import history: a status
 * filter of it (`/scans?status=…`) or one upload (`?job_id=` / `?error_id=`).
 * Every in-app link is built by `importResultsPath`.  The old addresses
 * (`/parse-errors`, `/ingestion-results`, `/scans?view=results`) redirected
 * there for bookmarks until every installation had upgraded; they are gone
 * (owner, 2026-10-23), and nothing names them.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

import { describe, it, expect } from 'vitest';

import { importResultsPath } from '../../utils/importResultsPath';

describe('importResultsPath', () => {
  it('is the history, with the filters it was given', () => {
    expect(importResultsPath()).toBe('/scans');
    expect(importResultsPath({ status: 'needs_attention' })).toBe('/scans?status=needs_attention');
    expect(importResultsPath({ job_id: 484 })).toBe('/scans?job_id=484');
    expect(importResultsPath({ error_id: 55 })).toBe('/scans?error_id=55');
    expect(importResultsPath({ status: 'failed', search: 'dmz sweep & more' }))
      .toBe('/scans?status=failed&search=dmz+sweep+%26+more');
  });

  it('leaves out a value that is not there', () => {
    expect(importResultsPath({ job_id: null, error_id: undefined, status: '' })).toBe('/scans');
  });
});

describe('in-app links to an import\'s result', () => {
  const SRC = join(__dirname, '..', '..');
  const files = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === 'tests' || name === 'node_modules' ? [] : files(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
  /** A line that is only a comment. */
  const COMMENT = /^\s*(\/\/|\/\*|\*)/;
  const OLD_PATH = /['"`]\/(parse-errors|ingestion-results)\b/;
  const OLD_VIEW = /view=results/;

  // The API's own `/parse-errors/…` routes are requests, not links, and live
  // under services/api.
  const lines = () => files(SRC)
    .filter((path) => !relative(SRC, path).startsWith(join('services', 'api')))
    .flatMap((path) => readFileSync(path, 'utf8').split('\n')
      .filter((line) => !COMMENT.test(line))
      .map((line) => ({ file: relative(SRC, path), line })));

  it('no page, component or route names the old paths', () => {
    expect(lines().filter(({ line }) => OLD_PATH.test(line)).map((n) => n.file)).toEqual([]);
  });

  it('nothing builds the old `?view=results` address', () => {
    expect(lines().filter(({ line }) => OLD_VIEW.test(line)).map((n) => n.file)).toEqual([]);
  });
});
