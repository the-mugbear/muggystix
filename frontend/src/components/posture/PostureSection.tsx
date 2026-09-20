/**
 * One section of a Posture page: a heading row over a thin rule, not a bordered
 * card (v5.254.0) — the Host inspector's density language (InspectorSection),
 * without its per-viewer collapse: these pages are read top to bottom in a
 * review, so nothing here hides.
 *
 * A card per measure made the Overview a wall of equal-weight boxes — the
 * conclusion, four stat cards, the grid, the priorities and the disposition all
 * competed. A section costs one heading row and keeps the full page width.
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

export const PostureSection: React.FC<PostureSectionProps> = ({
  title, description, actions, className, children,
}) => (
  <section className={cn('min-w-0', className)}>
    <div className="flex flex-wrap items-end justify-between gap-x-md gap-y-xxs border-b border-border pb-xs">
      <div className="min-w-0">
        <h2 className="flex items-center gap-xs text-caption font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h2>
        {description && <p className="mt-xxs max-w-3xl text-caption text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-sm text-caption">{actions}</div>}
    </div>
    <div className="pt-sm">{children}</div>
  </section>
);

export default PostureSection;
