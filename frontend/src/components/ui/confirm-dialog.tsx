import * as React from 'react';
import { Loader2 } from 'lucide-react';
import type { ButtonProps } from './button';
import { Button } from './button';
import {
  Dialog,
  DialogContent,
  DialogContentProps,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './dialog';
import { Label } from './label';
import { Textarea } from './textarea';

/**
 * ConfirmDialog — the single confirmation-modal primitive for the
 * agent-workflow surfaces (recon / execution / test plans).
 *
 * Before this existed, every "are you sure?" modal was hand-rolled:
 * the recon and execution Abandon dialogs were byte-for-byte
 * duplicates, the test-plan Abandon/Reject dialogs were near-copies
 * with drifting copy, button labels, and icon placement.  ConfirmDialog
 * collapses them into one component so the three workflows operate and
 * read identically.
 *
 *   <ConfirmDialog
 *     open={open}
 *     onOpenChange={setOpen}
 *     titleIcon={<CircleSlash className="size-5 text-destructive" />}
 *     title="Abandon recon session #42?"
 *     description="Use this when the agent never closed the session…"
 *     reason={{ value: reason, onChange: setReason,
 *               placeholder: 'e.g. agent process died after 3 hosts',
 *               helpText: 'Username + timestamp are recorded either way.' }}
 *     confirmLabel="Abandon session"
 *     confirmIcon={<CircleSlash className="size-4" />}
 *     busy={busy}
 *     onConfirm={handleAbandon}
 *   />
 *
 * Close is blocked while `busy` (a network request is in flight) so a
 * stray Esc / overlay click can't desync the dialog from the request.
 */

export interface ConfirmReasonField {
  value: string;
  onChange: (value: string) => void;
  /** Field label.  Defaults to "Reason (optional)". */
  label?: React.ReactNode;
  placeholder?: string;
  helpText?: React.ReactNode;
  /** Maxlength on the textarea.  Defaults to 512. */
  maxLength?: number;
  rows?: number;
  /** DOM id for the textarea/label pair.  Defaults to a stable id; only
   *  one ConfirmDialog is open at a time so collisions can't occur. */
  id?: string;
}

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  /** Optional icon rendered inline before the title (e.g. a warning glyph). */
  titleIcon?: React.ReactNode;
  description?: React.ReactNode;
  /** Extra body content rendered between the description and the reason field. */
  children?: React.ReactNode;
  /** When provided, renders an optional reason textarea. */
  reason?: ConfirmReasonField;
  confirmLabel: React.ReactNode;
  /** Icon shown on the confirm button when not busy. */
  confirmIcon?: React.ReactNode;
  /** Confirm button variant.  Defaults to `destructive`. */
  confirmVariant?: ButtonProps['variant'];
  confirmDisabled?: boolean;
  cancelLabel?: React.ReactNode;
  /** A request is in flight — disables actions and blocks close. */
  busy?: boolean;
  onConfirm: () => void;
  size?: DialogContentProps['size'];
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  onOpenChange,
  title,
  titleIcon,
  description,
  children,
  reason,
  confirmLabel,
  confirmIcon,
  confirmVariant = 'destructive',
  confirmDisabled = false,
  cancelLabel = 'Cancel',
  busy = false,
  onConfirm,
  size,
}) => {
  const reasonId = reason?.id ?? 'confirm-dialog-reason';
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Block close while a request is in flight so a stray Esc /
        // overlay click can't desync the dialog from the request.
        if (!next && busy) return;
        onOpenChange(next);
      }}
    >
      <DialogContent size={size}>
        <DialogHeader>
          <DialogTitle className={titleIcon ? 'flex items-center gap-xs' : undefined}>
            {titleIcon}
            {title}
          </DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>

        {children}

        {reason && (
          <div className="space-y-xxs">
            <Label htmlFor={reasonId}>{reason.label ?? 'Reason (optional)'}</Label>
            <Textarea
              id={reasonId}
              rows={reason.rows ?? 3}
              maxLength={reason.maxLength ?? 512}
              placeholder={reason.placeholder}
              value={reason.value}
              disabled={busy}
              onChange={(e) => reason.onChange(e.target.value)}
              // v4.58.0 (UX·8) — link the helpText so screen readers
              // announce it on textarea focus.
              aria-describedby={reason.helpText ? `${reasonId}-help` : undefined}
            />
            {reason.helpText && (
              <p
                id={`${reasonId}-help`}
                className="text-caption text-muted-foreground"
              >
                {reason.helpText}
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={confirmVariant}
            onClick={onConfirm}
            disabled={busy || confirmDisabled}
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              confirmIcon
            )}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ConfirmDialog;
