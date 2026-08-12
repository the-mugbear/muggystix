/**
 * Upload-format single-source guard.
 *
 * Three places independently name the accepted upload extensions, and they
 * MUST agree for an upload to work end-to-end:
 *
 *   1. backend  — ALLOWED_UPLOAD_EXTENSIONS in ingestion_service.py
 *                 (the server-side allowlist; a mismatch here 400s the upload)
 *   2. frontend — the react-dropzone `accept` map in Scans.tsx
 *                 (a mismatch here rejects the file before it ever uploads)
 *   3. docs     — the "Accepted Extensions" column of UPLOAD_FORMATS.md
 *                 (a mismatch here promises users a format that doesn't work)
 *
 * This test exists because they drifted twice in one change: `.ndjson` was
 * documented + parser-supported but missing from the allowlist (rdap-lookup.py
 * writes `.ndjson`, so the upload 400'd), and `.jsonl`/`.ndjson`/`.zip` were
 * in the allowlist but missing from the dropzone `accept` (so the picker
 * rejected them client-side).  Both were invisible until a user hit them.
 *
 * Read via fs (not import) so the Python/Markdown sources can be parsed
 * directly, the same approach as versionConsistency.test.ts.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, it, expect } from 'vitest';

const frontendRoot = join(__dirname, '..', '..');
const repoRoot = join(frontendRoot, '..');

/** Extensions in the backend frozenset literal — double-quoted tokens only,
 *  scoped to the frozenset(...) call so a `.ext` in a nearby comment or string
 *  elsewhere in the file can't leak in. */
function backendAllowlist(): Set<string> {
  const src = readFileSync(
    join(repoRoot, 'backend', 'app', 'services', 'ingestion_service.py'),
    'utf8',
  );
  const block = src.match(/ALLOWED_UPLOAD_EXTENSIONS[\s\S]*?\)/);
  if (!block) throw new Error('ALLOWED_UPLOAD_EXTENSIONS frozenset not found');
  return new Set([...block[0].matchAll(/"(\.[a-z0-9]+)"/g)].map((m) => m[1]));
}

/** Extensions in the react-dropzone `accept` map — single-quoted `.ext`
 *  values, scoped to the accept:{...} object.  The MIME-type keys
 *  ('text/xml', …) don't start with a dot, so they're naturally excluded. */
function dropzoneAccept(): Set<string> {
  const src = readFileSync(join(frontendRoot, 'src', 'pages', 'Scans.tsx'), 'utf8');
  const block = src.match(/accept:\s*\{[\s\S]*?\}/);
  if (!block) throw new Error('dropzone accept map not found in Scans.tsx');
  return new Set([...block[0].matchAll(/'(\.[a-z0-9]+)'/g)].map((m) => m[1]));
}

/** Extensions in the "Accepted Extensions" column (index 2) of the
 *  UPLOAD_FORMATS.md pipe table.  Scoping to that column keeps prose mentions
 *  (e.g. `.env` in the notes) and example filenames in the Notes column out. */
function documentedExtensions(): Set<string> {
  const md = readFileSync(join(repoRoot, 'documentation', 'UPLOAD_FORMATS.md'), 'utf8');
  const out = new Set<string>();
  for (const line of md.split('\n')) {
    if (!line.startsWith('|')) continue;
    const col = line.split('|')[2] ?? '';
    for (const m of col.matchAll(/`(\.[a-z0-9]+)`/g)) out.add(m[1]);
  }
  return out;
}

const sorted = (s: Set<string>) => [...s].sort();

describe('upload format contract', () => {
  const allowlist = backendAllowlist();
  const accept = dropzoneAccept();
  const documented = documentedExtensions();

  it('each source names at least one extension (parsing sanity)', () => {
    expect(allowlist.size).toBeGreaterThan(0);
    expect(accept.size).toBeGreaterThan(0);
    expect(documented.size).toBeGreaterThan(0);
  });

  // The two code artifacts must be identical: the dropzone must offer exactly
  // what the server accepts — no format the picker blocks, none it lets
  // through only to be 400'd on arrival.
  it('dropzone accept map equals the backend allowlist', () => {
    expect(sorted(accept)).toEqual(sorted(allowlist));
  });

  // Docs must promise exactly what the server accepts — no phantom formats,
  // no accepted format left undocumented.
  it('documented extensions equal the backend allowlist', () => {
    expect(sorted(documented)).toEqual(sorted(allowlist));
  });
});
