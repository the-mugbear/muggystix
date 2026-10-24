/**
 * Addresses of the Scanner observations page (Inventory).
 *
 * It lists EVERY observation by default; `obs_judged=waiting` narrows it to
 * those with a host not yet judged.
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
