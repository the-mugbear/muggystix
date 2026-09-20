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
 *  one is no queue: the page then offers Back only, as before. */
export const fromOperationsQueue = (
  hostIds: Array<number | null | undefined>,
  queueLabel: string,
): { state: OperationsNavState } => {
  const ids = uniqueHostIds(hostIds);
  return {
    state: ids.length > 1
      ? { fromOperations: true, hostIds: ids, queueLabel }
      : { fromOperations: true },
  };
};
