/**
 * A development-time check that a toggle has an accessible name.
 *
 * A Radix Switch or Checkbox is a `<button>` with no text of its own: without
 * a name a screen reader announces "switch, off" and nothing else.  Any of
 * these names it, and a type cannot tell them apart (a wrapping `<label>` is
 * the parent's business, not a prop):
 *
 *   - `aria-label` / `aria-labelledby` on the control;
 *   - a `<label htmlFor={id}>` (the `Label` primitive) for its `id`;
 *   - a `<label>` wrapped around it;
 *   - `title`.
 *
 * So the shared wrappers ask the DOM once, after mounting, and warn in the
 * console when none is there.  Nothing runs in a production build.
 */
import { useEffect } from 'react';

export function hasAccessibleName(el: HTMLElement): boolean {
  if (el.getAttribute('aria-label')?.trim()) return true;
  if (el.getAttribute('title')?.trim()) return true;
  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy && labelledBy.split(/\s+/).some((id) => id && document.getElementById(id))) return true;
  // `labels`: every <label> that names a labelable element, by `for` or by wrapping it.
  const labels = (el as HTMLButtonElement).labels;
  return !!labels && Array.from(labels).some((label) => !!label.textContent?.trim());
}

export function useAccessibleNameCheck(ref: React.RefObject<HTMLElement | null>, control: string): void {
  useEffect(() => {
    if (process.env.NODE_ENV === 'production') return;
    const el = ref.current;
    if (el && !hasAccessibleName(el)) {
      // eslint-disable-next-line no-console
      console.warn(
        `<${control}> has no accessible name: give it aria-label or aria-labelledby, `
        + 'a <Label htmlFor> for its id, or wrap it in a <label>.',
      );
    }
    // Once, when the control mounts: its label is rendered in the same commit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
