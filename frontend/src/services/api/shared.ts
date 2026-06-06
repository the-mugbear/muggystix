/**
 * Shared cross-domain primitive types.
 *
 * These string-literal unions are referenced by more than one domain
 * module (hosts, dashboard, …).  They live here — not in any one domain
 * file — so the domain modules can import them without importing each
 * other (or the barrel), which would create a cycle.
 */

/** Host review/follow state. */
export type FollowStatus = 'watching' | 'in_review' | 'reviewed';

/** Lifecycle state of a host note thread. */
export type NoteStatus = 'open' | 'in_progress' | 'resolved';

/** Thread-level note kinds (P3) — set on the thread's root note. */
export type NoteType = 'observation' | 'finding' | 'question' | 'decision' | 'action' | 'handoff';

/**
 * Standard server pagination envelope — mirrors the backend's
 * ``schemas.pagination.Paginated[T]``.  `total` is the unpaginated count, so
 * a UI can report the true size and flag truncation rather than presenting a
 * page length as the total.  Prefer this for new paginated endpoints instead
 * of another bespoke `{items,total,...}` shape.
 */
export interface Paginated<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}
