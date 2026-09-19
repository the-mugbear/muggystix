import { describe, expect, it, vi } from 'vitest';

import type { ImportHistoryEntry, Scan, ScanBatchSummary } from '../services/api';
import { hydrateHistoryRows, orderHistoryRows } from '../utils/importHistory';

const entry = (kind: 'batch' | 'scan', id: number): ImportHistoryEntry => ({ kind, id, at: null });
const scan = (id: number) => ({ id, filename: `s${id}.xml` }) as Scan;
const batch = (id: number) => ({ id, label: `b${id}` }) as ScanBatchSummary;

describe('hydrateHistoryRows', () => {
  it('fetches a page\'s files and batches by id — batches with the page filters', async () => {
    const api = {
      getScans: vi.fn().mockResolvedValue([scan(3), scan(9)]),
      getScanBatches: vi.fn().mockResolvedValue([batch(4)]),
    };
    const items = [entry('scan', 9), entry('batch', 4), entry('scan', 3)];
    const rows = await hydrateHistoryRows(items, { tool: 'NMAP' }, api);

    expect(api.getScans).toHaveBeenCalledWith(0, 2, { ids: [9, 3] });
    // A batch's counts are of its files matching the filters.
    expect(api.getScanBatches).toHaveBeenCalledWith({ tool: 'NMAP', ids: [4], limit: 1 });
    expect(rows).toMatchObject({ partial: false });
    expect(rows.scans).toHaveLength(2);
  });

  it('makes no request for a kind the page does not contain', async () => {
    const api = { getScans: vi.fn(), getScanBatches: vi.fn().mockResolvedValue([batch(4)]) };
    await hydrateHistoryRows([entry('batch', 4)], {}, api);
    expect(api.getScans).not.toHaveBeenCalled();
  });

  it('one kind failing leaves the other, and says the list is incomplete', async () => {
    const api = {
      getScans: vi.fn().mockRejectedValue(new Error('down')),
      getScanBatches: vi.fn().mockResolvedValue([batch(4)]),
    };
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const rows = await hydrateHistoryRows([entry('scan', 9), entry('batch', 4)], {}, api);
    spy.mockRestore();
    expect(rows.partial).toBe(true);
    expect(rows.batches).toHaveLength(1);
    expect(rows.scans).toEqual([]);
  });
});

describe('orderHistoryRows', () => {
  it('keeps the server\'s order across both kinds', () => {
    const items = [entry('scan', 9), entry('batch', 4), entry('scan', 3), entry('batch', 1)];
    // Rows arrive in whatever order each endpoint returns them.
    const rows = orderHistoryRows(items, [scan(3), scan(9)], [batch(1), batch(4)]);
    expect(rows.map((r) => r.key)).toEqual(['scan-9', 'batch-4', 'scan-3', 'batch-1']);
  });

  it('leaves out an entry whose row is not loaded, and never repeats one', () => {
    const items = [entry('scan', 9), entry('scan', 7), entry('batch', 4), entry('scan', 9)];
    const rows = orderHistoryRows(items, [scan(9)], [batch(4)]);
    expect(rows.map((r) => r.key)).toEqual(['scan-9', 'batch-4']);
  });
});
