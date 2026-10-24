/**
 * The server measures presentation slides by `backend/app/data/presentation_metrics.json`;
 * the browser draws them with the `@fontsource` files this frontend ships.  The table must be
 * the one those files give (frontend/scripts/generate-presentation-metrics.mjs): a font upgrade
 * without a regenerated table fails here.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { OUT, buildMetrics, serialise } from '../../../scripts/generate-presentation-metrics.mjs';

describe('presentation metrics', () => {
  it('the server’s width table is the one the shipped font files give', () => {
    const current = readFileSync(OUT as string, 'utf8');
    expect(current).toBe((serialise as (m: unknown) => string)((buildMetrics as () => unknown)()));
  });
});
