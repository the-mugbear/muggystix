/**
 * SeverityBar — the one shared severity-distribution rail, replacing the dated
 * thin-bar-plus-legend charts scattered across the app. Three variants:
 *
 *   summary  — 24px rail + a count·% summary row, interactive (hover/focus a
 *              severity emphasises its segment + row item and dims the rest).
 *              For page-level distributions (e.g. /operations exposure).
 *   inline   — 12px rail + a dot/count legend. For inside cards.
 *   compact  — ~10px rail, optional adjacent total, no legend. For table cells.
 *
 * Fixed order critical→high→medium→low (informational is excluded from every
 * severity visual — see VISIBLE_SEVERITIES below); canonical theme tokens; outer
 * edges rounded (not every segment); 1px separators; in-segment counts only
 * when a segment is wide enough to hold them. No gradient/glow — the semantic
 * colours are already strong. Zero categories read as quiet text, never a
 * coloured sliver.
 */
import React, { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type Severity, SEVERITY_ORDER, SEVERITY_LABEL, SEVERITY_HSL,
} from '../../utils/severity';

type Variant = 'summary' | 'inline' | 'compact';

interface SeverityBarProps {
  counts: Partial<Record<Severity, number>>;
  variant?: Variant;
  /** Override the denominator (else the sum of counts). */
  total?: number;
  /** compact only — render the total next to the rail. */
  showTotal?: boolean;
  className?: string;
  ariaLabel?: string;
  /**
   * Make each severity a drill-down (§26): return the in-app URL the segment
   * should open, or null for no link. When provided, rail segments and the
   * summary-row items render as <Link>s; otherwise behaviour is unchanged.
   */
  segmentHref?: (severity: Severity) => string | null;
}

// Severities rendered in every visual. Informational is deliberately omitted:
// scanner "info" findings outnumber real ones by orders of magnitude and turn
// any distribution into a wall of grey. Callers pass full counts (incl. info);
// the bar simply never draws/counts it.
const VISIBLE_SEVERITIES: Severity[] = SEVERITY_ORDER.filter((k) => k !== 'info');

const HEIGHTS: Record<Variant, number> = { summary: 24, inline: 12, compact: 10 };
// Min share before a count is drawn INSIDE its segment (else it'd clip).
const IN_SEGMENT_MIN = 0.12;

