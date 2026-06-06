import * as React from 'react';
import * as AccordionPrimitive from '@radix-ui/react-accordion';
import { ChevronDown } from 'lucide-react';
import { cn } from '../../utils/cn';

/**
 * Accordion — Radix Accordion is the right base; it handles single vs
 * multi-expanded modes (`type="single" | "multiple"`), controlled +
 * uncontrolled, and ARIA wiring out of the box.
 *
 * Single-expanded (only one open at a time):
 *   <Accordion type="single" collapsible>
 *     <AccordionItem value="a"><AccordionTrigger>A</AccordionTrigger><AccordionContent>...</AccordionContent></AccordionItem>
 *   </Accordion>
 *
 * Multi-expanded (any combination open):
 *   <Accordion type="multiple" value={openItems} onValueChange={setOpenItems}>
 *     ...
 *   </Accordion>
 */

export const Accordion = AccordionPrimitive.Root;

export const AccordionItem = React.forwardRef<
  React.ElementRef<typeof AccordionPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Item>
>(({ className, ...props }, ref) => (
  <AccordionPrimitive.Item ref={ref} className={cn('border-b border-border', className)} {...props} />
));
AccordionItem.displayName = AccordionPrimitive.Item.displayName;

export const AccordionTrigger = React.forwardRef<
  React.ElementRef<typeof AccordionPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
  <AccordionPrimitive.Header className="flex">
    <AccordionPrimitive.Trigger
      ref={ref}
      className={cn(
        'flex flex-1 items-center justify-between gap-sm py-sm text-left text-subheading font-semibold text-foreground',
        'transition-all hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control',
        '[&[data-state=open]>svg]:rotate-180',
        className,
      )}
      {...props}
    >
      {children}
      <ChevronDown
        className="size-4 shrink-0 text-muted-foreground transition-transform duration-base ease-standard"
        aria-hidden
      />
    </AccordionPrimitive.Trigger>
  </AccordionPrimitive.Header>
));
AccordionTrigger.displayName = AccordionPrimitive.Trigger.displayName;

export const AccordionContent = React.forwardRef<
  React.ElementRef<typeof AccordionPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <AccordionPrimitive.Content
    ref={ref}
    className={cn(
      'overflow-hidden text-metadata text-foreground',
      'data-[state=closed]:animate-out data-[state=closed]:fade-out-0',
      'data-[state=open]:animate-in data-[state=open]:fade-in-0',
    )}
    {...props}
  >
    <div className={cn('pb-md pt-0', className)}>{children}</div>
  </AccordionPrimitive.Content>
));
AccordionContent.displayName = AccordionPrimitive.Content.displayName;
