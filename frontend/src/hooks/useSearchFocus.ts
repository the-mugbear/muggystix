import { RefObject, useEffect } from 'react';

/**
 * Wire a page-level search input to the global `/` keyboard shortcut.
 *
 * The Layout's `useKeyboardShortcuts` dispatches a `nm:focus-search`
 * CustomEvent on `/`.  Pages that own a primary search input opt in by
 * calling this hook with the input's ref; the hook subscribes to the
 * event, calls `.focus()` (and `.select()` so the user can immediately
 * retype to replace whatever's there), and `event.preventDefault()` was
 * already handled at the shortcut layer.
 *
 * Pre-v2.43.0 (UX review #7) the shortcut fired into the void — Layout
 * dispatched the event but no page listened, so the documented
 * "press / to focus search" promise had no payoff.
 *
 * Pattern: declare the ref in the component, pass it to this hook AND
 * to the input's `ref={...}` prop.  Disabled when the page is not
 * mounted (the useEffect unsubscribe handles that automatically).
 */
export function useSearchFocus(ref: RefObject<HTMLInputElement>): void {
  useEffect(() => {
    const handler = () => {
      const el = ref.current;
      if (!el) return;
      el.focus();
      // .select() so the user can immediately type-to-replace; if the
      // field is empty this is a no-op.
      try {
        el.select();
      } catch {
        // Some inputs (e.g. type="number" in older browsers) throw on
        // .select(); focus alone is still useful.
      }
    };
    window.addEventListener('nm:focus-search', handler);
    return () => window.removeEventListener('nm:focus-search', handler);
  }, [ref]);
}
