/**
 * Binning for the Tool Activity snapshot chart (screenshot review 2026-09-23).
 *
 * The old timeline drew one dot per activity and stacked overlapping dots into
 * lanes, so a burst of 60 uploads in one hour made a ~1100px column of dots.
 * Counting activities per time bin keeps the chart a fixed height whatever the
 * burst. Pure functions so the binning is tested without rendering.
 */
import type { ActivityItem, ActivityKind } from '../services/api';

const HOUR_MS = 3600 * 1000;

/** Bin widths tried in order; the smallest that fits the plot wins. */
export const BIN_CANDIDATES_MS = [1, 2, 3, 6, 12, 24].map((h) => h * HOUR_MS);

/** Every kind, in the chart's row order. */
export const ACTIVITY_KINDS: ActivityKind[] = [
  'scan',
  'recon_session',
  'execution_session',
  'test_result',
  'sanity_check',
];

export const KIND_PLURAL: Record<ActivityKind, [string, string]> = {
  scan: ['scan upload', 'scan uploads'],
  recon_session: ['recon run', 'recon runs'],
  execution_session: ['execution run', 'execution runs'],
  test_result: ['command run', 'commands run'],
  sanity_check: ['target probe', 'target probes'],
};

export const kindCount = (kind: ActivityKind, n: number): string =>
  `${n.toLocaleString()} ${KIND_PLURAL[kind][n === 1 ? 0 : 1]}`;

/**
 * The bin width for a span drawn across `plotWidthPx`: the smallest candidate
 * leaving at least `minPxPerBin` per bin (a column plus its 2px gap). A week
 * on a normal desktop plot is hourly; a narrow window coarsens to 2h/3h….
 */
export function chooseBinMs(spanMs: number, plotWidthPx: number, minPxPerBin = 4): number {
  const maxBins = Math.max(1, Math.floor(plotWidthPx / minPxPerBin));
  for (const b of BIN_CANDIDATES_MS) {
    if (Math.ceil(spanMs / b) <= maxBins) return b;
  }
  return BIN_CANDIDATES_MS[BIN_CANDIDATES_MS.length - 1];
}

export interface ActivityBin {
  /** Epoch ms, inclusive. */
  start: number;
  /** Epoch ms, exclusive. */
  end: number;
  counts: Record<ActivityKind, number>;
  total: number;
}

const emptyCounts = (): Record<ActivityKind, number> => ({
  scan: 0,
  recon_session: 0,
  execution_session: 0,
  test_result: 0,
  sanity_check: 0,
});

/**
 * Count items per bin and kind by `start_time`. Bins are aligned to local
 * time (hour / day boundaries read as the analyst's clock), the first one
 * holding `windowStart`, the last one holding `windowEnd`. Items outside the
 * window or with an unreadable start are not counted.
 */
export function binActivity(
  items: ActivityItem[],
  windowStart: string | number,
  windowEnd: string | number,
  binMs: number,
): ActivityBin[] {
  const ws = new Date(windowStart).getTime();
  const we = new Date(windowEnd).getTime();
  if (!Number.isFinite(ws) || !Number.isFinite(we) || we <= ws || binMs <= 0) return [];

  // Align to local time: shift into "local epoch", floor, shift back.
  const offset = new Date(ws).getTimezoneOffset() * 60 * 1000;
  const first = Math.floor((ws - offset) / binMs) * binMs + offset;
  const bins: ActivityBin[] = [];
  for (let s = first; s < we; s += binMs) {
    bins.push({ start: s, end: s + binMs, counts: emptyCounts(), total: 0 });
  }
  for (const item of items) {
    const t = new Date(item.start_time).getTime();
    if (!Number.isFinite(t) || t < ws || t > we) continue;
    const i = Math.min(bins.length - 1, Math.floor((t - first) / binMs));
    if (i < 0) continue;
    bins[i].counts[item.kind] += 1;
    bins[i].total += 1;
  }
  return bins;
}

/** Totals per kind across the bins. */
export function kindTotals(bins: ActivityBin[]): Record<ActivityKind, number> {
  const t = emptyCounts();
  for (const b of bins) for (const k of ACTIVITY_KINDS) t[k] += b.counts[k];
  return t;
}

/** A round axis maximum at or above `v` (1, 2, 5 × 10ⁿ). */
export function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
