/**
 * A record id from the address (`/scans/:scanId`, `/findings/:findingId`, a
 * `?job_id=`): a positive whole number written in digits only, else null.
 *
 * The ONE reading of an id in a route (review 2026-10-23b, FEP-11): with
 * `parseInt`, `/scans/12abc` showed scan 12; with `Number`, `/findings/abc`
 * asked for `/findings/NaN`.  A detail page given null says "not found"
 * without making a request.
 */
export function routeId(raw: string | null | undefined): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
