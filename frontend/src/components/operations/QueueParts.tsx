/**
 * The pieces every Operations list shares (5.329.0, design review 2026-10-02).
 *
 * The page had three "show more" idioms and no bulk action.  These are the one
 * footer, the one bulk bar and the one selection rule its lists use:
 *
 *  - `ListFooter` — how much of the whole is on screen, then ONE way to the
 *    rest: "Open all N …" when a page lists exactly those N, or — only where
 *    no page can — "Show N more" in place.
 *  - `useRowSelection` — a selection that is always a subset of the rows on
 *    screen (UI_STYLE_GUIDE §41): a row that leaves the list leaves the
 *    selection, so a bulk action never reaches a row the reader cannot see.
 *  - `BulkBar` — what is selected and what can be done with it.
 *  - `UnavailableLine` — a list the server could not compute: said, never
 *    shown as empty.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import { Button } from '../ui/button';
import { selectAllState } from '../../utils/selection';

const LINK_CLASS =
  'rounded text-caption text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

export const ListFooter: React.FC<{
  /** Rows on screen. */
  shown: number;
  /** The server's count of the whole list. */
  total: number;
  /** The page that lists exactly this list — the label states the count it opens. */
  openAll?: { to: string; label: string };
  /** Only for a list no page can express: bring more rows here. */
  more?: { label: string; onClick: () => void; busy?: boolean };
  children?: React.ReactNode;
}> = ({ shown, total, openAll, more, children }) => {
  const partial = total > shown;
  if (!partial && !openAll && !more && !children) return null;
  return (
    <div className="mt-xs flex min-w-0 flex-wrap items-center gap-x-md gap-y-xxs">
      {partial && (
        <span className="text-caption tabular-nums text-muted-foreground">
          {shown.toLocaleString()} of {total.toLocaleString()}
        </span>
      )}
      {openAll && <Link to={openAll.to} className={LINK_CLASS}>{openAll.label}</Link>}
      {more && (
        <button type="button" onClick={more.onClick} disabled={more.busy} className={LINK_CLASS}>
          {more.busy && <Loader2 className="mr-xxs inline size-3 animate-spin" aria-hidden />}
          {more.label}
        </button>
      )}
      {children}
    </div>
  );
};

/** A queue the server could not compute: said, never shown as empty. */
export const UnavailableLine: React.FC<{ onRetry: () => void; children: React.ReactNode }> = ({ onRetry, children }) => (
  <div role="alert" className="flex flex-wrap items-center gap-xs text-caption text-warning">
    <span className="min-w-0 flex-1">{children}</span>
    <Button size="sm" variant="ghost" className="h-7" onClick={onRetry}>Retry</Button>
  </div>
);

export interface RowSelection<K extends string | number> {
  /** The selected keys, in the order of the rows on screen. */
  selected: K[];
  isSelected: (key: K) => boolean;
  toggle: (key: K) => void;
  /** The "select all" box: unchecked / checked / a dash for some. */
  allState: boolean | 'indeterminate';
  toggleAll: (checked: boolean) => void;
  clear: () => void;
}

/** A selection over the rows ON SCREEN.  `keys` is those rows' keys, in
 *  order; a key that is no longer among them is no longer selected. */
export function useRowSelection<K extends string | number>(keys: K[]): RowSelection<K> {
  const [picked, setPicked] = React.useState<Set<K>>(new Set());
  const selected = React.useMemo(() => keys.filter((k) => picked.has(k)), [keys, picked]);
  return {
    selected,
    isSelected: (key) => picked.has(key) && keys.includes(key),
    toggle: (key) => setPicked((prev) => {
      // Rebuilt from what is on screen, so the set never carries a gone row.
      const next = new Set(keys.filter((k) => prev.has(k)));
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    }),
    allState: selectAllState(selected.length, keys.length),
    toggleAll: (checked) => setPicked(checked ? new Set(keys) : new Set()),
    clear: () => setPicked(new Set()),
  };
}

/** What is selected, and what can be done with it.  Renders nothing with an
 *  empty selection, so the list does not jump for readers who never select. */
export const BulkBar: React.FC<{
  count: number;
  /** "host" → "3 hosts selected". */
  noun: string;
  onClear: () => void;
  /** A line under the actions: what the last bulk action did, honestly. */
  outcome?: React.ReactNode;
  children: React.ReactNode;
}> = ({ count, noun, onClear, outcome, children }) => {
  if (count === 0 && !outcome) return null;
  return (
    <div className="mb-xs flex min-w-0 flex-col gap-xxs border-l-2 border-l-primary pl-sm">
      {count > 0 && (
        <div role="toolbar" aria-label={`Actions for the selected ${noun}s`} className="flex min-w-0 flex-wrap items-center gap-xs">
          <span className="text-caption font-medium tabular-nums text-foreground">
            {count.toLocaleString()} {noun}{count === 1 ? '' : 's'} selected
          </span>
          {children}
          <Button size="sm" variant="ghost" className="h-7" onClick={onClear}>Clear</Button>
        </div>
      )}
      {outcome && <p role="status" className="break-words text-caption text-muted-foreground">{outcome}</p>}
    </div>
  );
};
