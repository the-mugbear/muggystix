import { describe, it, expect } from 'vitest';

import { moveItem } from '../../utils/reorder';

describe('moveItem', () => {
  const list = ['a', 'b', 'c', 'd'];

  it('puts the item at the index it is given, shifting the ones between', () => {
    expect(moveItem(list, 0, 2)).toEqual(['b', 'c', 'a', 'd']);
    expect(moveItem(list, 3, 0)).toEqual(['d', 'a', 'b', 'c']);
    expect(moveItem(list, 1, 2)).toEqual(['a', 'c', 'b', 'd']);
    expect(moveItem(list, 2, 1)).toEqual(['a', 'c', 'b', 'd']);
  });

  it('never changes the list it was given', () => {
    const before = list.slice();
    moveItem(list, 0, 3);
    expect(list).toEqual(before);
  });

  it('answers the same list when there is nothing to move', () => {
    expect(moveItem(list, 1, 1)).toBe(list);
    expect(moveItem(list, -1, 2)).toBe(list);
    expect(moveItem(list, 0, 4)).toBe(list);
    expect(moveItem(list, 0.5, 1)).toBe(list);
    expect(moveItem([], 0, 0)).toEqual([]);
  });
});
