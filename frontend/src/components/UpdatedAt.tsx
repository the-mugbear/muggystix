/**
 * When a section's data was loaded (v5.243.0).
 *
 * Operations is several independent fetches. One Refresh reaches them all, but
 * nothing said when each last SUCCEEDED — and several sections deliberately
 * keep their previous data when a refresh fails (so the page degrades instead
 * of blanking). That is the case this exists for: a card showing twenty-minute-
 * old numbers under a "could not load" banner must say they are twenty minutes
 * old. When fresh it is a quiet caption.
 *
 * Ticks on its own, so "just now" does not stay "just now".
 */
import React, { useEffect, useState } from 'react';

import { cn } from '../utils/cn';
import { formatRelativeTime } from '../utils/relativeTime';

export interface UpdatedAtProps {
  /** When the data on screen was successfully loaded; null before the first load. */
  at: Date | null;
  /** The latest attempt failed, so what is shown is the PREVIOUS load. */
  stale?: boolean;
  /** Say nothing while the data is current (5.304.0): Operations printed
   *  "updated just now" on four sections under a page header saying the
   *  same.  A section speaks up only when its own refresh failed. */
  hideWhenFresh?: boolean;
  className?: string;
}

const TICK_MS = 30_000;

export const UpdatedAt: React.FC<UpdatedAtProps> = ({ at, stale = false, hideWhenFresh = false, className }) => {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  if (!at) return null;
  if (hideWhenFresh && !stale) return null;
  const age = formatRelativeTime(at, { justNowBelowMs: 60_000 });
  return (
    <span
      className={cn('shrink-0 whitespace-nowrap text-caption', stale ? 'text-warning' : 'text-muted-foreground', className)}
      title={`${stale ? 'The last refresh failed — this is the previous load, from ' : 'Loaded '}${at.toLocaleString()}`}
    >
      {stale ? `showing data from ${age}` : `updated ${age}`}
    </span>
  );
};

export default UpdatedAt;
