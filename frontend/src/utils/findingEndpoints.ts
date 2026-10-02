/**
 * A finding's endpoints, client-free (review 2026-10-01 C2 / B13).
 *
 * A findings LIST row carries `hosts` = a preview of at most five endpoints
 * and `host_count` = the true total; only `GET /findings/{id}` has every
 * endpoint.  Nothing may read `hosts.length` of a list row as the total.
 */
import type { Finding, FindingHostInfo, FindingHostStatus } from '../services/api';
import { compareAddresses } from './ipAddress';

/** Endpoint rows the finding page renders before "Show more". */
export const ENDPOINT_CAP = 100;
/** `PATCH /findings/{id}/endpoints` takes at most this many ids per call. */
export const ENDPOINT_BULK_MAX = 500;

export const ENDPOINT_STATES: FindingHostStatus[] = ['open', 'retest', 'remediated', 'false_positive'];

type ListRow = Pick<Finding, 'hosts' | 'host_count'>;

/** True when the row's `hosts` is a cut preview, not every endpoint. */
export const endpointPreviewIsCut = (f: ListRow): boolean =>
  (f.host_count ?? 0) > (f.hosts?.length ?? 0);

const endpointLabel = (h: Pick<FindingHostInfo, 'ip_address' | 'hostname'>): string =>
  (h.ip_address ?? '—') + (h.hostname ? ` (${h.hostname})` : '');

/** The list row's tooltip: the previewed endpoints, and how many are not
 *  listed — never a list that reads as complete when it is cut. */
export const endpointPreviewTitle = (
  hosts: ReadonlyArray<Pick<FindingHostInfo, 'ip_address' | 'hostname'>>,
  hostCount: number,
): string => {
  const shown = hosts.map(endpointLabel).join(', ');
  const rest = hostCount - hosts.length;
  return rest > 0 ? `${shown}, and ${rest.toLocaleString()} more` : shown;
};

export type EndpointStateFilter = FindingHostStatus | 'all';

/** Endpoints in one state whose address, hostname or name contains `text`. */
export const filterEndpoints = <H extends Pick<FindingHostInfo, 'ip_address' | 'hostname' | 'fqdn' | 'host_status'>>(
  hosts: ReadonlyArray<H>,
  state: EndpointStateFilter,
  text: string,
): H[] => {
  const needle = text.trim().toLowerCase();
  return hosts.filter((h) => {
    if (state !== 'all' && h.host_status !== state) return false;
    if (!needle) return true;
    return [h.ip_address, h.hostname, h.fqdn].some((v) => !!v && v.toLowerCase().includes(needle));
  });
};

/** Per-state counts: the server's roll-up when sent, else counted here. */
export const endpointStateCounts = (
  hosts: ReadonlyArray<Pick<FindingHostInfo, 'host_status'>>,
  counts?: Partial<Record<FindingHostStatus, number>> | null,
): Record<FindingHostStatus, number> => {
  const out: Record<FindingHostStatus, number> = { open: 0, retest: 0, remediated: 0, false_positive: 0 };
  if (counts && Object.keys(counts).length > 0) {
    ENDPOINT_STATES.forEach((s) => { out[s] = counts[s] ?? 0; });
    return out;
  }
  hosts.forEach((h) => { out[h.host_status] = (out[h.host_status] ?? 0) + 1; });
  return out;
};

/**
 * The order the endpoint table lists its rows in: by address (numerically),
 * then by the named endpoint, then by host name, then by row id.
 *
 * The server returns a finding's endpoints in whatever order the query gives
 * them, and a row that was just changed came back LAST — so after every
 * change the rows moved under the reader (browser pass 2026-10-01).  The
 * order here depends on nothing a state change touches, so it is the same
 * for every response.  Returns a new array.
 */
export const sortEndpoints = <H extends Pick<FindingHostInfo, 'id' | 'ip_address' | 'hostname' | 'fqdn'>>(
  hosts: ReadonlyArray<H>,
): H[] => {
  const text = (v: string | null | undefined) => (v ?? '').toLowerCase();
  return [...hosts].sort((a, b) =>
    compareAddresses(a.ip_address, b.ip_address)
    || text(a.fqdn).localeCompare(text(b.fqdn))
    || text(a.hostname).localeCompare(text(b.hostname))
    || a.id - b.id);
};

/** The ids from `anchor` to `target` inclusive, in `ordered`'s order — a
 *  shift-click range.  Just `target` when the anchor is not in the list. */
export const idRange = (ordered: ReadonlyArray<number>, anchor: number | null, target: number): number[] => {
  const to = ordered.indexOf(target);
  const from = anchor === null ? -1 : ordered.indexOf(anchor);
  if (to < 0) return [];
  if (from < 0) return [target];
  return ordered.slice(Math.min(from, to), Math.max(from, to) + 1);
};

export const chunked = <T,>(items: ReadonlyArray<T>, size: number): T[][] => {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
};
