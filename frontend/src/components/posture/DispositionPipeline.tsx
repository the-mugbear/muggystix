/**
 * Finding disposition as a lifecycle pipeline: one horizontal bar of findings
 * flowing through their statuses, split ACTIVE | RESOLVED (active warm,
 * resolved cool), segment width ∝ count. Fully static — counts sit inside wide
 * segments and in the legend, so nothing is hidden behind a hover (an earlier
 * hover-reveal exposed an unlabelled severity bar that vanished on mouse-out;
 * removed). Severity lives in the "Active findings" headline card instead.
 */
import React, { useEffect, useState } from 'react';

import {
  STATUS_HSL, STATUS_LABEL, ACTIVE_STATUSES, RESOLVED_STATUSES,
} from './postureTheme';

interface DispositionPipelineProps {
  byStatus: Record<string, number>;
}

const DispositionPipeline: React.FC<DispositionPipelineProps> = ({ byStatus }) => {
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
    return (
      <div
        key={status}
        title={`${STATUS_LABEL[status] ?? status}: ${n}`}
        className={`flex h-full items-center justify-center overflow-hidden ${isFirst ? 'rounded-l-full' : ''} ${isLast ? 'rounded-r-full' : ''}`}
        style={{
          width: `${widthPct}%`,
          background: STATUS_HSL[status] ?? 'hsl(var(--muted))',
          transition: 'width 700ms cubic-bezier(0.22,1,0.36,1)',
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
        {ordered.map((s) => (
          <span key={s} className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
            <span className="size-2 rounded-full" style={{ background: STATUS_HSL[s] }} aria-hidden />
            <span className="font-medium text-foreground">{byStatus[s]}</span> {STATUS_LABEL[s] ?? s}
          </span>
        ))}
      </div>
    </div>
  );
};

export default DispositionPipeline;
