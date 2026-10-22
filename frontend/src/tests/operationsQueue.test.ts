import { describe, it, expect } from 'vitest';

import { fromOperationsQueue, uniqueHostIds } from '../utils/operationsQueue';

// A host opened from My work offered "Back to my work" but its Next went
// nowhere. The section's host ids now travel with the navigation.
describe('operationsQueue', () => {
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
