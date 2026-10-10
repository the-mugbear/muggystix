/**
 * The one cache of attachment images (plan B1): the bytes of a note's image,
 * fetched through the authenticated attachment route and shown from an object
 * URL.  The finding page has one for the whole page (`useFindingImages`, which
 * decides WHICH ids may be asked for); a host note's card has one for its own
 * thumbnails (`useNoteThumbnails`).  Both are this hook, so there is one
 * queue, one limit and one failure state — and one owner of the URLs
 * (`lib/objectUrls`): every URL made is revoked when its image is no longer
 * wanted, when the cache moves to another record, or when its owner goes, and
 * one that arrives after that is revoked on arrival.
 *
 *   - An image is fetched once, when something asks (`request`), a few at a
 *     time (`IMAGE_FETCH_CONCURRENCY` — a finding can carry dozens of
 *     full-size screenshots).
 *   - One that could not be fetched is `failed(id)` — said, with `retry(id)`,
 *     never a spinner for ever; asking again does not hammer it.
 *
 * The request is a mutation, not a query: what comes back is an object URL,
 * which is not something to cache and read again (it is revoked).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { getNoteAttachmentObjectUrl } from '../services/api';
import { ObjectUrlClaim, useObjectUrls } from '../lib/objectUrls';
import { IMAGE_FETCH_CONCURRENCY, ImageThumbnails } from '../utils/evidenceImages';
import { runLimited } from '../utils/runLimited';
import { useProjectId } from './useProjectId';

export interface AttachmentImages {
  /** Object URLs loaded so far, by attachment id. */
  urls: Record<number, string>;
  /** The bytes of this image could not be fetched. */
  failed: (id: number) => boolean;
  /** Fetch this image, unless it was asked for already (loaded, on its way,
   *  or failed — a failure is tried again only by `retry`). */
  request: (id: number) => void;
  /** Fetch it again. */
  retry: (id: number) => void;
  /** The images that are still shown: every other one is let go (its URL
   *  revoked, its failure forgotten, its request no longer awaited). */
  keepOnly: (wanted: (id: number) => boolean) => void;
}

const NO_URLS: Record<number, string> = {};
const NO_IDS: ReadonlySet<number> = new Set();

const without = (ids: ReadonlySet<number>, drop: (id: number) => boolean): ReadonlySet<number> => {
  const next = new Set([...ids].filter((id) => !drop(id)));
  return next.size === ids.size ? ids : next;
};
const withoutUrls = (urls: Record<number, string>, drop: (id: number) => boolean): Record<number, string> => {
  const kept = Object.entries(urls).filter(([id]) => !drop(Number(id)));
  return kept.length === Object.keys(urls).length ? urls : Object.fromEntries(kept);
};

/**
 * @param scope    What the images belong to (the finding's id).  Another
 *                 scope starts empty: the earlier one's URLs are revoked and
 *                 its late answers dropped.
 * @param fetchUrl The API function, injectable for a `renderHook` test; the
 *                 barrel's is read only when a fetch is made.
 */
