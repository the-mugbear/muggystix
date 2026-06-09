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
  return { dsl, consumedKeys: consumed };
}
