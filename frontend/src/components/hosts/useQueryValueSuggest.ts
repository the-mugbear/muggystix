import { useQuery } from '@tanstack/react-query';
import { suggestHostQueryValues, type HostQueryValueSuggestion } from '../../services/api';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { rememberFor } from '../../lib/query';

const DEBOUNCE_MS = 200;
/** How long an answer for one (field, text) is kept: typing back over the
 *  same text asks nothing. */
const REMEMBER_MS = 5 * 60_000;

/** Value sources the server does not enumerate — never asked. */
const LOCAL_ONLY = new Set(['enum', 'window', 'free']);

/**
 * The server's values for one query field containing `partial` (5.291.0).
 *
 * The page's facet lists are capped and follow the active filters, so a rare
 * port or a CVE could not be completed from them; this asks
 * `GET /hosts/query/suggest` instead.  Debounced, and an answer is remembered
 * per (field, text): one already in hand is returned at once, with no wait.
 * `values` is null until an answer for exactly this (field, text) is in, so
 * the caller keeps showing the page's facets meanwhile — and on failure, which
 * is silent: the facets are still a useful answer.
 */
export function useQueryValueSuggest(
  field: string | null,
  valueSource: string | null,
  partial: string,
): HostQueryValueSuggestion[] | null {
  const asked = !!field && !!valueSource && !LOCAL_ONLY.has(valueSource);
  const typed = `${field ?? ''}\u0000${partial}`;
  // The request waits for a pause in typing; the key does not, so an answer
  // that is remembered shows without one.
  const settled = useDebouncedValue(typed, DEBOUNCE_MS);
  const query = useQuery({
    queryKey: ['suggestHostQueryValues', field, partial],
    queryFn: ({ signal }) => suggestHostQueryValues(field ?? '', partial, signal),
    enabled: asked && settled === typed,
    ...rememberFor(REMEMBER_MS),
  });
  if (!asked || !query.data) return null;
  // An unsupported field or a timed-out lookup is "no answer", not "no
  // values": leave the facets on screen.
  return query.data.supported && !query.data.timed_out ? query.data.values : null;
}
