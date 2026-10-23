/**
 * The ONE reading of a record id in a route (review 2026-10-23b, FEP-11).
 */
import { describe, expect, it } from 'vitest';

import { routeId } from '../../utils/routeId';

describe('routeId', () => {
  it('reads a positive whole number written in digits', () => {
    expect(routeId('12')).toBe(12);
    expect(routeId('007')).toBe(7);
  });

  it('refuses anything else, so no request is made for it', () => {
    for (const raw of ['12abc', 'abc', '', ' 12', '1.5', '-3', '0', '1e3', '99999999999999999999', null, undefined]) {
      expect(routeId(raw)).toBeNull();
    }
  });
});
