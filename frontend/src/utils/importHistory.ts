/**
 * The import history (v5.239.0): upload batches and individually uploaded
 * files in ONE chronological list — and, since 5.418.0, the uploads that were
 * not imported (owner decision 2026-10-22, G15: ONE import history; they were
 * the Ingestion results view).
 *
 * The server owns the order (`GET /scans/history` — kind, id, time) because
 * it is decided over every kind at once; separately paginated lists cannot be
 * merged correctly in the browser past their first page.  The rows' content
 * comes from the endpoints that already compute it, by id.  These helpers
 * take their API as an argument so they are tested without a client.
 */
import type {
  ImportHistoryEntry, IngestionResultItem, IngestionResultsResponse, Scan, ScanBatchSummary,
} from '../services/api';

export interface HistoryFilters {
  search?: string;
  tool?: string;
  createdAfter?: string;
  uploadedBy?: number;
}

export interface HistoryApi {
  getScans: (
    projectId: number,
    skip: number,
    limit: number,
    options: HistoryFilters & { ids: number[] },
  ) => Promise<Scan[]>;
  getScanBatches: (projectId: number, options: HistoryFilters & { ids: number[]; limit: number }) => Promise<ScanBatchSummary[]>;
  /** The upload rows (analysts and above: only they are given upload entries). */
  getIngestionResults: (projectId: number, query: { ids: number[] }) => Promise<IngestionResultsResponse>;
}

export interface HydratedHistory {
  scans: Scan[];
  batches: ScanBatchSummary[];
  uploads: IngestionResultItem[];
  /** One kind could not be loaded: the list is missing rows, and must say so
   *  rather than present a shorter history as the whole of it. */
  partial: boolean;
}

export async function hydrateHistoryRows(
  projectId: number,
  items: ImportHistoryEntry[],
  filters: HistoryFilters,
  api: HistoryApi,
): Promise<HydratedHistory> {
  const scanIds = items.filter((e) => e.kind === 'scan').map((e) => e.id);
  const batchIds = items.filter((e) => e.kind === 'batch').map((e) => e.id);
  const uploadIds = items.filter((e) => e.kind === 'upload').map((e) => e.id);
  const [scanResult, batchResult, uploadResult] = await Promise.allSettled([
    scanIds.length ? api.getScans(projectId, 0, scanIds.length, { ids: scanIds }) : Promise.resolve([] as Scan[]),
    // A batch's counts are of its files MATCHING the page filters, so the
    // filters go with the ids; a single file is already a match by being listed.
    batchIds.length
      ? api.getScanBatches(projectId, { ...filters, ids: batchIds, limit: batchIds.length })
      : Promise.resolve([] as ScanBatchSummary[]),
    uploadIds.length
      ? api.getIngestionResults(projectId, { ids: uploadIds }).then((r) => r.items)
      : Promise.resolve([] as IngestionResultItem[]),
  ]);
  if (scanResult.status === 'rejected') console.error('Error loading history files:', scanResult.reason);
  if (batchResult.status === 'rejected') console.error('Error loading history batches:', batchResult.reason);
  if (uploadResult.status === 'rejected') console.error('Error loading history uploads:', uploadResult.reason);
  return {
    scans: scanResult.status === 'fulfilled' ? scanResult.value : [],
    batches: batchResult.status === 'fulfilled' ? batchResult.value : [],
    uploads: uploadResult.status === 'fulfilled' ? uploadResult.value : [],
    partial: scanResult.status === 'rejected' || batchResult.status === 'rejected' || uploadResult.status === 'rejected',
  };
}

export type HistoryRow =
  | { kind: 'scan'; key: string; scan: Scan }
  | { kind: 'batch'; key: string; batch: ScanBatchSummary }
  | { kind: 'upload'; key: string; upload: IngestionResultItem };

/** The rows in the server's order. An entry whose row is not loaded (deleted
 *  meanwhile, or its kind failed to load) is left out, never invented. */
export function orderHistoryRows(
  items: ImportHistoryEntry[],
  scans: Scan[],
  batches: ScanBatchSummary[],
  uploads: IngestionResultItem[] = [],
): HistoryRow[] {
  const scanById = new Map(scans.map((s) => [s.id, s]));
  const batchById = new Map(batches.map((b) => [b.id, b]));
  const uploadById = new Map(uploads.map((u) => [u.id, u]));
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
    } else if (entry.kind === 'batch') {
      const batch = batchById.get(entry.id);
      if (batch) rows.push({ kind: 'batch', key, batch });
    } else {
      const upload = uploadById.get(entry.id);
      if (upload) rows.push({ kind: 'upload', key, upload });
    }
  }
  return rows;
}
