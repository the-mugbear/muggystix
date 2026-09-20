/**
 * The queue a host was opened from on Operations (v5.243.0).
 *
 * A host opened from My work offered "Back to my work" (v5.237.0) but its Next
 * went nowhere: the standalone page walks a server-side Hosts query by index,
 * and a My-work section is not a query — it is the short list the analyst was
 * looking at. So the section's host ids travel with the navigation, in display
 * order, and the host page steps through exactly those.
 *
 * Pure — router state in, router state out.
 */
export interface OperationsNavState {
  fromOperations: true;
  /** The section's hosts, in the order they were shown. */
  hostIds?: number[];
  /** What to call the queue in the position counter ("2 of 5 in Worth a look"). */
  queueLabel?: string;
  /** The section holds more than Operations had loaded, so `hostIds` is its
   *  first part. A flag, not a number: the server's totals count ROWS, and a
   *  row is not always a host. */
  queuePartial?: boolean;
}

const HOST_PATH = /^\/hosts\/(\d+)(?:[/?#]|$)/;

/** The host id a link points at, or null for anything that is not a host page. */
export const hostIdOf = (to: string): number | null => {
  const match = HOST_PATH.exec(to);
  return match ? Number(match[1]) : null;
};

/** De-duplicated, order-preserving (a host can appear twice in one section —
 *  two reviewers' follow-ups, a note and a plan step). */
export const uniqueHostIds = (ids: Array<number | null | undefined>): number[] => {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const id of ids) {
    if (id == null || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
};

/** Navigation options for a host opened from an Operations section. A queue of
 *  one is no queue: the page then offers Back only, as before.
 *
 *  Pass the WHOLE section — every row Operations loaded for it — never the rows
 *  on screen. The cards preview three to five rows behind a "Show more", and a
 *  queue built from the preview silently dropped the rest (v5.243.0's first
 *  build: four hosts in review, Next walked three). */
export const fromOperationsQueue = (
  hostIds: Array<number | null | undefined>,
  queueLabel: string,
  options: { partial?: boolean } = {},
): { state: OperationsNavState } => {
  const ids = uniqueHostIds(hostIds);
  if (ids.length <= 1) return { state: { fromOperations: true } };
  return {
    state: {
      fromOperations: true,
      hostIds: ids,
      queueLabel,
      ...(options.partial ? { queuePartial: true } : {}),
    },
  };
};
