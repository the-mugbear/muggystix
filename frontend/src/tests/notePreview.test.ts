import { describe, it, expect } from 'vitest';

import { previewThreads, rootNoteId, threadActivity } from '../utils/notePreview';
import type { Annotation } from '../services/api';

const note = (id: number, over: Partial<Annotation> = {}): Annotation => ({
  id, body: `note ${id}`, status: 'open', parent_id: null, pinned: false,
  created_at: `2026-09-${String(id).padStart(2, '0')}T00:00:00Z`, updated_at: null,
  ...over,
} as unknown as Annotation);

// Code review 2026-09-19, findings 19 + D1: the preview was "the three newest
// roots by creation time", which hid a linked note, a pinned thread, and an old
// thread someone replied to today.
describe('notePreview', () => {
  const roots = [1, 2, 3, 4, 5].map((id) => note(id));

  it('finds the root a reply — or a reply to a reply — belongs to', () => {
    const notes = [note(1), note(10, { parent_id: 1 }), note(11, { parent_id: 10 })];
    expect(rootNoteId(11, notes)).toBe(1);
    expect(rootNoteId(1, notes)).toBe(1);
    expect(rootNoteId(99, notes)).toBeNull();
    // An orphaned reply stands as its own root rather than vanishing.
    expect(rootNoteId(20, [note(20, { parent_id: 404 })])).toBe(20);
  });

  it('survives a parent cycle', () => {
    const notes = [note(1, { parent_id: 2 }), note(2, { parent_id: 1 })];
    expect(rootNoteId(1, notes)).not.toBeUndefined();
  });

  it('keeps the root a #note- link points into, however old', () => {
    const p = previewThreads(roots, {}, { limit: 3, keepIds: [1] });
    expect(p.visible.map((n) => n.id)).toEqual([1, 4, 5]);
    expect(p.hidden).toBe(2);
  });

  it('never hides a pinned thread — five pinned threads show five', () => {
    const pinned = roots.map((r) => ({ ...r, pinned: true }));
    expect(previewThreads(pinned, {}, { limit: 3 }).visible).toHaveLength(5);
    const one = previewThreads([note(1, { pinned: true }), ...roots.slice(1)], {}, { limit: 3 });
    expect(one.visible.map((n) => n.id)).toEqual([1, 4, 5]);
  });

  it('ranks by latest ACTIVITY: a reply today keeps an old thread up', () => {
    const replies = { 1: [note(50, { parent_id: 1, created_at: '2026-09-30T00:00:00Z' })] };
    expect(threadActivity(roots[0], replies)).toBe(new Date('2026-09-30T00:00:00Z').getTime());
    const p = previewThreads(roots, replies, { limit: 3 });
    expect(p.visible.map((n) => n.id)).toEqual([1, 4, 5]);
  });

  it('preserves the API order — it decides membership, not sequence', () => {
    const reversed = [...roots].reverse();
    expect(previewThreads(reversed, {}, { limit: 3 }).visible.map((n) => n.id)).toEqual([5, 4, 3]);
  });

  it('says how much hidden work is still open', () => {
    const mixed = [note(1), note(2, { status: 'resolved' as never }), note(3), note(4), note(5)];
    const p = previewThreads(mixed, {}, { limit: 3 });
    expect(p.hidden).toBe(2);
    expect(p.hiddenOpen).toBe(1);
  });

  it('shows everything when asked, and nothing to hide under the limit', () => {
    expect(previewThreads(roots, {}, { limit: 3, showAll: true })).toMatchObject({ hidden: 0 });
    expect(previewThreads(roots.slice(0, 2), {}, { limit: 3 })).toMatchObject({ hidden: 0, hiddenOpen: 0 });
  });
});
