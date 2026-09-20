/**
 * "Where to focus" ranking — the acceptance scenarios from the Posture redesign
 * review, as arithmetic over one grid row.
 */
import { describe, it, expect } from 'vitest';

import {
  rankConcentration, leadingFamily, describeConcentration,
} from '../utils/postureConcentration';
import type { HeatmapCell, HeatmapRow, HeatmapSegment } from '../services/api/posture';

const cell = (
  segment: string, affected: number, assessed: number, in_scope: number,
  eligible = in_scope, eligible_assessed = assessed,
): HeatmapCell => ({
  segment, affected, assessed, in_scope, eligible, eligible_assessed,
  unassessed: assessed === 0, value: assessed ? affected / assessed : 0,
  numerator: affected, denominator: assessed,
});

const segments: HeatmapSegment[] = ['east', 'west', 'core', 'north', 'lab'].map((key) => ({
  key, label: key[0].toUpperCase() + key.slice(1), in_scope: 0, assessed: 0,
}));

const lifecycle: HeatmapRow = {
  family: 'lifecycle_patching', family_label: 'Lifecycle & patching', conditions: ['eol_os'],
  evidence_domain: 'os_detection', evidence_domain_label: 'OS identification', affected_total: 46,
  cells: [
    cell('core', 16, 160, 170),   // most affected hosts, ordinary rate (10%)
    cell('east', 18, 30, 36),     // 60% with good coverage — the story
    cell('west', 8, 80, 84),
    cell('north', 2, 2, 25),      // 100% of a tiny sample
    cell('lab', 0, 0, 12),        // never assessed
  ],
};

describe('rankConcentration', () => {
  const ranked = rankConcentration(lifecycle, segments);
  const by = Object.fromEntries(ranked.map((r) => [r.key, r]));

  it('leads with the disproportionate segment, not the largest one', () => {
    expect(ranked.map((r) => r.key)).toEqual(['east', 'core', 'west', 'north', 'lab']);
    expect(by.east.rate).toBeCloseTo(0.6);
    // Rest of the assessed project EXCLUDES the segment itself: 26 of 242.
    expect([by.east.restAffected, by.east.restAssessed]).toEqual([26, 242]);
    expect(Math.round(by.east.deltaPoints!)).toBe(49);
  });

  it('separates workload from prevalence for a large, ordinary site', () => {
    expect(by.core.affected).toBeGreaterThan(by.west.affected);
    expect(by.core.rate).toBeCloseTo(0.1);
    expect(by.core.state).toBe('comparable');
    expect(describeConcentration(by.core, 'Lifecycle & patching')).not.toMatch(/points higher/);
  });

  it('keeps a tiny 100% sample visible but does not rank it as the worst', () => {
    expect(by.north.state).toBe('limited');
    expect(by.north.rate).toBe(1);
    expect(by.north.unknown).toBe(23);
    expect(by.north.limitedBecause).toMatch(/only 2 assessed/);
    expect(by.north.limitedBecause).toMatch(/8% of eligible hosts assessed/);
    expect(ranked.indexOf(by.north)).toBeGreaterThan(ranked.indexOf(by.west));
    expect(describeConcentration(by.north, 'Lifecycle & patching')).toMatch(/Limited comparison/);
  });

  it('never reads an unassessed segment as clean', () => {
    expect(by.lab.state).toBe('unassessed');
    expect(by.lab.rate).toBeNull();
    expect(describeConcentration(by.lab, 'Lifecycle & patching')).toMatch(/unknown, not clean/);
  });

  it('reports no comparator rather than an invented one', () => {
    const only = rankConcentration({ ...lifecycle, cells: [cell('east', 18, 30, 36)] }, segments);
    expect(only[0].restRate).toBeNull();
    expect(only[0].deltaPoints).toBeNull();
    expect(describeConcentration(only[0], 'Lifecycle & patching')).toMatch(/No other segment was assessed/);
  });

  it('judges coverage against eligible hosts, not every host in the site', () => {
    // 12 of 40 hosts expose a web port and all 12 were assessed: complete, not thin.
    const [r] = rankConcentration({ ...lifecycle, cells: [cell('east', 6, 12, 40, 12, 12)] }, segments);
    expect(r.coverage).toBe(1);
    expect(r.state).toBe('comparable');
  });
});

describe('leadingFamily', () => {
  it('prefers the family with the sharpest concentration over the most affected hosts', () => {
    const broad: HeatmapRow = {
      ...lifecycle, family: 'encryption_trust', family_label: 'Encryption & trust', affected_total: 90,
      cells: [cell('core', 45, 150, 170), cell('east', 9, 30, 36), cell('west', 36, 120, 124)],
    };
    expect(leadingFamily([broad, lifecycle], segments)?.family).toBe('lifecycle_patching');
    expect(leadingFamily([{ ...lifecycle, affected_total: 0, cells: [] }], segments)).toBeNull();
  });
});
