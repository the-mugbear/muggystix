import * as React from 'react';
import { cn } from '../../utils/cn';

/**
 * InlineLoader — single source of truth for inline async-loading UI
 * across the app.  Replaces the bare `<Loader2 className="animate-spin"
 * aria-hidden />` idiom that was scattered across detail pages and
 * silently violated WCAG: `aria-label` on a decorative SVG is widely
 * ignored, so screen-reader users heard nothing while a filter change
 * or fresh detail-page mount fetched data.
 *
 * Wraps the spinner in `role="status" aria-live="polite"` plus a
 * visually-hidden text label so the page announces itself.
 *
 * Use `size` to match context: `sm` for inline (next to a Select,
 * inside a row), `md` for section-level loaders, `lg` for full-page.
 */
const SIZE_CLASS: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'size-4',
  md: 'size-5',
  lg: 'size-8',
};

export interface InlineLoaderProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Visually-hidden text announced to screen readers. */
  label?: string;
  /** Spinner size — sm (16) / md (20) / lg (32).  Default md. */
  size?: 'sm' | 'md' | 'lg';
  /** Centered with vertical padding for use as a full-section placeholder. */
  centered?: boolean;
}

export const InlineLoader = React.forwardRef<HTMLDivElement, InlineLoaderProps>(
  ({ label = 'Loading…', size = 'md', centered = false, className, ...props }, ref) => (
    <div
      ref={ref}
      role="status"
      aria-live="polite"
      className={cn(
        'inline-flex items-center gap-xs text-muted-foreground',
        centered && 'flex w-full justify-center py-xl',
        className,
      )}
      {...props}
    >
      <span className={cn(SIZE_CLASS[size], 'brand-inline-loader')} aria-hidden />
      <span className="sr-only">{label}</span>
    </div>
  ),
);
InlineLoader.displayName = 'InlineLoader';
