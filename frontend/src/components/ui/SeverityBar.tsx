/**
 * SeverityBar — the shared severity rail inside a card or a cell. Two variants
 * (a page section's distribution — rail, count · % row, links — is
 * `DistributionBar`, which took over the `summary` variant in 5.414.0 so that
 * Posture's severity and status splits are one design):
 *
 *   inline   — 12px rail + a dot/count legend. For inside cards.
 *   compact  — ~10px rail, optional adjacent total, no legend. For table cells.
 *
 * Fixed order critical→high→medium→low (informational is excluded from every
 * severity visual — see VISIBLE_SEVERITIES below); canonical theme tokens; outer
 * edges rounded (not every segment); 1px separators. No gradient/glow — the semantic
 * colours are already strong. Zero categories read as quiet text, never a
 * coloured sliver.
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type Severity, SEVERITY_ORDER, SEVERITY_LABEL, SEVERITY_HSL,
} from '../../utils/severity';

type Variant = 'inline' | 'compact';

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
   * should open, or null for no link. When provided, rail segments render as
   * <Link>s; otherwise behaviour is unchanged.
   */
  segmentHref?: (severity: Severity) => string | null;
}

// Severities rendered in every visual. Informational is deliberately omitted:
// scanner "info" findings outnumber real ones by orders of magnitude and turn
// any distribution into a wall of grey. Callers pass full counts (incl. info);
// the bar simply never draws/counts it.
const VISIBLE_SEVERITIES: Severity[] = SEVERITY_ORDER.filter((k) => k !== 'info');

const HEIGHTS: Record<Variant, number> = { inline: 12, compact: 10 };

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
    >
      {sum === 0 ? null : present.map((k, i) => {
        const n = counts[k] ?? 0;
        const share = n / sum;
        const dim = active != null && active !== k;
        const href = segmentHref?.(k) ?? null;
        const segStyle: React.CSSProperties = {
          width: `${(revealed ? share : 0) * 100}%`,
          background: SEVERITY_HSL[k],
          opacity: dim ? 0.4 : 1,
          transition: 'width 600ms cubic-bezier(0.22,1,0.36,1), opacity 150ms',
        };
        const segClass = `flex items-center justify-center ${i > 0 ? 'border-l border-background' : ''}`;
        const hover = {
          onMouseEnter: href ? () => setActive(k) : undefined,
          onMouseLeave: href ? () => setActive(null) : undefined,
        };
        if (href) {
          return (
            <Link key={k} to={href} {...hover}
              aria-label={`${n.toLocaleString()} ${SEVERITY_LABEL[k]} — view`}
              title={`${SEVERITY_LABEL[k]}: ${n.toLocaleString()} — view`}
              className={segClass} style={segStyle} />
          );
        }
        return (
          <div key={k} {...hover}
            title={`${SEVERITY_LABEL[k]}: ${n.toLocaleString()}`}
            className={segClass} style={segStyle} />
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
};

export default SeverityBar;
