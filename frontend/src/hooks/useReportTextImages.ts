/**
 * The project's own report images — the ones the report's Executive summary
 * and System description may place — as one load for a form: the editor's
 * "Insert image" picker, the
 * preview and the manager under the field (`ReportTextImages`) read this.
 *
 * They belong to the PROJECT, so its report defaults and every draft see the
 * same list (`['listReportTextImages', projectId]`).  The bytes come through
 * the attachment's own route, from the one cache of attachment images
 * (`useAttachmentImages`: a few fetches at a time, a failure said with a
 * retry, every object URL revoked by `lib/objectUrls`) — and only for an id
 * on THIS list: a reference in the text never becomes a URL.
 *
 * `markdown.upload` stores a screenshot pasted into the text (the field
 * writes its reference where it was pasted): the same route as "Upload
 * image", with no caption — the list under the field then asks for one.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { listReportTextImages, uploadReportTextImage } from '../services/api';
import type { ReportTextImage } from '../services/api';
import { invalidateReads, queryErrorText } from '../lib/query';
import type { EvidenceImages, ImageListStatus } from '../utils/evidenceImages';
import { ReportImageProblem, reportImageProblem, type MarkdownImages } from '../utils/reportImages';
import { useAttachmentImages } from './useAttachmentImages';
import { useProjectId } from './useProjectId';

export interface ReportTextImagesState {
  /** Every image of the project's report text, oldest first; empty until read. */
  images: ReportTextImage[];
  listStatus: ImageListStatus;
  /** Why the last read of the list failed. */
  error: string | null;
  reload: () => void;
  /** Object URLs loaded so far, by attachment id. */
  urls: Record<number, string>;
  /** Ask for an image's bytes (only one on the list; idempotent). */
  ensure: (id: number) => void;
  failed: (id: number) => boolean;
  retry: (id: number) => void;
  /** For `MarkdownField`: the picker's list and the preview's resolver. */
  markdown: MarkdownImages;
  /** A pasted image is uploading: the page holds Save until its reference
   *  is in the text (third review 2026-10-15, N1). */
  uploading: boolean;
}

const NO_IMAGES: ReportTextImage[] = [];

export function useReportTextImages({ enabled = true }: { enabled?: boolean } = {}): ReportTextImagesState {
  const projectId = useProjectId();
  const list = useQuery({
    queryKey: ['listReportTextImages', projectId],
    queryFn: ({ signal }) => listReportTextImages(projectId, signal),
    enabled,
  });
  const images = list.data ?? NO_IMAGES;
  const error = queryErrorText(list.error, 'Could not load the report’s images.');
  const listStatus: ImageListStatus = list.data ? 'ready' : error ? 'failed' : 'loading';
  const { refetch } = list;
  const reload = useCallback(() => { void refetch(); }, [refetch]);

  const {
    urls, failed, request, retry: fetchAgain, keepOnly,
  } = useAttachmentImages(projectId);
  const byId = useMemo(() => new Map(images.map((i) => [i.id, i])), [images]);
  const read = list.data !== undefined;
  // An image that left the list (deleted) is let go: its URL is revoked.
  useEffect(() => {
    if (read) keepOnly((id) => byId.has(id));
  }, [read, byId, keepOnly]);

  const ensure = useCallback((id: number) => {
    if (byId.has(id)) request(id);
  }, [byId, request]);
  const retry = useCallback((id: number) => {
    if (byId.has(id)) fetchAgain(id);
  }, [byId, fetchAgain]);

  const resolver = useMemo<EvidenceImages>(() => ({
    lookup: (id) => {
      const image = byId.get(id);
      return image ? { caption: image.caption ?? image.filename, src: urls[id] } : null;
    },
    ensure,
    listStatus,
    retryList: reload,
    failed,
    retry,
    source: 'report',
  }), [byId, urls, ensure, listStatus, reload, failed, retry]);

  const queryClient = useQueryClient();
  const { mutateAsync: store } = useMutation({
    mutationFn: (file: File) => uploadReportTextImage(projectId, file),
    onSuccess: () => { void invalidateReads(queryClient, 'listReportTextImages'); },
  });
  const [inFlight, setInFlight] = useState(0);
  const upload = useCallback(async (file: File) => {
    const problem = reportImageProblem(file);
    if (problem) throw new ReportImageProblem(problem);
    setInFlight((n) => n + 1);
    try {
      return (await store(file)).id;
    } finally {
      setInFlight((n) => n - 1);
    }
  }, [store]);

  const markdown = useMemo<MarkdownImages>(
    () => ({ placeable: images, urls, resolver, upload }), [images, urls, resolver, upload]);

  return {
    images, listStatus, error, reload, urls, ensure, failed, retry, markdown, uploading: inFlight > 0,
  };
}
