/**
 * Filtering and focus for the "What BlueStick reads" page (v5.296.0).
 *
 * Pure helpers so the page's rules are tested without the API client: which
 * rows a search / level filter keeps, which tool a `?tool=` or `?format=` link
 * lands on, and the per-level counts the page opens with.
 */
import type { CoverageLevel, CoverageSignal, ToolCoverage } from '../services/api';

export interface CoverageFilter {
  query: string;
  level: CoverageLevel | 'all';
}

const matches = (text: string | null | undefined, q: string): boolean =>
  !!text && text.toLowerCase().includes(q);

const signalMatches = (signal: CoverageSignal, q: string): boolean =>
  matches(signal.what, q) || matches(signal.input, q) || matches(signal.note, q)
  || matches(signal.shown, q) || signal.stored_as.some((ref) => matches(ref, q));

export interface FilteredTool {
  tool: ToolCoverage;
  signals: CoverageSignal[];
  gaps: string[];
}

/**
 * The tools a filter keeps, each with the rows and gaps that match.  A query
 * naming the tool (its name, a format label, a registry name such as
 * "gobuster") keeps all of that tool's rows; otherwise a row must match
 * itself.  The level filter always applies to rows; gaps are kept only while
 * no level is chosen, since a gap has no level.
 */
export function filterCoverage(tools: ToolCoverage[], filter: CoverageFilter): FilteredTool[] {
  const q = filter.query.trim().toLowerCase();
  const out: FilteredTool[] = [];
  for (const tool of tools) {
    const toolHit = !!q && (
      matches(tool.name, q)
      || tool.formats.some((f) => matches(f.label, q) || f.file_type === q)
      || tool.registry_tools.some((name) => name.toLowerCase() === q)
    );
    const byLevel = tool.signals.filter((s) => filter.level === 'all' || s.level === filter.level);
    const signals = !q || toolHit ? byLevel : byLevel.filter((s) => signalMatches(s, q));
    const gaps = filter.level !== 'all' ? [] : !q || toolHit ? tool.gaps : tool.gaps.filter((g) => matches(g, q));
    if (signals.length > 0 || gaps.length > 0) out.push({ tool, signals, gaps });
  }
  return out;
}

/** The tool a link lands on: `?tool=` accepts the page's id or a registry
 *  name (gobuster → the directory brute-force parser); `?format=` a
 *  file_type (Ingestion Results links by the format a file was parsed as). */
export function resolveFocus(
  tools: ToolCoverage[],
  params: { tool?: string | null; format?: string | null },
): ToolCoverage | null {
  const name = params.tool?.trim().toLowerCase();
  if (name) {
    const hit = tools.find((t) => t.id === name) ?? tools.find((t) => t.registry_tools.includes(name));
    if (hit) return hit;
  }
  const format = params.format?.trim();
  if (format) return tools.find((t) => t.formats.some((f) => f.file_type === format)) ?? null;
  return null;
}

/** Rows per level across every tool. */
export function countByLevel(tools: ToolCoverage[]): Record<CoverageLevel, number> {
  const counts: Record<CoverageLevel, number> = { observation: 0, field: 0, text: 0, stored: 0, discarded: 0 };
  for (const tool of tools) for (const s of tool.signals) counts[s.level] += 1;
  return counts;
}

/** Anchor id of a tool's section. */
export const toolAnchor = (id: string): string => `tool-${id}`;
