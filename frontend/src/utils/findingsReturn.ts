/**
 * Findings queue continuity (UX review M1, 5.199.0).
 *
 * The list keeps its whole queue (filters + page + sort) in the URL; a row
 * link carries that URL to the detail as `from`, and the detail's explicit
 * "Findings" action returns to it instead of the bare, unfiltered route.
 *
 * `from` is user-controllable (it rides in the URL), so it is validated as an
 * internal /findings path + search only — never a scheme, host, or another
 * route — and anything else falls back to the list default.
 */
export const FINDINGS_ROUTE = '/findings';
export const RETURN_PARAM = 'from';

/** A validated internal return target, or the bare list route. */
export const safeFindingsReturn = (raw: string | null | undefined): string => {
  if (!raw) return FINDINGS_ROUTE;
  // Must be a same-origin path under /findings: no scheme, no host
  // (`//evil`), no other route, and nothing that isn't a path/query.
  if (!raw.startsWith(FINDINGS_ROUTE)) return FINDINGS_ROUTE;
  if (raw.startsWith('//') || raw.includes('://')) return FINDINGS_ROUTE;
  const rest = raw.slice(FINDINGS_ROUTE.length);
  // Allowed continuations: end, or a query string. A sub-path (a detail page)
  // is not a queue and would loop.
  if (rest !== '' && !rest.startsWith('?')) return FINDINGS_ROUTE;
  if (/[\s<>"'`\\]/.test(raw)) return FINDINGS_ROUTE;
  return raw;
};

/** Detail href that carries the current list URL for the return action. */
export const findingDetailHref = (findingId: number, listSearch: string): string => {
  const from = `${FINDINGS_ROUTE}${listSearch ? (listSearch.startsWith('?') ? listSearch : `?${listSearch}`) : ''}`;
  // Only worth carrying when there is a queue to return to.
  return from === FINDINGS_ROUTE
    ? `${FINDINGS_ROUTE}/${findingId}`
    : `${FINDINGS_ROUTE}/${findingId}?${RETURN_PARAM}=${encodeURIComponent(from)}`;
};
