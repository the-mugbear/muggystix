import React, { useEffect, useState } from 'react';
import { ShieldAlert, AlertTriangle, Info, CheckCircle2, Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Alert, AlertDescription } from './ui/alert';
import { formatApiError } from '../utils/apiErrors';

/**
 * Shared confirmation dialog — replaces ad-hoc ``window.confirm``
 * calls and the per-page bespoke delete dialogs.  Used via the
 * ``useConfirm`` hook in ``hooks/useConfirm.tsx``.
 *
 * Migrated to v4 primitives — Radix Dialog under the hood; the public
 * prop surface is unchanged so every existing caller keeps working.
 *
 * Audit-fix carry-over from the MUI version:
 *  - Rich context (title, body, optional resource name highlight).
 *  - Severity-appropriate styling (icon + button variant).
 *  - Submitting state so destructive actions can be async without
 *    flickering the dialog closed.
 *  - Optional typed-confirmation gate (audit M8).
 *  - Inline error alert if onConfirm throws (audit C3).
 */

export type ConfirmSeverity = 'danger' | 'warning' | 'info' | 'success';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body?: React.ReactNode;
  resourceName?: string;
  severity?: ConfirmSeverity;
  confirmLabel?: string;
  cancelLabel?: string;
  /** If true, the user must type ``resourceName`` verbatim to unlock Confirm. */
  confirmTypedName?: boolean;
  /** Called when the user confirms; may be async. */
  onConfirm: () => void | Promise<void>;
  /** Called on cancel or backdrop dismiss. */
  onClose: () => void;
}

const SEVERITY_ICON: Record<ConfirmSeverity, React.ReactNode> = {
  danger: <ShieldAlert className="size-5 text-destructive" aria-hidden />,
  warning: <AlertTriangle className="size-5 text-warning" aria-hidden />,
  info: <Info className="size-5 text-info" aria-hidden />,
  success: <CheckCircle2 className="size-5 text-success" aria-hidden />,
};

const SEVERITY_BUTTON: Record<ConfirmSeverity, 'destructive' | 'default'> = {
  danger: 'destructive',
  warning: 'destructive',
  info: 'default',
  success: 'default',
};

const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title,
  body,
  resourceName,
  severity = 'warning',
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  confirmTypedName = false,
  onConfirm,
  onClose,
}) => {
  const [submitting, setSubmitting] = useState(false);
  const [typedValue, setTypedValue] = useState('');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTypedValue('');
      setSubmitting(false);
      setErrorMessage(null);
    }
  }, [open]);

  const typedMatch =
    !confirmTypedName || (!!resourceName && typedValue.trim() === resourceName.trim());

  const handleConfirm = async () => {
    setSubmitting(true);
    setErrorMessage(null);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      setErrorMessage(formatApiError(err, 'Action failed. Please try again.'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !submitting && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            {SEVERITY_ICON[severity]}
            {title}
          </DialogTitle>
          {typeof body === 'string' && <DialogDescription>{body}</DialogDescription>}
        </DialogHeader>

        {typeof body !== 'string' && body && <div className="text-metadata text-foreground">{body}</div>}

        {errorMessage && (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        )}

        {resourceName && !confirmTypedName && (
          <div className="rounded-control bg-muted p-sm font-mono text-caption text-foreground break-words">
            {resourceName}
          </div>
        )}

        {confirmTypedName && resourceName && (
          <div className="flex flex-col gap-xs">
            <p className="text-metadata text-foreground">
              Type <code className="font-mono">{resourceName}</code> to confirm:
            </p>
            <Input
              autoFocus
              value={typedValue}
              onChange={(e) => setTypedValue(e.target.value)}
              placeholder={resourceName}
              disabled={submitting}
              aria-label={`Type ${resourceName} to confirm`}
            />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            {cancelLabel}
          </Button>
          <Button
            variant={SEVERITY_BUTTON[severity]}
            onClick={handleConfirm}
            disabled={submitting || !typedMatch}
          >
            {submitting ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden /> Working…
              </>
            ) : (
              confirmLabel
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ConfirmDialog;
