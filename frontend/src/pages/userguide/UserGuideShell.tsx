/**
 * Shared scaffolding for the multi-page User Guide.
 *
 * The guide used to be one ~550-line accordion on a single route.  It is
 * now five focused pages under /reference/user-guide/*, each rendering
 * <UserGuideShell> (breadcrumb + title + section tab-strip) around a
 * <GuidePage> (the page's own collapsible sections).  This module owns
 * the chrome + the prose primitives so the five page files stay pure
 * content.
 */
import React, { useMemo, useState } from 'react';
import { Link as RouterLink, NavLink } from 'react-router-dom';
import { ChevronsDownUp, ChevronsUpDown, ChevronRight } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../../components/ui/accordion';
import { Button } from '../../components/ui/button';
import { cn } from '../../utils/cn';

// ---------------------------------------------------------------------------
// Section tab strip — the five guide pages.  Order = reading order.
// ---------------------------------------------------------------------------

export const GUIDE_TABS: { label: string; path: string }[] = [
  { label: 'Getting Started', path: '/reference/user-guide' },
  { label: 'Working with Data', path: '/reference/user-guide/data' },
  { label: 'Triage & Analysis', path: '/reference/user-guide/triage' },
  { label: 'Agentic Workflows', path: '/reference/user-guide/agents' },
  { label: 'Administration', path: '/reference/user-guide/admin' },
];

// ---------------------------------------------------------------------------
// Prose primitives — shared so typography stays consistent across pages.
// ---------------------------------------------------------------------------

export const Subhead: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <h3 className="mb-xs mt-md text-subheading font-semibold text-foreground">{children}</h3>
);

export const Para: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="mb-sm text-body leading-relaxed text-foreground">{children}</p>
);

export const OrderedList: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <ol className="ml-lg list-decimal space-y-xxs text-body text-foreground marker:text-muted-foreground">
    {children}
  </ol>
);

export const UnorderedList: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <ul className="ml-lg list-disc space-y-xxs text-body text-foreground marker:text-muted-foreground">
    {children}
  </ul>
);

/** Inline monospace token — used constantly for fields, commands, paths. */
export const Mono: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <code className="font-mono text-caption">{children}</code>
);

// ---------------------------------------------------------------------------
// Section model + GuidePage
// ---------------------------------------------------------------------------

export interface GuideSection {
  /** Stable id used as the accordion value + deep-link anchor. */
  id: string;
  title: string;
  Icon: LucideIcon;
  /** One-line scannable description shown under the title when collapsed. */
  summary: string;
  content: React.ReactNode;
}

/**
 * Renders a page's collapsible sections with an expand/collapse-all control.
 * Each trigger leads with an icon + a muted one-line summary so the closed
 * accordion reads like a table of contents, not a wall of titles.
 */
export const GuidePage: React.FC<{
  intro?: React.ReactNode;
  sections: GuideSection[];
  /** Section ids open on first render (default: all). */
  initiallyOpen?: string[];
}> = ({ intro, sections, initiallyOpen }) => {
  const allIds = useMemo(() => sections.map((s) => s.id), [sections]);
  const [open, setOpen] = useState<string[]>(initiallyOpen ?? allIds);
  const allExpanded = open.length === sections.length;

  return (
    <div>
      <div className="mb-md flex flex-col gap-sm sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-3xl text-body leading-relaxed text-muted-foreground">{intro}</div>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => setOpen(allExpanded ? [] : allIds)}
        >
          {allExpanded ? (
            <>
              <ChevronsDownUp className="size-4" aria-hidden /> Collapse all
            </>
          ) : (
            <>
              <ChevronsUpDown className="size-4" aria-hidden /> Expand all
            </>
          )}
        </Button>
      </div>

      <Accordion
        type="multiple"
        value={open}
        onValueChange={setOpen}
        className="rounded-panel border border-border bg-card"
      >
        {sections.map((section) => {
          const Icon = section.Icon;
          return (
            <AccordionItem
              key={section.id}
              value={section.id}
              id={section.id}
              className="px-md last:border-b-0"
            >
              <AccordionTrigger>
                <span className="flex min-w-0 items-start gap-sm">
                  <Icon className="mt-xxs size-5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="flex min-w-0 flex-col">
                    <span className="text-subheading font-semibold text-foreground">
                      {section.title}
                    </span>
                    <span className="text-metadata font-normal text-muted-foreground">
                      {section.summary}
                    </span>
                  </span>
                </span>
              </AccordionTrigger>
              <AccordionContent>{section.content}</AccordionContent>
            </AccordionItem>
          );
        })}
      </Accordion>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page shell — breadcrumb, title, section tab strip
// ---------------------------------------------------------------------------

export const UserGuideShell: React.FC<{
  /** Path of the active tab (one of GUIDE_TABS[].path). */
  activePath: string;
  children: React.ReactNode;
}> = ({ activePath, children }) => {
  const activeTab = GUIDE_TABS.find((t) => t.path === activePath);

  return (
    <div className="p-md md:p-lg">
      <nav
        className="mb-sm flex items-center gap-xs text-metadata text-muted-foreground"
        aria-label="Breadcrumb"
      >
        <RouterLink
          to="/reference"
          className="rounded-control hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Reference
        </RouterLink>
        <ChevronRight className="size-3" aria-hidden />
        <RouterLink
          to="/reference/user-guide"
          className="rounded-control hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          User Guide
        </RouterLink>
        {activeTab && activeTab.path !== '/reference/user-guide' && (
          <>
            <ChevronRight className="size-3" aria-hidden />
            <span className="text-foreground" aria-current="page">
              {activeTab.label}
            </span>
          </>
        )}
      </nav>

      <div className="mb-md">
        <h1 className="text-page-title">User Guide</h1>
        <p className="mt-xxs text-metadata text-muted-foreground">
          How BlueStick works, organised by what you're trying to do. Pick a section below.
        </p>
      </div>

      {/* Section tab strip — mirrors the app's hub secondary-nav pattern. */}
      <div
        className="mb-lg flex flex-wrap gap-xxs border-b border-border"
        role="tablist"
        aria-label="User guide sections"
      >
        {GUIDE_TABS.map((tab) => (
          <NavLink
            key={tab.path}
            to={tab.path}
            // The landing tab must not stay active on child routes, so only it
            // gets `end`; the rest match their own path exactly anyway.
            end={tab.path === '/reference/user-guide'}
            className={({ isActive }) =>
              cn(
                '-mb-px rounded-t-control border-b-2 px-md py-sm text-metadata font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                isActive
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </div>

      {children}
    </div>
  );
};
