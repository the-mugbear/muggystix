import * as React from 'react';
import { cn } from '../../utils/cn';

/**
 * Kbd — keyboard-key glyph for inline use (Cmd+K, ⌘ shortcuts, etc.).
 * Standardizes the ~6 bespoke `<kbd className="rounded border ..">`
 * inlines across the app.  Uses the design tokens (rounded-control,
 * font-mono, text-micro) instead of `rounded` / `text-[10px]`.
 */
export const Kbd = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement>>(
  ({ className, ...props }, ref) => (
    <kbd
      ref={ref}
      className={cn(
        'inline-flex min-w-[1.25rem] items-center justify-center rounded-control border border-border bg-muted px-xxs py-px font-mono text-micro font-semibold text-muted-foreground',
        className,
      )}
      {...props}
    />
  ),
);
Kbd.displayName = 'Kbd';
