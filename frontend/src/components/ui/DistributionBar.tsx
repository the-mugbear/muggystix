/**
 * DistributionBar — how one population splits into parts, as a page section
 * shows it (5.414.0): a heading row (what is split, and the whole), ONE rail,
 * and a row of the parts with their count · share.  Posture's "Scanner
 * observations by severity" and "Promoted findings" by status are both this
 * component: they sat one under the other as a 24px rail with counts inside,
 * its legend below, and a 12px full-width rail with its legend above — two
 * designs for one idea (owner, 2026-10-21: "incongruent").
 *
 * The rail carries no numbers: the row under it holds each count once.  A part
 * of zero is listed quietly and never drawn as a sliver.  Pointing at a part
 * (rail or row) dims the others.  `groupStart` puts a gap before a part, for
 * parts that belong to different groups (findings under investigation |
 * confirmed | closed).
 */
import React, { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';

export interface DistributionPart {
  key: string;
  label: string;
  count: number;
  /** The part's colour, a CSS colour (a theme token). */
  color: string;
  /** The list the part opens; null = no link (a zero never links). */
  href?: string | null;
  /** What the link opens when it is not the count shown ("63 hosts" under 70
   *  observations), said on the link so a number never opens a list of
   *  another size unannounced. */
  opens?: string | null;
  /** A gap before this part: it starts another group. */
  groupStart?: boolean;
}

interface DistributionBarProps {
  /** The heading of the split ("Scanner observations by severity"). */
  title: React.ReactNode;
  /** The whole, on the right of the heading — text or a link. */
  summary?: React.ReactNode;
  parts: DistributionPart[];
  /** Read by assistive technology for the rail. */
  ariaLabel: string;
  className?: string;
}

/** The rail's height: one for every distribution on a page. */
export const DISTRIBUTION_RAIL_PX = 12;

function useReveal(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setOn(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return on;
}

const DistributionBar: React.FC<DistributionBarProps> = ({ title, summary, parts, ariaLabel, className }) => {
  const revealed = useReveal();
  const [active, setActive] = useState<string | null>(null);
  const rowId = useId();
  const total = parts.reduce((sum, p) => sum + p.count, 0);
  const drawn = parts.filter((p) => p.count > 0);

  const hover = (key: string) => ({
    onMouseEnter: () => setActive(key),
    onMouseLeave: () => setActive(null),
    onFocus: () => setActive(key),
    onBlur: () => setActive(null),
  });

  return (
    <div className={`max-w-3xl ${className ?? ''}`}>
      <div className="mb-xs flex flex-wrap items-baseline justify-between gap-x-md gap-y-xxs">
        <h3 className="inline-flex items-center gap-xxs text-metadata font-semibold text-foreground">{title}</h3>
        {summary != null && <span className="text-caption tabular-nums text-muted-foreground">{summary}</span>}
      </div>

      <div className="flex w-full overflow-hidden rounded-full bg-muted" style={{ height: DISTRIBUTION_RAIL_PX }}
        role="img" aria-label={ariaLabel} aria-describedby={rowId}>
        {drawn.map((p, i) => {
          const style: React.CSSProperties = {
            width: `${(revealed && total > 0 ? p.count / total : 0) * 100}%`,
            background: p.color,
            opacity: active != null && active !== p.key ? 0.4 : 1,
            transition: 'width 600ms cubic-bezier(0.22,1,0.36,1), opacity 150ms',
          };
          // A part after the first is set off by a hairline; a new group by a gap.
          const cls = i === 0 ? '' : p.groupStart ? 'border-l-2 border-background' : 'border-l border-background';
          const name = `${p.label}: ${p.count.toLocaleString()}`;
          return p.href ? (
            <Link key={p.key} to={p.href} {...hover(p.key)} className={cls} style={style}
              title={`${name} — view${p.opens ? ` ${p.opens}` : ''}`}
              aria-label={`${p.count.toLocaleString()} ${p.label} — view${p.opens ? ` ${p.opens}` : ''}`} />
          ) : (
            <div key={p.key} {...hover(p.key)} className={cls} style={style} title={name} />
          );
        })}
      </div>

      <div id={rowId} className="mt-sm grid grid-cols-2 gap-x-md gap-y-xs sm:grid-cols-3 lg:grid-cols-4">
        {parts.map((p) => {
          const zero = p.count === 0;
          const pct = total > 0 ? Math.round((p.count / total) * 100) : 0;
          const href = zero ? null : p.href ?? null;
          const body = (
            <>
              <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
                <span className="size-2 rounded-full" aria-hidden
                  style={{ background: zero ? 'hsl(var(--muted-foreground) / 0.4)' : p.color }} />
                {p.label}
              </span>
              {zero ? (
                <span className="text-metadata text-muted-foreground/60">0</span>
              ) : (
                <span className="text-metadata font-semibold tabular-nums text-foreground">
                  {p.count.toLocaleString()} <span className="font-normal text-muted-foreground">· {pct}%</span>
                </span>
              )}
              {href && p.opens && <span className="text-caption text-info">{p.opens} →</span>}
            </>
          );
          const cls = 'flex flex-col items-start rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
          const style: React.CSSProperties = {
            opacity: active != null && active !== p.key ? 0.45 : 1, transition: 'opacity 150ms',
          };
          return href ? (
            <Link key={p.key} to={href} {...hover(p.key)} className={`${cls} hover:underline`} style={style}
              aria-label={`${p.count.toLocaleString()} ${p.label} — view${p.opens ? ` ${p.opens}` : ''}`}>
              {body}
            </Link>
          ) : (
            <div key={p.key} {...hover(p.key)} className={cls} style={style}>{body}</div>
          );
        })}
      </div>
    </div>
  );
};

export default DistributionBar;
