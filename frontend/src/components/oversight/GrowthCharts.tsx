/**
 * Host growth — three single-series charts on one shared date axis
 * (small multiples, 5.259.0): recorded hosts (cumulative line), hosts
 * first recorded per bucket, reviews concluded per bucket.  (v5.294.0 — "hosts",
 * not "targets", the word every other page uses; dates in the one format.)
 *
 * Why three and not one: a cumulative total and per-bucket counts are
 * different scales, and one plot with two y-axes invents a relationship; two
 * categorical columns would need a second hue the theme only has as a status
 * colour.  Each chart is one series in the info accent, so its title names it
 * and no legend is needed.
 *
 * Hover or arrow keys move ONE crosshair across all three; the readout above
 * lists every value at that bucket.  Every value is also in the table view.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { OversightGrowthPoint } from '../../services/api/oversight';
import { formatDate } from '../../utils/relativeTime';

const ACCENT = 'hsl(var(--info))';
const H = 72;              // plot height per chart
const PAD_L = 44;          // room for the y tick labels
const PAD_R = 44;          // room for the end label
const BAR_MAX = 24;

const niceMax = (v: number): number => {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
};

/** A bucket's start in the one date format: "Aug 29, 2026", or its month. */
export const bucketDate = (unit: string, start: string): string => {
  if (unit !== 'month') return formatDate(start.slice(0, 10));
  const [y, m] = start.slice(0, 7).split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
};

const unitLabel = (unit: string, start: string) =>
  unit === 'week' ? `Week of ${bucketDate(unit, start)}` : bucketDate(unit, start);

/** Container width, with a fallback where ResizeObserver is missing (tests).
 *
 *  A CALLBACK ref, re-observing whenever the container mounts (v5.265.0):
 *  the charts' container is replaced by the "no targets" line when a filter
 *  leaves no points, and an observer attached once on first render kept
 *  watching the detached element — it reported 0 (clamped to 280 px), and the
 *  charts stayed that narrow after the filter was cleared.  A zero width
 *  (a detached or hidden element) is ignored rather than trusted. */
export function useWidth(): [(el: HTMLDivElement | null) => void, number] {
  const [w, setW] = useState(640);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      if (width > 0) setW(Math.max(280, width));
    });
    ro.observe(el);
    observer.current = ro;
  }, []);
  useEffect(() => () => observer.current?.disconnect(), []);
  return [ref, w];
}

interface SeriesProps {
  title: string;
  points: OversightGrowthPoint[];
  value: (p: OversightGrowthPoint) => number;
  kind: 'line' | 'columns';
  width: number;
  hover: number | null;
  onHover: (i: number | null) => void;
  showDates: boolean;
  unit: string;
}

