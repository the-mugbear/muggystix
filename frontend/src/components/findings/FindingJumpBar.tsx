/**
 * The finding page's jump bar: one entry per section the page shows, in page
 * order, pinned under the app chrome.
 *
 * It is navigation, not a second explanation — a label and, where the page
 * knows one, a count.  An entry whose section renders nothing (no proposals
 * waiting, no test evidence) is left out: the bar asks the section's wrapper
 * whether it has content, so a section that loads for itself needs no wiring.
 *
 * The section in view is marked with an IntersectionObserver; the bar keeps
 * that state to itself, so reading down the page re-renders only the bar.
 */
import React, { useEffect, useRef, useState } from 'react';

import { cn } from '../../utils/cn';
import { scrollBelowChrome } from '../../utils/uiStyles';

/** Pinned FLUSH against the chrome: a full-width bar with a gap above it lets
 *  the page scroll through the gap (`stickyBelowChrome` leaves 0.5rem, which
 *  suits a small control, not a bar).  The space is the bar's own padding. */
const stickyFlushBelowChrome: React.CSSProperties = {
  top: 'calc(var(--topbar-h, 76px) + var(--secondary-nav-h, 0px))',
};

export interface JumpEntry {
  /** The id of the element that wraps the section. */
  id: string;
  label: string;
  /** A short count beside the label ("151", "2 empty"); nothing when unknown. */
  count?: string | null;
}

/** `style` for a section wrapper the bar jumps to: it lands below the app
 *  chrome AND this bar. */
export const jumpTargetStyle: React.CSSProperties = scrollBelowChrome('3.75rem');

const sameIds = (a: ReadonlyArray<string>, b: ReadonlyArray<string>) =>
  a.length === b.length && a.every((id, i) => id === b[i]);

const FindingJumpBar: React.FC<{ entries: JumpEntry[] }> = ({ entries }) => {
  const barRef = useRef<HTMLElement | null>(null);
  const ids = entries.map((e) => e.id);
  const idsKey = ids.join('|');
  // The sections that have something in them, in page order.
  const [present, setPresent] = useState<string[]>([]);
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    const wanted = idsKey ? idsKey.split('|') : [];
    const read = () => {
      const next = wanted.filter((id) => (document.getElementById(id)?.childElementCount ?? 0) > 0);
      setPresent((prev) => (sameIds(prev, next) ? prev : next));
    };
    read();
    if (typeof MutationObserver === 'undefined') return undefined;
    // Only the wrappers' own children: a section appearing or going away,
    // never every change inside one.
    const observer = new MutationObserver(read);
    wanted.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el, { childList: true });
    });
    return () => observer.disconnect();
  }, [idsKey]);

  const presentKey = present.join('|');
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined' || !presentKey) return undefined;
    const order = presentKey.split('|');
    const bar = barRef.current;
    // A section counts as "in view" in the band under the bar: from the bar's
    // lower edge to the middle of the window.
    const top = bar ? (parseFloat(getComputedStyle(bar).top) || 0) + bar.offsetHeight + 8 : 0;
    const inBand = new Set<string>();
    const observer = new IntersectionObserver((changes) => {
      changes.forEach((c) => {
        if (c.isIntersecting) inBand.add(c.target.id);
        else inBand.delete(c.target.id);
      });
      const first = order.find((id) => inBand.has(id));
      // Between two sections (a gap in the band) the last one stays marked.
      if (first) setCurrent(first);
    }, { rootMargin: `-${Math.round(top)}px 0px -50% 0px` });
    order.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [presentKey]);

  const shown = entries.filter((e) => present.includes(e.id));
  // One section is nowhere to jump to.
  if (shown.length < 2) return null;

  const jump = (id: string) => {
    // A jump, not an animation: the reader asked for the section, and a
    // smooth scroll does not finish in a background tab.
    document.getElementById(id)?.scrollIntoView?.({ block: 'start' });
    setCurrent(id);
  };

  return (
    <nav
      ref={barRef}
      aria-label="Sections of this finding"
      style={stickyFlushBelowChrome}
      className="sticky z-20 -mx-xs mb-md flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs border-b border-border bg-background px-xs pb-xxs pt-xs"
    >
      {shown.map((e) => {
        const here = current === e.id;
        return (
          <button
            key={e.id}
            type="button"
            aria-current={here ? 'location' : undefined}
            onClick={() => jump(e.id)}
            className={cn(
              'inline-flex min-w-0 max-w-full items-center gap-xs whitespace-nowrap rounded-control px-sm py-xxs text-metadata',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              here ? 'bg-accent font-medium text-foreground' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
          >
            <span className="truncate">{e.label}</span>
            {e.count != null && e.count !== '' && (
              <span className="tabular-nums text-muted-foreground">{e.count}</span>
            )}
          </button>
        );
      })}
    </nav>
  );
};

export default FindingJumpBar;
