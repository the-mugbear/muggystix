/**
 * Host growth — three single-series charts on one shared date axis (small
 * multiples, 5.259.0): recorded hosts (cumulative line), hosts first recorded
 * per bucket, reviews concluded per bucket.
 *
 * Why three and not one: a cumulative total and per-bucket counts are
 * different scales, and one plot with two y-axes invents a relationship. Each
 * chart is one series in the info accent, so its title names it and no
 * legend is needed.
 *
 * Drawn with Observable Plot since 5.307.0 (kept after a trial against the
 * hand-built SVG it replaced): a real UTC time axis with dated gridlines
 * shared by the three plots, columns sized to their interval, whole-number y
 * ticks. Hover or arrow keys move ONE crosshair across all three; the readout
 * above lists every value at that bucket. Every value is also in the table view.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as Plot from '@observablehq/plot';

import type { OversightGrowthPoint } from '../../services/api/oversight';
import { formatDate } from '../../utils/relativeTime';

const ACCENT = 'hsl(var(--info))';
const INK = 'hsl(var(--foreground))';
const PLOT_H = 84;
const MARGIN_L = 44;
const MARGIN_R = 56;

/** A bucket's start in the one date format: "Aug 29, 2026", or its month. */
export const bucketDate = (unit: string, start: string): string => {
  if (unit !== 'month') return formatDate(start.slice(0, 10));
  const [y, m] = start.slice(0, 7).split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
};

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

type Unit = 'day' | 'week' | 'month';

interface Row { date: Date; value: number; index: number }

const asDate = (start: string) => new Date(`${start.slice(0, 10)}T00:00:00Z`);

/** Whole-number y ticks: 0, a round top at or above the data, and its half
 *  when that is whole — counts never get a "0.5" tick. */
const countTicks = (values: number[]): { domain: [number, number]; ticks: number[] } => {
  const max = Math.max(1, ...values);
  const p = 10 ** Math.floor(Math.log10(max));
  const f = max / p;
  const top = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  return { domain: [0, top], ticks: top % 2 === 0 ? [0, top / 2, top] : [0, top] };
};

const unitLabel = (unit: string, start: string) =>
  unit === 'week' ? `Week of ${bucketDate(unit, start)}` : bucketDate(unit, start);

/** One Plot figure, redrawn when its options change; reports its x scale so
 *  the pointer can be mapped to a bucket. */
const Figure: React.FC<{
  options: Plot.PlotOptions;
  onScale: (invert: ((px: number) => Date) | null) => void;
  label: string;
}> = ({ options, onScale, label }) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current;
    if (!host) return undefined;
    const figure = Plot.plot(options);
    figure.setAttribute('role', 'img');
    figure.setAttribute('aria-label', label);
    host.replaceChildren(figure);
    const x = figure.scale('x');
    onScale(x?.invert ? (px: number) => x.invert!(px) as Date : null);
    return () => figure.remove();
  }, [options, onScale, label]);
  return <div ref={ref} className="min-w-0 [&_svg]:block [&_svg]:overflow-visible" />;
};

