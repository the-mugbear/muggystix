/**
 * The address of an import's result in the Scans page's ONE import history
 * (5.418.0; owner decision 2026-10-22, G15).  Until then the uploads were a
 * second view of the page, Ingestion results (`/scans?view=results`); now the
 * uploads that were not imported are rows of the history, and what the view's
 * filters listed is a status filter of it (`?status=needs_attention`), or one
 * upload (`?job_id=` / `?error_id=`).
 *
 * Every in-app link to an import's result is built here.  `/parse-errors`,
 * `/ingestion-results` (App.tsx) and `/scans?view=results` (pages/ScansHub)
 * only redirect to this address, with their query, for bookmarks and old
 * notification links.
 */

export type ImportResultsParams =
  | URLSearchParams
  | Readonly<Record<string, string | number | null | undefined>>;

/** What the old view's address carried that the history does not read: the
 *  view itself, its sort (the history is in upload order) and its page (the
 *  history loads more instead). */
const NOT_CARRIED = new Set(['view', 'sort', 'dir', 'page']);

/** `importResultsPath({ status: 'failed' })` → `/scans?status=failed`.
 *  A null / undefined / empty value is left out, as are `view`, `sort`, `dir`
 *  and `page`; no parameter is the whole history, `/scans`. */
export function importResultsPath(params?: ImportResultsParams): string {
  const query = new URLSearchParams();
  const entries: Iterable<[string, string | number | null | undefined]> =
    params instanceof URLSearchParams ? params.entries() : Object.entries(params ?? {});
  for (const [key, value] of entries) {
    if (NOT_CARRIED.has(key) || value == null || value === '') continue;
    query.append(key, String(value));
  }
  const qs = query.toString();
  return qs ? `/scans?${qs}` : '/scans';
}
