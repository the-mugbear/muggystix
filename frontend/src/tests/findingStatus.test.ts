/**
 * One vocabulary for scanner observations, findings under investigation,
 * confirmed findings and closed ones, plus the per-endpoint rollup that stops
 * a finding's status from being read as one state for every host (design
 * review item 7).
 */
import { describe, expect, it } from 'vitest';

import { ENDPOINT_STATUS_LABEL, POPULATION_LABEL, describeEndpointStates, populationOf } from '../utils/findingStatus';

describe('populationOf', () => {
  it('maps every finding status to one of the three named populations', () => {
    expect(populationOf('open')).toBe('investigating');
    expect(populationOf('retest')).toBe('investigating');
    expect(populationOf('confirmed')).toBe('confirmed');
    expect(populationOf('false_positive')).toBe('closed');
    expect(populationOf('accepted_risk')).toBe('closed');
    expect(populationOf('remediated')).toBe('closed');
    expect(POPULATION_LABEL.investigating).toBe('Under investigation');
  });
});

describe('describeEndpointStates', () => {
  it('is silent when every endpoint is open (nothing to qualify)', () => {
    expect(describeEndpointStates({ open: 3 }, 3)).toBeNull();
    expect(describeEndpointStates(undefined, 3)).toBeNull();
    expect(describeEndpointStates({}, 0)).toBeNull();
  });

  it('says how the endpoints stand when they differ', () => {
    expect(describeEndpointStates({ open: 3, remediated: 1, retest: 1 }, 5)).toBe('still present on 3 of 5 · 1 remediated · 1 retest');
    expect(describeEndpointStates({ remediated: 2 }, 2)).toBe('still present on 0 of 2 · 2 remediated');
  });

  it('labels an endpoint state as the host\'s, not the issue\'s', () => {
    // v5.290.0 — "open" is the finding status Open's word; an endpoint that
    // still has the issue reads "Still present".
    expect(ENDPOINT_STATUS_LABEL.open).toBe('Still present');
    expect(ENDPOINT_STATUS_LABEL.remediated).toBe('Remediated here');
    expect(ENDPOINT_STATUS_LABEL.false_positive).toBe('False positive here');
  });

  it('counts a host-only false positive as that host\'s, not the finding\'s', () => {
    expect(describeEndpointStates({ open: 2, false_positive: 1 }, 3)).toBe('still present on 2 of 3 · 1 false positive there');
  });
});
