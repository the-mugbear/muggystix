/**
 * A finding's images as the client report sees them — one load for the page:
 * the comment thread's image rows (caption, "In report", where each is
 * placed), the report-text editor's "Insert image" picker and the preview all
 * read this list.
 *
 * The bytes of an image are fetched only when something asks for them
 * (`resolver.ensure`, the picker's thumbnails) — through the authenticated
 * attachment route, as thumbnails are — and only for an id on THIS finding's
 * list: a reference in the text never becomes a URL.
 *
 * The API is an injectable dependency so the state machine is tested with
 * `renderHook` (see `tests/hooks/useFindingImages.test.ts`).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getFindingImages, getNoteAttachmentObjectUrl } from '../services/api';
import type { FindingImage } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import type { EvidenceResolver } from '../utils/reportImages';

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
  error: string | null;
  reload: () => void;
  /** Object URLs loaded so far, by attachment id. */
  urls: Record<number, string>;
  resolver: EvidenceResolver;
}

// Built on use, not at import: a page test that mocks the `services/api`
// barrel without these two would otherwise fail on import.
const defaultDeps = (): FindingImagesDeps => ({ getFindingImages, getNoteAttachmentObjectUrl });

export function useFindingImages(
  findingId: number | null | undefined,
  injected?: FindingImagesDeps,
): FindingImagesState {
  const deps = useMemo(() => injected ?? defaultDeps(), [injected]);
  const [images, setImages] = useState<FindingImage[]>([]);
  const [captionMax, setCaptionMax] = useState(2000);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [urls, setUrls] = useState<Record<number, string>>({});
  const [reloadKey, setReloadKey] = useState(0);
  // Which finding the state is for: a response that arrives after the page
  // moved to another finding is dropped.
  const generation = useRef(0);
  const requested = useRef(new Set<number>());
  const created = useRef<string[]>([]);
  const depsRef = useRef(deps);
  depsRef.current = deps;

  // Another finding: its images are not this one's.
  useEffect(() => {
    requested.current = new Set();
    setImages([]);
    setUrls({});
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

  useEffect(() => () => { created.current.forEach((u) => URL.revokeObjectURL(u)); }, []);

  const placeable = useMemo(() => images.filter((i) => i.in_report && i.printable), [images]);
  const byId = useMemo(() => new Map(placeable.map((i) => [i.id, i])), [placeable]);

  const ensure = useCallback((id: number) => {
    if (!byId.has(id) || requested.current.has(id)) return;
    requested.current.add(id);
    const mine = generation.current;
    depsRef.current.getNoteAttachmentObjectUrl(id).then(
      (url) => {
        created.current.push(url);
        if (generation.current !== mine && !requested.current.has(id)) return;
        setUrls((m) => ({ ...m, [id]: url }));
      },
      () => { requested.current.delete(id); },   // a later render may ask again
    );
  }, [byId]);

  const resolver = useMemo<EvidenceResolver>(() => ({
    lookup: (id) => {
      const image = byId.get(id);
      return image ? { caption: image.caption ?? image.filename, src: urls[id] } : null;
    },
    ensure,
  }), [byId, urls, ensure]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  return { images, placeable, captionMax, loading, error, reload, urls, resolver };
}
