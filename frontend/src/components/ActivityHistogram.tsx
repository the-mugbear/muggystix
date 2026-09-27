/**
 * The Tool Activity snapshot chart (screenshot review 2026-09-23): activities
 * counted per time bin, one row per kind, on one shared time axis.
 *
 * It replaced a dot-per-activity timeline (still used by Scans as
 * `ActivityTimeline`) whose overlapping dots stacked into lanes — a burst of
 * 60 uploads made a ~1100px column of dots over an otherwise empty week. Bins
 * keep the chart a fixed height (at most five rows) whatever the burst.
 *
 * Why rows and not stacked columns: the theme's only hues are its status
 * colours, and several palettes make them near-identical (magma's success /
 * warning / info are all orange; phosphor's success is its primary). A stack
 * would then encode kind by colour alone and fail. One row per kind in the
 * single info accent, named and counted above it, reads in every palette.
 * Each row has its own y-scale, labelled.
 *
 * Drawn with Observable Plot since 5.307.2 (UI_STYLE_GUIDE §35): a local-time
 * axis with a gridline at every midnight through all rows, columns spanning
 * exactly their bin, rows tall enough to read (they were 24px, ticks
 * crowding the bars). Hover or arrow keys move one crosshair across every row
 * and the readout above names every count in that bin; Enter or a click
 * correlates that bin. The table view lists every non-empty bin.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as Plot from '@observablehq/plot';

import type { ActivityItem, ActivityKind } from '../services/api';
import PlotFigure from './charts/PlotFigure';
import {
  ACTIVITY_KINDS,
  binActivity,
  chooseBinMs,
  kindCount,
  kindTotals,
  KIND_PLURAL,
  niceMax,
} from '../utils/activityBins';
import type { ActivityBin } from '../utils/activityBins';

const ACCENT = 'hsl(var(--info))';
const INK = 'hsl(var(--foreground))';
const ROW_H = 44;          // plot height per kind
const AXIS_H = 24;
const MARGIN_L = 32;       // the row's y ticks
const MARGIN_R = 8;

/** Container width, with a fallback where ResizeObserver is missing (tests). */
function useWidth(): [(el: HTMLDivElement | null) => void, number] {
  const [w, setW] = useState(900);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      if (width > 0) setW(Math.max(360, width));
    });
    ro.observe(el);
    observer.current = ro;
  }, []);
  useEffect(() => () => observer.current?.disconnect(), []);
  return [ref, w];
}

const binLabel = (start: number, end: number, binMs: number): string => {
  const s = new Date(start);
  const e = new Date(end);
  const day = s.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  if (binMs >= 24 * 3600 * 1000) return day;
  const hm = (d: Date) => d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  return `${day}, ${hm(s)}–${hm(e)}`;
};

const binWidthLabel = (binMs: number): string => {
  const h = binMs / 3600_000;
  return h >= 24 ? 'day' : h === 1 ? 'hour' : `${h} hours`;
};

const titleCase = (s: string) => s.replace(/^./, (c) => c.toUpperCase());

export interface ActivityHistogramProps {
  items: ActivityItem[];
  windowStart: string;
  windowEnd: string;
  /** The Correlate form's window, drawn as a band when it overlaps. */
  highlightStart?: string | null;
  highlightEnd?: string | null;
  /** A bin was chosen (click / Enter): correlate that range. */
  onSelectBin?: (startIso: string, endIso: string) => void;
}

