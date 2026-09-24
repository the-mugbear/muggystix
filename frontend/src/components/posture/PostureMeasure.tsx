/**
 * One quiet measure on a shared baseline — the Posture Overview's context
 * strip (v5.254.0), shared with Oversight (5.259.0).  A label with an explicit
 * (i), the number, and a line or two of support; no card, no icon, no meter.
 * Lay several out in a grid with `lg:divide-x` so they read as one strip.
 */
import React from 'react';
import { Link } from 'react-router-dom';

import { InfoTip } from '../ui/info-tip';

export interface PostureMeasureProps {
  label: string;
  /** Plain-English "what is this / how it's derived" — always on an explicit (i). */
  info: string;
  value: React.ReactNode;
  /** Drill-down for the number (§26) — the list it opens is the set it counts. */
  to?: string;
  toLabel?: string;
  children?: React.ReactNode;
}

export const PostureMeasure: React.FC<PostureMeasureProps> = ({ label, info, value, to, toLabel, children }) => (
  <div className="min-w-0 px-md first:pl-0">
    {/* Wraps to two lines before clamping (v5.294.0, UX review): one-line
        truncation cut "Targets tested (in review or revi…" mid-word. */}
    <p className="flex items-start gap-xxs text-caption text-muted-foreground">
      <span className="min-w-0 line-clamp-2 break-words" title={label}>{label}</span> <InfoTip text={info} />
    </p>
    <p className="mt-xxs text-subheading font-bold leading-none text-foreground">
      {to ? (
        <Link to={to} aria-label={toLabel ?? `${label} — view`}
          className="rounded hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {value}
        </Link>
      ) : value}
    </p>
    <div className="mt-xs min-w-0 text-caption text-muted-foreground">{children}</div>
  </div>
);

export default PostureMeasure;
