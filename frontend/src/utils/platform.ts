/**
 * Platform detection helpers.
 *
 * Extracted from Layout.tsx in v4.7.5 so the platform-aware modifier
 * key label can be reused by the KeyboardShortcutsDialog (and any
 * future consumer).  Pre-v4.7.5 the shortcuts cheat-sheet hardcoded
 * "Ctrl+K" while Layout already rendered "⌘K" on Mac — the help
 * dialog disagreed with the visible chrome on the very platform
 * where the discrepancy mattered.
 */

/**
 * True iff the user-agent looks like macOS or iOS.
 *
 * Prefers the modern userAgentData.platform when available (high-entropy
 * client hints — accurate even when the legacy UA string lies about
 * platform for compatibility), falls back to navigator.platform on
 * older browsers.  Returns false during SSR (no navigator).
 */
export const isMacLike = (): boolean => {
  if (typeof navigator === 'undefined') return false;
  const uaData = (navigator as unknown as {
    userAgentData?: { platform?: string };
  }).userAgentData;
  if (uaData?.platform) {
    return /mac|ios|iphone|ipad/i.test(uaData.platform);
  }
  return /mac|iphone|ipad/i.test(navigator.platform);
};

/**
 * The user-facing label for the "command" modifier key.
 *
 * "⌘" on Mac, "Ctrl" everywhere else.  Cheap enough to compute on
 * every call — consumers that want to render it stably across
 * re-renders should wrap in useMemo with an empty deps array.
 */
export const commandModifierLabel = (): string => (isMacLike() ? '⌘' : 'Ctrl');
