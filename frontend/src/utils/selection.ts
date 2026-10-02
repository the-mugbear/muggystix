/**
 * What a "select all" checkbox shows for the rows it covers: unchecked for
 * none, checked for all, `'indeterminate'` (a dash) for some.
 *
 * A header box that is merely `checked={all}` says "nothing selected" over a
 * partial selection, and one drawn with the tick for "some" says "everything
 * is" (visual pass 2026-10-01).  Radix answers a click on an indeterminate
 * box with `true`, so clicking a partial selection selects the rest.
 */
export const selectAllState = (selectedCount: number, total: number): boolean | 'indeterminate' => {
  if (total <= 0 || selectedCount <= 0) return false;
  return selectedCount >= total ? true : 'indeterminate';
};
