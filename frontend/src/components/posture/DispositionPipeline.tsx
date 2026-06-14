/**
 * Finding disposition as a lifecycle pipeline: one horizontal bar of findings
 * flowing through their statuses, split ACTIVE | RESOLVED (active warm,
 * resolved cool), segment width ∝ count. Fully static — counts sit inside wide
 * segments and in the legend, so nothing is hidden behind a hover (an earlier
 * hover-reveal exposed an unlabelled severity bar that vanished on mouse-out;
 * removed). Severity lives in the "Active findings" headline card instead.
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  STATUS_HSL, STATUS_LABEL, ACTIVE_STATUSES, RESOLVED_STATUSES,
} from './postureTheme';

interface DispositionPipelineProps {
  byStatus: Record<string, number>;
  /** Drill-down for a status segment/legend item (§26); null = no link. */
  statusHref?: (status: string) => string | null;
}

const DispositionPipeline: React.FC<DispositionPipelineProps> = ({ byStatus, statusHref }) => {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const active = ACTIVE_STATUSES.filter((s) => byStatus[s]);
  const resolved = RESOLVED_STATUSES.filter((s) => byStatus[s]);
  const ordered = [...active, ...resolved];
  const total = ordered.reduce((sum, s) => sum + byStatus[s], 0);
  const activeTotal = active.reduce((sum, s) => sum + byStatus[s], 0);
  const resolvedTotal = resolved.reduce((sum, s) => sum + byStatus[s], 0);

  if (total === 0) {
    return <p className="text-caption text-muted-foreground">No findings recorded yet.</p>;
  }

  const seg = (status: string, isFirst: boolean, isLast: boolean) => {
    const n = byStatus[status];
    const widthPct = mounted ? (n / total) * 100 : 0;
    const wide = (n / total) > 0.1;
    const href = statusHref?.(status) ?? null;
    const cls = `flex h-full items-center justify-center overflow-hidden ${isFirst ? 'rounded-l-full' : ''} ${isLast ? 'rounded-r-full' : ''}`;
    const style: React.CSSProperties = {
      width: `${widthPct}%`,
      background: STATUS_HSL[status] ?? 'hsl(var(--muted))',
      transition: 'width 700ms cubic-bezier(0.22,1,0.36,1)',
    };
    const inner = wide ? (
      <span className="truncate px-xxs text-[0.65rem] font-semibold text-white"
        style={{ textShadow: '0 1px 2px rgba(0,0,0,0.45)' }}>
        {n}
      </span>
    ) : null;
    const label = `${STATUS_LABEL[status] ?? status}: ${n}`;
    return href ? (
      <Link key={status} to={href} title={`${label} — view`} aria-label={`${label} — view`}
        className={cls} style={style}>
        {inner}
      </Link>
    ) : (
      <div key={status} title={label} className={cls} style={style}>
        {inner}
      </div>
    );
  };

  return (
    <div className="space-y-sm">
      <div className="flex items-center justify-between text-caption">
        <span className="font-semibold text-foreground">Active · {activeTotal}</span>
        <span className="text-muted-foreground">Resolved · {resolvedTotal}</span>
      </div>

      <div className="flex h-9 w-full overflow-hidden rounded-full bg-muted"
        role="img" aria-label={ordered.map((s) => `${byStatus[s]} ${STATUS_LABEL[s] ?? s}`).join(', ')}>
        {active.map((s, i) => seg(s, i === 0, resolved.length === 0 && i === active.length - 1))}
        {active.length > 0 && resolved.length > 0 && (
          <div className="h-full w-0.5 shrink-0 bg-background" aria-hidden />
        )}
        {resolved.map((s, i) => seg(s, active.length === 0 && i === 0, i === resolved.length - 1))}
      </div>

      {/* Static legend with counts — everything visible, nothing hover-gated. */}
      <div className="flex flex-wrap gap-x-md gap-y-xxs">
        {ordered.map((s) => {
          const href = statusHref?.(s) ?? null;
          const body = (
            <>
              <span className="size-2 rounded-full" style={{ background: STATUS_HSL[s] }} aria-hidden />
              <span className="font-medium text-foreground">{byStatus[s]}</span> {STATUS_LABEL[s] ?? s}
            </>
          );
          return href ? (
            <Link key={s} to={href}
              className="inline-flex items-center gap-xxs text-caption text-muted-foreground hover:text-foreground hover:underline">
              {body}
            </Link>
          ) : (
            <span key={s} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
              {body}
            </span>
          );
        })}
      </div>
    </div>
  );
};

export default DispositionPipeline;
