import type { HostFilterOptions } from '../HostFilters';

/**
 * Serialize the flat panel-filter state into boolean-DSL text for the
 * "Convert filters → query" button.
 *
 * The panel is always a flat AND of clauses, so it's trivially serializable.
 * Only fields the DSL can represent are converted; the rest (out-of-scope,
 * first-seen-in-scan, and id-based tag/label selections that the DSL expresses
 * by name) are left in the panel and reported back so the caller clears only
 * what it actually moved. The conversion is intentionally one-way — the DSL is
 * strictly more expressive than the panel, so a reverse mapping is lossy.
 */

// A value is "bare" (needs no quoting) only if it contains none of the DSL's
// structural characters. ':' must NOT be here — the lexer breaks tokens on it,
// so an unquoted IPv6 (fe80::1) or URL-shaped value would split mid-value.
const BARE = /^[A-Za-z0-9_./@-]+$/;

/** Quote a DSL value if it isn't a bare token (spaces, commas, quotes, parens
 *  would otherwise reparse as separate clauses/operators). Shared with the
 *  command-bar autocomplete so suggested values insert as valid DSL. */
export function quote(value: string): string {
  if (BARE.test(value)) return value;
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

function field(name: string, values: string[]): string {
  return `${name}:${values.map(quote).join(',')}`;
}

export interface DslConversion {
  dsl: string;
  consumedKeys: (keyof HostFilterOptions)[];
  /**
   * True when the conversion CHANGES result semantics and can't be made
   * faithful.  The panel fuses ports/services/port-states into one port row
   * (a single port must satisfy all — "HTTP on port 22"), but the DSL has no
   * same-row grouping, so it emits them as independent host-level clauses
   * ("port 22 anywhere AND HTTP anywhere").  That only diverges when ≥2 of
   * those three port dimensions are set together; a single one round-trips
   * fine.  The caller warns before applying a lossy conversion.
   */
  lossy: boolean;
}

export function dslFromFilters(filters: HostFilterOptions): DslConversion {
  const clauses: string[] = [];
  const consumed: (keyof HostFilterOptions)[] = [];
  const take = (key: keyof HostFilterOptions) => consumed.push(key);

  if (filters.search) { clauses.push(quote(filters.search)); take('search'); }
  if (filters.state) { clauses.push(field('state', [filters.state])); take('state'); }
  if (filters.osFilter) { clauses.push(field('os', [filters.osFilter])); take('osFilter'); }
  if (filters.ports?.length) { clauses.push(field('port', filters.ports)); take('ports'); }
  if (filters.services?.length) { clauses.push(field('service', filters.services)); take('services'); }
  if (filters.portStates?.length) { clauses.push(field('portstate', filters.portStates)); take('portStates'); }
  if (filters.subnets?.length) { clauses.push(field('subnet', filters.subnets)); take('subnets'); }
  if (filters.sites?.length) { clauses.push(field('site', filters.sites)); take('sites'); }
  if (filters.tech?.length) { clauses.push(field('tech', filters.tech)); take('tech'); }
  if (filters.scanIds?.length) { clauses.push(field('scan', filters.scanIds)); take('scanIds'); }

  if (filters.hasOpenPorts !== undefined) {
    clauses.push(filters.hasOpenPorts ? 'has:open_ports' : 'NOT has:open_ports');
    take('hasOpenPorts');
  }
  if (filters.hasWebInterface !== undefined) {
    clauses.push(filters.hasWebInterface ? 'has:web' : 'NOT has:web');
    take('hasWebInterface');
  }
  if (filters.hasCriticalVulns) { clauses.push('has:critical'); take('hasCriticalVulns'); }
  if (filters.hasHighVulns) { clauses.push('has:high'); take('hasHighVulns'); }
  // medium/low have DSL keywords too (has:medium / has:low) — without these a
  // drill-down into a medium/low severity left a filter that couldn't convert.
  if (filters.hasMediumVulns) { clauses.push('has:medium'); take('hasMediumVulns'); }
  if (filters.hasLowVulns) { clauses.push('has:low'); take('hasLowVulns'); }
  if (filters.hasExploitAvailable) { clauses.push('has:exploit'); take('hasExploitAvailable'); }
  if (filters.hasTestExecution) { clauses.push('has:tested'); take('hasTestExecution'); }
  if (filters.onlyWithNotes) { clauses.push('has:notes'); take('onlyWithNotes'); }
  if (filters.followFilter) {
    clauses.push(field('follow', [filters.followFilter]));
    take('followFilter');
  }
  if (filters.assignedToMe) { clauses.push('assigned:me'); take('assignedToMe'); }

  // Combine with any existing query (whitespace = implicit AND).
  const existing = (filters.query || '').trim();
  const dsl = [existing, ...clauses].filter(Boolean).join(' ');

  // Lossy when ≥2 port dimensions are combined — the panel correlates them to
  // one port row, the DSL can't.  has_open_ports is excluded: the backend
  // treats it as a standalone exclusion, not part of the fused port match.
  const portDimensions = [
    filters.ports?.length,
    filters.services?.length,
    filters.portStates?.length,
  ].filter(Boolean).length;
  const lossy = portDimensions >= 2;

  return { dsl, consumedKeys: consumed, lossy };
}
