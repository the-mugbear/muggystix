import { useQuery } from '@tanstack/react-query';
import { getQueryColumnValues, type QueryColumnValues } from '../../services/api';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { useProjectId } from '../../hooks/useProjectId';
import { rememberFor } from '../../lib/query';

const DEBOUNCE_MS = 200;
/** An answer for one (column, text) is kept: typing back over it asks nothing. */
const REMEMBER_MS = 5 * 60_000;

/**
 * The project's values of one text column containing `partial` (5.408.0) —
 * what the Query editor offers after `where col = "`.  Debounced; null until
 * the answer for exactly this (column, text) is in, and on a failure or a
 * timed-out lookup (the editor then simply offers nothing).  The Hosts page's
 * `useQueryValueSuggest` is the same shape for its language.
 */
export function useQueryColumnValues(
  need: { dataset: string; column: string; partial: string } | null,
): QueryColumnValues['values'] | null {
  const projectId = useProjectId();
  const typed = need ? `${need.dataset}\u0000${need.column}\u0000${need.partial}` : '';
  const settled = useDebouncedValue(typed, DEBOUNCE_MS);
  const query = useQuery({
    queryKey: ['getQueryColumnValues', projectId, need?.dataset ?? '', need?.column ?? '', need?.partial ?? ''],
    queryFn: ({ signal }) => getQueryColumnValues(projectId, need?.dataset ?? '', need?.column ?? '', need?.partial ?? '', signal),
    enabled: !!need && settled === typed,
    ...rememberFor(REMEMBER_MS),
  });
  if (!need || !query.data || !query.data.supported || query.data.timed_out) return null;
  return query.data.values;
}
