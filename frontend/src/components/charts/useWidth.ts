/**
 * A chart container's width, for a Plot figure that is given its width
 * (5.406.2: one hook — GrowthCharts exported it to two other charts and
 * ActivityHistogram kept a private copy; second review 2026-10-16, I10).
 *
 * A CALLBACK ref, re-observing whenever the container mounts (v5.265.0): a
 * chart's container is replaced by an empty-state line when a filter leaves
 * no points, and an observer attached once on first render kept watching the
 * detached element — it reported 0, and the chart stayed narrow after the
 * filter was cleared.  A zero width (a detached or hidden element) is ignored
 * rather than trusted.  Where ResizeObserver is missing (tests) the width
 * stays at `initial`.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export function useWidth(initial = 640, min = 280): [(el: HTMLDivElement | null) => void, number] {
  const [w, setW] = useState(initial);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      if (width > 0) setW(Math.max(min, width));
    });
    ro.observe(el);
    observer.current = ro;
  }, [min]);
  useEffect(() => () => observer.current?.disconnect(), []);
  return [ref, w];
}

export default useWidth;
