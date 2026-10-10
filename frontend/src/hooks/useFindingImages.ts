/**
 * A finding's images as the client report sees them — one load for the page:
 * the comment thread's image rows (caption, "In report", where each is
 * placed), the report-text editor's "Insert image" picker and the preview all
 * read this list.
 *
 * The bytes of an image are fetched only when something asks for them
 * (`resolver.ensure` for a placed image, `thumbnails.ensure` for a row of the
 * comment thread, the picker when it opens) — through the authenticated
 * attachment route — and only for an id on THIS finding's list: a reference
 * in the text never becomes a URL.
 *
 * **One cache of object URLs for the page** (review 2026-10-01 M6): the
 * placed images, the picker and the thread's thumbnails share it, so an image
 * is fetched once.  The cache itself is `hooks/useAttachmentImages` (the one
 * a host note's card uses too): a few fetches at a time, not a finding's
 * every screenshot at once; one that fails is `failed(id)` — said, with a
 * retry, never a spinner for ever — and a URL made after the page is gone,
 * or for the finding it has left, is revoked at once.
 *
 * **Three states for the list** (S4): `loading` until it has been read,
 * `ready`, `failed`.  "Not one of this finding's images" may only be said
 * when the list is `ready`.
 *
 * The list is a query (`['getFindingImages', projectId, findingId]`, lib/query).  The
 * object URLs are NOT server state: the cache holds them and revokes every
 * one (`lib/objectUrls`).
 *
 * The API is an injectable dependency so the state machine is tested with
 * `renderHook` (see `tests/hooks/useFindingImages.test.ts`).
 */
import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getFindingImages, getNoteAttachmentObjectUrl } from '../services/api';
import type { FindingImage } from '../services/api';
import { queryErrorText } from '../lib/query';
import { EvidenceImages, ImageListStatus, ImageThumbnails } from '../utils/evidenceImages';
import { useAttachmentImages } from './useAttachmentImages';
import { useProjectId } from './useProjectId';

export interface FindingImagesDeps {
  getFindingImages: typeof getFindingImages;
  getNoteAttachmentObjectUrl: typeof getNoteAttachmentObjectUrl;
}

export interface FindingImagesState {
  /** Every image on the finding; empty until loaded. */
  images: FindingImage[];
  /** The ones a section may place: ticked "In report", in a printable format. */
  placeable: FindingImage[];
  captionMax: number;
  loading: boolean;
  /** Why the last read of the list failed; cleared by the next success. */
  error: string | null;
  /** `loading` until the list has been read once, then `ready` — or `failed`
   *  when it never could be.  A failed RE-read keeps `ready` (and `error`). */
  listStatus: ImageListStatus;
  reload: () => void;
  /** Object URLs loaded so far, by attachment id. */
  urls: Record<number, string>;
  /** For the preview and the editor: only images a section may place. */
  resolver: EvidenceImages;
  /** For the comment thread: any image on the finding's list. */
  thumbnails: ImageThumbnails;
}

// Built on use, not at import: a page test that mocks the `services/api`
// barrel without these two would otherwise fail on import.
const defaultDeps = (): FindingImagesDeps => ({ getFindingImages, getNoteAttachmentObjectUrl });

const NO_IMAGES: FindingImage[] = [];

export function useFindingImages(
  findingId: number | null | undefined,
  injected?: FindingImagesDeps,
): FindingImagesState {
  const deps = useMemo(() => injected ?? defaultDeps(), [injected]);
  const projectId = useProjectId();
  // The list: one query per finding, so another finding's first render is
  // already "loading" and a late answer for the one the page left lands
  // nowhere.
  const list = useQuery({
    queryKey: ['getFindingImages', projectId, findingId],
    queryFn: ({ signal }) => deps.getFindingImages(projectId, findingId as number, signal),
    enabled: findingId != null,
  });
  const images = list.data?.items ?? NO_IMAGES;
  const captionMax = list.data?.caption_max ?? 2000;
  const error = queryErrorText(list.error, 'Could not load the finding’s images.');
  const listStatus: ImageListStatus = findingId == null || list.data ? 'ready' : error ? 'failed' : 'loading';
  const { refetch } = list;
  const reload = useCallback(() => {
    if (findingId != null) void refetch();
  }, [findingId, refetch]);

  // The bytes: the page's one cache (`useAttachmentImages` — the queue, the
  // limit, the failures, and the object URLs with their revoking), for THIS
  // finding.  Another finding starts empty.  What this hook adds is which ids
  // may be asked for at all: only an image on the finding's own list.
  const {
    urls, failed, request, retry: fetchAgain,
  } = useAttachmentImages(findingId, deps.getNoteAttachmentObjectUrl);

  const placeable = useMemo(() => images.filter((i) => i.in_report && i.printable), [images]);
  const byId = useMemo(() => new Map(placeable.map((i) => [i.id, i])), [placeable]);
  const listed = useMemo(() => new Set(images.map((i) => i.id)), [images]);

  const retry = useCallback((id: number) => {
    if (listed.has(id)) fetchAgain(id);
  }, [listed, fetchAgain]);

  const ensure = useCallback((id: number) => {
    if (byId.has(id)) request(id);
  }, [byId, request]);
  const ensureListed = useCallback((id: number) => {
    if (listed.has(id)) request(id);
  }, [listed, request]);

  const resolver = useMemo<EvidenceImages>(() => ({
    lookup: (id) => {
      const image = byId.get(id);
      return image ? { caption: image.caption ?? image.filename, src: urls[id] } : null;
    },
    ensure,
    listStatus,
    retryList: reload,
    failed,
    retry: (id) => { if (byId.has(id)) retry(id); },
  }), [byId, urls, ensure, listStatus, reload, failed, retry]);

  const thumbnails = useMemo<ImageThumbnails>(() => ({
    listStatus,
    has: (id) => listed.has(id),
    urls,
    ensure: ensureListed,
    failed,
    retry,
  }), [listStatus, listed, urls, ensureListed, failed, retry]);

  return {
    images, placeable, captionMax, loading: list.isFetching, error, listStatus, reload, urls, resolver, thumbnails,
  };
}
