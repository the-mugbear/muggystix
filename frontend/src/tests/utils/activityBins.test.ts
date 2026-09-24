import { describe, expect, it } from 'vitest';

import type { ActivityItem, ActivityKind } from '../../services/api';
import { binActivity, chooseBinMs, kindTotals, niceMax } from '../../utils/activityBins';

const HOUR = 3600 * 1000;

const item = (kind: ActivityKind, start: string, ref = 1): ActivityItem => ({
  kind,
  ref_id: ref,
  project_id: 1,
  project_name: 'P',
  label: 'nmap',
  secondary_label: null,
  start_time: start,
  end_time: null,
  recorded_time: null,
  start_time_is_fallback: false,
  has_end_time: false,
  host_count: null,
  status: null,
  target: null,
  parent_id: null,
} as ActivityItem);

describe('chooseBinMs', () => {
  it('bins a week hourly on a desktop-width plot', () => {
    expect(chooseBinMs(7 * 24 * HOUR, 900)).toBe(HOUR);
  });
  it('coarsens when the plot is too narrow for hourly columns', () => {
    // 168 hourly bins need 672px at 4px each; 400px fits 100, so 2-hour (84).
    expect(chooseBinMs(7 * 24 * HOUR, 400)).toBe(2 * HOUR);
    expect(chooseBinMs(7 * 24 * HOUR, 300)).toBe(3 * HOUR);
  });
});

describe('binActivity', () => {
  const start = '2026-09-16T00:00:00Z';
  const end = '2026-09-23T00:00:00Z';

  it('counts a burst into one bin instead of one mark per activity', () => {
    const burst = Array.from({ length: 60 }, (_, i) =>
      item('scan', `2026-09-20T10:${String(i % 60).padStart(2, '0')}:00Z`, i),
    );
    const bins = binActivity(burst, start, end, HOUR);
    // 7 days of hourly bins (+1 when local time is not on a UTC hour).
    expect(bins.length).toBeGreaterThanOrEqual(168);
    expect(bins.length).toBeLessThanOrEqual(169);
    const nonEmpty = bins.filter((b) => b.total > 0);
    expect(nonEmpty).toHaveLength(1);
    expect(nonEmpty[0].counts.scan).toBe(60);
    expect(nonEmpty[0].end - nonEmpty[0].start).toBe(HOUR);
  });

  it('keeps kinds apart and drops items outside the window or unreadable', () => {
    const bins = binActivity(
      [
        item('scan', '2026-09-17T01:00:00Z'),
        item('execution_session', '2026-09-17T01:30:00Z'),
        item('test_result', '2026-09-18T05:00:00Z'),
        item('scan', '2026-09-10T00:00:00Z'), // before the window
        item('scan', 'not a date'),
      ],
      start,
      end,
      HOUR,
    );
    const totals = kindTotals(bins);
    expect(totals).toEqual({
      scan: 1,
      recon_session: 0,
      execution_session: 1,
      test_result: 1,
      sanity_check: 0,
    });
  });

  it('covers the whole window with contiguous bins', () => {
    const bins = binActivity([], start, end, 6 * HOUR);
    expect(bins[0].start).toBeLessThanOrEqual(new Date(start).getTime());
    expect(bins[bins.length - 1].end).toBeGreaterThanOrEqual(new Date(end).getTime());
    for (let i = 1; i < bins.length; i++) expect(bins[i].start).toBe(bins[i - 1].end);
  });
});

describe('niceMax', () => {
  it('rounds up to 1/2/5 × 10ⁿ', () => {
    expect(niceMax(0)).toBe(1);
    expect(niceMax(3)).toBe(5);
    expect(niceMax(58)).toBe(100);
    expect(niceMax(20)).toBe(20);
  });
});
