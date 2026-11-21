/**
 * The one read of a report's slide list for a host filter (5.424.0): the page,
 * the presenter view's previews (a Display may show another filter's list) and
 * the agenda editor share it.  An issued report never changes, so the list for
 * a filter is the same on every read — remembered for good.
 */
import { getPresentationSlides, type HostFilter, type Slide } from '../../services/api';
import { rememberFor } from '../../lib/query';

export const slidesKey = (projectId: number, reportId: number, filter: HostFilter | null) =>
  ['getPresentationSlides', projectId, reportId, filter?.kind ?? null, filter?.value ?? null] as const;

export const slidesQuery = (projectId: number, reportId: number, filter: HostFilter | null) => ({
  queryKey: slidesKey(projectId, reportId, filter),
  queryFn: ({ signal }: { signal: AbortSignal }) => getPresentationSlides(projectId, reportId, filter, signal),
  // An issued report's slides do not change, so the unfiltered list is kept
  // for the life of the tab.  A FILTERED one is kept for a while only: every
  // filter the driver tried used to stay in every following tab's memory for
  // the session (megabytes each on a large report).
  ...rememberFor(filter ? FILTERED_LIST_KEPT_MS : Infinity),
});

/** How long a filtered slide list nobody shows any more stays in memory. */
export const FILTERED_LIST_KEPT_MS = 10 * 60_000;

/** How soon a slide-list read that failed is asked again (`pollEvery`). */
export const SLIDES_RETRY_MS = 2_000;

/** A slide as the lists name it: "F-01 · Impact — 2 of 3". */
export const slideLabel = (slide: Slide | undefined | null, key?: string): string =>
  slide ? `${slide.header || 'Overview'}${slide.pages > 1 ? ` — ${slide.page} of ${slide.pages}` : ''}` : (key ?? '');
