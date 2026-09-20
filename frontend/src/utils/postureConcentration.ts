/**
 * "Where to focus" on the Posture overview (v5.254.0): one pattern family's
 * grid row, ranked into a comparison a presenter can defend.
 *
 * Everything here is arithmetic over ONE row of the server's family × site
 * grid. Site columns are disjoint (a host inherits exactly one site, or lands in
 * Unassigned), so "the rest of the assessed project" is a plain sum of the other
 * cells — no second aggregation that could drift from the grid below it.
 *
 * The rules, all shown to the reader rather than buried:
 *  - rate = affected / assessed, never affected / in scope: an unassessed host
 *    is unknown, not clean.
 *  - the comparator is the REST OF THE ASSESSED PROJECT (every other segment's
 *    affected / assessed). It is not called "peers": nothing records asset role
 *    or scan method, so nothing supports "comparable".
 *  - a difference is in percentage points. A multiplier is undefined against a
 *    zero baseline and flatters small ones.
 *  - a LIMITED comparison (few assessed hosts, or thin evidence) keeps its
 *    counts on screen but is ranked after the comparable segments: 2 of 2 is
 *    not "the worst-run subnet". These are product heuristics, not statistics.
 */
import type { HeatmapCell, HeatmapRow, HeatmapSegment } from '../services/api/posture';

/** Below this many assessed hosts a rate is too unstable to rank on. */
export const LIMITED_MIN_ASSESSED = 10;
/** Below this share of eligible hosts assessed, the picture is mostly unknown. */
export const LIMITED_MIN_COVERAGE = 0.6;

export type ConcentrationState = 'comparable' | 'limited' | 'unassessed';

export interface ConcentrationRow {
  key: string;
  label: string;
  cell: HeatmapCell;
  affected: number;
  assessed: number;
  inScope: number;
  /** affected / assessed; null when nothing was assessed. */
  rate: number | null;
  /** Assessed share of the hosts the domain applies to; null when unknown. */
  coverage: number | null;
  /** Eligible hosts with no evidence — what is still unknown here. */
  unknown: number | null;
  restAffected: number;
  restAssessed: number;
  /** Rest-of-assessed-project rate; null when no other segment was assessed. */
  restRate: number | null;
  /** rate − restRate, in percentage points. */
  deltaPoints: number | null;
  /** This segment's share of every affected host in the row. */
  shareOfAffected: number | null;
  state: ConcentrationState;
  /** Why the comparison is limited, in words; empty otherwise. */
  limitedBecause: string;
}

const ratio = (n: number, d: number): number | null => (d > 0 ? n / d : null);

export const rankConcentration = (row: HeatmapRow, segments: HeatmapSegment[]): ConcentrationRow[] => {
  const labels = new Map(segments.map((s) => [s.key, s.label]));
  const totalAffected = row.cells.reduce((sum, c) => sum + c.affected, 0);
  const totalAssessed = row.cells.reduce((sum, c) => sum + c.assessed, 0);

  const rows = row.cells.map((cell): ConcentrationRow => {
    const rate = ratio(cell.affected, cell.assessed);
    const restAffected = totalAffected - cell.affected;
    const restAssessed = totalAssessed - cell.assessed;
    const restRate = ratio(restAffected, restAssessed);
    const coverage = cell.eligible == null || cell.eligible_assessed == null
      ? null : ratio(cell.eligible_assessed, cell.eligible);
    const unknown = cell.eligible == null || cell.eligible_assessed == null
      ? null : Math.max(0, cell.eligible - cell.eligible_assessed);

    const reasons: string[] = [];
    if (cell.assessed > 0 && cell.assessed < LIMITED_MIN_ASSESSED) {
      reasons.push(`only ${cell.assessed} assessed`);
    }
    if (coverage != null && coverage < LIMITED_MIN_COVERAGE) {
      reasons.push(`${Math.round(coverage * 100)}% of eligible hosts assessed`);
    }
    const state: ConcentrationState = cell.assessed === 0 ? 'unassessed'
      : reasons.length > 0 ? 'limited' : 'comparable';

    return {
      key: cell.segment,
      label: labels.get(cell.segment) ?? cell.segment,
      cell,
      affected: cell.affected,
      assessed: cell.assessed,
      inScope: cell.in_scope,
      rate,
      coverage,
      unknown,
      restAffected,
      restAssessed,
      restRate,
      deltaPoints: rate == null || restRate == null ? null : (rate - restRate) * 100,
      shareOfAffected: ratio(cell.affected, totalAffected),
      state,
      limitedBecause: reasons.join(' · '),
    };
  });

  const stateOrder: Record<ConcentrationState, number> = { comparable: 0, limited: 1, unassessed: 2 };
  return rows.sort((a, b) =>
    stateOrder[a.state] - stateOrder[b.state]
    // Comparable segments rank by rate; limited ones by what is KNOWN (count).
    || (a.state === 'comparable' ? (b.rate ?? 0) - (a.rate ?? 0) : 0)
    || b.affected - a.affected
    || b.inScope - a.inScope
    || a.label.localeCompare(b.label));
};

/** The family worth leading with: the one whose top comparable segment stands
 *  furthest above the rest of the project; failing that, the most affected. */
export const leadingFamily = (rows: HeatmapRow[], segments: HeatmapSegment[]): HeatmapRow | null => {
  let best: { row: HeatmapRow; delta: number } | null = null;
  for (const row of rows) {
    if (row.affected_total === 0) continue;
    const top = rankConcentration(row, segments).find((r) => r.state === 'comparable' && r.affected > 0);
    const delta = top?.deltaPoints ?? -Infinity;
    if (!best || delta > best.delta) best = { row, delta };
  }
  return best?.row ?? rows.find((r) => r.affected_total > 0) ?? null;
};

/** One sentence a presenter can read out; every figure is on the row beside it. */
export const describeConcentration = (r: ConcentrationRow, familyLabel: string): string => {
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  if (r.state === 'unassessed') {
    return `${r.label}: no evidence for ${familyLabel.toLowerCase()} — ${r.inScope} hosts in scope are unknown, not clean.`;
  }
  const head = `${r.label}: ${r.affected} of ${r.assessed} assessed hosts affected (${pct(r.rate ?? 0)})`;
  if (r.state === 'limited') {
    return `${head}. Limited comparison — ${r.limitedBecause}; collect the missing evidence before reading much into the rate.`;
  }
  if (r.restRate == null) return `${head}. No other segment was assessed to compare it with.`;
  const delta = Math.round(r.deltaPoints ?? 0);
  const versus = `${r.restAffected} of ${r.restAssessed} (${pct(r.restRate)}) across the rest of the assessed project`;
  if (delta >= 10) return `${head}, against ${versus} — ${delta} points higher.`;
  if (r.shareOfAffected != null && r.shareOfAffected >= 0.5 && r.affected > 0) {
    return `${head}, against ${versus}. It holds ${pct(r.shareOfAffected)} of the affected hosts because it is large, not because its rate stands out.`;
  }
  return `${head}, against ${versus} — in line with the rest.`;
};
