/**
 * Which note threads the Host inspector shows before "Show earlier threads".
 *
 * v5.241.0 capped the thread at its three NEWEST roots, by creation time. Two
 * things that cap broke (code review 2026-09-19, findings 19 and D1):
 *
 *  - a `#note-<id>` link (My work, the activity feed, a finding's evidence) to a
 *    note in a hidden thread found no element to scroll to — the evidence the
 *    link was for was not on the page;
 *  - creation time alone hid a PINNED thread, and an old thread someone replied
 *    to today.
 *
 * So: some threads are always visible (pinned, the one being replied to, the
 * one a link points into), and the rest compete on their latest ACTIVITY — the
 * newest of the root and its replies — not on when the root was written.
 * The API's order is preserved; this only decides membership.
 *
 * Pure: no React, no DOM.
 */
import type { Annotation } from '../services/api';

const stamp = (value?: string | null): number => {
  if (!value) return 0;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/** The root thread a note belongs to (itself when it is a root); null when the
 *  note is not in this host's notes. Cycle-safe. */
export function rootNoteId(noteId: number, notes: Annotation[]): number | null {
  const byId = new Map(notes.map((n) => [n.id, n]));
  let current = byId.get(noteId);
  const seen = new Set<number>();
  while (current && current.parent_id != null && !seen.has(current.id)) {
    seen.add(current.id);
    const parent = byId.get(current.parent_id);
    if (!parent) break; // orphaned reply: treat the reply as its own root
    current = parent;
  }
  return current ? current.id : null;
}

/** Newest moment anything happened in a thread. */
export function threadActivity(root: Annotation, repliesByParent: Record<number, Annotation[]>): number {
  let latest = Math.max(stamp(root.created_at), stamp(root.updated_at));
  const walk = (id: number, depth: number) => {
    if (depth > 50) return;
    for (const reply of repliesByParent[id] ?? []) {
      latest = Math.max(latest, stamp(reply.created_at), stamp(reply.updated_at));
      walk(reply.id, depth + 1);
    }
  };
  walk(root.id, 0);
  return latest;
}

export interface NotePreview {
  visible: Annotation[];
  /** Roots behind "Show earlier threads". */
  hidden: number;
  /** …of which not resolved — hidden open work must not be silent. */
  hiddenOpen: number;
}

export function previewThreads(
  topLevel: Annotation[],
  repliesByParent: Record<number, Annotation[]>,
  options: { limit: number; keepIds?: Array<number | null | undefined>; showAll?: boolean },
): NotePreview {
  if (options.showAll) return { visible: topLevel, hidden: 0, hiddenOpen: 0 };

  const keep = new Set<number>();
  for (const id of options.keepIds ?? []) if (id != null) keep.add(id);
  for (const root of topLevel) if (root.pinned) keep.add(root.id);

  // The always-visible threads count against the limit but are never dropped
  // for it: five pinned threads show five.
  const slots = Math.max(0, options.limit - topLevel.filter((r) => keep.has(r.id)).length);
  const byActivity = topLevel
    .filter((r) => !keep.has(r.id))
    .map((r) => ({ id: r.id, at: threadActivity(r, repliesByParent) }))
    .sort((a, b) => b.at - a.at || b.id - a.id)
    .slice(0, slots);
  for (const r of byActivity) keep.add(r.id);

  const visible = topLevel.filter((r) => keep.has(r.id));
  const hiddenRoots = topLevel.filter((r) => !keep.has(r.id));
  return {
    visible,
    hidden: hiddenRoots.length,
    hiddenOpen: hiddenRoots.filter((r) => r.status !== 'resolved').length,
  };
}
