/**
 * HubLanding — shared shape for the four secondary-IA hubs added in
 * beta.2 (Inventory / Workflows / Collaboration / Settings).
 *
 * Each hub destination renders a brief framing + a grid of large
 * link cards.  This is intentionally minimal — the operator's
 * "real" landing is one of the sub-pages; this surface exists so
 * the URL `/inventory` (clicked from the sidebar) doesn't redirect
 * silently and so users can scan what's under the hub before
 * committing to a sub-route.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { roleForPath } from '../../config/navigation';
import { cn } from '../../utils/cn';
import { Card, CardContent } from '../../components/ui/card';

export interface HubLink {
  label: string;
  path: string;
  description: string;
  Icon: React.FC<{ className?: string }>;
  // No requiredRole here — the role gate is sourced from the navigation
  // manifest via roleForPath(path) so it has one source of truth (CR5-R1).
}

export interface HubSection {
  /** Section heading, e.g. "Your account". */
  label: string;
  /** One-line framing under the heading (optional). */
  description?: string;
  links: HubLink[];
}

export interface HubLandingProps {
  /** Page title (h1). */
  title: string;
  /** One-sentence framing under the title. */
  subtitle: string;
  /**
   * Either a flat link list (legacy, Inventory / Workflows /
   * Collaboration) or a grouped sections array (Settings).  Passing
   * `sections` opts into the grouped layout: each section gets its
   * own heading + card grid.  Sections with no visible links (all
   * permission-hidden) are dropped.
   */
  links?: HubLink[];
  sections?: HubSection[];
}

const renderCard = (link: HubLink) => (
  <Link
    key={link.path}
    to={link.path}
    className={cn(
      'group rounded-panel border border-border bg-card p-md shadow-raised transition-colors',
      'hover:border-primary/40 hover:bg-accent/40',
      'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
    )}
  >
    <div className="flex items-start gap-sm">
      <div className="rounded-control bg-accent/60 p-xs text-primary">
        <link.Icon className="size-5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-xs">
          <h2 className="text-subheading font-semibold text-foreground">{link.label}</h2>
          <ChevronRight
            className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5"
            aria-hidden
          />
        </div>
        <p className="mt-xxs text-metadata text-muted-foreground">{link.description}</p>
      </div>
    </div>
  </Link>
);

const HubLanding: React.FC<HubLandingProps> = ({ title, subtitle, links, sections }) => {
  const { hasPermission } = useAuth();

  const visibleSections: HubSection[] = sections
    ? sections
        .map((s) => ({ ...s, links: s.links.filter((l) => hasPermission(roleForPath(l.path))) }))
        .filter((s) => s.links.length > 0)
    : [];
  const visibleFlatLinks = (links ?? []).filter((l) => hasPermission(roleForPath(l.path)));

  // FRX·M5 (auto-redirect to sole visible child) was REMOVED — the
  // useEffect was a navigation loop trap whose downstream symptom
  // ("/inventory blank on Firefox even though the page should render
  // 3 cards") could not be fully pinned. The user-facing cost of
  // skipping the redirect is one extra click for single-child hubs;
  // the user-facing cost of the bug was every hub page rendering
  // blank. The right trade is obvious.
  const everythingHidden =
    (sections ? visibleSections.length === 0 : visibleFlatLinks.length === 0);

  return (
    <div className="space-y-md">
      <div>
        {/* Audit FRX·M6: orient the operator inside the IA — every
            hub is reachable from Operations, so the crumb anchors
            the current hub against its parent. */}
        <nav aria-label="Breadcrumb" className="mb-xxs">
          <ol className="flex items-center gap-xs text-caption text-muted-foreground">
            <li>
              <Link to="/operations" className="hover:text-foreground hover:underline">
                Operations
              </Link>
            </li>
            <li aria-hidden>›</li>
            <li className="text-foreground">{title}</li>
          </ol>
        </nav>
        <h1 className="text-page-title">{title}</h1>
        <p className="mt-xxs text-metadata text-muted-foreground">{subtitle}</p>
      </div>

      {everythingHidden ? (
        <Card>
          <CardContent className="py-xl text-center text-metadata text-muted-foreground">
            No sections in this hub are available for your role.
          </CardContent>
        </Card>
      ) : sections ? (
        // Grouped layout — section heading + per-section card grid.
        // Sections render in declaration order; permission-hidden ones
        // were already dropped above.
        <div className="space-y-lg">
          {visibleSections.map((section) => (
            <section key={section.label} aria-labelledby={`hub-section-${section.label}`}>
              <div className="mb-sm">
                <h2
                  id={`hub-section-${section.label}`}
                  className="text-subheading font-semibold text-foreground"
                >
                  {section.label}
                </h2>
                {section.description && (
                  <p className="mt-xxs text-metadata text-muted-foreground">
                    {section.description}
                  </p>
                )}
              </div>
              <div className="grid gap-md sm:grid-cols-2 lg:grid-cols-3">
                {section.links.map(renderCard)}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="grid gap-md sm:grid-cols-2 lg:grid-cols-3">
          {visibleFlatLinks.map(renderCard)}
        </div>
      )}
    </div>
  );
};


export default HubLanding;
