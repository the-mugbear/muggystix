/**
 * Collapse repeat observations of the same thing to the latest one.
 *
 * Several evidence tables keep one row PER SCAN (web_interfaces,
 * netexec_results): the same URL captured by EyeWitness in August and again in
 * September is two rows. That history is worth keeping, but the Host inspector
 * listed every row in full, so a re-scanned host showed each URL twice with
 * identical status, server and title.
 *
 * This is display-only: nothing is merged or discarded in the data, and the
 * result says how many observations stand behind each row so the count is not
 * silently lost. What makes two rows "the same thing" is the caller's key.
 */
export interface LatestObservation<T> {
  latest: T;
  /** How many rows shared the key, the latest included. */
  count: number;
  /** Earliest timestamp seen for the key, when any row carried one. */
  firstSeen: string | null;
}

const toTime = (value: string | null | undefined): number => {
  if (!value) return 0;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? 0 : t;
};

export function latestObservations<T extends { id: number }>(
  rows: T[],
  keyOf: (row: T) => string,
  timeOf: (row: T) => string | null | undefined,
): LatestObservation<T>[] {
  const groups = new Map<string, LatestObservation<T>>();
  for (const row of rows) {
    const key = keyOf(row);
    const when = timeOf(row) ?? null;
    const group = groups.get(key);
    if (!group) {
      groups.set(key, { latest: row, count: 1, firstSeen: when });
      continue;
    }
    group.count += 1;
    // Newest by time; the id breaks a tie (or decides when rows carry no time).
    const newer = toTime(when) > toTime(timeOf(group.latest))
      || (toTime(when) === toTime(timeOf(group.latest)) && row.id > group.latest.id);
    if (newer) group.latest = row;
    if (when && (!group.firstSeen || toTime(when) < toTime(group.firstSeen))) group.firstSeen = when;
  }
  // First-appearance order of each key, so the list does not reshuffle.
  return Array.from(groups.values());
}
