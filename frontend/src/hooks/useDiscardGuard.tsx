/**
 * Unsaved-work protection, shared (review 2026-09-23 B-UI-7).
 *
 * The app uses <Routes>, not a data router, so a click on the global nav
 * cannot be intercepted; what can be is the page's own navigation (through
 * `confirmLeave`, and its links through `useLinkGuard`) and the browser's
 * unload (tab close, reload).  HostDetail
 * had its own copy of this; ReportDetail had a `dirty` flag and a Back button
 * that discarded the narrative without asking.
 *
 * `isDirty` is read at the moment of leaving, so a ref or a fresh closure
 * both work.
 */
import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

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

/**
 * The page's own LINKS ask too (review 2026-10-14, H10: a checklist line
 * opened a finding and the draft's pending edits were gone, with no word).
 * Put the answer on the page's container as `onClickCapture`: a plain click
 * on a link inside it to another page of the app is held, `confirmLeave`
 * asked, and the link followed only if it says yes.  One guard for every
 * link the page holds or will hold, never a handler per link — a dialog's
 * included: React events reach the container through a portal, so a link in
 * a dialog the page renders is guarded too (which is why a way out of the
 * page is a `<Link>`, never a bare `navigate()`).  A link to the same page
 * (a tab, a hash), a new tab or window, a download and another site are left
 * alone.
 */
export function useLinkGuard(confirmLeave: () => Promise<boolean>): (e: React.MouseEvent) => void {
  const navigate = useNavigate();
  return (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
    if (!link || (link.target && link.target !== '_self') || link.hasAttribute('download')) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || url.pathname === window.location.pathname) return;
    e.preventDefault();
    void confirmLeave().then((ok) => { if (ok) navigate(url.pathname + url.search + url.hash); });
  };
}
