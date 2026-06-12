/**
 * Finding disposition as a lifecycle pipeline: one horizontal bar of findings
 * flowing through their statuses, split ACTIVE | RESOLVED. Segment width ∝
 * count; active states are warm, resolved cool. Hovering a segment reveals its
 * severity split below — keeps the bar clean while the detail stays one move
 * away. Replaces the old row-of-bars.
 */
import React, { useEffect, useState } from 'react';

import type { SeverityCounts } from '../../services/api';
import SeverityBar from '../ui/SeverityBar';
import {
  STATUS_HSL, STATUS_LABEL, ACTIVE_STATUSES, RESOLVED_STATUSES,
} from './postureTheme';

interface DispositionPipelineProps {
  byStatus: Record<string, number>;
  byStatusSeverity: Record<string, Partial<SeverityCounts>>;
}

const DispositionPipeline: React.FC<DispositionPipelineProps> = ({ byStatus, byStatusSeverity }) => {
  const [mounted, setMounted] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
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
    return (
      <div
        key={status}
        onMouseEnter={() => setHover(status)}
        onMouseLeave={() => setHover(null)}
        title={`${STATUS_LABEL[status] ?? status}: ${n}`}
        className={`flex h-full items-center justify-center overflow-hidden ${isFirst ? 'rounded-l-full' : ''} ${isLast ? 'rounded-r-full' : ''}`}
        style={{
          width: `${widthPct}%`,
          background: STATUS_HSL[status] ?? 'hsl(var(--muted))',
          opacity: hover && hover !== status ? 0.55 : 1,
          cursor: 'default',
          transition: 'width 700ms cubic-bezier(0.22,1,0.36,1), opacity 150ms',
        }}
      >
        {wide && (
          <span className="truncate px-xxs text-[0.65rem] font-semibold text-white"
            style={{ textShadow: '0 1px 2px rgba(0,0,0,0.45)' }}>
            {n}
          </span>
        )}
      </div>
    );
  };

  const detail = hover && byStatus[hover] ? (
    <div className="flex items-center gap-sm">
      <span className="w-32 shrink-0 truncate text-caption font-medium capitalize text-foreground">
        {STATUS_LABEL[hover] ?? hover} · {byStatus[hover]}
      </span>
      <div className="min-w-0 flex-1"><SeverityBar counts={byStatusSeverity[hover] ?? {}} variant="compact" /></div>
    </div>
  ) : (
    <p className="text-caption text-muted-foreground">Hover a segment for its severity split.</p>
  );

  return (
    <div className="space-y-sm">
      <div className="flex items-center justify-between text-caption">
        <span className="font-semibold text-foreground">Active · {activeTotal}</span>
        <span className="text-muted-foreground">Resolved · {resolvedTotal}</span>
      </div>

      {/* The pipeline bar. */}
      <div className="flex h-10 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={ordered.map((s) => `${byStatus[s]} ${STATUS_LABEL[s] ?? s}`).join(', ')}>
        {active.map((s, i) => seg(s, i === 0, resolved.length === 0 && i === active.length - 1))}
        {active.length > 0 && resolved.length > 0 && (
          <div className="h-full w-0.5 shrink-0 bg-background" aria-hidden />
        )}
        {resolved.map((s, i) => seg(s, active.length === 0 && i === 0, i === resolved.length - 1))}
      </div>

      {/* Legend. */}
      <div className="flex flex-wrap gap-x-md gap-y-xxs">
        {ordered.map((s) => (
          <span key={s} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
            <span className="size-2 rounded-full" style={{ background: STATUS_HSL[s] }} aria-hidden />
            {STATUS_LABEL[s] ?? s}
          </span>
        ))}
      </div>

      {/* Hover detail — fixed height so the layout doesn't jump. */}
      <div className="min-h-[1.5rem] border-t border-border pt-xs">{detail}</div>
    </div>
  );
};

export default DispositionPipeline;