export const GrowthCharts: React.FC<{ unit: Unit | string; points: OversightGrowthPoint[] }> = ({ unit, points }) => {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const invertRef = useRef<((px: number) => Date) | null>(null);
  const onScale = useMemo(() => (inv: ((px: number) => Date) | null) => { invertRef.current = inv; }, []);
  const focus = hover ?? points.length - 1;
  const p = points[focus];
  const interval = (unit === 'day' || unit === 'week' || unit === 'month' ? unit : 'day') as Unit;

  const series = useMemo(() => {
    const rows = (value: (q: OversightGrowthPoint) => number): Row[] =>
      points.map((q, index) => ({ date: asDate(q.start), value: value(q), index }));
    return {
      total: rows((q) => q.cumulative_targets),
      added: rows((q) => q.targets_added),
      reviews: rows((q) => q.reviews_concluded),
    };
  }, [points]);

  // The x domain covers the last bucket whole, so its column is not clipped.
  const xDomain = useMemo((): [Date, Date] | null => {
    if (!points.length) return null;
    const first = asDate(points[0].start);
    const last = asDate(points[points.length - 1].start);
    const end = new Date(last);
    if (interval === 'month') end.setUTCMonth(end.getUTCMonth() + 1);
    else end.setUTCDate(end.getUTCDate() + (interval === 'week' ? 7 : 1));
    return [first, end];
  }, [points, interval]);

  const build = useMemo(() => (rows: Row[], kind: 'line' | 'columns', showAxis: boolean): Plot.PlotOptions => {
    const hovered = hover == null ? null : rows[hover];
    const y = countTicks(rows.map((r) => r.value));
    const mid = (d: Date) => {
      // A bucket's centre, where its column and the crosshair sit.
      const e = new Date(d);
      if (interval === 'month') e.setUTCDate(15);
      else if (interval === 'week') e.setUTCDate(e.getUTCDate() + 3.5);
      else e.setUTCHours(12);
      return e;
    };
    const marks: Plot.Markish[] = [
      Plot.gridX({ stroke: 'currentColor', strokeOpacity: 0.12 }),
      Plot.gridY(y.ticks, { stroke: 'currentColor', strokeOpacity: 0.12 }),
      Plot.ruleY([0], { stroke: 'currentColor', strokeOpacity: 0.35 }),
    ];
    if (kind === 'line') {
      marks.push(
        Plot.areaY(rows, { x: (r: Row) => mid(r.date), y: 'value', fill: ACCENT, fillOpacity: 0.1, curve: 'monotone-x' }),
        Plot.lineY(rows, { x: (r: Row) => mid(r.date), y: 'value', stroke: ACCENT, strokeWidth: 2, curve: 'monotone-x' }),
        Plot.dot(rows, Plot.selectLast({ x: (r: Row) => mid(r.date), y: 'value', r: 4, fill: ACCENT, stroke: 'hsl(var(--background))', strokeWidth: 2 })),
        Plot.text(rows, Plot.selectLast({
          x: (r: Row) => mid(r.date), y: 'value', text: (r: Row) => r.value.toLocaleString(),
          dx: 10, textAnchor: 'start', fill: INK, fontWeight: 600,
        })),
      );
    } else {
      marks.push(
        Plot.rectY(rows.filter((r) => r.value > 0), {
          x: 'date', interval, y: 'value', fill: ACCENT, inset: 1, rx: 3,
          fillOpacity: (r: Row) => (hover == null || hover === r.index ? 1 : 0.55),
        }),
        // The last bucket's value, beside its column (the one value worth
        // reading without hovering).
        Plot.text(rows, Plot.selectLast({
          x: (r: Row) => mid(r.date), y: 'value', text: (r: Row) => r.value.toLocaleString(),
          dx: 14, textAnchor: 'start', fill: INK, fontWeight: 600,
        })),
      );
    }
    if (hovered) marks.push(Plot.ruleX([mid(hovered.date)], { stroke: INK, strokeOpacity: 0.5 }));
    return {
      width,
      height: PLOT_H + (showAxis ? 36 : 4),
      marginLeft: MARGIN_L,
      marginRight: MARGIN_R,
      marginTop: 8,
      // Room for Plot's two-line date ticks (day, then month under the first of each).
      marginBottom: showAxis ? 36 : 4,
      style: { background: 'transparent', color: 'hsl(var(--muted-foreground))', fontSize: '11px', fontFamily: 'inherit', overflow: 'visible' },
      x: { type: 'utc', domain: xDomain ?? undefined, axis: showAxis ? 'bottom' : null, tickSize: 0, tickPadding: 6, label: null },
      y: { domain: y.domain, ticks: y.ticks, tickSize: 0, tickFormat: (v: number) => v.toLocaleString(), label: null },
      marks,
    };
  }, [hover, width, interval, xDomain]);

  const optsTotal = useMemo(() => build(series.total, 'line', false), [build, series]);
  const optsAdded = useMemo(() => build(series.added, 'columns', false), [build, series]);
  const optsReviews = useMemo(() => build(series.reviews, 'columns', true), [build, series]);

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const invert = invertRef.current;
    if (!invert || !points.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < MARGIN_L || px > width - MARGIN_R) { setHover(null); return; }
    const t = invert(px).getTime();
    // The bucket whose start is the last one at or before the pointer.
    let i = 0;
    while (i + 1 < points.length && asDate(points[i + 1].start).getTime() <= t) i += 1;
    setHover(i);
  };

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

  const last = points[points.length - 1];
  return (
    <div ref={ref} className="min-w-0 space-y-sm">
      <p className="text-caption text-muted-foreground" aria-live="polite" id="growth-readout">
        <span className="font-medium text-foreground">{unitLabel(unit, p.start)}</span>
        {' · '}<span className="font-semibold text-foreground tabular-nums">{p.cumulative_targets.toLocaleString()}</span> recorded {p.cumulative_targets === 1 ? 'host' : 'hosts'}
        {' · '}<span className="font-semibold text-foreground tabular-nums">+{p.targets_added.toLocaleString()}</span> first recorded
        {' · '}<span className="font-semibold text-foreground tabular-nums">{p.reviews_concluded.toLocaleString()}</span> {p.reviews_concluded === 1 ? 'review' : 'reviews'} concluded
        {hover == null && <span> (latest; hover or use ← → to move)</span>}
      </p>
      <div tabIndex={0} onKeyDown={onKey} onPointerMove={onPointerMove} onPointerLeave={() => setHover(null)}
        aria-describedby="growth-readout"
        aria-label="Host growth charts — use the left and right arrow keys to move between dates"
        className="space-y-xs rounded-control focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <p className="text-caption font-medium text-foreground">Recorded hosts (cumulative)</p>
        <Figure options={optsTotal} onScale={onScale}
          label={`Recorded hosts (cumulative): ${last.cumulative_targets.toLocaleString()} in the last bucket`} />
        <p className="text-caption font-medium text-foreground">Hosts first recorded per {unit}</p>
        <Figure options={optsAdded} onScale={onScale}
          label={`Hosts first recorded per ${unit}: ${last.targets_added.toLocaleString()} in the last bucket`} />
        <p className="text-caption font-medium text-foreground">Reviews concluded per {unit}</p>
        <Figure options={optsReviews} onScale={onScale}
          label={`Reviews concluded per ${unit}: ${last.reviews_concluded.toLocaleString()} in the last bucket`} />
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
