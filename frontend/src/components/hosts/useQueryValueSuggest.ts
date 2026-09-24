import { useEffect, useRef, useState } from 'react';
import { suggestHostQueryValues, type HostQueryValueSuggestion } from '../../services/api';

const DEBOUNCE_MS = 200;
const CACHE_MAX = 100;

/** Value sources the server does not enumerate — never asked. */
const LOCAL_ONLY = new Set(['enum', 'window', 'free']);

/**
 * The server's values for one query field containing `partial` (5.291.0).
 *
 * The page's facet lists are capped and follow the active filters, so a rare
 * port or a CVE could not be completed from them; this asks
 * `GET /hosts/query/suggest` instead.  Debounced, the previous request aborted,
 * answers cached per (field, text) for the life of the bar.  `values` is null
 * until an answer for exactly this (field, text) is in, so the caller keeps
 * showing the page's facets meanwhile — and on failure, which is silent: the
 * facets are still a useful answer.
 */
export function useQueryValueSuggest(
  field: string | null,
  valueSource: string | null,
  partial: string,
): HostQueryValueSuggestion[] | null {
  const cache = useRef(new Map<string, HostQueryValueSuggestion[] | null>());
  const [result, setResult] = useState<{ key: string; values: HostQueryValueSuggestion[] | null } | null>(null);
  const key = field && valueSource && !LOCAL_ONLY.has(valueSource) ? `${field}\u0000${partial}` : null;

  useEffect(() => {
    if (!key || !field) return;
    if (cache.current.has(key)) {
      setResult({ key, values: cache.current.get(key) ?? null });
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      suggestHostQueryValues(field, partial, controller.signal)
        .then((r) => {
          // An unsupported field or a timed-out lookup is "no answer", not
          // "no values": leave the facets on screen.
          const values = r.supported && !r.timed_out ? r.values : null;
          if (cache.current.size >= CACHE_MAX) cache.current.clear();
          cache.current.set(key, values);
          if (!controller.signal.aborted) setResult({ key, values });
        })
        .catch(() => { /* best-effort: the page's facets remain */ });
    }, DEBOUNCE_MS);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [key, field, partial]);

  return result && result.key === key ? result.values : null;
}
