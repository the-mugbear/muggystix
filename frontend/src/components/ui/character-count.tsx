import * as React from 'react';
import { cn } from '../../utils/cn';

/**
 * CharacterCount (v5.290.0) — "93/100" under an input whose `maxLength`
 * matches an API limit, shown only near the limit so a field does not stop
 * accepting keystrokes without saying why.  Point the input's
 * `aria-describedby` at `id` while it shows.
 */
export interface CharacterCountProps {
  id?: string;
  value: string;
  max: number;
  /** Fraction of `max` from which the count shows (default 0.8). */
  showFrom?: number;
  className?: string;
}

export const shouldShowCharacterCount = (length: number, max: number, showFrom = 0.8) =>
  length > Math.floor(max * showFrom);

export const CharacterCount: React.FC<CharacterCountProps> = ({ id, value, max, showFrom = 0.8, className }) => {
  const length = value.length;
  if (!shouldShowCharacterCount(length, max, showFrom)) return null;
  const atLimit = length >= max;
  return (
    <p
      id={id}
      className={cn('text-right text-caption tabular-nums', atLimit ? 'text-destructive' : 'text-muted-foreground', className)}
      aria-live="polite"
    >
      {length}/{max}{atLimit && ' — the limit'}
    </p>
  );
};
