/**
 * Horizontal timeline for the /tool-activity surface.
 *
 * Plots every ActivityItem across the queried window (window_start →
 * window_end) so the analyst sees scanning + agent activity laid out
 * in time, not just listed in a table.  Each row's kind drives the
 * marker colour; items with a recorded end_time render as a bar
 * spanning [start, end]; items without get a dot at start_time.
 *
 * Overlapping markers stack into lanes — same lane-packing trick the
 * old Scans page used, generalised for cross-kind, cross-project
 * data and re-anchored on scan_start instead of upload time (the
 * SOC use case wants when-the-tool-ran, not when-the-file-arrived).
 *
 * Marker click navigates to the kind-specific detail route via the
 * onItemClick callback so the parent page can route + log.
 */

import React from 'react';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from '../utils/cn';
import { ActivityItem, ActivityKind } from '../services/api';

const LANE_HEIGHT_PX = 16;
const TIMELINE_INNER_HEIGHT = 36;
const TIMELINE_PADDING_TOP = 24;
// 2% of the visible window — keeps markers visually distinct at any
// zoom level while preventing 100 hosts in the same minute from
// stacking into 100 lanes.
const LANE_PACK_THRESHOLD_PERCENT = 4;
const MIN_BAR_WIDTH_PERCENT = 0.4;

function dotClass(kind: ActivityKind): string {
  switch (kind) {
    case 'scan':
      return 'bg-info';
    case 'recon_session':
      return 'bg-warning';
    case 'execution_session':
      return 'bg-success';
    default:
      return 'bg-primary';
  }
}

function fmt(iso: string | Date | null | undefined): string {
  if (!iso) return 'Unknown';
  const d = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Unknown';
  return d.toLocaleString();
}

function durationLabel(start: string, end: string | null): string {
  if (!end) return '—';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms <= 0) return 'Instant';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export interface ActivityTimelineProps {
  items: ActivityItem[];
  /** Echoed from the API; used as the timeline X-axis range. */
  windowStart: string;
  windowEnd: string;
  /** Triggered when a marker is clicked.  Navigation lives in the parent. */
  onItemClick: (item: ActivityItem) => void;
  /** Optional heading shown above the timeline.  Defaults to "Activity Timeline". */
  title?: string;
  /** Optional helper copy.  Callers wrap their own text element. */
  helperText?: React.ReactNode;
  /**
   * Optional highlighted band rendered as an overlay across the
   * timeline — used by the /tool-activity page to show where the
   * user's `±tolerance` focus window falls inside a wider snapshot
   * (e.g. the past-7-days view).  The band is clipped to the
   * visible window: if the highlight is entirely outside
   * [windowStart, windowEnd] it doesn't render.
   */
  highlightStart?: string | null;
  highlightEnd?: string | null;
}

interface PositionedItem {
  item: ActivityItem;
  /** Percent across the window of the start_time. */
  startPct: number;
  /** Percent across the window of the effective end (NULL → start). */
  endPct: number;
  /** Lane index assigned by the pack algorithm; 0 = topmost. */
  lane: number;
}

