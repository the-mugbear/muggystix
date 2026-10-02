/**
 * Row cursor for a list page — the Hosts keyboard model (j/k or ↓/↑ move,
 * Enter opens) for the other lists (review 2026-09-23 B-UI-6).
 *
 * A window listener guarded by `utils/keyboard.isPageShortcutEvent`: never
 * while typing in a field, never with a modifier, never while a dialog, a
 * Select or a menu is open, and Enter on a focused button or link is left to
 * that element.  Mark the cursor row with `cursorRowProps(i)` so it is
 * highlighted and scrolled into view.  `resetKey` (a page, a filter) clears
 * the cursor when the rows it indexed are replaced.
 *
 * **Anchor by id when the list can change under the reader** (review
 * 2026-10-01 S2).  A bare index is only right while the rows stay put: on a
 * newest-first list that re-reads itself, one arriving row shifts every
 * other, and the highlight — with whatever the page's keys act on — lands on
 * a different row.  Pass `getId` (the id of row `index`) and the cursor
 * follows its ROW: after a reload it is wherever that id now is; if the row
 * is gone, it is the row that took its place.  The index is derived during
 * render, so there is no frame in which the cursor names the wrong row.
 */
import { useEffect, useRef, useState } from 'react';
import { cn } from '../utils/cn';
import { isPageShortcutEvent } from '../utils/keyboard';

/**
 * The cursor row's look, on every list (Hosts included): a primary tint and a
 * 2px inset ring.
 *
 * It was `bg-accent ring-1`, and the fill never painted: `--accent` (like
 * `--muted` and `--border`) carries its own alpha, and tailwind.config wrapped
 * each token (until 5.327.0) as `hsl(var(--x) / <alpha-value>)`, so the browser got the
 * invalid `hsl(H S% L% / 0.12 / 1)` and dropped the declaration.  What was left
 * was a 1px ring nobody saw (walkthrough 2026-10-01).  `--primary` and
 * `--ring` are opaque tokens, so these classes resolve in every theme.
 */
export const LIST_CURSOR_CLASS = 'bg-primary/10 ring-2 ring-inset ring-ring';

export type ListCursorId = string | number;

interface CursorState {
  index: number;
  /** The id of the row the cursor was put on (only with `getId`). */
  id: ListCursorId | null;
}

const NONE: CursorState = { index: -1, id: null };

export interface UseListCursorOptions {
  enabled?: boolean;
  resetKey?: unknown;
  /** The id of row `index`: anchors the cursor to its row across reloads. */
  getId?: (index: number) => ListCursorId | null | undefined;
}

export function useListCursor(
  count: number,
  onOpen: (index: number) => void,
  { enabled = true, resetKey, getId }: UseListCursorOptions = {},
) {
  const [state, setState] = useState<CursorState>(NONE);
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  const getIdRef = useRef(getId);
  getIdRef.current = getId;

  // Where the cursor IS, given the rows as they are now.
  let cursor = state.index;
  if (cursor >= 0) {
    if (getId && state.id != null && !(cursor < count && getId(cursor) === state.id)) {
      let found = -1;
      for (let i = 0; i < count; i += 1) {
        if (getId(i) === state.id) { found = i; break; }
      }
      // Gone: the row that took its place.
      cursor = found >= 0 ? found : Math.min(cursor, count - 1);
    } else if (cursor >= count) {
      // A shorter list (a filter, a removal) must not leave the cursor past
      // its end.
      cursor = count - 1;
    }
  }
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  const cursorId = cursor >= 0 && getId ? getId(cursor) ?? null : null;

  const place = (index: number) => {
    setState(index < 0 ? NONE : { index, id: getIdRef.current?.(index) ?? null });
  };
  const placeRef = useRef(place);
  placeRef.current = place;

  useEffect(() => { setState(NONE); }, [resetKey]);

  // Keep the stored cursor in step with the derived one, so "the row that
  // took its place" becomes the anchor.  An anchored cursor waits out an
  // empty list (a reload that shows no rows while it loads): its row is
  // looked for again when the rows are back.
  useEffect(() => {
    const anchored = getIdRef.current != null;
    if (anchored && count === 0) return;
    setState((prev) => {
      if (prev.index < 0) return prev;
      const id = anchored ? cursorId : null;
      return prev.index === cursor && prev.id === id ? prev : { index: cursor, id };
    });
  }, [cursor, cursorId, count]);

  useEffect(() => {
    if (cursor < 0 || typeof document === 'undefined') return;
    document.querySelector('[data-list-cursor="true"]')?.scrollIntoView?.({ block: 'nearest' });
  }, [cursor]);

  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (e: KeyboardEvent) => {
      // Holding j/k walks the list; Enter opens once per press.
      if (!isPageShortcutEvent(e, { allowRepeat: e.key !== 'Enter' })) return;
      const t = e.target as HTMLElement | null;
      if ((t?.tagName === 'BUTTON' || t?.tagName === 'A') && (e.key === 'Enter' || e.key === ' ')) return;
      if (count === 0) return;
      const c = cursorRef.current;
      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault();
        placeRef.current(Math.min(c + 1, count - 1));
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault();
        placeRef.current(c <= 0 ? 0 : c - 1);
      } else if (e.key === 'Enter') {
        if (c >= 0 && c < count) {
          e.preventDefault();
          onOpenRef.current(c);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [count, enabled]);

  /** Props for row `index`: the cursor marker and highlight, merged with the
   *  row's own classes. */
  const cursorRowProps = (index: number, className?: string) =>
    index === cursor
      ? { 'data-list-cursor': 'true' as const, className: cn(className, LIST_CURSOR_CLASS) }
      : { className };

  return { cursor, cursorId, setCursor: place, cursorRowProps };
}
