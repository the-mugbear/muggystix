/**
 * "Where to focus" — the Posture overview's one primary visual (v5.254.0).
 *
 * One named measure (a pattern family), the leading segments ranked for it on a
 * COMMON 0–100% scale, the rest-of-assessed-project rate as a tick on every bar,
 * and the evidence behind each row beside it. Selecting a row writes the
 * sentence a presenter would say, with the links that back it.
 *
 * The arithmetic and its rules live in utils/postureConcentration.ts; this file
 * only draws them. Every figure is a direct label — nothing is hover-only.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';

import type { PostureHeatmap } from '../../services/api/posture';
import { familyCellHostsHref, UNASSIGNED_SITE } from '../../services/api/insights';
import {
  rankConcentration, leadingFamily, describeConcentration,
  LIMITED_MIN_ASSESSED, LIMITED_MIN_COVERAGE, type ConcentrationRow,
} from '../../utils/postureConcentration';
import { cn } from '../../utils/cn';

/** Rows shown before "N more" — the overview summarises; the grid has them all. */
const TOP_ROWS = 5;

const hatch: React.CSSProperties = {
  backgroundImage:
    'repeating-linear-gradient(135deg, hsl(var(--muted-foreground) / 0.18) 0 3px, transparent 3px 8px)',
};

const pct = (v: number | null) => (v == null ? '—' : `${Math.round(v * 100)}%`);

const RateBar: React.FC<{ r: ConcentrationRow }> = ({ r }) => {
  if (r.state === 'unassessed') {
    return <div className="h-4 w-full rounded-sm" style={hatch} aria-hidden />;
  }
  const limited = r.state === 'limited';
  return (
    <div className="relative h-4 w-full rounded-sm bg-muted" aria-hidden>
      <div className="h-full rounded-sm"
        style={{
          width: `${Math.max(r.affected > 0 ? 2 : 0, (r.rate ?? 0) * 100)}%`,
          // A limited comparison is drawn hollow: the count is real, the rate is soft.
          ...(limited
            ? { border: '1px solid hsl(var(--destructive) / 0.55)', ...hatch }
            : { backgroundColor: 'hsl(var(--destructive) / 0.55)' }),
        }} />
      {r.restRate != null && (
        <div className="absolute inset-y-[-2px] w-0.5 bg-foreground"
          style={{ left: `calc(${Math.min(100, r.restRate * 100)}% - 1px)` }} />
      )}
    </div>
  );
};

