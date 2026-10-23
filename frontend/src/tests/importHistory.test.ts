import { describe, expect, it, vi } from 'vitest';

import type { ImportHistoryEntry, IngestionResultItem, Scan, ScanBatchSummary } from '../services/api';
import { hydrateHistoryRows, orderHistoryRows } from '../utils/importHistory';

const entry = (kind: ImportHistoryEntry['kind'], id: number): ImportHistoryEntry => ({ kind, id, at: null });
const scan = (id: number) => ({ id, filename: `s${id}.xml` }) as Scan;
const batch = (id: number) => ({ id, label: `b${id}` }) as ScanBatchSummary;
const upload = (id: number) => ({ id, original_filename: `u${id}.xml`, status: 'failed' }) as IngestionResultItem;
const results = (items: IngestionResultItem[]) => ({ items, total: items.length, summary: {} as never });

const anApi = (over: Partial<Record<'getScans' | 'getScanBatches' | 'getIngestionResults', ReturnType<typeof vi.fn>>> = {}) => ({
  getScans: vi.fn().mockResolvedValue([]),
  getScanBatches: vi.fn().mockResolvedValue([]),
  getIngestionResults: vi.fn().mockResolvedValue(results([])),
  ...over,
});

describe('hydrateHistoryRows', () => {
  it('fetches a page\'s files, batches and uploads by id — batches with the page filters', async () => {
    const api = anApi({
      getScans: vi.fn().mockResolvedValue([scan(3), scan(9)]),
      getScanBatches: vi.fn().mockResolvedValue([batch(4)]),
      getIngestionResults: vi.fn().mockResolvedValue(results([upload(12)])),
    });
    const items = [entry('scan', 9), entry('upload', 12), entry('batch', 4), entry('scan', 3)];
    const rows = await hydrateHistoryRows(5, items, { tool: 'NMAP' }, api);

    // Every kind is asked of the project the page was read for.
    expect(api.getScans).toHaveBeenCalledWith(5, 0, 2, { ids: [9, 3] });
    // A batch's counts are of its files matching the filters.
    expect(api.getScanBatches).toHaveBeenCalledWith(5, { tool: 'NMAP', ids: [4], limit: 1 });
    // An upload is named by its ingestion job id.
    expect(api.getIngestionResults).toHaveBeenCalledWith(5, { ids: [12] });
    expect(rows).toMatchObject({ partial: false });
    expect(rows.scans).toHaveLength(2);
    expect(rows.uploads.map((u) => u.id)).toEqual([12]);
  });

  it('makes no request for a kind the page does not contain', async () => {
    const api = anApi({ getScanBatches: vi.fn().mockResolvedValue([batch(4)]) });
    await hydrateHistoryRows(5, [entry('batch', 4)], {}, api);
    expect(api.getScans).not.toHaveBeenCalled();
    // A viewer's history holds no upload entries, so nothing analyst-only is asked.
    expect(api.getIngestionResults).not.toHaveBeenCalled();
  });

  it('one kind failing leaves the others, and says the list is incomplete', async () => {
    const api = anApi({
      getScans: vi.fn().mockRejectedValue(new Error('down')),
      getScanBatches: vi.fn().mockResolvedValue([batch(4)]),
      getIngestionResults: vi.fn().mockRejectedValue(new Error('down')),
    });
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const rows = await hydrateHistoryRows(5, [entry('scan', 9), entry('batch', 4), entry('upload', 1)], {}, api);
    spy.mockRestore();
    expect(rows.partial).toBe(true);
    expect(rows.batches).toHaveLength(1);
    expect(rows.scans).toEqual([]);
    expect(rows.uploads).toEqual([]);
  });
});

describe('orderHistoryRows', () => {
  it('keeps the server\'s order across every kind', () => {
    const items = [entry('scan', 9), entry('upload', 12), entry('batch', 4), entry('scan', 3), entry('batch', 1)];
    // Rows arrive in whatever order each endpoint returns them.
    const rows = orderHistoryRows(items, [scan(3), scan(9)], [batch(1), batch(4)], [upload(12)]);
    expect(rows.map((r) => r.key)).toEqual(['scan-9', 'upload-12', 'batch-4', 'scan-3', 'batch-1']);
  });

  it('leaves out an entry whose row is not loaded, and never repeats one', () => {
    const items = [entry('scan', 9), entry('scan', 7), entry('batch', 4), entry('scan', 9), entry('upload', 2)];
    const rows = orderHistoryRows(items, [scan(9)], [batch(4)]);
    expect(rows.map((r) => r.key)).toEqual(['scan-9', 'batch-4']);
  });
});
