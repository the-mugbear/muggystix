import { describe, it, expect } from 'vitest';

import { latestObservations } from '../utils/latestObservations';

interface Row { id: number; url: string; source: string; status: number; last_seen: string | null }

const row = (over: Partial<Row>): Row => ({ id: 1, url: 'https://h/', source: 'eyewitness', status: 200, last_seen: null, ...over });
const key = (r: Row) => `${r.source}|${r.url}`;
const time = (r: Row) => r.last_seen;

// The evidence tables keep one row per scan; the inspector listed every one in
// full, so a re-scanned host showed each URL twice. Display-only collapse.
describe('latestObservations', () => {
  it('collapses repeats to the LATEST row and says how many stood behind it', () => {
    const out = latestObservations([
      row({ id: 5, status: 401, last_seen: '2026-08-07T18:12:26Z' }),
      row({ id: 41, status: 200, last_seen: '2026-09-10T02:22:38Z' }),
    ], key, time);
    expect(out).toHaveLength(1);
    expect(out[0].latest.id).toBe(41);
    expect(out[0].latest.status).toBe(200);
    expect(out[0].count).toBe(2);
    expect(out[0].firstSeen).toBe('2026-08-07T18:12:26Z');
  });

  it('picks the latest whatever order the rows arrive in', () => {
    const out = latestObservations([
      row({ id: 41, last_seen: '2026-09-10T02:22:38Z' }),
      row({ id: 5, last_seen: '2026-08-07T18:12:26Z' }),
    ], key, time);
    expect(out[0].latest.id).toBe(41);
  });

  it('never merges across the key — another tool or another URL stays its own row', () => {
    const out = latestObservations([
      row({ id: 1 }),
      row({ id: 2, source: 'httpx' }),
      row({ id: 3, url: 'https://h/login' }),
    ], key, time);
    expect(out.map((o) => o.latest.id)).toEqual([1, 2, 3]);
    expect(out.every((o) => o.count === 1)).toBe(true);
  });

  it('falls back to the id when rows carry no usable time', () => {
    const out = latestObservations([
      row({ id: 9, last_seen: null }),
      row({ id: 4, last_seen: 'not a date' }),
    ], key, time);
    expect(out[0].latest.id).toBe(9);
    expect(out[0].firstSeen).toBe('not a date');
  });

  it('keeps first-appearance order so the list does not reshuffle', () => {
    const out = latestObservations([
      row({ id: 1, url: 'https://h/b' }),
      row({ id: 2, url: 'https://h/a' }),
      row({ id: 3, url: 'https://h/b', last_seen: '2026-09-10T00:00:00Z' }),
    ], key, time);
    expect(out.map((o) => o.latest.url)).toEqual(['https://h/b', 'https://h/a']);
  });

  // Code review finding 20: the first version kept only `latest`.
  it('keeps every member of the group, newest first — collapsing never discards access', () => {
    const [group] = latestObservations([
      row({ id: 5, status: 401, last_seen: '2026-08-07T18:12:26Z' }),
      row({ id: 41, status: 200, last_seen: '2026-09-10T02:22:38Z' }),
      row({ id: 20, status: 302, last_seen: '2026-08-20T00:00:00Z' }),
    ], key, time);
    expect(group.members.map((m) => m.id)).toEqual([41, 20, 5]);
    expect(group.members[0]).toBe(group.latest);
    expect(group.count).toBe(group.members.length);
  });

  it('returns nothing for nothing', () => {
    expect(latestObservations([], key, time)).toEqual([]);
  });
});
