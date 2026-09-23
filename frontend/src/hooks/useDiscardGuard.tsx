/**
 * Unsaved-work protection, shared (review 2026-09-23 B-UI-7).
 *
 * The app uses <Routes>, not a data router, so a click on the global nav
 * cannot be intercepted; what can be is the page's own navigation (through
 * `confirmLeave`) and the browser's unload (tab close, reload).  HostDetail
 * had its own copy of this; ReportDetail had a `dirty` flag and a Back button
 * that discarded the narrative without asking.
 *
 * `isDirty` is read at the moment of leaving, so a ref or a fresh closure
 * both work.
 */
import React, { useEffect, useRef } from 'react';

import { useConfirm } from './useConfirm';

export function useDiscardGuard(
  isDirty: () => boolean,
  body: string,
): { confirmLeave: () => Promise<boolean>; confirmEl: React.ReactElement | null } {
  const [confirmEl, confirm] = useConfirm();
  const dirtyRef = useRef(isDirty);
  dirtyRef.current = isDirty;

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!dirtyRef.current()) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, []);

  const confirmLeave = async (): Promise<boolean> => {
    if (!dirtyRef.current()) return true;
    return confirm({ title: 'Discard unsaved work?', body, severity: 'warning', confirmLabel: 'Discard' });
  };

  return { confirmLeave, confirmEl };
}
