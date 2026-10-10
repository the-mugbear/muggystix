/**
 * Following scans this tab did not start (v5.207.0): an agent's, another
 * tab's.  The page's job polling only follows its own uploads, so the lists
 * stayed as they were while counters rose elsewhere.
 *
 * The marker is how many scans the project has and the newest one's id — one
 * indexed query — read every 15 s while the tab is visible.  The first answer
 * is the baseline; when a later one differs, `onMoved` is called (the page
 * says which reads are out of date).
 *
 *   const marker = useScanInventoryMarker(projectId, () => { refreshInventory(); refreshQueue(); });
 *   …after a change made HERE that moves the marker (a delete):
 *   marker.rebase();
 *
 * `rebase` reads the marker at once and makes that answer the baseline: the
 * page is already reading its lists again for the change it made, and the
 * marker's new value is not a second reason to.  It holds whichever hears of
 * the answer first — this hook's effect or `rebase`'s own continuation (the
 * order is the library's scheduler's, and differs between the browser and the
 * tests; written twice in the page, only the browser's order was right).
 */
import { useCallback, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getScanInventoryMarker, type ScanInventoryMarker } from '../../services/api';
import { pollEvery } from '../../lib/query';

export const SCAN_MARKER_POLL_MS = 15_000;

const markerKeyOf = (marker: ScanInventoryMarker): string => `${marker.count}:${marker.latest_id ?? ''}`;

export interface ScanInventoryMarkerHandle {
  /** Read the marker now and take its answer as the baseline. */
  rebase: () => void;
}

export function useScanInventoryMarker(projectId: number, onMoved: () => void): ScanInventoryMarkerHandle {
  const query = useQuery({
    queryKey: ['getScanInventoryMarker', projectId],
    queryFn: ({ signal }) => getScanInventoryMarker(projectId, signal),
    ...pollEvery(SCAN_MARKER_POLL_MS),
  });
  const markerKey = query.data ? markerKeyOf(query.data) : null;
  const baseline = useRef<string | null>(null);
  // True from `rebase()` until its read is back: the answer is the baseline.
  const rebasing = useRef(false);
  useEffect(() => {
    if (markerKey === null) return;
    if (!rebasing.current && baseline.current !== null && baseline.current !== markerKey) onMoved();
    baseline.current = markerKey;
  }, [markerKey, onMoved]);

  const { refetch } = query;
  const rebase = useCallback(() => {
    rebasing.current = true;
    void refetch().then((answer) => {
      if (answer.data) baseline.current = markerKeyOf(answer.data);
      rebasing.current = false;
    });
  }, [refetch]);
  return { rebase };
}
