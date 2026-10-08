/**
 * Finding disposition as one horizontal bar of findings by status, grouped
 * under investigation | confirmed | closed (the vocabulary in
 * utils/findingStatus.ts), segment width ∝ count. Fully static — counts sit in the
 * heading and, when a population splits into statuses, in the legend, so nothing is hidden behind a hover (an earlier
 * hover-reveal exposed an unlabelled severity bar that vanished on mouse-out;
 * removed). Severity lives in the "Active findings" headline card instead.
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { STATUS_HSL, STATUS_LABEL, POPULATION_STATUSES } from './postureTheme';
import { POPULATION_LABEL } from '../../utils/findingStatus';

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

  const groups = POPULATION_STATUSES.map((g) => {
    const statuses = g.statuses.filter((s) => byStatus[s]);
    return { key: g.key, statuses, total: statuses.reduce((sum, s) => sum + byStatus[s], 0) };
  });
  const shown = groups.filter((g) => g.statuses.length > 0);
  const ordered = shown.flatMap((g) => g.statuses);
  const splits = shown.some((g) => g.statuses.length > 1);
  const total = groups.reduce((sum, g) => sum + g.total, 0);

  if (total === 0) {
    return <p className="text-caption text-muted-foreground">No findings recorded yet.</p>;
  }

  const seg = (status: string, isFirst: boolean, isLast: boolean) => {
    const n = byStatus[status];
    const widthPct = mounted ? (n / total) * 100 : 0;
    const href = statusHref?.(status) ?? null;
    const cls = `flex h-full items-center justify-center overflow-hidden ${isFirst ? 'rounded-l-full' : ''} ${isLast ? 'rounded-r-full' : ''}`;
    const style: React.CSSProperties = {
      width: `${widthPct}%`,
      background: STATUS_HSL[status] ?? 'hsl(var(--muted))',
      transition: 'width 700ms cubic-bezier(0.22,1,0.36,1)',
    };
    // The count is in the heading above (and in the legend when a population
    // splits): it is not printed a third time inside the bar.
    const inner = null;
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
      {/* All three populations always named — a zero is information here. */}
      <div className="flex flex-wrap items-center gap-x-lg gap-y-xxs text-caption">
        {groups.map((g) => (
          <span key={g.key}
            className={`inline-flex items-center gap-xxs ${g.key === 'closed' ? 'text-muted-foreground' : 'font-semibold text-foreground'}`}>
            {/* The bar's key when the population is one status; a split one is keyed by the legend. */}
            {g.statuses.length === 1 && (
              <span className="size-2 rounded-full" style={{ background: STATUS_HSL[g.statuses[0]] }} aria-hidden />
            )}
            {POPULATION_LABEL[g.key]} · {g.total}
          </span>
        ))}
      </div>

      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted"
        role="img" aria-label={ordered.map((s) => `${byStatus[s]} ${STATUS_LABEL[s] ?? s}`).join(', ')}>
        {shown.map((g, gi) => (
          <React.Fragment key={g.key}>
            {gi > 0 && <div className="h-full w-0.5 shrink-0 bg-background" aria-hidden />}
            {g.statuses.map((s) => seg(s, s === ordered[0], s === ordered[ordered.length - 1]))}
          </React.Fragment>
        ))}
      </div>

      {/* The legend splits a population into its statuses (open / retest…).
          When no population holds more than one status it would repeat the
          heading word for word, under other names — so it is left out. */}
      {splits && <div className="flex flex-wrap gap-x-md gap-y-xxs">
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
      </div>}
    </div>
  );
};

export default DispositionPipeline;
