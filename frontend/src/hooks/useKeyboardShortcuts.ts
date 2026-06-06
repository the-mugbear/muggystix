/**
 * Global keyboard shortcut listener — audit FRX·H5.
 *
 * Supports two shapes:
 *   - Single-key shortcuts: `?`, `/`.
 *   - Two-step `g` prefix combos (vim-style): press `g`, then within
 *     `PREFIX_TIMEOUT_MS` press a second key.  Frees up the rest of
 *     the keyboard for typing — there is no other state to worry about.
 *
 * The handler skips every event whose target is an input, textarea,
 * select, or contenteditable region so we don't hijack the user's
 * typing.  Combined with the explicit shortcut list, this means we
 * touch zero keys outside our small allowlist.
 *
 * Usage:
 *   useKeyboardShortcuts({
 *     '?': () => setShortcutsOpen(true),
 *     '/': () => window.dispatchEvent(new Event('nm:focus-search')),
 *     'g h': () => navigate('/hosts'),
 *   });
 */
import { useEffect, useRef } from 'react';

const PREFIX_TIMEOUT_MS = 1500;

export type ShortcutHandler = (event: KeyboardEvent) => void;
export type ShortcutMap = Record<string, ShortcutHandler>;

const isEditableTarget = (target: EventTarget | null): boolean => {
  if (!(target instanceof HTMLElement)) return false;
  // closest() also matches the element itself, so this covers both
  // <input> directly and elements nested inside a contenteditable host.
  return !!target.closest(
    'input, textarea, select, [contenteditable=""], [contenteditable="true"]',
  );
};

export function useKeyboardShortcuts(shortcuts: ShortcutMap, enabled: boolean = true): void {
  // Ref so the handler effect doesn't re-bind on every keystroke;
  // the latest map is always available via .current.
  const shortcutsRef = useRef<ShortcutMap>(shortcuts);
  shortcutsRef.current = shortcuts;

  useEffect(() => {
    if (!enabled) return;

    let pendingPrefix: string | null = null;
    let pendingTimer: ReturnType<typeof setTimeout> | null = null;

    const clearPrefix = () => {
      pendingPrefix = null;
      if (pendingTimer) {
        clearTimeout(pendingTimer);
        pendingTimer = null;
      }
    };

    const handler = (event: KeyboardEvent) => {
      // Ignore modifier-combos — we don't own those (Cmd+K palette
      // lives in Layout, browser shortcuts everywhere else).
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isEditableTarget(event.target)) return;

      // Normalise to lowercase for matching; `?` is `shift+/` on most
      // layouts, so we read `event.key` directly which gives us the
      // glyph the user typed.
      const key = event.key;

      // Two-step combo continuation.
      if (pendingPrefix) {
        const combo = `${pendingPrefix} ${key.toLowerCase()}`;
        clearPrefix();
        const handlerFn = shortcutsRef.current[combo];
        if (handlerFn) {
          event.preventDefault();
          handlerFn(event);
        }
        return;
      }

      // Single-key shortcut (e.g. `?`, `/`).
      const single = shortcutsRef.current[key];
      if (single) {
        event.preventDefault();
        single(event);
        return;
      }

      // Start a prefix sequence if any registered shortcut uses this
      // key as the first step.  Currently we only support `g`-prefixed
      // combos, but the check is generic.
      const lowered = key.toLowerCase();
      const hasPrefix = Object.keys(shortcutsRef.current).some((seq) =>
        seq.startsWith(`${lowered} `),
      );
      if (hasPrefix) {
        pendingPrefix = lowered;
        pendingTimer = setTimeout(clearPrefix, PREFIX_TIMEOUT_MS);
      }
    };

    window.addEventListener('keydown', handler);
    return () => {
      window.removeEventListener('keydown', handler);
      clearPrefix();
    };
  }, [enabled]);
}