export const ActivityHistogram: React.FC<ActivityHistogramProps> = ({
  items,
  windowStart,
  windowEnd,
  highlightStart,
  highlightEnd,
  onSelectBin,
}) => {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const invertRef = useRef<((px: number) => unknown) | null>(null);
  const onScale = useCallback((inv: ((px: number) => unknown) | null) => { invertRef.current = inv; }, []);

  const plotW = Math.max(1, width - MARGIN_L - MARGIN_R);
  const ws = new Date(windowStart).getTime();
  const we = new Date(windowEnd).getTime();
  const binMs = chooseBinMs(we - ws, plotW);
  const bins = useMemo(
    () => binActivity(items, windowStart, windowEnd, binMs),
    [items, windowStart, windowEnd, binMs],
  );
  const totals = useMemo(() => kindTotals(bins), [bins]);
  const present = ACTIVITY_KINDS.filter((k) => totals[k] > 0);
  const absent = ACTIVITY_KINDS.filter((k) => totals[k] === 0);

  const n = bins.length;
  const first = n ? bins[0].start : ws;
  const last = n ? bins[n - 1].end : we;

  // The Correlate window, clipped to the chart.
  const highlight = useMemo(() => {
    if (!highlightStart || !highlightEnd) return null;
    const hs = new Date(highlightStart).getTime();
    const he = new Date(highlightEnd).getTime();
    if (!Number.isFinite(hs) || !Number.isFinite(he) || he < first || hs > last) return null;
    // At least a visible sliver: ± 5 minutes on a week is under a pixel.
    const minW = (last - first) / Math.max(1, plotW) * 2;
    const x1 = Math.max(hs, first);
    const x2 = Math.max(Math.min(he, last), x1 + minW);
    return { x1: new Date(x1), x2: new Date(x2) };
  }, [highlightStart, highlightEnd, first, last, plotW]);

  const rowOptions = useMemo(() => present.map((kind, r): Plot.PlotOptions => {
    const showAxis = r === present.length - 1;
    const max = niceMax(Math.max(...bins.map((b) => b.counts[kind])));
    const marks: Plot.Markish[] = [];
    if (highlight) {
      marks.push(Plot.rect([highlight], {
        x1: 'x1', x2: 'x2', fill: 'hsl(var(--primary))', fillOpacity: 0.14,
        stroke: 'hsl(var(--primary))', strokeOpacity: 0.45, className: 'activity-focus-band',
      }));
    }
    marks.push(
      Plot.gridX({ ticks: 'day', stroke: 'currentColor', strokeOpacity: 0.14 }),
      Plot.ruleY([0], { stroke: 'currentColor', strokeOpacity: 0.35 }),
      Plot.rectY(bins.map((b, i) => ({ b, i })).filter(({ b }) => b.counts[kind] > 0), {
        x1: ({ b }: { b: ActivityBin }) => new Date(b.start),
        x2: ({ b }: { b: ActivityBin }) => new Date(b.end),
        y: ({ b }: { b: ActivityBin }) => b.counts[kind],
        // A column never thinner than 2px, with a 1px surface gap each side.
        insetLeft: 0.5, insetRight: 0.5,
        fill: ACCENT,
        fillOpacity: ({ i }: { i: number }) => (hover == null || hover === i ? 1 : 0.5),
      }),
    );
    if (hover != null && bins[hover]) {
      marks.push(Plot.ruleX([new Date((bins[hover].start + bins[hover].end) / 2)], { stroke: INK, strokeOpacity: 0.5 }));
    }
    return {
      width,
      height: ROW_H + (showAxis ? AXIS_H : 2),
      marginLeft: MARGIN_L,
      marginRight: MARGIN_R,
      marginTop: 4,
      marginBottom: showAxis ? AXIS_H : 2,
      style: { background: 'transparent', color: 'hsl(var(--muted-foreground))', fontSize: '11px', fontFamily: 'inherit', overflow: 'visible' },
      x: {
        type: 'time', domain: [new Date(first), new Date(last)], axis: showAxis ? 'bottom' : null,
        ticks: 'day', tickSize: 0, tickPadding: 6, label: null,
        tickFormat: (d: Date) => d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }),
      },
      y: { domain: [0, max], ticks: [0, max], tickSize: 0, label: null, tickFormat: (v: number) => v.toLocaleString() },
      marks,
    };
  }), [present, bins, highlight, hover, width, first, last]);

  const binAtPointer = (e: React.PointerEvent | React.MouseEvent): number | null => {
    const invert = invertRef.current;
    if (!invert || !n) return null;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < MARGIN_L || px > width - MARGIN_R) return null;
    const t = (invert(px) as Date).getTime();
    return Math.min(n - 1, Math.max(0, Math.floor((t - first) / binMs)));
  };
  const select = (i: number | null) => {
    if (i == null || !bins[i] || !onSelectBin) return;
    onSelectBin(new Date(bins[i].start).toISOString(), new Date(bins[i].end).toISOString());
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!n) return;
    const cur = hover ?? n - 1;
    if (e.key === 'ArrowRight') { setHover(Math.min(n - 1, cur + 1)); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { setHover(Math.max(0, cur - 1)); e.preventDefault(); }
    if (e.key === 'Enter' && hover != null) { select(hover); e.preventDefault(); }
    if (e.key === 'Escape') setHover(null);
  };

  const focus = hover != null ? bins[hover] : null;
  const busiest = bins.reduce<number | null>(
    (best, b, i) => (b.total > 0 && (best == null || b.total > bins[best].total) ? i : best),
    null,
  );

  return (
    <div ref={ref} className="min-w-0 space-y-xs">
      {/* Readout: the hovered bin, else the busiest one — values lead. */}
      <p className="min-h-[1.25rem] break-words text-caption text-muted-foreground" aria-live="polite" id="activity-readout">
        {focus ? (
          <>
            <span className="font-medium text-foreground">{binLabel(focus.start, focus.end, binMs)}</span>
            {' · '}
            {focus.total === 0
              ? 'nothing started'
              : present.map((k) => kindCount(k, focus.counts[k])).join(' · ')}
            {onSelectBin && focus.total > 0 && ' — click or press Enter to correlate this range'}
          </>
        ) : busiest != null ? (
          <>
            Busiest {binWidthLabel(binMs)}:{' '}
            <span className="font-medium text-foreground">
              {binLabel(bins[busiest].start, bins[busiest].end, binMs)}
            </span>{' '}
            · <span className="font-semibold tabular-nums text-foreground">{bins[busiest].total.toLocaleString()}</span> started
            {' '}(hover or use ← → to move)
          </>
        ) : null}
      </p>

      {present.length === 0 ? (
        <p className="text-metadata text-muted-foreground">No activity in this window.</p>
      ) : (
        <div
          tabIndex={0}
          onKeyDown={onKey}
          onPointerMove={(e) => setHover(binAtPointer(e))}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => select(binAtPointer(e))}
          aria-describedby="activity-readout"
          aria-label="Activity per time bin — use the left and right arrow keys to move, Enter to correlate a bin"
          data-testid="activity-histogram"
          className={`space-y-xxs rounded-control focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${onSelectBin ? 'cursor-pointer' : ''}`}
        >
          {present.map((kind: ActivityKind, r) => (
            <div key={kind} data-kind={kind} className="min-w-0">
              <p className="pl-[32px] text-caption text-foreground">
                {titleCase(KIND_PLURAL[kind][1])}{' '}
                <span className="tabular-nums text-muted-foreground">{totals[kind].toLocaleString()}</span>
              </p>
              <PlotFigure
                options={rowOptions[r]}
                onScale={r === 0 ? onScale : undefined}
                label={`${titleCase(KIND_PLURAL[kind][1])} started per ${binWidthLabel(binMs)}: ${totals[kind].toLocaleString()} in this window`}
              />
            </div>
          ))}
        </div>
      )}

      <p className="break-words text-caption text-muted-foreground">
        Counted by start time, per {binWidthLabel(binMs)}; each row has its own scale.
        {absent.length > 0 && present.length > 0 && <> None in this window: {absent.map((k) => KIND_PLURAL[k][1]).join(', ')}.</>}{' '}
        {present.length > 0 && (
          <button
            type="button"
            className="text-info hover:underline"
            onClick={() => setShowTable((s) => !s)}
            aria-expanded={showTable}
          >
            {showTable ? 'Hide table' : 'Show as table'}
          </button>
        )}
      </p>

      {showTable && present.length > 0 && (
        <div className="max-h-72 overflow-auto">
          <table className="w-full table-fixed text-caption">
            <caption className="sr-only">Activities started per {binWidthLabel(binMs)}</caption>
            <thead className="sticky top-0 bg-background text-muted-foreground">
              <tr>
                <th className="w-[34%] px-sm py-xxs text-left font-medium">{binMs >= 24 * 3600_000 ? 'Day' : 'From – to'}</th>
                {present.map((k) => (
                  <th key={k} className="truncate px-sm py-xxs text-right font-medium">{KIND_PLURAL[k][1]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bins.filter((b) => b.total > 0).map((b) => (
                <tr key={b.start} className="border-t border-border">
                  <td className="truncate px-sm py-xxs">{binLabel(b.start, b.end, binMs)}</td>
                  {present.map((k) => (
                    <td key={k} className="px-sm py-xxs text-right tabular-nums">{b.counts[k].toLocaleString()}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default ActivityHistogram;
