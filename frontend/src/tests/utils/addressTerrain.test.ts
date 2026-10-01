import { describe, expect, it } from 'vitest';
import type { TerrainBlock } from '../../services/api';
import {
  DISTRICT_SIZE, districtSide, hilbertPoint, layoutTerrain, summariseTerrain, terrainBlockQuery,
} from '../../utils/addressTerrain';
import { hslComponentsToRgb, terrainPalette } from '../../utils/terrainPalette';

const block = (cidr: string, over: Partial<TerrainBlock> = {}): TerrainBlock => ({
  cidr, hosts: 10, tested: 1, planned: 2, worked: 3, untouched: 4, critical: 0, critical_untouched: 0, ...over,
});

describe('hilbertPoint', () => {
  it('visits every plot once, each step to a neighbouring plot', () => {
    const seen = new Set<string>();
    let prev: [number, number] | null = null;
    for (let d = 0; d < DISTRICT_SIZE * DISTRICT_SIZE; d += 1) {
      const p = hilbertPoint(DISTRICT_SIZE, d);
      seen.add(p.join(','));
      if (prev) expect(Math.abs(p[0] - prev[0]) + Math.abs(p[1] - prev[1])).toBe(1);
      prev = p;
    }
    expect(seen.size).toBe(256);
  });
});

describe('layoutTerrain', () => {
  it('puts each /16 in its own district and adjacent /24s on adjacent plots', () => {
    const layout = layoutTerrain([
      block('10.0.0.0/24'), block('10.0.1.0/24'), block('10.1.0.0/24'), block('2001:db8::/64'),
    ]);
    expect(layout.districts.map((d) => d.label)).toEqual(['10.0.0.0/16', '10.1.0.0/16', 'IPv6 /64s']);
    const [a, b] = layout.placed;
    expect(Math.abs(a.x - b.x) + Math.abs(a.z - b.z)).toBe(1);
    // Districts never overlap.
    const [d0, d1] = layout.districts;
    const inside = (d: typeof d0, p: { x: number; z: number }) =>
      p.x > d.x && p.x < d.x + d.size && p.z > d.z && p.z < d.z + d.size;
    expect(inside(d0, a) && !inside(d1, a)).toBe(true);
    expect(inside(d1, layout.placed[2]) && !inside(d0, layout.placed[2])).toBe(true);
  });

  it('gives a district only the plots its blocks need', () => {
    expect(districtSide(1)).toBe(2);
    expect(districtSide(4)).toBe(2);
    expect(districtSide(5)).toBe(4);
    expect(districtSide(17)).toBe(8);
    expect(districtSide(256)).toBe(DISTRICT_SIZE);
    const layout = layoutTerrain([
      ...Array.from({ length: 8 }, (_, i) => block(`10.0.${i}.0/24`)), block('10.1.200.0/24'),
    ]);
    expect(layout.districts.map((d) => d.size)).toEqual([4, 2]);
  });

  it('carries the scale the towers and beacons are drawn to', () => {
    const layout = layoutTerrain([block('10.0.0.0/24', { hosts: 250, critical_untouched: 3 }), block('10.0.9.0/24')]);
    expect(layout.maxHosts).toBe(250);
    expect(layout.maxCriticalUntouched).toBe(3);
  });
});

describe('summariseTerrain', () => {
  it('names the block with the most untouched critical exposure', () => {
    const s = summariseTerrain([
      block('10.0.0.0/24', { critical_untouched: 2 }),
      block('10.0.1.0/24', { critical_untouched: 5 }),
      block('10.0.2.0/24'),
    ]);
    expect(s.criticalUntouched).toBe(7);
    expect(s.blocksWithCriticalUntouched).toBe(2);
    expect(s.worst?.cidr).toBe('10.0.1.0/24');
    expect(s.byStage).toEqual({ tested: 3, planned: 6, worked: 9, untouched: 12 });
    expect(s.hosts).toBe(30);
  });
});

describe('terrainBlockQuery', () => {
  it('opens exactly the hosts each count counts (AND before NOT: implicit AND does not reach a NOT)', () => {
    expect(terrainBlockQuery('10.0.0.0/24')).toBe('subnet:"10.0.0.0/24"');
    expect(terrainBlockQuery('10.0.0.0/24', 'planned')).toBe('subnet:"10.0.0.0/24" has:planned AND NOT has:tested');
    expect(terrainBlockQuery('10.0.0.0/24', 'worked')).toBe('subnet:"10.0.0.0/24" AND NOT has:untouched AND NOT has:planned AND NOT has:tested');
    expect(terrainBlockQuery('10.0.0.0/24', 'critical_untouched')).toBe('subnet:"10.0.0.0/24" has:untouched has:critical');
  });
});

describe('terrainPalette', () => {
  const tokens = {
    info: '208 79% 51%', foreground: '189 42% 9%', mutedForeground: '193 13% 37%', critical: '0 66% 47%',
  };
  it('reads the theme tokens as HSL components', () => {
    expect(hslComponentsToRgb('0 100% 50%').map(Math.round)).toEqual([255, 0, 0]);
  });
  it('anchors a light surface past the accent, a dark one at it', () => {
    const light = terrainPalette({ ...tokens, background: '120 10% 96%' });
    const dark = terrainPalette({ ...tokens, background: '217 37% 7%' });
    expect(dark.tested).toBe('#1f89e5'); // the accent itself
    expect(light.tested).not.toBe('#1f89e5');
    expect(light.beacon).toBe(dark.beacon); // severity colour, not derived from the surface
  });
});
