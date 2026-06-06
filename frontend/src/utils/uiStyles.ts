/**
 * Tiny shared UI utilities. Tailwind v4 + Radix is the primary surface
 * system; these helpers cover the cases where Tailwind can't help
 * (runtime null fallback) or where a tiny shared sx-like style is
 * useful in legacy spots.
 *
 * NOTE: pre-v4 this file also exported MUI-targeted `sx` constants
 * (`raisedSurfaceSx`, `wrappingChipSx` etc.). MUI was removed in
 * alpha.22; those exports are gone. Prefer the Tailwind utilities
 * documented in `documentation/UI_STYLE_GUIDE.md`.
 */

/** Render a safe fallback when a value is null/undefined/empty. */
export const safeFallback = (
  value: string | null | undefined,
  fallback = '—',
): string => (value && value.trim() ? value : fallback);

// ---------------------------------------------------------------------------
// Sticky positioning helper — read the Layout-published CSS vars so
// sticky controls (filter bars, action toolbars) stay clear of the
// fixed topbar + secondary nav strip at any zoom level / long project
// name. Replaces hard-coded `sticky top-md` patterns that slid under
// the chrome at zoom ≥ 125%.
// ---------------------------------------------------------------------------

/** Reusable `style` for `sticky` controls that should clear the chrome. */
export const stickyBelowChrome: React.CSSProperties = {
  top: 'calc(var(--topbar-h, 76px) + var(--secondary-nav-h, 0px) + 0.5rem)',
};

/* Re-import React's type only — kept inline to avoid a top-level React
 * import in a pure-utility module. */
import type * as React from 'react';
