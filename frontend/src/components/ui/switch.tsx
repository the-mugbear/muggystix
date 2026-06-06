import * as React from 'react';
import * as SwitchPrimitive from '@radix-ui/react-switch';
import { cn } from '../../utils/cn';

/**
 * Switch — Radix wraps it as a button with `role="switch"` +
 * `aria-checked`.  Use for boolean preferences; for true on/off
 * affecting state immediately (auto-refresh, dark mode, etc.).
 *
 * For boolean form fields submitted with a form, prefer Checkbox.
 */
export const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    className={cn(
      'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent',
      'transition-colors transition-base',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
      'disabled:cursor-not-allowed disabled:opacity-50',
      // v4.27.0 — `--secondary` is a brand accent in every palette
      // (sage in light; mint, cyan, pink, lavender across the dark
      // themes), so the v4.7.7 "use bg-secondary for OFF" choice
      // made every OFF switch read as a second active color —
      // especially bad on `magma`, where OFF was bright pink.
      // `--muted-foreground` is the textSecondary token, the
      // least-saturated value in every palette; at ~35% alpha it
      // stays visible against any card surface (light or dark)
      // while reading as inactive next to the primary-saturated ON
      // state.  Earlier history (v4.7.7): plain `bg-muted` was
      // invisible because `--muted` is brand teal at 6% alpha — the
      // alpha bump here vs. `--muted` is deliberate so we don't
      // regress to invisible-OFF.
      'data-[state=checked]:bg-primary data-[state=unchecked]:bg-muted-foreground/35',
      className,
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      className={cn(
        'pointer-events-none block size-4 rounded-full bg-card shadow-raised ring-0 transition-transform transition-base',
        'data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0',
      )}
    />
  </SwitchPrimitive.Root>
));
Switch.displayName = SwitchPrimitive.Root.displayName;
