/**
 * A project's members have ONE loader (review 2026-10-23, R-FE3): the
 * `['listProjectMembers', projectId]` entry was filled by four hand-written
 * queries — Project settings (with its own `Member` type and a cast),
 * Portfolio's members sheet, the admins' memberships dialog and the roster
 * hook — one normalising the answer and remembering it, three not.  Now the
 * request is made in `hooks/useProjectMembers` only (`projectMembersQuery`);
 * the others spread its options.  Scans the frontend's source (tests and the
 * barrel that defines the function excepted) for a call of the function.
 */
import { readdirSync, readFileSync, statSync } from 'fs';
import { join, relative } from 'path';

import { describe, expect, it } from 'vitest';

const srcRoot = join(__dirname, '..');
const OWNER = 'hooks/useProjectMembers.ts';
const DEFINES = 'services/api.ts';
const CALL = /\blistProjectMembers\s*\(/;

function* files(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === 'tests') continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.tsx?$/.test(entry)) yield path;
  }
}

describe('the members of a project', () => {
  it('are requested by the roster hook alone', () => {
    const callers = [...files(srcRoot)]
      .map((path) => relative(srcRoot, path).split('\\').join('/'))
      .filter((rel) => rel !== DEFINES && CALL.test(readFileSync(join(srcRoot, rel), 'utf8')));
    expect(callers).toEqual([OWNER]);
  });
});
