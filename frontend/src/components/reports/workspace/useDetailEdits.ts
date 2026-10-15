/**
 * The pending edits of a report's details, held by the PAGE (owner,
 * 2026-10-14; the proposal's "Editing and saving", revised after its second
 * review):
 *
 * - "Edit" opens one section and remembers the section as it was then.
 * - "Done" closes it; its changes stay pending.
 * - "Cancel" returns the section to how it was when Edit was last pressed
 *   (changes accepted earlier with Done stay pending).
 * - "Discard changes in this section" returns it to the last save.
 * - Nothing here touches another section; pending work survives switching
 *   sections and tabs, because the page owns this state.
 *
 * What an edit means is `detailModel` (`withEdit`, `toSave`).
 */
import { useCallback, useMemo, useState } from 'react';

import {
  DETAIL_SECTIONS, type DetailContext, type DetailKey, type DetailSection, type DetailValue, type Pending,
  edited, withEdit,
} from './detailModel';

type SectionId = DetailSection['id'];

export interface DetailEdits {
  ctx: DetailContext;
  /** Open sections, each with the pending values of its keys at its Edit. */
  isOpen: (id: SectionId) => boolean;
  edit: (id: SectionId) => void;
  done: (id: SectionId) => void;
  cancel: (id: SectionId) => void;
  discardSection: (id: SectionId) => void;
  /** Every pending edit dropped, every section closed. */
  discardAll: () => void;
  set: (key: DetailKey, value: DetailValue | null) => void;
  /** Drop these keys' pending edits (they were saved, or pushed up). */
  forget: (keys: ReadonlyArray<DetailKey>) => void;
  /** Sections holding a pending edit. */
  dirtySections: SectionId[];
  dirty: boolean;
}

const keysOf = (id: SectionId): DetailKey[] => DETAIL_SECTIONS.find((s) => s.id === id)?.keys ?? [];
const pick = (pending: Pending, keys: DetailKey[]): Pending =>
  Object.fromEntries(keys.filter((k) => k in pending).map((k) => [k, pending[k]])) as Pending;

export function useDetailEdits(base: Omit<DetailContext, 'pending'>): DetailEdits {
  const [pending, setPending] = useState<Pending>({});
  const [snapshots, setSnapshots] = useState<Partial<Record<SectionId, Pending>>>({});
  const ctx = useMemo<DetailContext>(() => ({ ...base, pending }), [base, pending]);

  const replaceSection = (id: SectionId, with_: Pending) => setPending((p) => {
    const next = { ...p };
    for (const k of keysOf(id)) delete next[k];
    return { ...next, ...with_ };
  });
  const close = (id: SectionId) => setSnapshots((s) => {
    const next = { ...s };
    delete next[id];
    return next;
  });

  const set = useCallback((key: DetailKey, value: DetailValue | null) => {
    setPending((p) => withEdit({ ...base, pending: p }, key, value));
  }, [base]);

  // Judged on what a save would store (third review 2026-10-15, H6/H7): a
  // blank row or a cleared followed text holds nothing unsaved.
  const dirtySections = DETAIL_SECTIONS
    .filter((s) => s.keys.some((k) => edited(ctx, k)))
    .map((s) => s.id);

  return {
    ctx,
    isOpen: (id) => id in snapshots,
    // An open section keeps the snapshot of its FIRST Edit: opening it again
    // (a checklist line focusing its field) must not move what Cancel returns
    // to (second review 2026-10-14, nitpick).
    edit: (id) => setSnapshots((s) => (id in s ? s : { ...s, [id]: pick(pending, keysOf(id)) })),
    done: close,
    cancel: (id) => {
      replaceSection(id, snapshots[id] ?? {});
      close(id);
    },
    discardSection: (id) => {
      replaceSection(id, {});
      close(id);
    },
    discardAll: () => {
      setPending({});
      setSnapshots({});
    },
    set,
    forget: (keys) => {
      setPending((p) => {
        const next = { ...p };
        for (const k of keys) delete next[k];
        return next;
      });
      // Out of every open section's snapshot too: Cancel after a push-up
      // brought the promoted value back as a pending edit (second review
      // 2026-10-14, nitpick).
      setSnapshots((s) => Object.fromEntries(Object.entries(s).map(([id, snap]) => {
        const kept = { ...snap };
        for (const k of keys) delete kept[k];
        return [id, kept];
      })) as typeof s);
    },
    dirtySections,
    dirty: dirtySections.length > 0,
  };
}
