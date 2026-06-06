import React, { useCallback, useRef, useState } from 'react';
import ConfirmDialog, { ConfirmSeverity } from '../components/ConfirmDialog';

/**
 * Imperative confirmation dialog hook.
 *
 * Returns a tuple of ``[ConfirmDialogElement, askConfirmation]`` so a
 * component can render a single ``<ConfirmDialog>`` instance and
 * trigger it from anywhere (including async handlers) without per-call
 * state plumbing.
 *
 * Usage:
 *   const [confirmEl, confirm] = useConfirm();
 *   …
 *   {confirmEl}   // render once, anywhere in the component tree
 *   …
 *   const ok = await confirm({
 *     title: 'Delete scope',
 *     body: 'This will drop all host mappings.',
 *     resourceName: scope.name,
 *     severity: 'danger',
 *   });
 *   if (ok) await deleteScope(scope.id);
 *
 * The returned promise resolves to ``true`` if the user confirmed,
 * ``false`` otherwise.  The caller runs the destructive work *after*
 * the promise resolves — this keeps the dialog visually decoupled from
 * the async side-effect, matching the UX audit recommendation (#6).
 */

export interface ConfirmOptions {
  title: string;
  body?: React.ReactNode;
  resourceName?: string;
  severity?: ConfirmSeverity;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmTypedName?: boolean;
}

type Resolver = (confirmed: boolean) => void;

export function useConfirm(): [React.ReactElement | null, (opts: ConfirmOptions) => Promise<boolean>] {
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolverRef = useRef<Resolver | null>(null);

  const askConfirmation = useCallback((o: ConfirmOptions): Promise<boolean> => {
    setOpts(o);
    setOpen(true);
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
    });
  }, []);

  const handleConfirm = useCallback(() => {
    if (resolverRef.current) {
      resolverRef.current(true);
      resolverRef.current = null;
    }
    // We deliberately do NOT call setOpen(false) here; the
    // ConfirmDialog closes itself via its own onClose after onConfirm
    // resolves.  Keeping this as a no-op (return void) lets
    // ConfirmDialog manage its submitting spinner correctly.
  }, []);

  const handleClose = useCallback(() => {
    setOpen(false);
    if (resolverRef.current) {
      resolverRef.current(false);
      resolverRef.current = null;
    }
  }, []);

  const element = opts ? (
    <ConfirmDialog
      open={open}
      title={opts.title}
      body={opts.body}
      resourceName={opts.resourceName}
      severity={opts.severity}
      confirmLabel={opts.confirmLabel}
      cancelLabel={opts.cancelLabel}
      confirmTypedName={opts.confirmTypedName}
      onConfirm={handleConfirm}
      onClose={handleClose}
    />
  ) : null;

  return [element, askConfirmation];
}
