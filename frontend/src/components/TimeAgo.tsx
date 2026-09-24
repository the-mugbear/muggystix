/**
 * A moment in a list: its relative age, the exact time on hover (v5.294.0).
 *
 * The UX review found five date formats across the pages. The rule now: lists
 * show how long ago ("13d ago") with the absolute moment in the tooltip and the
 * `<time>` element; everything else prints `formatTimestamp` / `formatDate`.
 */
import React from 'react';

import { cn } from '../utils/cn';
import { formatRelativeTime, formatTimestamp, type RelativeTimeStyle } from '../utils/relativeTime';

export interface TimeAgoProps {
  value: string | number | Date | null | undefined;
  style?: RelativeTimeStyle;
  /** Past this many days, print the date instead of "412d ago". */
  absoluteAfterDays?: number;
  /** Rendered when there is no value. */
  fallback?: React.ReactNode;
  className?: string;
}

export const TimeAgo: React.FC<TimeAgoProps> = ({
  value, style = 'short', absoluteAfterDays, fallback = '—', className,
}) => {
  const absolute = formatTimestamp(value, null);
  if (absolute === null) return <span className={cn('text-muted-foreground', className)}>{fallback}</span>;
  const ms = value instanceof Date ? value.getTime() : new Date(value as string | number).getTime();
  return (
    <time
      dateTime={new Date(ms).toISOString()}
      title={absolute}
      className={cn('whitespace-nowrap', className)}
    >
      {formatRelativeTime(value, { style, absoluteAfterDays })}
    </time>
  );
};

export default TimeAgo;
