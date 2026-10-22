/**
 * Addresses of the Scanner observations page (5.404.0, Inventory).
 *
 * It lists EVERY observation by default; `obs_judged=waiting` narrows it to
 * those with a host not yet judged — what the Findings page's "Scanner
 * observations" view listed, where the page used to live
 * (`/findings?view=observations`).  (`obs_pending=1` said that until 5.418.0;
 * the Judged filter has four values since.)
 */

export const OBSERVATIONS_PATH = '/observations';
/** What still waits for a judgment. */
export const OBSERVATIONS_TO_JUDGE_PATH = `${OBSERVATIONS_PATH}?obs_judged=waiting`;

/** One issue open in the page's inspector (review 2026-10-16, O7: a host's
 *  weakness row links here). */
export const observationPath = (issueKey: string): string =>
  `${OBSERVATIONS_PATH}?issue=${encodeURIComponent(issueKey)}`;

/** The host page's element for one issue's weakness row, and the hash that
 *  opens the page there (O4 / O7: the inspector's host links land on it). */
export const weaknessAnchorId = (issueKey: string): string => `weakness-${issueKey}`;
export const weaknessHash = (issueKey: string): string => `#${encodeURIComponent(weaknessAnchorId(issueKey))}`;
/** The issue a host page's hash names, or null. */
export const issueFromWeaknessHash = (hash: string): string | null => {
  if (!hash.startsWith('#')) return null;
  let id: string;
  try {
    id = decodeURIComponent(hash.slice(1));
  } catch {
    return null;
  }
  return id.startsWith('weakness-') ? id.slice('weakness-'.length) : null;
};

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
  if (params.get('obs_judged') !== '1') next.set('obs_judged', 'waiting');
  const query = next.toString();
  return query ? `${OBSERVATIONS_PATH}?${query}` : OBSERVATIONS_PATH;
};
