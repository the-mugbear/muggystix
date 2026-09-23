/**
 * The Oversight page's project subset, as it lives in the URL.
 *
 * `?projects=1,4,9` — the chosen project ids; absent means every project.
 * The single-project `?project=4` links from before the multi-select still
 * open as a subset of one. Ids are positive integers, de-duplicated, sorted
 * (so the same subset is always the same URL), capped at the server's limit.
 */

export const MAX_PROJECT_FILTER = 500;

export function parseProjectIds(params: URLSearchParams): number[] {
  const raw = params.get('projects') ?? params.get('project') ?? '';
  const ids = raw
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
  return [...new Set(ids)].sort((a, b) => a - b).slice(0, MAX_PROJECT_FILTER);
}

/** The URL value for a subset; null (drop the parameter) for every project. */
export function serializeProjectIds(ids: readonly number[], total: number): string | null {
  const unique = [...new Set(ids)].sort((a, b) => a - b);
  if (unique.length === 0 || (total > 0 && unique.length >= total)) return null;
  return unique.join(',');
}

/** The subset in words: "Alpha", "Alpha and Beta", "Alpha, Beta and 3 more". */
export function describeProjects(ids: readonly number[], names: ReadonlyMap<number, string>, show = 2): string {
  const labels = ids.map((id) => names.get(id) ?? `#${id}`);
  if (labels.length <= show + 1) {
    return labels.length <= 1 ? (labels[0] ?? '') : `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
  }
  return `${labels.slice(0, show).join(', ')} and ${labels.length - show} more`;
}
