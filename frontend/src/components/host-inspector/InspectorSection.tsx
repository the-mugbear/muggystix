/**
 * One section of the Host inspector: a heading row over a thin divider, not a
 * bordered card (v5.240.0).
 *
 * The inspector used to give every data source its own Card — border, shadow,
 * a page-sized title and two layers of padding — so a host with two ports and
 * two observations needed three screens, most of it chrome. A section costs
 * one heading row, keeps the full sheet width for its content, and collapses.
 *
 * Collapse is a per-viewer convenience kept in localStorage by section id (it
 * is about how this analyst reads every host, not about one host). A jump link
 * (`openInspectorSection`) re-opens its target first, so a collapsed section
 * never makes "12 notes · add" look like a dead link.
 */
import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

import { cn } from '../../utils/cn';

const STORAGE_KEY = 'bluestick.inspector.collapsed';
const OPEN_EVENT = 'bluestick:inspector-open-section';

const readCollapsed = (): string[] => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
};

const writeCollapsed = (id: string, collapsed: boolean) => {
  try {
    const next = readCollapsed().filter((v) => v !== id);
    if (collapsed) next.push(id);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* private window / blocked storage — the section still toggles. */
  }
};

/** Re-open a section before scrolling to it. */
export const openInspectorSection = (id: string) => {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: id }));
};

export interface InspectorSectionProps {
  /** DOM id — the jump-link target and the collapse-memory key. */
  id: string;
  title: React.ReactNode;
  icon?: React.ReactNode;
  /** Quiet count beside the title (rows, notes, issues). */
  count?: number | null;
  /** Right-aligned controls; stay visible while collapsed. */
  actions?: React.ReactNode;
  /** Native tooltip on the title. */
  titleHint?: string;
  titleClassName?: string;
  className?: string;
  children: React.ReactNode;
}

export const InspectorSection: React.FC<InspectorSectionProps> = ({
  id, title, icon, count, actions, titleHint, titleClassName, className, children,
}) => {
  const [open, setOpen] = useState(() => !readCollapsed().includes(id));

  useEffect(() => {
    const onOpen = (event: Event) => {
      if ((event as CustomEvent<string>).detail === id) setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, [id]);

  const toggle = () => {
    setOpen((prev) => {
      writeCollapsed(id, prev);
      return !prev;
    });
  };

  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <section id={id} className={cn('scroll-mt-16 border-t border-border pt-sm', className)}>
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        <h3 className="min-w-0">
          <button
            type="button"
            onClick={toggle}
            aria-expanded={open}
            aria-controls={`${id}-body`}
            title={titleHint}
            className="inline-flex min-w-0 max-w-full items-center gap-xs rounded text-left text-subheading font-semibold text-foreground hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Chevron className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            {icon}
            <span className={cn('min-w-0 truncate', titleClassName)}>{title}</span>
            {count != null && (
              <span className="shrink-0 text-caption font-normal text-muted-foreground">{count}</span>
            )}
          </button>
        </h3>
        {actions && <div className="ml-auto flex min-w-0 flex-wrap items-center gap-xs">{actions}</div>}
      </div>
      {open && (
        <div id={`${id}-body`} className="pt-sm">
          {children}
        </div>
      )}
    </section>
  );
};

export default InspectorSection;
