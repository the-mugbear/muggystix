/**
 * The address terrain (5.306.0; shown on Posture since 5.330.0) — pure layout
 * and vocabulary, no three.js.
 *
 * Each address block (/24; IPv6 /64) is a plot. The blocks of a /16 form a
 * district: in address order along a Hilbert curve, so consecutive blocks stay
 * neighbours on the ground (a /23 is a pair of plots, a run of /24s a compact
 * patch, never a stripe). Only OCCUPIED blocks take a plot — a district is the
 * smallest square holding them — so one busy /16 among quiet ones is not a
 * speck beside empty plates. Exact addresses are in the readout and table.
 * Districts are laid out in address order; IPv6 /64s have their own.
 */
import type { TerrainBlock } from '../services/api';
import { buildHostsUrl } from './drilldownLinks';

export type TerrainStage = 'tested' | 'planned' | 'worked' | 'untouched';

/** Bottom to top on a tower, and the legend's order. */
export const TERRAIN_STAGES: Array<{ key: TerrainStage; label: string; description: string }> = [
  { key: 'tested', label: 'Tested', description: 'Evidence of a test that ran is recorded for it.' },
  { key: 'planned', label: 'Planned', description: 'A test is proposed or in progress; none has run yet.' },
  { key: 'worked', label: 'Someone has it', description: 'Reviewed, assigned, noted or in a finding — not planned.' },
  { key: 'untouched', label: 'Untouched', description: 'Nobody has reviewed, assigned, noted, planned or reported it.' },
];

/** A /16 holds 256 /24s: a 16 × 16 district at most. */
export const DISTRICT_SIZE = 16;
const DISTRICT_GAP = 3;

export interface PlacedBlock {
  block: TerrainBlock;
  /** Plot centre on the ground plane (x right, z towards the viewer). */
  x: number;
  z: number;
}

export interface TerrainDistrict {
  label: string;
  /** Corner of the district's ground plate, and its side in plots. */
  x: number;
  z: number;
  size: number;
}

export interface TerrainLayout {
  placed: PlacedBlock[];
  districts: TerrainDistrict[];
  width: number;
  depth: number;
  maxHosts: number;
  maxCriticalUntouched: number;
}

/** Hilbert curve index → (x, y) on an n × n grid (n a power of two). */
export function hilbertPoint(n: number, d: number): [number, number] {
  let x = 0;
  let y = 0;
  let t = d;
  for (let s = 1; s < n; s *= 2) {
    const rx = 1 & (t / 2);
    const ry = 1 & (t ^ rx);
    if (ry === 0) {
      if (rx === 1) {
        x = s - 1 - x;
        y = s - 1 - y;
      }
      [x, y] = [y, x];
    }
    x += s * rx;
    y += s * ry;
    t = Math.floor(t / 4);
  }
  return [x, y];
}

const districtOf = (cidr: string, v6Index: number): { key: string; label: string } => {
  if (!cidr.includes(':')) {
    const [a, b] = cidr.split('/')[0].split('.').map(Number);
    return { key: `4:${a}.${b}`, label: `${a}.${b}.0.0/16` };
  }
  const page = Math.floor(v6Index / (DISTRICT_SIZE * DISTRICT_SIZE));
  return { key: `6:${page}`, label: page === 0 ? 'IPv6 /64s' : `IPv6 /64s (${page + 1})` };
};

/** The smallest Hilbert square (side a power of two, at least 2) with a plot
 *  for each of `count` blocks. */
export const districtSide = (count: number): number => {
  let side = 2;
  while (side * side < count && side < DISTRICT_SIZE) side *= 2;
  return side;
};

