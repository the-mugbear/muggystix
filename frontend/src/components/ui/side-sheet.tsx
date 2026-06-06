import * as React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';

/**
 * SideSheet — a right-edge slide-over panel built on Radix Dialog with
 * modal={false}.  Focus trap + Esc + focus restoration stay; there is
 * no backdrop and outside-click does not close, so the page behind
 * stays legible and interactive.  Use this for master-detail surfaces
 * where the list should stay in view while a detail row is open
 * (Hosts / HostDetail, recon row inspector, etc).
 *
 * Compose like Dialog:
 *
 *   <SideSheet open onOpenChange={...}>
 *     <SideSheetContent>
 *       <SideSheetHeader>
 *         <SideSheetTitle>{host.ip_address}</SideSheetTitle>
 *         <SideSheetDescription>Discovered host</SideSheetDescription>
 *       </SideSheetHeader>
 *       ... body ...
 *     </SideSheetContent>
 *   </SideSheet>
 *
 * Default mode is the *non-modal panel*: no backdrop, page stays
 * interactive, outside-click does not close — right for master-detail
 * (the Hosts inspector).  Anything that must BLOCK the user —
 * destructive confirms, focused forms — is a centered modal `Dialog`.
 *
 * The one exception is `overlay` (v4.8.0 — restored after 4.7.13
 * wrongly removed it as "unused": the mobile nav drawer in Layout.tsx
 * legitimately needs it).  Pass `overlay` together with `modal` on the
 * root for a true modal side panel — a scrim behind it, outside-click
 * closes.  That combination is for edge-anchored *navigation* drawers,
 * not for content panels; content panels stay non-modal.
 *
 * Widths: pass `width="md"` (default 32rem) / "lg" (40rem) / "xl"
 * (56rem) / "full" (full width minus a 1-rem gutter).  Override via
 * className for one-off sizes.  Closes via the X button or Esc.
 */

export const SideSheet = ({
  modal = false,
  ...props
}: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Root>) => (
  <DialogPrimitive.Root modal={modal} {...props} />
);
SideSheet.displayName = 'SideSheet';

export const SideSheetTrigger = DialogPrimitive.Trigger;
export const SideSheetPortal = DialogPrimitive.Portal;
export const SideSheetClose = DialogPrimitive.Close;

type SideSheetWidth = 'md' | 'lg' | 'xl' | 'full';
type SideSheetSide = 'left' | 'right';

const widthClass: Record<SideSheetWidth, string> = {
  md: 'w-full sm:max-w-[32rem]',
  lg: 'w-full sm:max-w-[40rem]',
  xl: 'w-full sm:max-w-[56rem]',
  full: 'w-[calc(100vw-1rem)]',
};

const sideClass: Record<SideSheetSide, string> = {
  right:
    'right-0 border-l data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right',
  left:
    'left-0 border-r data-[state=open]:slide-in-from-left data-[state=closed]:slide-out-to-left',
};

export interface SideSheetContentProps
  extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  /** Maximum width tier.  Defaults to `md` (32rem). */
  width?: SideSheetWidth;
  /** Which edge the sheet slides in from.  Defaults to `right`. */
  side?: SideSheetSide;
  /** When true, render the built-in close (X) button.  Defaults to true. */
  showClose?: boolean;
  /**
   * When true, render a dimmed backdrop and let outside-click close
   * the sheet — a true modal side panel.  Pair with `modal` on the
   * SideSheet root.  Reserved for edge-anchored navigation drawers
   * (the mobile nav); content panels stay non-modal (the default).
   */
  overlay?: boolean;
}

export const SideSheetContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  SideSheetContentProps
>(({ className, children, width = 'md', side = 'right', showClose = true, overlay = false, ...props }, ref) => (
  <SideSheetPortal>
    {overlay && (
      <DialogPrimitive.Overlay
        className={cn(
          'fixed inset-0 z-40 bg-black/40',
          'data-[state=open]:animate-in data-[state=closed]:animate-out',
          'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
        )}
      />
    )}
    <DialogPrimitive.Content
      ref={ref}
      onInteractOutside={(event) => {
        // Non-modal (no overlay): suppress outside-click-close —
        // operators scroll the list / change selection while the
        // sheet is open; close is via the X button or Esc.  Modal
        // (overlay): let outside-click close, the standard drawer
        // behaviour.  Callers can override with their own handler.
        if (!overlay) {
          event.preventDefault();
        }
        props.onInteractOutside?.(event);
      }}
      className={cn(
        'fixed inset-y-0 z-50 flex h-full flex-col gap-md border-border bg-card shadow-overlay',
        'data-[state=open]:animate-in data-[state=closed]:animate-out duration-base ease-standard',
        'focus:outline-none',
        sideClass[side],
        widthClass[width],
        className,
      )}
      {...props}
    >
      {children}
      {showClose && (
        <DialogPrimitive.Close
          className={cn(
            'absolute right-md top-md rounded-control p-xxs opacity-70',
            'text-muted-foreground hover:text-foreground hover:opacity-100',
            'focus:outline-none focus:ring-2 focus:ring-inset focus:ring-ring',
            'disabled:pointer-events-none',
          )}
        >
          <X className="size-4" aria-hidden />
          <span className="sr-only">Close</span>
        </DialogPrimitive.Close>
      )}
    </DialogPrimitive.Content>
  </SideSheetPortal>
));
SideSheetContent.displayName = 'SideSheetContent';

export const SideSheetHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      'flex flex-col gap-xxs border-b border-border px-lg py-md pr-xl text-left',
      className,
    )}
    {...props}
  />
);
SideSheetHeader.displayName = 'SideSheetHeader';

export const SideSheetBody = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('flex-1 overflow-y-auto px-lg py-md', className)} {...props} />
);
SideSheetBody.displayName = 'SideSheetBody';

export const SideSheetFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      'flex flex-col-reverse gap-xs border-t border-border px-lg py-md sm:flex-row sm:justify-end sm:gap-sm',
      className,
    )}
    {...props}
  />
);
SideSheetFooter.displayName = 'SideSheetFooter';

export const SideSheetTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn('text-section-title font-semibold leading-tight text-foreground', className)}
    {...props}
  />
));
SideSheetTitle.displayName = DialogPrimitive.Title.displayName;

export const SideSheetDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn('text-metadata text-muted-foreground', className)}
    {...props}
  />
));
SideSheetDescription.displayName = DialogPrimitive.Description.displayName;
