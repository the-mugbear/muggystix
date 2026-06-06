import * as React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';

/**
 * Dialog primitive — Radix Dialog with shadcn styling.  Composes:
 *
 *   <Dialog open onOpenChange={...}>
 *     <DialogContent>
 *       <DialogHeader>
 *         <DialogTitle>Delete plan?</DialogTitle>
 *         <DialogDescription>This cannot be undone.</DialogDescription>
 *       </DialogHeader>
 *       ... body ...
 *       <DialogFooter>
 *         <Button variant="outline" onClick={close}>Cancel</Button>
 *         <Button variant="destructive" onClick={submit}>Delete</Button>
 *       </DialogFooter>
 *     </DialogContent>
 *   </Dialog>
 *
 * Focus trap + Esc + outside-click + focus restoration on close are
 * handled by Radix.  aria-labelledby / aria-describedby wired
 * automatically from DialogTitle + DialogDescription.
 *
 * For non-modal slide-over panels (the SideSheet pattern from the
 * revamp plan), pass `modal={false}` to <Dialog> and use the
 * Dialog primitives the same way — focus trap stays, backdrop is
 * removed.
 */

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogPortal = DialogPrimitive.Portal;
export const DialogClose = DialogPrimitive.Close;

export const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      'fixed inset-0 z-50 bg-black/60 backdrop-blur-sm',
      'data-[state=open]:animate-in data-[state=closed]:animate-out',
      'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
      className,
    )}
    {...props}
  />
));
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName;

type DialogSize = 'sm' | 'md' | 'lg' | 'xl';

const DIALOG_SIZE_CLASS: Record<DialogSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
};

export interface DialogContentProps
  extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  /**
   * Width preset.  Mirrors SideSheet's `size` prop for consistency.
   * Defaults to `md` (max-w-lg), the previous baseline.
   */
  size?: DialogSize;
  /**
   * Whether to render the top-right X close button.  Defaults to true.
   *
   * Pass `false` for acknowledgement-gated flows (StartReconDialog and
   * any future "you must check the box before closing" surface).  The
   * previous unconditional X created a deceptive affordance: the dialog
   * advertised "dismissible" but the parent's onOpenChange vetoed the
   * close, so click/Esc/backdrop all silently did nothing.  Set false
   * here and pair with an explicit Close button in the footer that's
   * only enabled once the gating condition is met.  (UX review #1)
   */
  showClose?: boolean;
}

export const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  DialogContentProps
>(({ className, children, size = 'md', showClose = true, ...props }, ref) => (
  <DialogPortal>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        // flex-col + max-h-[85vh] + overflow-hidden means body content
        // scrolls *inside* the frame instead of pushing the
        // DialogFooter (Cancel/Submit) below the viewport.  Required
        // per style guide §11 and UX audit C4.  Pages that want a
        // long body should put it inside a child <div
        // className="overflow-y-auto -mx-lg px-lg"> between header
        // and footer to inherit the scrolling automatically.
        'fixed left-[50%] top-[50%] z-50 flex max-h-[85vh] w-full translate-x-[-50%] translate-y-[-50%] flex-col gap-md',
        DIALOG_SIZE_CLASS[size],
        'border border-border bg-card p-lg shadow-overlay rounded-panel overflow-hidden',
        'data-[state=open]:animate-in data-[state=closed]:animate-out',
        'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
        'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
        'duration-base',
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
  </DialogPortal>
));
DialogContent.displayName = DialogPrimitive.Content.displayName;

/**
 * Scrolling body wrapper for dialogs with long content.  Use between
 * DialogHeader and DialogFooter so the footer's actions stay pinned
 * while the body scrolls.
 */
export const DialogBody = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('-mx-lg flex-1 overflow-y-auto px-lg', className)} {...props} />
  ),
);
DialogBody.displayName = 'DialogBody';

export const DialogHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('flex flex-col gap-xxs text-left', className)} {...props} />
);
DialogHeader.displayName = 'DialogHeader';

export const DialogFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn('flex flex-col-reverse gap-xs sm:flex-row sm:justify-end sm:gap-sm', className)}
    {...props}
  />
);
DialogFooter.displayName = 'DialogFooter';

export const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn('text-section-title font-semibold leading-tight text-foreground', className)}
    {...props}
  />
));
DialogTitle.displayName = DialogPrimitive.Title.displayName;

export const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn('text-metadata text-muted-foreground', className)}
    {...props}
  />
));
DialogDescription.displayName = DialogPrimitive.Description.displayName;
