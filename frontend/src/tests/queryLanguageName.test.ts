/**
 * The Query page's language is BlueStick's own pipe query language and is
 * never called after a commercial product (owner, 2026-10-17: "replace
 * Splunk wording"). Scans the code and docs that ship (the frontend's source,
 * the backend's application and tests, the documentation) for the product's
 * name and its language's abbreviation. `frontend/public` is not scanned: its
 * default-credentials reference names products on purpose.
 */
import { readdirSync, readFileSync, statSync } from 'fs';
import { join, relative } from 'path';

import { describe, expect, it } from 'vitest';

const repoRoot = join(__dirname, '..', '..', '..');
const ROOTS = ['frontend/src', 'backend/app', 'backend/tests', 'backend/alembic', 'documentation'];
const EXTENSIONS = /\.(ts|tsx|py|md)$/;
// The product, and "SPL" as a word or as `SPL-style` / `SPL's` (never inside
// another word: `_split`, `display`).
const NAMES = /splunk|\bSPL\b/i;
// This file names what it refuses.
const SELF = 'frontend/src/tests/queryLanguageName.test.ts';

function* files(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '__pycache__' || entry.startsWith('.')) continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (EXTENSIONS.test(entry)) yield path;
  }
}

describe('the query language has no other product’s name', () => {
  it('no shipped source or document calls it after one', () => {
    const hits: string[] = [];
    for (const root of ROOTS) {
      for (const path of files(join(repoRoot, root))) {
        const rel = relative(repoRoot, path);
        if (rel === SELF) continue;
        readFileSync(path, 'utf8').split('\n').forEach((line, i) => {
          if (NAMES.test(line)) hits.push(`${rel}:${i + 1}: ${line.trim().slice(0, 120)}`);
        });
      }
    }
    expect(hits).toEqual([]);
  });
});