export const ActivityTimeline: React.FC<ActivityTimelineProps> = ({
  items,
  windowStart,
  windowEnd,
  onItemClick,
  title = 'Activity Timeline',
  helperText,
  highlightStart,
  highlightEnd,
}) => {
  const placement = React.useMemo(() => {
    const startMs = new Date(windowStart).getTime();
    const endMs = new Date(windowEnd).getTime();
    const span = Math.max(endMs - startMs, 1);

    const positionPercent = (iso: string | null | undefined): number => {
      if (!iso) return 0;
      const t = new Date(iso).getTime();
      if (Number.isNaN(t)) return 0;
      return Math.max(0, Math.min(100, ((t - startMs) / span) * 100));
    };

    const sorted = [...items].sort(
      (a, b) =>
        new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
    );

    // Lane packing: walk start-sorted, place each item in the topmost
    // lane whose last item's `endPct` finishes early enough that this
    // item's `startPct` won't overlap visually.
    const laneEnds: number[] = [];
    const positioned: PositionedItem[] = sorted.map((it) => {
      const startPct = positionPercent(it.start_time);
      const effectiveEnd = it.end_time ?? it.start_time;
      const endPct = Math.max(startPct, positionPercent(effectiveEnd));
      const minEdge = startPct - LANE_PACK_THRESHOLD_PERCENT;
      let lane = laneEnds.findIndex((last) => last <= minEdge);
      if (lane === -1) lane = laneEnds.length;
      laneEnds[lane] = endPct;
      return { item: it, startPct, endPct, lane };
    });

    // Compute the highlight band's percent range (if any).  Clipped to
    // [0, 100] so partial-overlap bands render flush with the timeline
    // edge.  Falsy when the highlight is entirely outside the visible
    // window.
    let highlight: { startPct: number; endPct: number } | null = null;
    if (highlightStart && highlightEnd) {
      const hStartMs = new Date(highlightStart).getTime();
      const hEndMs = new Date(highlightEnd).getTime();
      if (!Number.isNaN(hStartMs) && !Number.isNaN(hEndMs)) {
        // Outside the visible window in either direction → don't render.
        if (hEndMs >= startMs && hStartMs <= endMs) {
          const startPct = Math.max(0, ((hStartMs - startMs) / span) * 100);
          const endPct = Math.min(100, ((hEndMs - startMs) / span) * 100);
          if (endPct > startPct) {
            highlight = { startPct, endPct };
          }
        }
      }
    }

    return {
      positioned,
      laneCount: laneEnds.length || 1,
      highlight,
    };
  }, [items, windowStart, windowEnd, highlightStart, highlightEnd]);

  const trackTop =
    TIMELINE_PADDING_TOP + Math.max((placement.laneCount - 1) * LANE_HEIGHT_PX, 0);
  const containerHeight =
    TIMELINE_INNER_HEIGHT + 16 + Math.max((placement.laneCount - 1) * LANE_HEIGHT_PX, 0);

  return (
    <div className="rounded-control border border-border bg-muted/30 p-md">
      <h3 className="text-subheading font-semibold">{title}</h3>
      {helperText ?? (
        <p className="text-metadata text-muted-foreground">
          Markers are placed at each scan&apos;s recorded <code>start_time</code>;
          bars span to <code>end_time</code> where known. Items without a
          recorded end render as dots at start. Click any marker for the
          scan / session detail.
        </p>
      )}
      <div className="relative mb-sm mt-md" style={{ height: containerHeight }}>
        {/* Highlight band — drawn FIRST so markers render on top of it.
            Spans the full visual height of the track so it reads as a
            background brush, not a chip in the markers' rows. */}
        {placement.highlight && (
          <div
            className="absolute top-0 rounded-control border border-primary/40 bg-primary/15"
            style={{
              left: `${placement.highlight.startPct}%`,
              width: `${Math.max(
                placement.highlight.endPct - placement.highlight.startPct,
                0.25,
              )}%`,
              height: containerHeight,
              pointerEvents: 'none',
            }}
            aria-hidden
          />
        )}
        {/* Baseline */}
        <div
          className="absolute left-0 right-0 h-px bg-border"
          style={{ top: trackTop }}
        />
        {placement.positioned.length === 0 && (
          <p
            className="absolute left-0 right-0 text-center text-caption text-muted-foreground"
            style={{ top: trackTop - 8 }}
          >
            No activity in this window.
          </p>
        )}
        {placement.positioned.map(({ item, startPct, endPct, lane }) => {
          const widthPct = Math.max(endPct - startPct, MIN_BAR_WIDTH_PERCENT);
          const isBar = item.has_end_time && endPct - startPct >= MIN_BAR_WIDTH_PERCENT;
          const colour = dotClass(item.kind);
          return (
            <Tooltip key={`${item.kind}-${item.ref_id}`}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => onItemClick(item)}
                  className="absolute focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  style={{
                    top: lane * LANE_HEIGHT_PX,
                    left: `${startPct}%`,
                    width: isBar ? `${widthPct}%` : '12px',
                    height: 12,
                    transform: isBar ? undefined : 'translateX(-50%)',
                  }}
                  aria-label={`${item.kind} ${item.label}`}
                >
                  <span
                    className={cn(
                      'block size-3 rounded-full border-2 border-card shadow-md transition-transform hover:scale-125',
                      colour,
                      isBar && 'rounded-control w-full',
                    )}
                    style={isBar ? { height: 8, marginTop: 2 } : undefined}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent>
                <p className="text-metadata font-semibold">{item.label}</p>
                <p className="text-caption text-muted-foreground">
                  Project: {item.project_name}
                </p>
                {/* v2.61.0 — explicit signal from the backend; see
                    ActivityItem.start_time_is_fallback. */}
                {item.start_time_is_fallback && (
                  <p className="text-caption text-warning">
                    No scanner start_time recorded — anchor is the
                    BlueStick upload time.
                  </p>
                )}
                <p className="text-caption">Start: {fmt(item.start_time)}</p>
                <p className="text-caption">
                  End: {item.has_end_time ? fmt(item.end_time) : 'no end_time recorded'}
                </p>
                {item.has_end_time && (
                  <p className="text-caption">
                    Duration: {durationLabel(item.start_time, item.end_time)}
                  </p>
                )}
                {item.host_count != null && (
                  <p className="text-caption">Hosts: {item.host_count}</p>
                )}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
      <div className="flex justify-between text-caption text-muted-foreground">
        <span>{fmt(windowStart)}</span>
        <span>{fmt(windowEnd)}</span>
      </div>
      {/* Legend */}
      <div className="mt-sm flex flex-wrap items-center gap-md text-metadata text-muted-foreground">
        <span className="flex items-center gap-xs">
          <span className="inline-block size-3 rounded-full bg-info" />
          Scan
        </span>
        <span className="flex items-center gap-xs">
          <span className="inline-block size-3 rounded-full bg-warning" />
          Recon session
        </span>
        <span className="flex items-center gap-xs">
          <span className="inline-block size-3 rounded-full bg-success" />
          Execution session
        </span>
      </div>
    </div>
  );
};

export default ActivityTimeline;
