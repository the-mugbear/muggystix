/**
 * R5 (2026-10-22) — the most hosts a promotion or a dismissal names for one
 * issue is what the page can send: the inspector lists `HOST_CAP` hosts and a
 * longer list cannot be narrowed there, so a narrowed host set is at most that
 * many.  The server refuses more (`HOST_IDS_CAP`, the schemas' `max_length`).
 * Read via fs, not import, as subnetBulkLimit.test.ts does.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, expect, it } from 'vitest';

const frontendRoot = join(__dirname, '..', '..');
const repoRoot = join(frontendRoot, '..');

describe('the host list of a promotion or a dismissal', () => {
  it('is capped at the number of hosts the inspector can narrow', () => {
    const page = readFileSync(join(frontendRoot, 'src', 'components', 'observations', 'ObservationInspector.tsx'), 'utf8');
    const service = readFileSync(
      join(repoRoot, 'backend', 'app', 'services', 'scanner_observation_service.py'), 'utf8',
    );
    const ours = /export const HOST_CAP = (\d+);/.exec(page);
    const theirs = /^HOST_IDS_CAP = (\d+)$/m.exec(service);
    expect(ours, 'HOST_CAP not found in ObservationInspector.tsx').not.toBeNull();
    expect(theirs, 'HOST_IDS_CAP not found in scanner_observation_service.py').not.toBeNull();
    expect(Number(ours![1])).toBe(Number(theirs![1]));
  });
});
