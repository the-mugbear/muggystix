/**
 * The import history (v5.239.0): upload batches and individually uploaded
 * files in ONE chronological list.
 *
 * The server owns the order (`GET /scans/history` — kind, id, time) because
 * it is decided over both kinds at once; two separately paginated lists
 * cannot be merged correctly in the browser past their first page.  The rows'
 * content comes from the endpoints that already compute it, by id.  These
 * helpers take their API as an argument so they are tested without a client.
 */
import type { ImportHistoryEntry, Scan, ScanBatchSummary } from '../services/api';

export interface HistoryFilters {
  search?: string;
  tool?: string;
  createdAfter?: string;
  uploadedBy?: number;
}

export interface HistoryApi {
  getScans: (
    skip: number,
    limit: number,
    options: HistoryFilters & { ids: number[] },
  ) => Promise<Scan[]>;
  getScanBatches: (options: HistoryFilters & { ids: number[]; limit: number }) => Promise<ScanBatchSummary[]>;
}

export interface HydratedHistory {
  scans: Scan[];
  batches: ScanBatchSummary[];
  /** One kind could not be loaded: the list is missing rows, and must say so
   *  rather than present a shorter history as the whole of it. */
  partial: boolean;
}

export async function hydrateHistoryRows(
  items: ImportHistoryEntry[],
  filters: HistoryFilters,
  api: HistoryApi,
): Promise<HydratedHistory> {
  const scanIds = items.filter((e) => e.kind === 'scan').map((e) => e.id);
  const batchIds = items.filter((e) => e.kind === 'batch').map((e) => e.id);
  const [scanResult, batchResult] = await Promise.allSettled([
    scanIds.length ? api.getScans(0, scanIds.length, { ids: scanIds }) : Promise.resolve([] as Scan[]),
    // A batch's counts are of its files MATCHING the page filters, so the
    // filters go with the ids; a single file is already a match by being listed.
    batchIds.length
      ? api.getScanBatches({ ...filters, ids: batchIds, limit: batchIds.length })
      : Promise.resolve([] as ScanBatchSummary[]),
  ]);
  if (scanResult.status === 'rejected') console.error('Error loading history files:', scanResult.reason);
  if (batchResult.status === 'rejected') console.error('Error loading history batches:', batchResult.reason);
  return {
    scans: scanResult.status === 'fulfilled' ? scanResult.value : [],
    batches: batchResult.status === 'fulfilled' ? batchResult.value : [],
    partial: scanResult.status === 'rejected' || batchResult.status === 'rejected',
  };
}

export type HistoryRow =
  | { kind: 'scan'; key: string; scan: Scan }
  | { kind: 'batch'; key: string; batch: ScanBatchSummary };

/** The rows in the server's order. An entry whose row is not loaded (deleted
 *  meanwhile, or its kind failed to load) is left out, never invented. */
export function orderHistoryRows(
  items: ImportHistoryEntry[],
  scans: Scan[],
  batches: ScanBatchSummary[],
): HistoryRow[] {
  const scanById = new Map(scans.map((s) => [s.id, s]));
  const batchById = new Map(batches.map((b) => [b.id, b]));
  const seen = new Set<string>();
  const rows: HistoryRow[] = [];
  for (const entry of items) {
    const key = `${entry.kind}-${entry.id}`;
    // A row that moved between two "load more" pages must not appear twice.
    if (seen.has(key)) continue;
    seen.add(key);
    if (entry.kind === 'scan') {
      const scan = scanById.get(entry.id);
      if (scan) rows.push({ kind: 'scan', key, scan });
    } else {
      const batch = batchById.get(entry.id);
      if (batch) rows.push({ kind: 'batch', key, batch });
    }
  }
  return rows;
}
