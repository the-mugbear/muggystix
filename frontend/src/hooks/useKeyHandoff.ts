import { useState } from 'react';
import type { UseMutationResult } from '@tanstack/react-query';

/**
 * A dialog that hands the reader an agent key ONCE — the start dialog and the
 * resume dialog (plan B2b; the rule was written out in each).
 *
 * The key is the answer of the dialog's own mutation (which spreads
 * `SECRET_MUTATION`, lib/query) and nothing else's.  This is what guards it
 * and what drops it:
 *
 *   - the dialog does not close while the request is out;
 *   - it does not close by accident (Escape, a click outside, the corner ×)
 *     while the key is on screen and nothing holding it was copied — the
 *     footer's Done (`KeyHandoffFooter`) says why, and is the way out;
 *   - closing, by any path, `reset()`s the mutation first: the key is then
 *     in neither the observer nor (with `gcTime: 0`) the cache, and the next
 *     opening starts at the first step with nothing copied.
 *
 *   const handoff = useKeyHandoff(rotate, { onOpenChange });
 *   <Dialog open={…} onOpenChange={handoff.onDialogOpenChange}>
 *     <DialogContent showClose={handoff.showClose}>
 *       … <AgentSessionCredentials onCopied={handoff.markKeyCopied} />
 *       … <KeyHandoffFooter copied={handoff.keyCopied} onDone={handoff.close} />
 */
export interface KeyHandoffOptions {
  /** The dialog's own `onOpenChange` prop: told `false` when it closes. */
  onOpenChange: (next: boolean) => void;
  /** Whatever else an opening must not inherit (the caller's own state). */
  onReset?: () => void;
  /** After the page was told the dialog closed (the caller reads what it
   *  needs of the answer while rendering — by now the answer is gone). */
  afterClose?: () => void;
}

export interface KeyHandoff<R> {
  /** The answer that holds the key, while it is shown. */
  result: R | null;
  /** The request is out. */
  loading: boolean;
  /** Something holding the key reached the clipboard. */
  keyCopied: boolean;
  markKeyCopied: () => void;
  /** Close now: drop the key, tell the page.  Done, Cancel, a link away. */
  close: () => void;
  /** For `<Dialog onOpenChange>`: a close the reader may not have meant. */
  onDialogOpenChange: (next: boolean) => void;
  /** For `<DialogContent showClose>`: no corner × while the key could be lost. */
  showClose: boolean;
}

export function useKeyHandoff<R, V>(
  mutation: Pick<UseMutationResult<R, Error, V>, 'data' | 'isPending' | 'reset'>,
  { onOpenChange, onReset, afterClose }: KeyHandoffOptions,
): KeyHandoff<R> {
  const [keyCopied, setKeyCopied] = useState(false);
  const result = mutation.data ?? null;
  const loading = mutation.isPending;

  const close = () => {
    mutation.reset();
    setKeyCopied(false);
    onReset?.();
    onOpenChange(false);
    afterClose?.();
  };

  return {
    result,
    loading,
    keyCopied,
    markKeyCopied: () => setKeyCopied(true),
    close,
    onDialogOpenChange: (next) => {
      if (next) {
        onOpenChange(true);
        return;
      }
      // Veto close while in-flight.
      if (loading) return;
      // Veto an accidental close while the key is on screen and nothing
      // holding it was copied (audit C1) — Done says why.
      if (result && !keyCopied) return;
      close();
    },
    showClose: !result || keyCopied,
  };
}

export default useKeyHandoff;
