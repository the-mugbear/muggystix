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
  const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'))
    .filter((tab) => !tab.hasAttribute('disabled') && tab.getAttribute('aria-disabled') !== 'true');
  if (tabs.length === 0) return;
  const at = tabs.findIndex((tab) => tab === document.activeElement || tab.contains(document.activeElement));
  const from = at >= 0 ? at : Math.max(0, tabs.findIndex((tab) => tab.getAttribute('aria-selected') === 'true'));
  const to = step === 'first' ? 0
    : step === 'last' ? tabs.length - 1
      : (from + (step as number) + tabs.length) % tabs.length;
  event.preventDefault();
  tabs[to].focus();
  if (to !== from || at < 0) tabs[to].click();
}
