import { describe, it, expect } from 'vitest';

import {
  describeScanRun,
  describeUpload,
  formatWallClock,
  viewerTimeZone,
} from '../../utils/scanTime';

// Pin zone + locale: the helpers otherwise read the browser's.
const NY = { timeZone: 'America/New_York', locale: 'en-US' };
const base = { tool_name: 'nmap', created_at: '2026-09-10T12:00:00Z', end_time: null };

describe('describeScanRun', () => {
  // The defect this replaces: a UTC scanner time arrived without an offset and
  // was rendered as if it were already the viewer's local time.
  it('converts a tool-reported instant to the viewer zone and names the zone', () => {
    const run = describeScanRun(
      {
        ...base,
        start_time: '2026-09-10T02:51:07Z',
        end_time: '2026-09-10T03:01:07Z',
        time_source: 'tool_run',
      },
      NY,
    );
    expect(run.kind).toBe('tool_run');
    // 02:51Z is 22:51 the previous evening in New York (EDT).
    expect(run.startLabel).toContain('Sep 9');
    expect(run.startLabel).toContain('10:51 PM');
    expect(run.startLabel).toContain('EDT');
    expect(run.durationLabel).toBe('10m 0s');
    expect(run.utcLabel).toBe('2026-09-10 02:51:07 UTC – 2026-09-10 03:01:07 UTC');
    expect(run.uploadLagLabel).toBe('uploaded 8h 58m later');
    expect(run.sourceLabel).toBe('Reported by nmap');
    expect(run.explanation).toContain('America/New_York');
  });

  it('shows a scanner wall clock exactly as written, with no zone and no conversion', () => {
    const run = describeScanRun(
      {
        ...base,
        start_time: '2024-07-15T10:30:01',
        end_time: '2024-07-15T10:45:01',
        time_source: 'tool_clock',
      },
      NY,
    );
    expect(run.kind).toBe('tool_clock');
    expect(run.startLabel).toContain('10:30 AM');
    expect(run.startLabel).not.toMatch(/EDT|EST|UTC|GMT/);
    expect(run.durationLabel).toBe('15m 0s');
    // A zone-less clock can't be compared with the upload instant.
    expect(run.uploadLagLabel).toBeNull();
    expect(run.utcLabel).toBeNull();
    expect(run.sourceLabel).toBe('Scanner clock · zone unknown');
  });

  it('labels a window derived from per-record timestamps', () => {
    const run = describeScanRun(
      { ...base, tool_name: 'httpx', start_time: '2026-09-10T11:00:00Z', time_source: 'tool_records' },
      NY,
    );
    expect(run.kind).toBe('tool_records');
    expect(run.sourceLabel).toBe('Span of httpx records');
    expect(run.endLabel).toBeNull();
    expect(run.durationLabel).toBeNull();
  });

  it('says plainly when the file carries no scan time', () => {
    const run = describeScanRun({ ...base, tool_name: 'whatweb', start_time: null, time_source: null }, NY);
    expect(run.kind).toBe('none');
    expect(run.startLabel).toBeNull();
    expect(run.sourceLabel).toBe('No time in the file');
    expect(run.explanation).toContain('Whatweb output');
  });

  it('marks rows imported before sources were recorded', () => {
    const run = describeScanRun({ ...base, start_time: '2026-09-10T02:00:00Z', time_source: null }, NY);
    expect(run.kind).toBe('legacy');
    expect(run.sourceLabel).toBe('Source not recorded');
    expect(run.explanation).toContain('assumed to be UTC');
  });

  it('ignores an end before the start instead of reporting a negative duration', () => {
    const run = describeScanRun(
      {
        ...base,
        start_time: '2026-09-10T03:00:00Z',
        end_time: '2026-09-10T02:00:00Z',
        time_source: 'tool_run',
      },
      NY,
    );
    expect(run.endLabel).toBeNull();
    expect(run.durationLabel).toBeNull();
  });
});

describe('time helpers', () => {
  it('formats a wall clock without shifting its digits', () => {
    expect(formatWallClock('2024-07-15T23:59:00', NY)).toContain('11:59 PM');
    expect(formatWallClock('2024-07-15T23:59:00', NY)).toContain('Jul 15');
    expect(formatWallClock('not a time', NY)).toBeNull();
  });

  it('names the viewer zone with its UTC offset', () => {
    const zone = viewerTimeZone({ timeZone: 'Asia/Kolkata', locale: 'en-US' }, new Date('2026-01-01T00:00:00Z'));
    expect(zone.name).toBe('Asia/Kolkata');
    expect(zone.offset).toBe('UTC+05:30');
  });

  it('describes the upload time with the zone and the UTC value', () => {
    const upload = describeUpload({ created_at: '2026-09-10T12:00:00Z' }, NY);
    expect(upload.label).toContain('8:00 AM');
    expect(upload.explanation).toContain('2026-09-10 12:00:00 UTC');
  });
});
