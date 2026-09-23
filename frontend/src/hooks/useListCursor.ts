/**
 * Row cursor for a list page — the Hosts keyboard model (j/k or ↓/↑ move,
 * Enter opens) for the other lists (review 2026-09-23 B-UI-6).
 *
 * A window listener with the same guards as Hosts: never while typing in a
 * field, never with a modifier, never while a dialog is open, and Enter on
 * a focused button or link is left to that element.  The cursor row is the
 * one whose index equals `cursor`; mark it with `cursorRowProps(i)` so it
 * is highlighted and scrolled into view.  `resetKey` (a page, a filter)
 * clears the cursor when the rows it indexed are replaced.
 */
import { useEffect, useRef, useState } from 'react';
import { cn } from '../utils/cn';

export const LIST_CURSOR_CLASS = 'bg-accent ring-1 ring-inset ring-ring';

const isTyping = (t: HTMLElement | null) =>
  !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);

export function useListCursor(
  count: number,
  onOpen: (index: number) => void,
  { enabled = true, resetKey }: { enabled?: boolean; resetKey?: unknown } = {},
) {
  const [cursor, setCursor] = useState(-1);
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;

  useEffect(() => { setCursor(-1); }, [resetKey]);
  // A shorter list (a filter, a removal) must not leave the cursor past its end.
  useEffect(() => { setCursor((c) => (c >= count ? count - 1 : c)); }, [count]);

  useEffect(() => {
    if (cursor < 0 || typeof document === 'undefined') return;
    document.querySelector('[data-list-cursor="true"]')?.scrollIntoView?.({ block: 'nearest' });
  }, [cursor]);

  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (isTyping(t)) return;
      if ((t?.tagName === 'BUTTON' || t?.tagName === 'A') && (e.key === 'Enter' || e.key === ' ')) return;
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return;
      if (count === 0) return;
      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault();
        setCursor((c) => Math.min(c + 1, count - 1));
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault();
        setCursor((c) => (c <= 0 ? 0 : c - 1));
      } else if (e.key === 'Enter') {
        const c = cursorRef.current;
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

  return { cursor, setCursor, cursorRowProps };
}
