/**
 * One section of a Posture page: a heading row over a thin rule, not a bordered
 * card (v5.254.0) — the Host inspector's density language (InspectorSection),
 * without its per-viewer collapse: these pages are read top to bottom in a
 * review, so nothing here hides.
 *
 * A card per measure made the Overview a wall of equal-weight boxes — the
 * conclusion, four stat cards, the grid, the priorities and the disposition all
 * competed. A section costs one heading row and keeps the full page width.
 *
 * v5.269.0 — the heading carries the break between sections: a short accent
 * bar before a sentence-case title in the foreground colour (was small grey
 * capitals, which let long pages run together), and more room above it.
 */
import React from 'react';

import { cn } from '../../utils/cn';

export interface PostureSectionProps {
  title: React.ReactNode;
  /** One quiet line under the heading: what this section covers. */
  description?: React.ReactNode;
  /** Right-aligned links / controls on the heading row. */
  actions?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

/** A quiet count or summary beside a section title ("Worth a look  8"). */
export const SectionCount: React.FC<{ className?: string; children: React.ReactNode }> = ({ className, children }) => (
  <span className={cn('text-metadata font-normal tabular-nums text-muted-foreground', className)}>{children}</span>
);

export const PostureSection: React.FC<PostureSectionProps> = ({
  title, description, actions, className, children,
}) => (
  <section className={cn('min-w-0 pt-xs', className)}>
    <div className="flex flex-wrap items-end justify-between gap-x-md gap-y-xxs border-b border-border pb-xs">
      <div className="min-w-0">
        <h2 className="flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs text-subheading font-semibold text-foreground">
          <span aria-hidden className="h-4 w-1 shrink-0 rounded-full bg-primary" />
          {title}
        </h2>
        {description && <p className="mt-xxs max-w-3xl pl-sm text-caption text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-sm text-caption">{actions}</div>}
    </div>
    <div className="pt-sm">{children}</div>
  </section>
);

export default PostureSection;
