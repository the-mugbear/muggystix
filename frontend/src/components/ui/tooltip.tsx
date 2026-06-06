import * as React from 'react';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { cn } from '../../utils/cn';

/**
 * Tooltip primitive.  Wrap the app once in <TooltipProvider> (done
 * at the layout level) and then any tooltip is:
 *
 *   <Tooltip>
 *     <TooltipTrigger asChild><Button>...</Button></TooltipTrigger>
 *     <TooltipContent>Helpful detail</TooltipContent>
 *   </Tooltip>
 *
 * Works on disabled triggers because Radix uses event-level focus
 * tracking instead of pointer events (fixes audit M12).
 */

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <TooltipPrimitive.Content
    ref={ref}
    sideOffset={sideOffset}
    className={cn(
      'z-50 overflow-hidden rounded-control border border-border bg-popover px-sm py-xxs text-caption text-popover-foreground shadow-overlay',
      'animate-in fade-in-0 zoom-in-95',
      'data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95',
      'data-[side=bottom]:slide-in-from-top-2',
      'data-[side=left]:slide-in-from-right-2',
      'data-[side=right]:slide-in-from-left-2',
      'data-[side=top]:slide-in-from-bottom-2',
      className,
    )}
    {...props}
  />
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;