export const FocusComparison: React.FC<{ heatmap: PostureHeatmap }> = ({ heatmap }) => {
  const families = useMemo(() => heatmap.rows.filter((r) => r.affected_total > 0), [heatmap]);
  const lead = useMemo(() => leadingFamily(heatmap.rows, heatmap.segments), [heatmap]);
  const [familyKey, setFamilyKey] = useState<string | null>(lead?.family ?? null);
  const family = families.find((f) => f.family === familyKey) ?? lead;
  const ranked = useMemo(
    () => (family ? rankConcentration(family, heatmap.segments) : []),
    [family, heatmap.segments],
  );
  const [segmentKey, setSegmentKey] = useState<string | null>(null);
  // New data or another measure: fall back to that measure's leading segment.
  useEffect(() => { setSegmentKey(null); }, [family?.family]);
  useEffect(() => { setFamilyKey(lead?.family ?? null); }, [lead?.family]);

  if (!family) return null;
  const shown = ranked.slice(0, TOP_ROWS);
  const selected = ranked.find((r) => r.key === segmentKey) ?? ranked[0];
  const hostsHref = selected && selected.affected > 0
    ? familyCellHostsHref(
        family.conditions,
        selected.key === 'unassigned' ? UNASSIGNED_SITE : selected.cell.drilldown_filter?.site,
      )
    : null;

  return (
    <div className="space-y-sm">
      <div className="flex flex-wrap items-center gap-xs">
        <span className="text-caption text-muted-foreground">Measure</span>
        {families.map((f) => (
          <button key={f.family} type="button" aria-pressed={f.family === family.family}
            onClick={() => setFamilyKey(f.family)}
            className={cn(
              'max-w-[16rem] truncate rounded-control border px-xs py-0.5 text-caption transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              f.family === family.family
                ? 'border-foreground bg-foreground/5 font-semibold text-foreground'
                : 'border-border text-muted-foreground hover:text-foreground',
            )}
            title={`${f.family_label} — ${f.affected_total} hosts affected`}>
            {f.family_label}
          </button>
        ))}
      </div>

      <div className="grid items-start gap-lg lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="min-w-0">
          <table className="w-full border-collapse text-metadata" style={{ tableLayout: 'fixed' }}>
            <thead>
              <tr className="text-left text-caption text-muted-foreground">
                <th className="w-[26%] pb-xxs pr-sm font-medium">Segment</th>
                <th className="pb-xxs pr-sm font-medium">
                  Affected / assessed — {family.family_label}
                </th>
                <th className="w-[28%] pb-xxs font-medium">Evidence ({family.evidence_domain_label})</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const active = r.key === selected?.key;
                return (
                  <tr key={r.key} data-state={r.state}
                    className={cn('border-t border-border/60', active && 'bg-muted/50')}>
                    <td className="py-xs pr-sm align-middle">
                      <button type="button" onClick={() => setSegmentKey(r.key)} aria-pressed={active}
                        className="block w-full min-w-0 truncate rounded text-left font-medium text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        title={r.label}>
                        {r.label}
                      </button>
                    </td>
                    <td className="py-xs pr-sm align-middle">
                      <div className="flex min-w-0 items-center gap-sm">
                        <div className="min-w-0 flex-1"><RateBar r={r} /></div>
                        <span className="w-[8.5rem] shrink-0 truncate text-right tabular-nums text-foreground">
                          {r.state === 'unassessed'
                            ? <span className="italic text-muted-foreground">not assessed</span>
                            : <>{r.affected}/{r.assessed} · {pct(r.rate)}</>}
                        </span>
                      </div>
                      {r.state === 'limited' && (
                        <p className="mt-xxs truncate text-caption text-warning" title={r.limitedBecause}>
                          Limited comparison — {r.limitedBecause}
                        </p>
                      )}
                    </td>
                    <td className="py-xs align-middle text-caption text-muted-foreground">
                      <span className="block truncate tabular-nums">
                        {r.cell.eligible == null
                          ? `${r.assessed} of ${r.inScope} in scope`
                          : `${r.cell.eligible_assessed} of ${r.cell.eligible} eligible`}
                      </span>
                      {r.unknown != null && r.unknown > 0 && (
                        <span className="block truncate tabular-nums">{r.unknown} still unknown</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-xs text-caption text-muted-foreground">
            <span className="mr-xxs inline-block h-3 w-0.5 translate-y-0.5 bg-foreground" aria-hidden />
            marks the rate across the rest of the assessed project. A comparison is limited below{' '}
            {LIMITED_MIN_ASSESSED} assessed hosts or {Math.round(LIMITED_MIN_COVERAGE * 100)}% of eligible
            hosts assessed — counts stay visible, the segment is ranked after the comparable ones.
            {ranked.length > shown.length && ` ${ranked.length - shown.length} more segment${ranked.length - shown.length === 1 ? '' : 's'} in the grid below.`}
          </p>
        </div>

        {selected && (
          <div className="min-w-0 border-l-2 border-border pl-md" aria-live="polite">
            <p className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">Why it matters</p>
            <p className="mt-xxs break-words text-metadata text-foreground">
              {describeConcentration(selected, family.family_label)}
            </p>
            <p className="mt-xs break-words text-caption text-muted-foreground">
              A rate describes what was observed. It is not a cause: a deliberate legacy enclave, different
              asset roles or uneven scan depth can produce the same difference.
            </p>
            <div className="mt-sm flex flex-wrap gap-x-md gap-y-xxs text-caption">
              {hostsHref && (
                <Link to={hostsHref} className="inline-flex items-center gap-xxs text-info hover:underline">
                  {selected.affected} affected host{selected.affected === 1 ? '' : 's'} <ArrowUpRight className="size-3" aria-hidden />
                </Link>
              )}
              <Link to="/posture/evidence" className="inline-flex items-center gap-xxs text-info hover:underline">
                Evidence gaps <ArrowUpRight className="size-3" aria-hidden />
              </Link>
              <Link to="/posture/patterns" className="inline-flex items-center gap-xxs text-info hover:underline">
                Pattern detail <ArrowUpRight className="size-3" aria-hidden />
              </Link>
              <Link to="/posture/segments" className="inline-flex items-center gap-xxs text-info hover:underline">
                Compare segments <ArrowUpRight className="size-3" aria-hidden />
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default FocusComparison;
