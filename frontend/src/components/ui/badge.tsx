import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../utils/cn';

/**
 * Badge — small inline status indicator.  Use for counts, severity,
 * state labels.  Per the audit (severity is conveyed by color only),
 * always pair with a text label and prefer adding an icon for
 * accessibility.
 *
 *   <Badge variant="destructive"><AlertOctagonIcon className="size-3"/> 3 Critical</Badge>
 */
const badgeVariants = cva(
  // Default badge is sized to its content with no clipping. The
  // earlier audit pass added `max-w-full overflow-hidden` to the
  // default so `max-w-[Nrem]` overrides would clip inner truncating
  // spans (the OS Badge case in HostInspector/Hosts, audit RSP·M6).
  // That broke status badges in narrow columns (e.g. /recon/runs
  // Status column at w-28) — short labels like "abandoned" hard-cut
  // mid-word. Reverted: keep default behaviour, opt into truncation
  // via `Badge className="max-w-[Nrem] overflow-hidden"` at the call
  // site when it's actually needed. text-caption was reverted to
  // text-micro for the same reason (smaller default letters fit
  // standard dense-table columns better; a11y·L2 wanted larger but
  // the layout cost outweighed the legibility gain for status pills).
  'inline-flex items-center gap-xxs rounded-chip border px-xs py-px text-micro font-semibold uppercase tracking-wider transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border-transparent bg-destructive text-destructive-foreground',
        success: 'border-transparent bg-success text-success-foreground',
        warning: 'border-transparent bg-warning text-warning-foreground',
        info: 'border-transparent bg-info text-info-foreground',
        'severity-critical': 'border-destructive/30 bg-destructive text-destructive-foreground shadow-[inset_3px_0_0_hsl(var(--destructive-foreground)/0.42)]',
        'severity-high': 'border-warning/35 bg-warning text-warning-foreground shadow-[inset_3px_0_0_hsl(var(--warning-foreground)/0.36)]',
        'severity-medium': 'border-info/30 bg-info text-info-foreground shadow-[inset_3px_0_0_hsl(var(--info-foreground)/0.36)]',
        'severity-low': 'border-success/30 bg-success text-success-foreground shadow-[inset_3px_0_0_hsl(var(--success-foreground)/0.36)]',
        outline: 'border-border text-foreground',
        muted: 'border-transparent bg-muted text-muted-foreground',
        // Outline-only severity tones — for places that want a lighter
        // chip than the solid `warning`/`destructive`/`info`/`success`
        // variants but still need the semantic color carried through.
        // Replaces the bespoke `border-warning/40 text-warning` class
        // soup that appeared in ~19 sites across ExecutionDetail,
        // ReconRunDetail, PortfolioDashboard (audit H15).
        'destructive-outline': 'border-destructive/40 text-destructive',
        'warning-outline': 'border-warning/40 text-warning',
        'info-outline': 'border-info/40 text-info',
        'success-outline': 'border-success/40 text-success',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
