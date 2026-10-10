/**
 * The address of Ingestion results — a VIEW of the Scans page since 5.380.0
 * (`/scans?view=results`), not a page of its own.
 *
 * Every in-app link to it is built here, so the view parameter is written in
 * one place.  `/parse-errors` and `/ingestion-results` only redirect to this
 * address (App.tsx) for bookmarks and old notification links.
 */

/** The value of `view` that names Ingestion results. */
export const IMPORT_RESULTS_VIEW = 'results';

export type ImportResultsParams =
  | URLSearchParams
  | Readonly<Record<string, string | number | null | undefined>>;

/** `importResultsPath({ status: 'failed' })` → `/scans?view=results&status=failed`.
 *  A null / undefined / empty value is left out; a `view` among the
 *  parameters is ignored (the view is this function's to name). */
export function importResultsPath(params?: ImportResultsParams): string {
  const query = new URLSearchParams({ view: IMPORT_RESULTS_VIEW });
  const entries: Iterable<[string, string | number | null | undefined]> =
    params instanceof URLSearchParams ? params.entries() : Object.entries(params ?? {});
  for (const [key, value] of entries) {
    if (key === 'view' || value == null || value === '') continue;
    query.append(key, String(value));
  }
  return `/scans?${query.toString()}`;
}
