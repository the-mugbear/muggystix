import { describe, it, expect } from 'vitest';
import { presetRange, customRangeError } from '../../utils/oversightDates';

// 2026-08-14 23:30 UTC — late in the UTC day, mid-quarter (Q3 starts Jul 1).
const NOW = new Date(Date.UTC(2026, 7, 14, 23, 30));

describe('oversight date presets (UTC days, inclusive)', () => {
  it('maps each preset to its range', () => {
    expect(presetRange('today', NOW)).toEqual({ start: '2026-08-14', end: '2026-08-14' });
    expect(presetRange('last7', NOW)).toEqual({ start: '2026-08-08', end: '2026-08-14' });
    expect(presetRange('last30', NOW)).toEqual({ start: '2026-07-16', end: '2026-08-14' });
    expect(presetRange('month', NOW)).toEqual({ start: '2026-08-01', end: '2026-08-14' });
    expect(presetRange('quarter', NOW)).toEqual({ start: '2026-07-01', end: '2026-08-14' });
    expect(presetRange('ytd', NOW)).toEqual({ start: '2026-01-01', end: '2026-08-14' });
    expect(presetRange('all', NOW)).toEqual({});
  });

  it('refuses what the server refuses', () => {
    expect(customRangeError('2026-08-10', '2026-08-01', NOW)).toMatch(/on or before/);
    expect(customRangeError('2026-08-10', '2026-08-15', NOW)).toMatch(/future/);
    expect(customRangeError('', '2026-08-01', NOW)).toMatch(/both/);
    expect(customRangeError('2026-08-01', '2026-08-14', NOW)).toBeNull();
  });
});
