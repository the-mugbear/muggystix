/**
 * Why a row is in the list when a weakness condition is applied (5.303.0).
 *
 * The Exposure cell's "Matched" line named only the port a port / service
 * condition matched; under `has:smb_unsigned` a row said "Matched ftp 21/tcp"
 * and nothing about SMB.  The criteria are the flags and checks the conditions
 * NAME — the structured Weakness / Check filters and `has:` / `check:` terms in
 * the query — and a row shows the ones it carries.  Showing only what the host
 * has AND a condition names is true however the query combines them (a
 * negated term names a flag the matched host does not carry, so it shows
 * nothing).
 */
export interface WeaknessMatchCriteria {
  flags: string[];
  checks: string[];
}

const termValues = (query: string, field: string): string[] => {
  const out: string[] = [];
  const re = new RegExp(`(?:^|[\\s(])${field}:("[^"]*"|[^\\s()]+)`, 'gi');
  for (const m of query.matchAll(re)) {
    m[1].replace(/^"|"$/g, '').split(',').forEach((v) => {
      const value = v.trim().toLowerCase();
      if (value) out.push(value);
    });
  }
  return out;
};

export function weaknessMatchCriteria(filters: {
  weaknesses?: string[];
  checks?: string[];
  query?: string;
}): WeaknessMatchCriteria | null {
  const query = filters.query ?? '';
  const flags = [...new Set([...(filters.weaknesses ?? []), ...termValues(query, 'has')])];
  const checks = [...new Set([...(filters.checks ?? []), ...termValues(query, 'check')])];
  return flags.length || checks.length ? { flags, checks } : null;
}

export interface MatchedWeakness {
  key: string;
  label: string;
}

export function matchedWeaknesses(
  host: { weakness_flags?: string[]; check_ids?: string[] },
  criteria: WeaknessMatchCriteria | null,
  labels: { flag?: (f: string) => string | undefined; check?: (id: string) => string | undefined } = {},
): MatchedWeakness[] {
  if (!criteria) return [];
  const humanize = (v: string) => v.replace(/_/g, ' ');
  return [
    ...(host.weakness_flags ?? [])
      .filter((f) => criteria.flags.includes(f))
      .map((f) => ({ key: `has:${f}`, label: labels.flag?.(f) ?? humanize(f) })),
    ...(host.check_ids ?? [])
      .filter((c) => criteria.checks.includes(c))
      .map((c) => ({ key: `check:${c}`, label: labels.check?.(c) ?? humanize(c) })),
  ];
}