function useReveal(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setOn(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return on;
}

const SeverityBar: React.FC<SeverityBarProps> = ({
  counts, variant = 'inline', total, showTotal, className, ariaLabel, segmentHref,
}) => {
  const revealed = useReveal();
  const [active, setActive] = useState<Severity | null>(null);
  const titleId = useId();

  // Informational severity is excluded from every severity VISUAL: scanner
  // "info" findings vastly outnumber real ones and distort the distribution
  // (a bar that's 95% grey info tells you nothing). The denominator is the
  // non-info total so the shown segments fill the rail and percentages are of
  // actionable findings. (Filters/counts elsewhere may still use info.)
  const present = VISIBLE_SEVERITIES.filter((k) => (counts[k] ?? 0) > 0);
  const sum = VISIBLE_SEVERITIES.reduce((acc, k) => acc + (counts[k] ?? 0), 0);
  const denom = total ?? sum;
  const height = HEIGHTS[variant];
  const label = ariaLabel
    ?? (present.length
      ? present.map((k) => `${counts[k]} ${SEVERITY_LABEL[k]}`).join(', ')
      : 'no data');

  const rail = (
    <div
      className="flex w-full overflow-hidden rounded-full bg-muted"
      style={{ height }}
      role="img"
      aria-label={label}
      aria-describedby={variant === 'summary' ? titleId : undefined}
    >
      {sum === 0 ? null : present.map((k, i) => {
        const n = counts[k] ?? 0;
        const share = n / sum;
        const dim = active != null && active !== k;
        const showCount = variant === 'summary' && share >= IN_SEGMENT_MIN;
        const href = segmentHref?.(k) ?? null;
        const inner = showCount ? (
          <span className="truncate px-1 text-[0.7rem] font-semibold text-white"
            style={{ textShadow: '0 1px 2px rgba(0,0,0,0.45)' }}>
            {n.toLocaleString()}
          </span>
        ) : null;
        const segStyle: React.CSSProperties = {
          width: `${(revealed ? share : 0) * 100}%`,
          background: SEVERITY_HSL[k],
          opacity: dim ? 0.4 : 1,
          transition: 'width 600ms cubic-bezier(0.22,1,0.36,1), opacity 150ms',
        };
        const segClass = `flex items-center justify-center ${i > 0 ? 'border-l border-background' : ''}`;
        const hover = {
          onMouseEnter: variant === 'summary' || href ? () => setActive(k) : undefined,
          onMouseLeave: variant === 'summary' || href ? () => setActive(null) : undefined,
        };
        if (href) {
          return (
            <Link key={k} to={href} {...hover}
              aria-label={`${n.toLocaleString()} ${SEVERITY_LABEL[k]} — view`}
              title={`${SEVERITY_LABEL[k]}: ${n.toLocaleString()} — view`}
              className={segClass} style={segStyle}>
              {inner}
            </Link>
          );
        }
        return (
          <div key={k} {...hover}
            title={`${SEVERITY_LABEL[k]}: ${n.toLocaleString()}`}
            className={segClass} style={segStyle}>
            {inner}
          </div>
        );
      })}
    </div>
  );

  // --- compact: rail + optional adjacent total ---------------------------
  if (variant === 'compact') {
    if (!showTotal) return <div className={className}>{rail}</div>;
    return (
      <div className={`flex items-center gap-xs ${className ?? ''}`}>
        <div className="min-w-0 flex-1">{rail}</div>
        <span className="shrink-0 text-caption tabular-nums text-muted-foreground">
          {denom.toLocaleString()}
        </span>
      </div>
    );
  }

  // --- inline: rail + dot/count legend -----------------------------------
  if (variant === 'inline') {
    return (
      <div className={className}>
        {rail}
        <div className="mt-xs flex flex-wrap gap-x-md gap-y-xxs">
          {present.length === 0 ? (
            <span className="text-caption text-muted-foreground">No findings</span>
          ) : present.map((k) => (
            <span key={k} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
              <span className="size-2 rounded-full" style={{ background: SEVERITY_HSL[k] }} aria-hidden />
              {counts[k]?.toLocaleString()} {SEVERITY_LABEL[k]}
            </span>
          ))}
        </div>
      </div>
    );
  }

  // --- summary: rail + interactive count·% row ---------------------------
  return (
    <div className={className}>
      {rail}
      <div id={titleId} className="mt-sm grid grid-cols-2 gap-x-md gap-y-xs sm:grid-cols-3 lg:grid-cols-4">
        {VISIBLE_SEVERITIES.map((k) => {
          const n = counts[k] ?? 0;
          const pct = denom > 0 ? Math.round((n / denom) * 100) : 0;
          const zero = n === 0;
          const dim = active != null && active !== k;
          // Only link a non-zero severity that has a destination (clicking a
          // zero count would land on an empty filtered list).
          const href = !zero ? segmentHref?.(k) ?? null : null;
          const itemClass = 'flex flex-col items-start rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
          const itemStyle: React.CSSProperties = { opacity: dim ? 0.45 : 1, transition: 'opacity 150ms' };
          const itemBody = (
            <>
              <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
                <span className="size-2 rounded-full" aria-hidden
                  style={{ background: zero ? 'hsl(var(--muted-foreground) / 0.4)' : SEVERITY_HSL[k] }} />
                {SEVERITY_LABEL[k]}
              </span>
              {zero ? (
                <span className="text-metadata text-muted-foreground/60">0</span>
              ) : (
                <span className="text-metadata font-semibold tabular-nums text-foreground">
                  {n.toLocaleString()} <span className="font-normal text-muted-foreground">· {pct}%</span>
                </span>
              )}
            </>
          );
          const hoverHandlers = {
            onMouseEnter: () => setActive(k),
            onMouseLeave: () => setActive(null),
            onFocus: () => setActive(k),
            onBlur: () => setActive(null),
          };
          if (href) {
            return (
              <Link key={k} to={href} {...hoverHandlers}
                aria-label={`${n.toLocaleString()} ${SEVERITY_LABEL[k]} — view`}
                className={`${itemClass} hover:underline`} style={itemStyle}>
                {itemBody}
              </Link>
            );
          }
          return (
            <button type="button" key={k} {...hoverHandlers} className={itemClass} style={itemStyle}>
              {itemBody}
            </button>
          );
        })}
      </div>
    </div>
  );
};

export default SeverityBar;
