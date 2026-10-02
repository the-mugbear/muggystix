import * as React from 'react';
import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import { Check, Minus } from 'lucide-react';
import { cn } from '../../utils/cn';

/**
 * Checkbox — Radix handles indeterminate state via `checked="indeterminate"`.
 * For row selection in tables, pair with `aria-label`:
 *
 *   <Checkbox checked={selected} onCheckedChange={...} aria-label="Select row" />
 *
 * A "select all" box over a PARTIAL selection is `checked="indeterminate"`
 * and draws a dash, not the tick: the two used to share one mark, so 2 of 25
 * rows read as "all selected" (visual pass 2026-10-01).
 */
export const Checkbox = React.forwardRef<
  React.ElementRef<typeof CheckboxPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, checked, defaultChecked, onCheckedChange, ...props }, ref) => {
  // Which mark to draw.  A controlled box is told; one that keeps its own
  // state is followed here (Radix does not expose it to its children).
  const [own, setOwn] = React.useState<CheckboxPrimitive.CheckedState>(defaultChecked ?? false);
  const state = checked !== undefined ? checked : own;
  return (
    <CheckboxPrimitive.Root
      ref={ref}
      checked={checked}
      defaultChecked={defaultChecked}
      onCheckedChange={(value) => {
        setOwn(value);
        onCheckedChange?.(value);
      }}
      className={cn(
        // v4.7.6 — border-input (the form-control border token), not
        // border-border (the faint 12%-alpha divider).  An unchecked
        // checkbox is defined solely by this border; the divider token
        // made it effectively invisible.
        'peer size-4 shrink-0 rounded-[4px] border border-input ring-offset-background',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        'data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground data-[state=checked]:border-primary',
        'data-[state=indeterminate]:bg-primary data-[state=indeterminate]:text-primary-foreground data-[state=indeterminate]:border-primary',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
        {state === 'indeterminate'
          ? <Minus className="size-3" strokeWidth={3} aria-hidden data-glyph="some" />
          : <Check className="size-3" strokeWidth={3} aria-hidden data-glyph="all" />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
});
Checkbox.displayName = CheckboxPrimitive.Root.displayName;
