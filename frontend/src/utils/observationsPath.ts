/**
 * Addresses of the Scanner observations page (5.404.0, Inventory).
 *
 * It lists EVERY observation by default; `obs_pending=1` narrows it to those
 * with a host not yet judged — what the Findings page's "Scanner observations"
 * view listed, where the page used to live (`/findings?view=observations`).
 */

export const OBSERVATIONS_PATH = '/observations';
/** What still waits for a judgment. */
export const OBSERVATIONS_TO_JUDGE_PATH = `${OBSERVATIONS_PATH}?obs_pending=1`;

/**
 * Where an old `/findings?view=observations…` link lands: the same filters and
 * page, narrowed to what is not yet judged unless it asked for the judged too
 * (`obs_judged=1`, the old switch — the new page's default).
 */
export const observationsPathFromFindingsView = (params: URLSearchParams): string => {
  const next = new URLSearchParams();
  params.forEach((value, key) => {
    if (key === 'view' || key === 'obs_judged') return;
    next.append(key, value);
  });
  if (params.get('obs_judged') !== '1') next.set('obs_pending', '1');
  const query = next.toString();
  return query ? `${OBSERVATIONS_PATH}?${query}` : OBSERVATIONS_PATH;
};
