import type React from 'react';

/**
 * Arrow keys for a tab strip a page draws by hand (`role="tablist"` over
 * `role="tab"` children): Left / Right move to the neighbouring tab and
 * choose it, Home / End go to the first / last — what `components/ui/tabs`
 * does for the strips built from it.  Six strips had the roles and none of
 * the keys (review 2026-11-20, T7); a lint selector now refuses a
 * `role="tablist"` with no `onKeyDown`.
 *
 * The tab is chosen by clicking it, so each strip keeps its own way of
 * choosing (a state setter, a link, an address write).
 */
export function tabListKeys(event: React.KeyboardEvent<HTMLElement>): void {
  const step = { ArrowRight: 1, ArrowLeft: -1, Home: 'first', End: 'last' }[event.key];
  if (step === undefined || event.altKey || event.ctrlKey || event.metaKey) return;
  // A strip of links (the user guide's sections are `NavLink`s, which carry
  // `aria-current` and no `role="tab"`) is walked by its links: the first
  // version looked for `role="tab"` only and did nothing there (found in the
  // browser, 2026-11-21).
  const strip = event.currentTarget;
  const marked = strip.querySelectorAll<HTMLElement>('[role="tab"]');
  const tabs = Array.from(marked.length ? marked : strip.querySelectorAll<HTMLElement>('a[href], button'))
    .filter((tab) => !tab.hasAttribute('disabled') && tab.getAttribute('aria-disabled') !== 'true');
  if (tabs.length === 0) return;
  const at = tabs.findIndex((tab) => tab === document.activeElement || tab.contains(document.activeElement));
  const chosen = (tab: HTMLElement) =>
    tab.getAttribute('aria-selected') === 'true' || tab.getAttribute('aria-current') === 'page';
  const from = at >= 0 ? at : Math.max(0, tabs.findIndex(chosen));
  const to = step === 'first' ? 0
    : step === 'last' ? tabs.length - 1
      : (from + (step as number) + tabs.length) % tabs.length;
  event.preventDefault();
  tabs[to].focus();
  if (to !== from || at < 0) tabs[to].click();
}
