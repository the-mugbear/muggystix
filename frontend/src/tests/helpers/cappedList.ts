import type { CappedList } from '../../services/api';

/** A capped list as the server answers it, from the rows a test names: the
 *  whole list unless `total` says there are more. */
export const capped = <T>(items: T[], total = items.length, limit = 200): CappedList<T> => ({ items, total, limit });

/** For a mock wrapper: a test that resolves the bare rows gets the answer's shape. */
export const asCapped = <T>(answer: T[] | CappedList<T> | undefined): CappedList<T> | undefined =>
  Array.isArray(answer) ? capped(answer) : answer;
