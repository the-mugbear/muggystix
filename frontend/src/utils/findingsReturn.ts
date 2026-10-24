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
 *
 * `findingFiltersOf` is the ONE reading of a list address as what the server
 * is asked for: the list reads its own address with it, and the finding page
 * reads `from` with it for Previous / Next (5.423.0), so both name one list.
 */
import type { FindingFilters, FindingHostStatus, FindingSeverity, FindingSortField, FindingSource } from '../services/api';
import { ENDPOINT_STATES } from './findingEndpoints';
import { needsFilterOf } from './findingNeeds';

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

/** `?owner=`: `me`, `unowned`, or one person's user id (5.356.0 — the filter
 *  offered only the reader and "nobody", so findings promoted by a teammate,
 *  who owns what they promote, could not be listed).  Anything else is "any". */
export type OwnerFilterValue = 'any' | 'me' | 'unowned' | `${number}`;
export const ownerFilterOf = (raw: string | null, myId: number | null | undefined): OwnerFilterValue => {
  if (raw === 'me' || raw === 'unowned') return raw;
  if (raw && /^[1-9]\d*$/.test(raw)) return Number(raw) === myId ? 'me' : (raw as `${number}`);
  return 'any';
};

/** What a Findings list address asks the server for, without the page.  An
 *  absent `status` is the default list, `active`; `status=all` is no status
 *  filter.  With no sort the list is worst first (v5.288.0), then newest. */
export const findingFiltersOf = (params: URLSearchParams, myId: number | null | undefined): FindingFilters => {
  const f: FindingFilters = {};
  const status = params.get('status') ?? 'active';
  if (status !== 'all') f.status = status as FindingFilters['status'];
  const severity = params.get('severity');
  if (severity && severity !== 'all') f.severity = severity as FindingSeverity;
  const source = params.get('source');
  if (source && source !== 'all') f.source = source as FindingSource;
  const owner = ownerFilterOf(params.get('owner'), myId);
  if (owner === 'unowned') f.unowned = true;
  else if (owner === 'me') { if (myId != null) f.owner_id = myId; }
  else if (owner !== 'any') f.owner_id = Number(owner);
  const needs = needsFilterOf(params.get('needs'));
  if (needs !== 'all') f.needs = needs;
  const hostState = params.get('host_state');
  if ((ENDPOINT_STATES as string[]).includes(hostState ?? '')) f.host_state = hostState as FindingHostStatus;
  const search = (params.get('search') ?? '').trim();
  if (search) f.search = search;
  f.sort = (params.get('sort') as FindingSortField | null) ?? 'severity';
  f.dir = params.get('dir') === 'desc' ? 'desc' : 'asc';
  return f;
};

/** The list a finding page walks: its `from` address, else the default list. */
export const findingQueueFilters = (rawFrom: string | null, myId: number | null | undefined): FindingFilters => {
  const from = safeFindingsReturn(rawFrom);
  const at = from.indexOf('?');
  return findingFiltersOf(new URLSearchParams(at < 0 ? '' : from.slice(at + 1)), myId);
};
