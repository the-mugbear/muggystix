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
 * is fetched once.  Fetches go through `runLimited` (a few at a time, not a
 * finding's every screenshot at once); one that fails is `failed(id)` — said,
 * with a retry, never a spinner for ever — and a URL made after the page is
 * gone is revoked at once.
 *
 * **Three states for the list** (S4): `loading` until it has been read,
 * `ready`, `failed`.  "Not one of this finding's images" may only be said
 * when the list is `ready`.
 *
 * The API is an injectable dependency so the state machine is tested with
 * `renderHook` (see `tests/hooks/useFindingImages.test.ts`).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getFindingImages, getNoteAttachmentObjectUrl } from '../services/api';
import type { FindingImage } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import {
  EvidenceImages, IMAGE_FETCH_CONCURRENCY, ImageListStatus, ImageThumbnails,
} from '../utils/evidenceImages';
import { runLimited } from '../utils/runLimited';

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

const NO_IDS: ReadonlySet<number> = new Set();

export function useFindingImages(
  findingId: number | null | undefined,
  injected?: FindingImagesDeps,
): FindingImagesState {
  const deps = useMemo(() => injected ?? defaultDeps(), [injected]);
  const [images, setImages] = useState<FindingImage[]>([]);
  const [captionMax, setCaptionMax] = useState(2000);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which finding's list has been read.  Compared with `findingId` in render,
  // so the first render for another finding is already "loading" — not one
  // frame of "ready, and empty".
  const [readFor, setReadFor] = useState<number | null>(null);
  const [urls, setUrls] = useState<Record<number, string>>({});
  const [failedIds, setFailedIds] = useState<ReadonlySet<number>>(NO_IDS);
  const [reloadKey, setReloadKey] = useState(0);
  // Which finding the state is for: a response that arrives after the page
  // moved to another finding is dropped.
  const generation = useRef(0);
  // Which finding the byte cache is for.
  const cacheEpoch = useRef(0);
  const requested = useRef(new Set<number>());
  const queue = useRef<number[]>([]);
  const draining = useRef(false);
  const created = useRef<string[]>([]);
  const unmounted = useRef(false);
  const depsRef = useRef(deps);
  depsRef.current = deps;

  // Another finding: its images are not this one's.
  useEffect(() => {
    cacheEpoch.current += 1;
    requested.current = new Set();
    queue.current = [];
    setImages([]);
    setUrls({});
    setFailedIds(NO_IDS);
    setError(null);
  }, [findingId]);

  useEffect(() => {
    if (findingId == null) return undefined;
    const mine = ++generation.current;
    setLoading(true);
    depsRef.current.getFindingImages(findingId).then(
      (list) => {
        if (generation.current !== mine) return;
        setImages(list.items ?? []);
        setCaptionMax(list.caption_max ?? 2000);
        setReadFor(findingId);
        setError(null);
        setLoading(false);
      },
      (err) => {
        if (generation.current !== mine) return;
        setError(formatApiError(err, 'Could not load the finding’s images.'));
        setLoading(false);
      },
    );
    return () => { generation.current += 1; };
  }, [findingId, reloadKey]);

  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
      created.current.forEach((u) => URL.revokeObjectURL(u));
      created.current = [];
    };
  }, []);

  const known = findingId != null && readFor === findingId;
  const listStatus: ImageListStatus = findingId == null || known ? 'ready' : error ? 'failed' : 'loading';

  const placeable = useMemo(() => images.filter((i) => i.in_report && i.printable), [images]);
  const byId = useMemo(() => new Map(placeable.map((i) => [i.id, i])), [placeable]);
  const listed = useMemo(() => new Set(images.map((i) => i.id)), [images]);

  const fetchOne = useCallback(async (id: number) => {
    const epoch = cacheEpoch.current;
    try {
      const url = await depsRef.current.getNoteAttachmentObjectUrl(id);
      // The page is gone, or moved to another finding: nothing will show this
      // URL and nothing would ever revoke it.
      if (unmounted.current || cacheEpoch.current !== epoch) {
        URL.revokeObjectURL(url);
        return;
      }
      created.current.push(url);
      setUrls((m) => ({ ...m, [id]: url }));
    } catch {
      if (unmounted.current || cacheEpoch.current !== epoch) return;
      setFailedIds((prev) => new Set(prev).add(id));
    }
  }, []);

  const drain = useCallback(async () => {
    if (draining.current) return;
    draining.current = true;
    try {
      while (queue.current.length > 0 && !unmounted.current) {
        const batch = queue.current;
        queue.current = [];
        // eslint-disable-next-line no-await-in-loop
        await runLimited(batch, IMAGE_FETCH_CONCURRENCY, fetchOne);
      }
    } finally {
      draining.current = false;
    }
  }, [fetchOne]);

  const request = useCallback((id: number) => {
    if (requested.current.has(id)) return;
    requested.current.add(id);
    queue.current.push(id);
    // After the callers of this tick have all asked: they go as one batch.
    void Promise.resolve().then(drain);
  }, [drain]);

  const retry = useCallback((id: number) => {
    if (!listed.has(id)) return;
    requested.current.delete(id);
    setFailedIds((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    request(id);
  }, [listed, request]);

  const ensure = useCallback((id: number) => {
    if (byId.has(id)) request(id);
  }, [byId, request]);
  const ensureListed = useCallback((id: number) => {
    if (listed.has(id)) request(id);
  }, [listed, request]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  const failed = useCallback((id: number) => failedIds.has(id), [failedIds]);

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
    images, placeable, captionMax, loading, error, listStatus, reload, urls, resolver, thumbnails,
  };
}
