/**
 * What the page knows about a finding's images at a given moment — the
 * client-free half of `hooks/useFindingImages` (review 2026-10-01 S4 / M6),
 * read by `SafeMarkdown`, the editor's picker and the comment thread's
 * thumbnails.
 *
 * "This id is not one of the finding's images" is only true once the list
 * has been READ.  Before that, and when the read failed, the honest answers
 * are "not known yet" and "could not be loaded" — a placed image used to read
 * "Image not available: evidence:N is not one of this finding's images"
 * while the list was on its way, and for good when the request failed.
 */
import type { EvidenceResolver } from './reportImages';

/** The finding's image list: being read, read, or the read failed. */
export type ImageListStatus = 'loading' | 'ready' | 'failed';

/** An `EvidenceResolver` that also says what it does not know.  Every extra
 *  member is optional: a plain resolver is a list that is known. */
export interface EvidenceImages extends EvidenceResolver {
  /** Absent means `ready`. */
  listStatus?: ImageListStatus;
  /** Read the list again (after `failed`). */
  retryList?: () => void;
  /** The bytes of this image could not be fetched. */
  failed?: (id: number) => boolean;
  /** Fetch them again. */
  retry?: (id: number) => void;
}

/** The comment thread's thumbnails, from the same cache as the placed images
 *  and the picker — one fetch per image for the page. */
export interface ImageThumbnails {
  listStatus: ImageListStatus;
  /** The id is on the finding's list (so the cache serves it). */
  has: (id: number) => boolean;
  urls: Record<number, string>;
  ensure: (id: number) => void;
  failed: (id: number) => boolean;
  retry: (id: number) => void;
}

/** Image fetches in flight at once.  A finding can carry dozens of full-size
 *  screenshots; the picker used to ask for all of them together. */
export const IMAGE_FETCH_CONCURRENCY = 4;
