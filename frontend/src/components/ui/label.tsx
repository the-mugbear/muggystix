import * as React from 'react';
import * as LabelPrimitive from '@radix-ui/react-label';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../utils/cn';

/**
 * Label primitive — Radix Label handles `htmlFor` association
 * automatically when wrapping a form control, and falls back to
 * clicking the label focusing the next associated input.
 *
 * Pair with `aria-required` / `aria-invalid` on the input itself for
 * screen-reader-correct forms.
 */
const labelVariants = cva(
  'text-caption font-medium leading-none text-foreground peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
);

export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root> &
    VariantProps<typeof labelVariants>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root ref={ref} className={cn(labelVariants(), className)} {...props} />
));
Label.displayName = LabelPrimitive.Root.displayName;
