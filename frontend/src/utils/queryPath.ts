/**
 * Addresses into the Query page and out of it (review 2026-10-16, Q7 / O4):
 * the committed query is `?q=`, a later page of a listing `?after=` (the
 * answer's `next_after`).  Built here, not in the page, so a component that
 * links into Query does not import the page.
 */
import { buildHostsUrl } from './drilldownLinks';

export const QUERY_PATH = '/query';

export const queryPath = (q: string, after?: string | null): string =>
  `${QUERY_PATH}?q=${encodeURIComponent(q)}${after ? `&after=${encodeURIComponent(after)}` : ''}`;

/** A value written into a query as the language reads it back: quoted, escaped. */
export const queryString = (value: string): string => `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

/** The Hosts page's query as a Query-page query: `hosts <terms>` lists the same hosts. */
export const hostsInQueryPath = (hostsQ: string): string =>
  queryPath(hostsQ.trim() ? `hosts ${hostsQ.trim()}` : 'hosts');

/** The Query answer's `hosts_q` on the Hosts page ("" = every host). */
export const hostsPageFor = (hostsQ: string): string => buildHostsUrl({ q: hostsQ.trim() || undefined });

/** Every scanner row of one issue, in Query.  A `row:` key names a row with no
 *  issue of its own, which no `where` names: null. */
export const issueInQueryPath = (issueKey: string): string | null =>
  issueKey.startsWith('row:') ? null : queryPath(`observations | where issue=${queryString(issueKey)}`);