/** Blocks arrive in address order (the API sorts them). */
export function layoutTerrain(blocks: TerrainBlock[]): TerrainLayout {
  const order: string[] = [];
  const labels = new Map<string, string>();
  const count = new Map<string, number>();
  const cells: Array<{ block: TerrainBlock; key: string; d: number }> = [];
  let v6 = 0;
  for (const block of blocks) {
    const { key, label } = districtOf(block.cidr, block.cidr.includes(':') ? v6++ : 0);
    if (!labels.has(key)) {
      labels.set(key, label);
      order.push(key);
    }
    const d = count.get(key) ?? 0; // the block's rank in its district, in address order
    count.set(key, d + 1);
    cells.push({ block, key, d });
  }
  const side = new Map(order.map((key) => [key, districtSide(count.get(key) ?? 1)]));
  // Districts on a grid in address order; each column as wide and each row
  // as deep as its largest district.
  const cols = Math.max(1, Math.ceil(Math.sqrt(order.length)));
  const rows = Math.max(1, Math.ceil(order.length / cols));
  const colW = Array.from({ length: cols }, (_, c) => Math.max(0, ...order.filter((_, i) => i % cols === c).map((k) => side.get(k) ?? 0)));
  const rowD = Array.from({ length: rows }, (_, r) => Math.max(0, ...order.filter((_, i) => Math.floor(i / cols) === r).map((k) => side.get(k) ?? 0)));
  const offset = (sizes: number[], i: number) => sizes.slice(0, i).reduce((a, v) => a + v + DISTRICT_GAP, 0);
  const width = offset(colW, cols) - DISTRICT_GAP;
  const depth = offset(rowD, rows) - DISTRICT_GAP;
  const corner = new Map<string, [number, number]>();
  const districts: TerrainDistrict[] = order.map((key, i) => {
    const x = offset(colW, i % cols) - width / 2;
    const z = offset(rowD, Math.floor(i / cols)) - depth / 2;
    corner.set(key, [x, z]);
    return { label: labels.get(key) ?? key, x, z, size: side.get(key) ?? DISTRICT_SIZE };
  });
  const placed = cells.map(({ block, key, d }) => {
    const [cx, cz] = corner.get(key) ?? [0, 0];
    const [hx, hy] = hilbertPoint(side.get(key) ?? DISTRICT_SIZE, d);
    return { block, x: cx + hx + 0.5, z: cz + hy + 0.5 };
  });
  return {
    placed,
    districts,
    width,
    depth,
    maxHosts: blocks.reduce((m, b) => Math.max(m, b.hosts), 0),
    maxCriticalUntouched: blocks.reduce((m, b) => Math.max(m, b.critical_untouched), 0),
  };
}

// -- drill-downs: every count opens exactly its hosts -------------------------

const inBlock = (cidr: string) => `subnet:"${cidr}"`;

export const terrainBlockQuery = (cidr: string, part?: TerrainStage | 'critical_untouched'): string => {
  const b = inBlock(cidr);
  switch (part) {
    case 'tested': return `${b} has:tested`;
    case 'planned': return `${b} has:planned AND NOT has:tested`;
    // Tested no longer implies planned (5.320.0), so both are excluded.
    case 'worked': return `${b} AND NOT has:untouched AND NOT has:planned AND NOT has:tested`;
    case 'untouched': return `${b} has:untouched`;
    case 'critical_untouched': return `${b} has:untouched has:critical`;
    default: return b;
  }
};

export const terrainBlockHref = (cidr: string, part?: TerrainStage | 'critical_untouched'): string =>
  buildHostsUrl({ q: terrainBlockQuery(cidr, part) });

export const UNTOUCHED_CRITICAL_HREF = buildHostsUrl({ q: 'has:untouched has:critical' });

export interface TerrainSummary {
  hosts: number;
  byStage: Record<TerrainStage, number>;
  criticalUntouched: number;
  blocksWithCriticalUntouched: number;
  /** The block holding the most untouched critical hosts (ties: lower address). */
  worst: TerrainBlock | null;
}

export function summariseTerrain(blocks: TerrainBlock[]): TerrainSummary {
  const byStage: Record<TerrainStage, number> = { tested: 0, planned: 0, worked: 0, untouched: 0 };
  let hosts = 0;
  let criticalUntouched = 0;
  let blocksWithCriticalUntouched = 0;
  let worst: TerrainBlock | null = null;
  for (const b of blocks) {
    hosts += b.hosts;
    TERRAIN_STAGES.forEach(({ key }) => { byStage[key] += b[key]; });
    criticalUntouched += b.critical_untouched;
    if (b.critical_untouched > 0) {
      blocksWithCriticalUntouched += 1;
      if (!worst || b.critical_untouched > worst.critical_untouched) worst = b;
    }
  }
  return { hosts, byStage, criticalUntouched, blocksWithCriticalUntouched, worst };
}
