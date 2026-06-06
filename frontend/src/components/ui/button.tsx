import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../utils/cn';

/**
 * Button primitive — shadcn/ui shape.
 *
 * Variants:
 *   default      filled primary
 *   destructive  filled destructive (e.g. Delete)
 *   outline      bordered, foreground text
 *   secondary    filled secondary surface
 *   ghost        no chrome, hover only (icon buttons, nav)
 *   link         text + underline-on-hover (rare; prefer <a>)
 *
 * Sizes (v4.7.13 — default density raised to 40px; the prior 32px
 * default read as cramped and undershot the 44px touch-target
 * guideline.  `sm` stays for genuinely compact contexts — dense
 * table rows, inline toolbars):
 *   sm    32px height, compact (opt-in)
 *   md    40px height (default)
 *   lg    44px height (CTA buttons; meets the touch-target guideline)
 *   icon  square 40px (icon-only, matches md)
 *
 * `asChild` swaps the rendered <button> for the immediate child via
 * Radix Slot.  Use when the button needs to be an <a> or
 * <Link from react-router>:
 *
 *   <Button asChild><Link to="/operations">Go</Link></Button>
 */
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-xs whitespace-nowrap rounded-control text-metadata font-medium ring-offset-background transition-[background-color,color,border-color,box-shadow,transform] transition-base ease-standard focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground shadow-[inset_0_1px_0_hsl(var(--primary-foreground)/0.24),0_1px_2px_hsl(var(--primary)/0.18)] hover:bg-primary/90 hover:shadow-[inset_0_1px_0_hsl(var(--primary-foreground)/0.30),0_0_0_3px_hsl(var(--primary)/0.10)]',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        outline:
          'border border-border bg-transparent text-foreground hover:bg-accent hover:text-accent-foreground',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        ghost: 'text-foreground hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary underline-offset-4 hover:underline',
        // Severity variants — eliminate hand-rolled
        // `border-warning/40 text-warning hover:bg-warning/10` soup
        // that previously appeared on Abandon/danger Buttons across
        // ExecutionDetail, ReconRunDetail, ForceChangePassword.
        warning: 'bg-warning text-warning-foreground hover:bg-warning/90',
        'warning-outline':
          'border border-warning/40 bg-transparent text-warning hover:bg-warning/10',
        success: 'bg-success text-success-foreground hover:bg-success/90',
      },
      size: {
        sm: 'h-8 px-sm text-caption',
        md: 'h-10 px-md',
        lg: 'h-11 px-lg text-body',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: { variant: 'default', size: 'md' },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
    );
  },
);
Button.displayName = 'Button';

export { buttonVariants };
