import * as React from 'react';
import { cn } from '../../utils/cn';

/**
 * MetaField — one label/value pair, rendered consistently.
 *
 * The app had hand-rolled label/value markup in dozens of places —
 * each detail card spelled out its own `<p class="text-caption
 * text-muted-foreground">Label</p><p>value</p>`, with drifting
 * spacing, truncation, and null handling.  MetaField is the single
 * primitive for that pattern (UX2 review item 5).
 *
 *   <MetaField label="OS" value={host.os_name} />
 *   <MetaField label="IP" value={host.ip_address} mono />
 *   <MetaField label="Notes" value={note} orientation="inline" />
 *
 * Null / empty / whitespace-only values render the `—` fallback
 * (override via `fallback`) instead of collapsing to nothing, so a
 * missing field still occupies a readable row.  A non-string
 * `value` (a Badge, a link, an element) is rendered as-is and the
 * fallback is skipped.
 */

export interface MetaFieldProps {
  label: React.ReactNode;
  value: React.ReactNode;
  /**
   * `stacked` (default) — label above value, for grids/cards.
   * `inline` — label and value on one row, label fixed-width.
   */
  orientation?: 'stacked' | 'inline';
  /** Render the value in the monospace face (IPs, hashes, ports). */
  mono?: boolean;
  /** Fallback shown when `value` is a null/empty/blank string. */
  fallback?: string;
  className?: string;
}

// A string/number/null/undefined value gets the `—` fallback when
// blank; anything else (an element) is passed through untouched.
const renderValue = (value: React.ReactNode, fallback: string): React.ReactNode => {
  if (value == null) return fallback;
  if (typeof value === 'string') return value.trim() ? value : fallback;
  if (typeof value === 'number') return String(value);
  return value;
};

export const MetaField: React.FC<MetaFieldProps> = ({
  label,
  value,
  orientation = 'stacked',
  mono = false,
  fallback = '—',
  className,
}) => {
  const rendered = renderValue(value, fallback);
  const isFallback = rendered === fallback;

  const valueClass = cn(
    'min-w-0 break-words text-metadata',
    mono && 'font-mono',
    isFallback ? 'text-muted-foreground' : 'text-foreground',
  );

  if (orientation === 'inline') {
    return (
      <div className={cn('flex min-w-0 flex-wrap items-baseline gap-x-sm gap-y-xxs', className)}>
        <span className="shrink-0 text-caption font-medium text-muted-foreground">{label}</span>
        <span className={valueClass}>{rendered}</span>
      </div>
    );
  }

  return (
    <div className={cn('min-w-0 space-y-xxs', className)}>
      <div className="text-caption font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className={valueClass}>{rendered}</div>
    </div>
  );
};

export default MetaField;
