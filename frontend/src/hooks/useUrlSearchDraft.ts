import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useDebouncedValue } from './useDebouncedValue';

/**
 * A search box whose committed value lives in the address (UI_STYLE_GUIDE §39).
 *
 *   const search = useUrlSearchDraft('search');
 *   <Input value={search.draft} onChange={(e) => search.setDraft(e.target.value)} />
 *   useListQuery('listThings', fetcher, [projectId, search.value]);
 *
 * A filter has ONE owner, the address: `value` is read from it on every
 * render and is what the list is asked for.  The only thing kept in state is
 * what is not committed yet — the text while it is being typed (`draft`).
 *   - Typing commits `draft` (trimmed) to the address after `delay`, with
 *     replace — a keystroke is not a place Back should stop at — and drops
 *     `page` (and whatever `also` names): the result set changed.
 *   - When the address changes from ELSEWHERE — Back / Forward, a link to
 *     the same page, "Clear filters" — the box follows it.  (Copying the
 *     address into state once and writing the state back from an effect made
 *     two owners: the stale state overwrote the address the reader had just
 *     gone back to.)
 *   - An empty value is left out of the address.
 *
 * One write per action.  react-router hands a `setSearchParams(fn)` updater
 * the address of the CURRENT RENDER, not of a write made a moment earlier in
 * the same tick: `commit('')` followed by the page's own write loses the
 * first.  A "Clear filters" that also clears the box is therefore ONE write
 * by the page (deleting this param with the others) plus `setDraft('')` —
 * the box then agrees with the address and commits nothing.
 */
export interface UrlSearchDraft {
  /** The committed value: the address's, trimmed; '' when absent. */
  value: string;
  /** What the box shows. */
  draft: string;
  setDraft: (text: string) => void;
  /** Commit now (Enter, a "Search" button), without waiting for the delay. */
  commit: (text?: string) => void;
}

export function useUrlSearchDraft(
  param = 'search',
  { delay = 300, also = ['page'] }: { delay?: number; also?: readonly string[] } = {},
): UrlSearchDraft {
  const [params, setParams] = useSearchParams();
  const value = (params.get(param) ?? '').trim();
  // (This hook IS the sanctioned draft of an address value: both states are
  // re-seeded below whenever the address changes from elsewhere.)
  // eslint-disable-next-line bluestick/state-from-address
  const [draft, setDraftState] = useState(value);
  // The address's value this box last agreed with — written by it, or
  // followed from it.  Another value in the address came from elsewhere.
  // eslint-disable-next-line bluestick/state-from-address
  const [agreed, setAgreed] = useState(value);
  if (value !== agreed) {
    setAgreed(value);
    setDraftState(value);
  }

  const alsoKey = also.join(',');
  const write = useCallback((text: string) => {
    const next = text.trim();
    setAgreed(next);
    setParams((prev) => {
      if ((prev.get(param) ?? '').trim() === next) return prev;
      const out = new URLSearchParams(prev);
      if (next) out.set(param, next); else out.delete(param);
      alsoKey.split(',').filter(Boolean).forEach((name) => out.delete(name));
      return out;
    }, { replace: true });
  }, [param, alsoKey, setParams]);

  // What was typed, once the typing has stopped.  Only a draft the reader
  // typed is committed: one that was just re-seeded from the address already
  // equals it.
  const settled = useDebouncedValue(draft, delay);
  useEffect(() => {
    if (settled !== draft) return;                 // still typing
    if (settled.trim() === agreed) return;         // nothing new
    write(settled);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs when the typing settles, not when the address moves
  }, [settled]);

  const commit = useCallback((text?: string) => {
    const next = text ?? draft;
    setDraftState(next);
    write(next);
  }, [draft, write]);

  return { value, draft, setDraft: setDraftState, commit };
}

export default useUrlSearchDraft;
