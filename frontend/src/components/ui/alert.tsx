import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../utils/cn';

/**
 * Inline Alert — semantic banner.  Use inside the section that
 * produced the message, not as a floating notification (those are
 * toasts).  For announcements that should be announced by screen
 * readers, set `role="alert"` on destructive ones; success/info
 * default to `role="status"`.
 */
const alertVariants = cva(
  'relative w-full rounded-panel border px-md py-sm text-metadata [&>svg]:size-4 [&>svg]:text-foreground [&>svg+div]:translate-y-[-2px] [&>svg~*]:pl-lg',
  {
    variants: {
      variant: {
        default: 'bg-card text-card-foreground border-border',
        destructive: 'border-destructive/40 bg-destructive/10 text-destructive [&>svg]:text-destructive',
        success: 'border-success/40 bg-success/10 text-success [&>svg]:text-success',
        warning: 'border-warning/40 bg-warning/10 text-foreground [&>svg]:text-warning',
        info: 'border-info/40 bg-info/10 text-foreground [&>svg]:text-info',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export const Alert = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof alertVariants>
>(({ className, variant, ...props }, ref) => (
  <div
    ref={ref}
    role={variant === 'destructive' ? 'alert' : 'status'}
    className={cn(alertVariants({ variant }), className)}
    {...props}
  />
));
Alert.displayName = 'Alert';

export const AlertTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h5
    ref={ref}
    className={cn('mb-xxs font-semibold leading-none tracking-tight', className)}
    {...props}
  />
));
AlertTitle.displayName = 'AlertTitle';

export const AlertDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('text-metadata leading-relaxed', className)} {...props} />
));
AlertDescription.displayName = 'AlertDescription';

export { alertVariants };
