/**
 * The Tool Activity snapshot chart (screenshot review 2026-09-23): activities
 * counted per time bin, one row per kind, on one shared time axis.
 *
 * It replaced a dot-per-activity timeline (still used by Scans as
 * `ActivityTimeline`) whose overlapping dots stacked into lanes — a burst of
 * 60 uploads made a ~1100px column of dots over an otherwise empty week. Bins
 * keep the chart a fixed height (≤ ~190px) whatever the burst.
 *
 * Why rows and not stacked columns: the theme's only hues are its status
 * colours, and several palettes make them near-identical (magma's success /
 * warning / info are all orange; phosphor's success is its primary). A stack
 * would then encode kind by colour alone and fail. One row per kind in the
 * single info accent, named and counted at its left edge, reads in every
 * palette (the GrowthCharts precedent). Each row has its own y-scale, labelled.
 *
 * Hover or arrow keys move one crosshair across every row and the readout
 * above names every count in that bin; Enter or a click correlates that bin.
 * The table view lists every non-empty bin.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { ActivityItem, ActivityKind } from '../services/api';
import {
  ACTIVITY_KINDS,
  binActivity,
  chooseBinMs,
  kindCount,
  kindTotals,
  KIND_PLURAL,
  niceMax,
} from '../utils/activityBins';

const ACCENT = 'hsl(var(--info))';
const ROW_H = 24;          // plot height per kind
const ROW_GAP = 10;
const AXIS_H = 18;
const PAD_L = 168;         // kind label + y tick
const PAD_R = 8;
const BAR_MAX = 24;

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

  const plotW = Math.max(1, width - PAD_L - PAD_R);
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
  const span = Math.max(1, last - first);
  const xOf = (t: number) => PAD_L + ((t - first) / span) * plotW;
  const band = plotW / Math.max(1, n);
  const barW = Math.max(1, Math.min(BAR_MAX, band - 2)); // 2px gap between columns

  const rows = present.length;
  const plotH = rows * ROW_H + Math.max(0, rows - 1) * ROW_GAP;
  const svgH = plotH + AXIS_H;

  // Day ticks at local midnight inside the window.
  const dayTicks = useMemo(() => {
    const ticks: number[] = [];
    const d = new Date(first);
    d.setHours(24, 0, 0, 0);
    while (d.getTime() < last) {
      ticks.push(d.getTime());
      d.setDate(d.getDate() + 1);
    }
    return ticks;
  }, [first, last]);

  let highlight: { x: number; w: number } | null = null;
  if (highlightStart && highlightEnd) {
    const hs = new Date(highlightStart).getTime();
    const he = new Date(highlightEnd).getTime();
    if (Number.isFinite(hs) && Number.isFinite(he) && he >= first && hs <= last) {
      const x0 = xOf(Math.max(hs, first));
      const x1 = xOf(Math.min(he, last));
      highlight = { x: x0, w: Math.max(2, x1 - x0) };
    }
  }

  const binAt = (clientX: number, rect: DOMRect) => {
    const px = ((clientX - rect.left) / rect.width) * plotW;
    return Math.min(n - 1, Math.max(0, Math.floor(px / band)));
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

      {rows === 0 ? (
        <p className="text-metadata text-muted-foreground">No activity in this window.</p>
      ) : (
        <div
          tabIndex={0}
          onKeyDown={onKey}
          aria-describedby="activity-readout"
          aria-label="Activity per time bin — use the left and right arrow keys to move, Enter to correlate a bin"
          className="rounded-control focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <svg
            width={width}
            height={svgH}
            role="img"
            aria-label={`Activities started per ${binWidthLabel(binMs)}: ${present.map((k) => kindCount(k, totals[k])).join(', ')}`}
            className="block overflow-visible"
            data-testid="activity-histogram"
          >
            {highlight && (
              <rect
                x={highlight.x}
                y={0}
                width={highlight.w}
                height={plotH}
                fill="hsl(var(--primary) / 0.14)"
                stroke="hsl(var(--primary) / 0.45)"
                data-testid="activity-focus-band"
              />
            )}
            {dayTicks.map((t) => (
              <line key={t} x1={xOf(t)} x2={xOf(t)} y1={0} y2={plotH} stroke="hsl(var(--border))" />
            ))}
            {present.map((kind: ActivityKind, r) => {
              const top = r * (ROW_H + ROW_GAP);
              const base = top + ROW_H;
              const max = niceMax(Math.max(...bins.map((b) => b.counts[kind])));
              return (
                <g key={kind} data-kind={kind}>
                  <text x={0} y={top + ROW_H / 2} dominantBaseline="middle" className="fill-foreground text-[12px]">
                    {KIND_PLURAL[kind][1].replace(/^./, (c) => c.toUpperCase())}
                    <tspan className="fill-muted-foreground tabular-nums"> {totals[kind].toLocaleString()}</tspan>
                  </text>
                  <text x={PAD_L - 6} y={top} dominantBaseline="hanging" textAnchor="end" className="fill-muted-foreground text-[10px] tabular-nums">
                    {max}
                  </text>
                  <text x={PAD_L - 6} y={base} textAnchor="end" className="fill-muted-foreground text-[10px] tabular-nums">0</text>
                  <line x1={PAD_L} x2={PAD_L + plotW} y1={base + 0.5} y2={base + 0.5} stroke="hsl(var(--muted-foreground) / 0.35)" />
                  {bins.map((b, i) => {
                    const v = b.counts[kind];
                    if (v <= 0) return null;
                    const h = Math.max(2, (v / max) * ROW_H);
                    const y0 = base - h;
                    const x0 = PAD_L + band * i + (band - barW) / 2;
                    const rr = Math.min(4, h, barW / 2);
                    // Rounded data-end, square at the baseline.
                    const d = `M${x0},${base} V${y0 + rr} Q${x0},${y0} ${x0 + rr},${y0} H${x0 + barW - rr} Q${x0 + barW},${y0} ${x0 + barW},${y0 + rr} V${base} Z`;
                    return (
                      <path key={b.start} d={d} fill={ACCENT} fillOpacity={hover == null || hover === i ? 1 : 0.5} />
                    );
                  })}
                </g>
              );
            })}
            {hover != null && n > 0 && (
              <line
                x1={PAD_L + band * hover + band / 2}
                x2={PAD_L + band * hover + band / 2}
                y1={0}
                y2={plotH}
                stroke="hsl(var(--foreground) / 0.5)"
              />
            )}
            {dayTicks.map((t) => (
              <text key={`l${t}`} x={xOf(t) + 3} y={plotH + 13} className="fill-muted-foreground text-[11px]">
                {new Date(t).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })}
              </text>
            ))}
            <rect
              x={PAD_L}
              y={0}
              width={plotW}
              height={plotH}
              fill="transparent"
              className={onSelectBin ? 'cursor-pointer' : undefined}
              data-testid="activity-hit-area"
              onPointerMove={(e) => setHover(binAt(e.clientX, e.currentTarget.getBoundingClientRect()))}
              onPointerLeave={() => setHover(null)}
              onClick={(e) => select(binAt(e.clientX, e.currentTarget.getBoundingClientRect()))}
            />
          </svg>
        </div>
      )}

      <p className="break-words text-caption text-muted-foreground">
        Counted by start time, per {binWidthLabel(binMs)}; each row has its own scale.
        {absent.length > 0 && rows > 0 && <> None in this window: {absent.map((k) => KIND_PLURAL[k][1]).join(', ')}.</>}{' '}
        {rows > 0 && (
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

      {showTable && rows > 0 && (
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