const Series: React.FC<SeriesProps> = ({ title, points, value, kind, width, hover, onHover, showDates, unit }) => {
  const n = points.length;
  const plotW = Math.max(1, width - PAD_L - PAD_R);
  const band = plotW / Math.max(1, n);
  const max = niceMax(Math.max(0, ...points.map(value)));
  const x = (i: number) => PAD_L + band * i + band / 2;
  const y = (v: number) => H - (v / max) * H;
  const last = n ? value(points[n - 1]) : 0;
  const barW = Math.max(1, Math.min(BAR_MAX, band - 2));   // 2px surface gap between columns

  const line = kind === 'line' && n > 0
    ? points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(value(p)).toFixed(1)}`).join(' ')
    : '';
  const area = line ? `${line} L${x(n - 1).toFixed(1)},${H} L${x(0).toFixed(1)},${H} Z` : '';

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * plotW;
    onHover(Math.min(n - 1, Math.max(0, Math.floor(px / band))));
  };

  return (
    <div className="min-w-0">
      <p className="text-caption font-medium text-foreground">{title}</p>
      <svg width={width} height={H + (showDates ? 22 : 6)} role="img" aria-label={`${title}: ${last.toLocaleString()} in the last bucket`}
        className="block overflow-visible">
        {/* recessive hairline grid: the top tick and the baseline */}
        <line x1={PAD_L} x2={PAD_L + plotW} y1={0.5} y2={0.5} stroke="hsl(var(--border))" />
        <line x1={PAD_L} x2={PAD_L + plotW} y1={H + 0.5} y2={H + 0.5} stroke="hsl(var(--muted-foreground) / 0.35)" />
        <text x={PAD_L - 6} y={4} textAnchor="end" dominantBaseline="hanging" className="fill-muted-foreground text-[11px] tabular-nums">
          {max.toLocaleString()}
        </text>
        <text x={PAD_L - 6} y={H} textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">0</text>

        {kind === 'line' ? (
          <>
            <path d={area} fill={ACCENT} fillOpacity={0.1} />
            <path d={line} fill="none" stroke={ACCENT} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {n > 0 && (
              <circle cx={x(n - 1)} cy={y(last)} r={4} fill={ACCENT} stroke="hsl(var(--background))" strokeWidth={2} />
            )}
          </>
        ) : (
          points.map((p, i) => {
            const v = value(p);
            if (v <= 0) return null;
            const top = y(v);
            const h = H - top;
            const r = Math.min(4, h, barW / 2);
            const x0 = x(i) - barW / 2;
            // 4px rounded data-end, square at the baseline.
            const d = `M${x0},${H} V${top + r} Q${x0},${top} ${x0 + r},${top} H${x0 + barW - r} Q${x0 + barW},${top} ${x0 + barW},${top + r} V${H} Z`;
            return <path key={p.start} d={d} fill={ACCENT} fillOpacity={hover == null || hover === i ? 1 : 0.55} />;
          })
        )}

        {/* end label: the one value worth reading without hovering */}
        <text x={PAD_L + plotW + 6} y={kind === 'line' ? y(last) : H} dominantBaseline={kind === 'line' ? 'middle' : 'auto'}
          className="fill-foreground text-[11px] font-semibold tabular-nums">
          {last.toLocaleString()}
        </text>

        {hover != null && n > 0 && (
          <line x1={x(hover)} x2={x(hover)} y1={0} y2={H} stroke="hsl(var(--foreground) / 0.5)" strokeWidth={1} />
        )}
        {showDates && n > 0 && (
          <>
            <text x={PAD_L} y={H + 16} className="fill-muted-foreground text-[11px]">{bucketDate(unit, points[0].start)}</text>
            <text x={PAD_L + plotW} y={H + 16} textAnchor="end" className="fill-muted-foreground text-[11px]">{bucketDate(unit, points[n - 1].start)}</text>
          </>
        )}
        <rect x={PAD_L} y={0} width={plotW} height={H} fill="transparent"
          onPointerMove={onMove} onPointerLeave={() => onHover(null)} />
      </svg>
    </div>
  );
};

export const GrowthCharts: React.FC<{ unit: string; points: OversightGrowthPoint[] }> = ({ unit, points }) => {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const focus = hover ?? points.length - 1;
  const p = points[focus];

  const onKey = (e: React.KeyboardEvent) => {
    if (!points.length) return;
    if (e.key === 'ArrowRight') { setHover(Math.min(points.length - 1, (hover ?? points.length - 1) + 1)); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { setHover(Math.max(0, (hover ?? points.length - 1) - 1)); e.preventDefault(); }
    if (e.key === 'Escape') setHover(null);
  };

  const totals = useMemo(() => points.reduce(
    (a, q) => ({ added: a.added + q.targets_added, reviews: a.reviews + q.reviews_concluded }),
    { added: 0, reviews: 0 },
  ), [points]);

  if (points.length === 0) {
    return <p className="text-metadata text-muted-foreground">No hosts recorded in these projects yet.</p>;
  }

  return (
    <div ref={ref} className="min-w-0 space-y-sm">
      {/* Readout: values lead, labels follow. */}
      <p className="text-caption text-muted-foreground" aria-live="polite" id="growth-readout">
        <span className="font-medium text-foreground">{unitLabel(unit, p.start)}</span>
        {' · '}<span className="font-semibold text-foreground tabular-nums">{p.cumulative_targets.toLocaleString()}</span> recorded {p.cumulative_targets === 1 ? 'host' : 'hosts'}
        {' · '}<span className="font-semibold text-foreground tabular-nums">+{p.targets_added.toLocaleString()}</span> first recorded
        {' · '}<span className="font-semibold text-foreground tabular-nums">{p.reviews_concluded.toLocaleString()}</span> {p.reviews_concluded === 1 ? 'review' : 'reviews'} concluded
        {hover == null && <span> (latest; hover or use ← → to move)</span>}
      </p>
      <div tabIndex={0} onKeyDown={onKey} aria-describedby="growth-readout"
        aria-label="Host growth charts — use the left and right arrow keys to move between dates"
        className="space-y-sm rounded-control focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Series title="Recorded hosts (cumulative)" points={points} value={(q) => q.cumulative_targets} kind="line"
          width={width} hover={hover} onHover={setHover} showDates={false} unit={unit} />
        <Series title={`Hosts first recorded per ${unit}`} points={points} value={(q) => q.targets_added} kind="columns"
          width={width} hover={hover} onHover={setHover} showDates={false} unit={unit} />
        <Series title={`Reviews concluded per ${unit}`} points={points} value={(q) => q.reviews_concluded} kind="columns"
          width={width} hover={hover} onHover={setHover} showDates unit={unit} />
      </div>
      <p className="text-caption text-muted-foreground">
        In these dates: {totals.added.toLocaleString()} {totals.added === 1 ? 'host' : 'hosts'} first recorded,{' '}
        {totals.reviews.toLocaleString()} {totals.reviews === 1 ? 'review' : 'reviews'} concluded.
        Counts surviving host records; a host removed with its scan is not counted.{' '}
        <button type="button" className="text-info hover:underline" onClick={() => setShowTable((s) => !s)} aria-expanded={showTable}>
          {showTable ? 'Hide table' : 'Show as table'}
        </button>
      </p>
      {showTable && (
        <div className="max-h-72 overflow-auto rounded-panel border border-border">
          <table className="w-full table-fixed text-caption">
            <caption className="sr-only">Host growth by {unit}</caption>
            <thead className="sticky top-0 bg-background text-muted-foreground">
              <tr>
                <th className="px-sm py-xxs text-left font-medium">{unit === 'day' ? 'Day' : unit === 'week' ? 'Week of' : 'Month'}</th>
                <th className="px-sm py-xxs text-right font-medium">Recorded hosts</th>
                <th className="px-sm py-xxs text-right font-medium">First recorded</th>
                <th className="px-sm py-xxs text-right font-medium">Reviews concluded</th>
              </tr>
            </thead>
            <tbody>
              {points.map((q) => (
                <tr key={q.start} className="border-t border-border">
                  <td className="px-sm py-xxs">{bucketDate(unit, q.start)}</td>
                  <td className="px-sm py-xxs text-right tabular-nums">{q.cumulative_targets.toLocaleString()}</td>
                  <td className="px-sm py-xxs text-right tabular-nums">{q.targets_added.toLocaleString()}</td>
                  <td className="px-sm py-xxs text-right tabular-nums">{q.reviews_concluded.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default GrowthCharts;
