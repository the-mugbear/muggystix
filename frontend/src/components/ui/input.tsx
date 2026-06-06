import * as React from 'react';
import { cn } from '../../utils/cn';

/**
 * Input primitive.  Density-matched to the default Button (h-10 / 40px,
 * v4.7.13) so they line up in inline form rows.
 *
 * For password fields with show/hide eye, compose with a wrapper that
 * absolutely-positions an icon button over the right padding region.
 * See the migrated Login form for the canonical pattern.
 *
 * Validation feedback: pass `aria-invalid` when in an error state.
 * The class hooks via `aria-[invalid=true]:` set the destructive ring.
 */
export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        'flex h-10 w-full rounded-control border border-input bg-card px-sm py-xs text-metadata text-foreground placeholder:text-muted-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:cursor-not-allowed disabled:opacity-50',
        'aria-[invalid=true]:border-destructive aria-[invalid=true]:focus-visible:ring-destructive',
        'file:border-0 file:bg-transparent file:text-metadata file:font-medium',
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = 'Input';
