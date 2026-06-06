import { useEffect } from 'react';

/**
 * Dev-time guard against horizontal overflow at the document level
 * (v2.43.0 — UX review #4).
 *
 * Pre-v2.43.0 the Layout shell set `overflow-x-hidden` on <main> as a
 * safety belt while cell-level truncation was still being audited.
 * That class masked real bugs — content that was wider than the
 * viewport got clipped silently and operators couldn't see what they
 * were missing.  The class is gone; this hook is the replacement.
 *
 * In development mode, it watches the document body for any condition
 * where `scrollWidth > clientWidth` and logs a warning to the console
 * with the offending element's path so the developer can fix the
 * underlying cell-level overflow.  In production it's a no-op (the
 * `if (import.meta.env.PROD) return;` guard short-circuits before any
 * observer is attached).
 *
 * Wire once at the Layout root.  ResizeObserver is supported in every
 * browser the app targets; the polyfill question doesn't apply.
 */
export function useHorizontalOverflowGuard(): void {
  useEffect(() => {
    // Only run in dev — production users shouldn't pay the observer cost,
    // and the warning is for developer attention.
    // Vite-style env check; falsy in any CRA/Jest path so safe to gate on.
    if (typeof import.meta !== 'undefined' && (import.meta as any)?.env?.PROD) {
      return;
    }
    if (typeof document === 'undefined' || typeof ResizeObserver === 'undefined') {
      return;
    }

    let lastWarn = 0;
    const WARN_INTERVAL_MS = 5000; // throttle to avoid console flood

    const check = () => {
      const body = document.body;
      if (!body) return;
      const overflow = body.scrollWidth - body.clientWidth;
      if (overflow <= 0) return;
      const now = Date.now();
      if (now - lastWarn < WARN_INTERVAL_MS) return;
      lastWarn = now;
      const culprit = findWidestDescendant(body, body.clientWidth);

      console.warn(
        '[overflow-guard] document body overflows viewport by %dpx. ' +
          'Likely culprit: %o. ' +
          'See UI_STYLE_GUIDE.md — every text-bearing cell must define ' +
          'truncate/wrap/clamp behavior.',
        overflow,
        culprit,
      );
    };

    const observer = new ResizeObserver(check);
    observer.observe(document.body);
    check();
    return () => observer.disconnect();
  }, []);
}

/**
 * Best-effort: walk visible descendants of `root` and return the widest
 * one that exceeds `maxWidth`.  Used in the console warning so the
 * developer can jump straight to the offending element instead of
 * grep-hunting through the DOM.
 */
function findWidestDescendant(root: HTMLElement, maxWidth: number): HTMLElement | null {
  let widest: HTMLElement | null = null;
  let widestWidth = maxWidth;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null);
  // Cap the walk so a 50k-row table doesn't freeze the page just to log
  // a warning.  Anything past 500 elements is probably noise anyway.
  let count = 0;
  while (walker.nextNode() && count < 500) {
    count++;
    const el = walker.currentNode as HTMLElement;
    if (!el || typeof el.scrollWidth !== 'number') continue;
    if (el.scrollWidth > widestWidth) {
      widest = el;
      widestWidth = el.scrollWidth;
    }
  }
  return widest;
}
