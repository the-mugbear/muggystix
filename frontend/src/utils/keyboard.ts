/**
 * "Is this key press for the page?" — the ONE test every single-letter
 * shortcut asks before it acts (review 2026-10-01 S1).
 *
 * The page shortcuts (`j` / `k`, `a` / `r` on Proposals, `g h`…) are window
 * listeners, so they see every key the page's widgets did not stop.  Radix's
 * typeahead does not stop propagation: with a Select trigger focused, or a
 * Select list or a menu open, a letter both moves the widget's highlight AND
 * reached the page — `a` on the Status select accepted the proposal under
 * the cursor.  Each listener had its own copy of the "is the user typing"
 * check and none of them knew about a popup.
 *
 * A key press is NOT the page's when:
 *   - a modifier is held (the browser's, or the command palette's);
 *   - the key is auto-repeating (holding `a` must not accept a row per
 *     repeat) — a listener that moves a cursor opts in with `allowRepeat`;
 *   - the target is where text is typed (input, textarea, select,
 *     contenteditable);
 *   - the target is a Select trigger, or inside an open list, menu or any
 *     popper content (their typeahead owns the letters);
 *   - a dialog is open — unless the listener's own surface IS a dialog
 *     (`allowDialog`: the Hosts inspector is a sheet that j/k step through).
 */
const TEXT_ENTRY = 'input, textarea, select, [contenteditable=""], [contenteditable="true"]';
const POPUP = '[role="listbox"], [role="menu"], [role="combobox"], [data-radix-popper-content-wrapper]';
const DIALOG = '[role="dialog"], [role="alertdialog"]';

const asElement = (target: EventTarget | null): Element | null => {
  if (!target || typeof (target as Element).closest !== 'function') return null;
  return target as Element;
};

/** The target is a field the user types into. */
export function isTextEntryTarget(target: EventTarget | null): boolean {
  const el = asElement(target);
  if (!el) return false;
  if ((el as HTMLElement).isContentEditable) return true;
  return el.closest(TEXT_ENTRY) !== null;
}

/** The target is a Select trigger, or inside a list, menu or popper. */
export function isPopupTarget(target: EventTarget | null): boolean {
  return asElement(target)?.closest(POPUP) != null;
}

export interface PageShortcutOptions {
  /** Accept auto-repeat (cursor movement).  Default false. */
  allowRepeat?: boolean;
  /** Accept the key while a dialog is open.  Default false. */
  allowDialog?: boolean;
}

type KeyLike = Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey' | 'repeat' | 'target'>;

export function isPageShortcutEvent(
  e: KeyLike,
  { allowRepeat = false, allowDialog = false }: PageShortcutOptions = {},
): boolean {
  if (e.ctrlKey || e.metaKey || e.altKey) return false;
  if (e.repeat && !allowRepeat) return false;
  if (isTextEntryTarget(e.target) || isPopupTarget(e.target)) return false;
  if (!allowDialog && typeof document !== 'undefined' && document.querySelector(DIALOG)) return false;
  return true;
}
