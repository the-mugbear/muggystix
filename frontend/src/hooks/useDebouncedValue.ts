import { useEffect, useState } from 'react';

/**
 * Returns a value that lags `delayMs` behind its input. Use to gate
 * expensive effects (network calls, large filter pipelines) behind a
 * keystroke quiet period.
 *
 *   const debouncedSearch = useDebouncedValue(searchText, 300);
 *   useEffect(() => { fetch(`?q=${debouncedSearch}`) }, [debouncedSearch]);
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
