import { describe, it, expect } from 'vitest';

import { fromOperationsQueue, hostIdOf, uniqueHostIds } from '../utils/operationsQueue';

// v5.243.0 — a host opened from My work offered "Back to my work" but its Next
// went nowhere. The section's host ids now travel with the navigation.
describe('operationsQueue', () => {
  it('reads the host id off a host link, deep-linked or not, and nothing else', () => {
    expect(hostIdOf('/hosts/42')).toBe(42);
    expect(hostIdOf('/hosts/42#note-17')).toBe(42);
    expect(hostIdOf('/hosts/42?from=hosts')).toBe(42);
    expect(hostIdOf('/findings/42')).toBeNull();
    expect(hostIdOf('/test-plans/3#entry-9')).toBeNull();
    expect(hostIdOf('/hosts/')).toBeNull();
    expect(hostIdOf('/hosts/42abc')).toBeNull();
  });

  it('keeps display order and drops repeats (a host can sit in one section twice)', () => {
    expect(uniqueHostIds([7, 3, 7, null, 9, 3, undefined])).toEqual([7, 3, 9]);
  });

  it('carries the section as the queue', () => {
    expect(fromOperationsQueue([5, 8, 5, 2], 'Worth a look')).toEqual({
      state: { fromOperations: true, hostIds: [5, 8, 2], queueLabel: 'Worth a look' },
    });
  });

  it('a queue of one is no queue — Back only, as before', () => {
    expect(fromOperationsQueue([5], 'In review')).toEqual({ state: { fromOperations: true } });
    expect(fromOperationsQueue([5, 5, null], 'In review')).toEqual({ state: { fromOperations: true } });
  });
});
