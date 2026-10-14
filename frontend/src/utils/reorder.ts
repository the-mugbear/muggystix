/**
 * Reordering a list by hand (drag a row, Move up / Move down).  Pure: the
 * caller's list is never changed.
 */

/**
 * `list` with the item at `from` taken out and put back at `to` — the index it
 * has in the answer, so the items between the two shift by one towards `from`.
 * An index outside the list, or `from === to`, answers the same list.
 */
export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  const inside = (i: number) => Number.isInteger(i) && i >= 0 && i < list.length;
  if (!inside(from) || !inside(to) || from === to) return list as T[];
  const next = list.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}
