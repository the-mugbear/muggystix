import { useCallback, useState } from 'react';

import { useToast } from '../contexts/ToastContext';

/**
 * Two-item selection helper for "compare these two rows" surfaces.
 *
 * v2.44.1 (UX review #7): extracted because ExecutionsList and
 * ReconRunsList had divergent rules — one blocked the 3rd selection
 * with a toast, the other silently ejected the oldest selected row.
 * Same interaction class, different rules → "creates distrust in
 * selection state."  The hook standardizes on the strict-block rule
 * (the more conservative choice): once two items are picked, a third
 * click is rejected with a toast asking the user to uncheck first.
 *
 * Usage:
 *
 *   const { selected, toggle, isCompareReady, clear } =
 *     useCompareSelection<number>({ kind: 'execution runs' });
 *
 *   <Checkbox checked={selected.includes(row.id)}
 *             onCheckedChange={() => toggle(row.id)} />
 *
 * The `kind` string seeds the toast id (so back-to-back blocks
 * dedupe via Sonner's id-based collapse) and the user-facing copy
 * ("Already comparing two {kind} …").
 */
export interface UseCompareSelectionOptions {
  kind: string;
  /** Maximum picks (the underlying compare endpoints all take 2). */
  max?: number;
}

export interface CompareSelection<T> {
  selected: T[];
  toggle: (id: T) => void;
  clear: () => void;
  /** True when exactly `max` items are picked (default 2). */
  isCompareReady: boolean;
}

export function useCompareSelection<T>(
  { kind, max = 2 }: UseCompareSelectionOptions,
): CompareSelection<T> {
  const toast = useToast();
  const [selected, setSelected] = useState<T[]>([]);

  const toggle = useCallback(
    (id: T) => {
      setSelected((curr) => {
        if (curr.includes(id)) return curr.filter((x) => x !== id);
        if (curr.length >= max) {
          toast.info(
            `Already comparing ${max} ${kind}. Uncheck one of the selected rows to swap in a different one.`,
            { id: `compare-limit-${kind}` },
          );
          return curr;
        }
        return [...curr, id];
      });
    },
    [kind, max, toast],
  );

  const clear = useCallback(() => setSelected([]), []);

  return {
    selected,
    toggle,
    clear,
    isCompareReady: selected.length === max,
  };
}
