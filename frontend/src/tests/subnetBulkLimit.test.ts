/**
 * Review 2026-10-10, R9 — `SUBNET_BULK_MAX` is the route's own limit.
 *
 * `POST /scopes/subnet-labels/{label_id}/subnets` refuses a list longer than
 * `SubnetLabelBulkAssignMany.subnet_ids`'s `max_length` whole (422, no label
 * applied), so the Scope page stops ticking at that many and says so.  The
 * number lives on both sides; this pins them to each other — the same
 * approach as proposalBulkLimit.test.ts (read via fs, not import).
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, expect, it } from 'vitest';

const frontendRoot = join(__dirname, '..', '..');
const repoRoot = join(frontendRoot, '..');

describe('the bulk subnet-label limit', () => {
  it('is the same number on the Scope page and in the route’s schema', () => {
    const page = readFileSync(join(frontendRoot, 'src', 'pages', 'Scopes.tsx'), 'utf8');
    const schema = readFileSync(join(repoRoot, 'backend', 'app', 'schemas', 'schemas.py'), 'utf8');
    const ours = /const SUBNET_BULK_MAX = (\d+);/.exec(page);
    const theirs = /class SubnetLabelBulkAssignMany\(BaseModel\):[\s\S]*?subnet_ids: List\[int\] = Field\([^)]*max_length=(\d+)\)/.exec(schema);
    expect(ours, 'SUBNET_BULK_MAX not found in pages/Scopes.tsx').not.toBeNull();
    expect(theirs, 'SubnetLabelBulkAssignMany.subnet_ids max_length not found in schemas/schemas.py').not.toBeNull();
    expect(Number(ours![1])).toBe(Number(theirs![1]));
  });
});
