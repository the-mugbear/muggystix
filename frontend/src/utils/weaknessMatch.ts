/**
 * Why a row is in the list when a weakness condition is applied (5.303.0).
 *
 * The Exposure cell's "Matched" line named only the port a port / service
 * condition matched; under `has:smb_unsigned` a row said "Matched ftp 21/tcp"
 * and nothing about SMB.  Which flags and checks the conditions NAME is the
 * server's answer (`matched_weakness_flags` / `matched_check_ids` on a
 * `GET /hosts` row — `host_query.matched_weakness_terms`, over the filter's
 * own parse of the query): this only words them.  It used to read `has:` and
 * `check:` out of the query text with a regex — a second reader of the Hosts
 * language that missed `issue:"check:…"` and `kind:misconfiguration`
 * (review 2026-10-23b, FEP-5; the same defect R-FE2 removed from
 * `endpointMatch`).
 */
export interface MatchedWeakness {
  key: string;
  label: string;
}

export function matchedWeaknesses(
  host: { matched_weakness_flags?: string[]; matched_check_ids?: string[] },
  labels: { flag?: (f: string) => string | undefined; check?: (id: string) => string | undefined } = {},
): MatchedWeakness[] {
  const humanize = (v: string) => v.replace(/_/g, ' ');
  return [
    ...(host.matched_weakness_flags ?? [])
      .map((f) => ({ key: `has:${f}`, label: labels.flag?.(f) ?? humanize(f) })),
    ...(host.matched_check_ids ?? [])
      .map((c) => ({ key: `check:${c}`, label: labels.check?.(c) ?? humanize(c) })),
  ];
}