export function useAttachmentImages(
  scope: unknown,
  fetchUrl?: typeof getNoteAttachmentObjectUrl,
): AttachmentImages {
  const projectId = useProjectId();
  const { mutateAsync: fetchBytes } = useMutation({
    mutationFn: (id: number) => (fetchUrl ?? getNoteAttachmentObjectUrl)(projectId, id),
    gcTime: 0,
  });
  const store = useObjectUrls<number>();
  const [urls, setUrls] = useState(NO_URLS);
  const [failedIds, setFailedIds] = useState(NO_IDS);
  const requested = useRef(new Set<number>());
  const queue = useRef<Array<{ id: number; claim: ObjectUrlClaim }>>([]);
  const draining = useRef(false);

  // Leaving a scope (or the screen): its images are not the next one's.
  useEffect(() => () => {
    store.clear();
    requested.current = new Set();
    queue.current = [];
    setUrls(NO_URLS);
    setFailedIds(NO_IDS);
  }, [scope, store]);

  const fetchOne = useCallback(async ({ id, claim }: { id: number; claim: ObjectUrlClaim }) => {
    // Let go while it waited its turn: nothing would show it.
    if (!claim.isCurrent()) return;
    try {
      // A URL nobody will show (the owner has gone, another scope, let go
      // meanwhile) is revoked by the claim, here, where it arrives.
      const url = claim.accept(await fetchBytes(id));
      if (url !== null) setUrls((m) => ({ ...m, [id]: url }));
    } catch {
      if (claim.isCurrent()) setFailedIds((prev) => new Set(prev).add(id));
    }
  }, [fetchBytes]);

  const drain = useCallback(async () => {
    if (draining.current) return;
    draining.current = true;
    try {
      while (queue.current.length > 0) {
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
    if (requested.current.has(id) || store.closed) return;
    requested.current.add(id);
    queue.current.push({ id, claim: store.claim(id) });
    // After the callers of this tick have all asked: they go as one batch.
    void Promise.resolve().then(drain);
  }, [drain, store]);

  const forget = useCallback((drop: (id: number) => boolean) => {
    requested.current.forEach((id) => { if (drop(id)) requested.current.delete(id); });
    store.keepOnly((id) => !drop(id));
    setUrls((m) => withoutUrls(m, drop));
    setFailedIds((prev) => without(prev, drop));
  }, [store]);

  const retry = useCallback((id: number) => {
    forget((other) => other === id);
    request(id);
  }, [forget, request]);

  const keepOnly = useCallback((wanted: (id: number) => boolean) => forget((id) => !wanted(id)), [forget]);

  const failed = useCallback((id: number) => failedIds.has(id), [failedIds]);

  return { urls, failed, request, retry, keepOnly };
}

const idsOf = (key: string): number[] => (key ? key.split(',').map(Number) : []);

export interface NoteThumbnails {
  url: (id: number) => string | undefined;
  /** The fetch failed: the thumbnail says so instead of spinning for ever. */
  failed: (id: number) => boolean;
  retry: (id: number) => void;
}

/**
 * The thumbnails of one note's images.
 *
 * On a finding the page has a cache already (`shared` =
 * `useFindingImages().thumbnails`): an image on the finding's list is shown
 * from it — the same object URL the placed images and the editor's picker
 * use — and is not fetched a second time.  While that list is still being
 * read nothing is fetched here (it would be fetched again from the page's
 * cache a moment later).  An image the list does not carry — every image of a
 * host note, and every image when the list could not be read — is this
 * card's own: fetched here, and released with the card or when the image
 * leaves the note.
 */
export function useNoteThumbnails(ids: readonly number[], shared?: ImageThumbnails): NoteThumbnails {
  const own = useAttachmentImages(null);
  const fromShared = (id: number) => !!shared && shared.has(id);
  const idsKey = ids.join(',');
  const ownKey = shared?.listStatus === 'loading' ? '' : ids.filter((id) => !fromShared(id)).join(',');
  const ensureShared = shared?.ensure;
  const { request, keepOnly } = own;
  // Ask for what is shown — the one effect of a note's images.
  useEffect(() => {
    const shown = idsOf(idsKey);
    keepOnly((id) => shown.includes(id));
    if (ensureShared) shown.forEach((id) => ensureShared(id));   // a no-op for an id off the list
    idsOf(ownKey).forEach((id) => request(id));
  }, [idsKey, ownKey, ensureShared, request, keepOnly]);

  return {
    url: (id) => (fromShared(id) ? shared?.urls[id] : own.urls[id]),
    failed: (id) => (fromShared(id) ? !!shared?.failed(id) : own.failed(id)),
    retry: (id) => {
      if (fromShared(id)) shared?.retry(id);
      else own.retry(id);
    },
  };
}
