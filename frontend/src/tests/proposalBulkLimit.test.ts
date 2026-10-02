/**
 * Review 2026-10-02 H4 — `PROPOSAL_BULK_MAX` is the route's own limit.
 *
 * `POST /proposals/bulk` refuses a list longer than `BulkBody.ids`'s
 * `max_length` whole (422, nothing decided), so the page sends at most that
 * many and says so.  The number lives on both sides; this pins them to each
 * other.  Read via fs (not import): the API module pulls in the HTTP client,
 * which does not load in jsdom — the same approach as
 * uploadFormatContract.test.ts.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, expect, it } from 'vitest';

const frontendRoot = join(__dirname, '..', '..');
const repoRoot = join(frontendRoot, '..');

describe('the bulk proposal limit', () => {
  it('is the same number in the client and in the route', () => {
    const client = readFileSync(join(frontendRoot, 'src', 'services', 'api', 'proposals.ts'), 'utf8');
    const route = readFileSync(
      join(repoRoot, 'backend', 'app', 'api', 'v1', 'endpoints', 'proposals.py'), 'utf8',
    );
    const ours = /export const PROPOSAL_BULK_MAX = (\d+);/.exec(client);
    const theirs = /class BulkBody\(BaseModel\):\s*\n\s*ids: List\[int\] = Field\([^)]*max_length=(\d+)\)/.exec(route);
    expect(ours, 'PROPOSAL_BULK_MAX not found in services/api/proposals.ts').not.toBeNull();
    expect(theirs, 'BulkBody.ids max_length not found in endpoints/proposals.py').not.toBeNull();
    expect(Number(ours![1])).toBe(Number(theirs![1]));
  });
});
