/**
 * The pieces every Operations list shares (5.329.0; one list at a time since
 * 5.331.0 — UI_STYLE_GUIDE §42).
 *
 * The page shows ONE list, complete, a page at a time.  These are the one
 * footer, the one set of states, the one bulk bar and the one selection rule
 * its tabs use:
 *
 *  - `PagedFooter` — "1–10 of N", previous / next, and — only where a page
 *    lists EXACTLY this list — one "Open … in Hosts" link.  No "Show more",
 *    no samples.
 *  - `ListBody` — the panel's states: loading (a skeleton; the tab bar stays),
 *    could not be checked (said, with Retry — never an empty list), empty
 *    (one line saying what would put something here), or the rows.
 *  - `FilterChips` — filter chips with their counts (the tiers, the kinds of
 *    test).
 *  - `useRowSelection` — a selection that is always a subset of the rows on
 *    screen (§41): a row that leaves the list leaves the selection, so a bulk
 *    action never reaches a row the reader cannot see.
 *  - `BulkBar` — what is selected and what can be done with it.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { Button } from '../ui/button';
import { cn } from '../../utils/cn';
import { selectAllState } from '../../utils/selection';

const LINK_CLASS =
  'rounded text-caption text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

/** Where the reader is in a paged list. */
export interface Pager {
  /** Zero-based. */
  page: number;
  pageSize: number;
  /** The server's count of the whole list. */
  total: number;
  onPage: (page: number) => void;
}

/** "1–10 of 112" — the rows on screen, by position in the whole list. */
export const pageRange = (pager: Pager, shown: number): string => {
  const first = pager.page * pager.pageSize + 1;
  const last = pager.page * pager.pageSize + shown;
  return `${first.toLocaleString()}–${last.toLocaleString()} of ${pager.total.toLocaleString()}`;
};

