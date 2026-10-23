/**
 * The case tables a client rule shares with its server twin
 * (backend/tests/fixtures/twins/*.json): the backend's tests run the server's
 * function over the same rows, so the two cannot drift without a test failing
 * (backlog 2026-10-22, H-d).  The client keeps its own copy of these rules
 * because it applies them while the author types — the editor's preview, the
 * composer's mention highlighting and its "isn't a member" hint.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

const TWINS = join(__dirname, '..', '..', '..', '..', 'backend', 'tests', 'fixtures', 'twins');

/** One shared case table, parsed. */
export const twinCases = <T,>(file: string): T =>
  JSON.parse(readFileSync(join(TWINS, file), 'utf8')) as T;
