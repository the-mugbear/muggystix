/**
 * The address of an import's result in the Scans page's ONE import history:
 * the uploads that were not imported are rows of it, narrowed by a status
 * filter (`?status=needs_attention`) or to one upload (`?job_id=` /
 * `?error_id=`).  Every in-app link to an import's result is built here.
 */

export type ImportResultsParams = Readonly<Record<string, string | number | null | undefined>>;

/** `importResultsPath({ status: 'failed' })` → `/scans?status=failed`.
 *  A null / undefined / empty value is left out; no parameter is the whole
 *  history, `/scans`. */
export function importResultsPath(params?: ImportResultsParams): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value == null || value === '') continue;
    query.append(key, String(value));
  }
  const qs = query.toString();
  return qs ? `/scans?${qs}` : '/scans';
}