export const PagedFooter: React.FC<{
  pager: Pager;
  /** Rows on screen. */
  shown: number;
  /** What the rows are, for the buttons' names ("hosts"). */
  noun: string;
  /** The page that lists exactly this list — the label states what it opens. */
  openAll?: { to: string; label: string };
  children?: React.ReactNode;
}> = ({ pager, shown, noun, openAll, children }) => {
  const pages = Math.max(1, Math.ceil(pager.total / pager.pageSize));
  return (
    <div className="mt-xs flex min-w-0 flex-wrap items-center gap-x-md gap-y-xxs">
      <span className="text-caption tabular-nums text-muted-foreground">{pageRange(pager, shown)}</span>
      {pages > 1 && (
        <span className="inline-flex items-center gap-xxs">
          <Button
            size="sm" variant="ghost" className="size-7 p-0"
            disabled={pager.page <= 0}
            onClick={() => pager.onPage(pager.page - 1)}
            aria-label={`Previous ${pager.pageSize} ${noun}`}
          >
            <ChevronLeft className="size-4" aria-hidden />
          </Button>
          <span className="text-caption tabular-nums text-muted-foreground">
            page {(pager.page + 1).toLocaleString()} of {pages.toLocaleString()}
          </span>
          <Button
            size="sm" variant="ghost" className="size-7 p-0"
            disabled={pager.page >= pages - 1}
            onClick={() => pager.onPage(pager.page + 1)}
            aria-label={`Next ${pager.pageSize} ${noun}`}
          >
            <ChevronRight className="size-4" aria-hidden />
          </Button>
        </span>
      )}
      {openAll && <Link to={openAll.to} className={LINK_CLASS}>{openAll.label}</Link>}
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

/** How a tab's list is doing, beside its rows. */
export interface ListState {
  loading: boolean;
  /** Why the last read failed; null when it worked. */
  error: string | null;
  onRetry: () => void;
}

/** Lines of a list that is on its way: the panel keeps its place. */
const ListSkeleton: React.FC<{ label: string }> = ({ label }) => (
  <div role="status" aria-label={label} className="flex flex-col gap-xs py-xs">
    {[0, 1, 2, 3, 4, 5].map((i) => (
      <div key={i} className="h-6 animate-pulse rounded-control bg-muted" aria-hidden />
    ))}
    <span className="sr-only">{label}</span>
  </div>
);

/**
 * A tab's list in one of its four states.  `rows === null` is "not loaded":
 * a skeleton while the request is out, "could not be checked" when it failed
 * — never the empty line, which is a statement about the list.
 */
export function ListBody<T>({ rows, state, what, empty, children }: {
  rows: T[] | null;
  state: ListState;
  /** The list, for the loading and failure lines ("the findings that need you"). */
  what: string;
  /** One line: nothing is here, and what would put something here. */
  empty: React.ReactNode;
  children: (rows: T[]) => React.ReactNode;
}): React.ReactElement {
  if (rows === null) {
    if (state.error && !state.loading) {
      return (
        <UnavailableLine onRetry={state.onRetry}>
          Could not be checked — {what} could not be loaded ({state.error}). This is not an empty list.
        </UnavailableLine>
      );
    }
    return <ListSkeleton label={`Loading ${what}…`} />;
  }
  return (
    <>
      {state.error && (
        <UnavailableLine onRetry={state.onRetry}>
          These rows could not be refreshed ({state.error}) — they are as last loaded.
        </UnavailableLine>
      )}
      {rows.length === 0
        ? <p className="text-metadata text-muted-foreground">{empty}</p>
        : children(rows)}
    </>
  );
}

/** One chip of a filter row: the selected one is filled; `strong` marks the
 *  one to act on first by weight, not by size or colour. */
export const filterChipClass = (on: boolean, strong = false) => cn(
  'inline-flex max-w-full items-center gap-xxs rounded-chip border px-xs py-px text-caption',
  'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
  on ? 'border-primary bg-primary text-primary-foreground' : 'border-border text-foreground hover:bg-accent',
  strong && 'font-semibold',
);

export interface FilterChip<K extends string | number> {
  key: K;
  label: string;
  count: number | null;
  title?: string;
  strong?: boolean;
}

/** Filter chips with their whole-list counts.  "All" first; a chip with
 *  nothing behind it is text, not a button. */
export function FilterChips<K extends string | number>({
  label, allLabel, allCount, chips, selected, onSelect,
}: {
  /** The group's accessible name ("Filter by tier"). */
  label: string;
  allLabel: string;
  allCount: number | null;
  chips: Array<FilterChip<K>>;
  selected: K | null;
  onSelect?: (key: K | null) => void;
}): React.ReactElement {
  const n = (v: number | null) => (v == null ? '—' : v.toLocaleString());
  return (
    <div role="group" aria-label={label} className="mb-sm flex min-w-0 flex-wrap items-center gap-xs">
      <button type="button" aria-pressed={selected == null} onClick={() => onSelect?.(null)}
        disabled={!onSelect} className={filterChipClass(selected == null)}>
        {allLabel} <span className="tabular-nums">{n(allCount)}</span>
      </button>
      {chips.map((chip) => {
        const on = selected === chip.key;
        return chip.count !== 0 || on ? (
          <button
            key={chip.key}
            type="button"
            aria-pressed={on}
            disabled={!onSelect}
            onClick={() => onSelect?.(on ? null : chip.key)}
            title={on ? `Show ${allLabel.toLowerCase()}` : (chip.title ?? `Show only: ${chip.label}`)}
            className={filterChipClass(on, chip.strong)}
          >
            <span className="min-w-0 truncate">{chip.label}</span>
            <span className="tabular-nums">{n(chip.count)}</span>
          </button>
        ) : (
          <span key={chip.key} className="inline-flex items-center gap-xxs px-xs text-caption text-muted-foreground">
            {chip.label} <span className="tabular-nums">0</span>
          </span>
        );
      })}
    </div>
  );
}

/** The age column of every tab: compact, and a dash when nothing recorded it. */
export const WaitingCell: React.FC<{ waiting: string; title?: string }> = ({ waiting, title }) => (
  <span
    className="text-caption tabular-nums text-muted-foreground"
    aria-label={waiting ? `waiting ${waiting}` : 'waiting time not recorded'}
    title={title ?? (waiting ? `Waiting ${waiting}` : 'No change was recorded for this row.')}
  >
    {waiting || '—'}
  </span>
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
