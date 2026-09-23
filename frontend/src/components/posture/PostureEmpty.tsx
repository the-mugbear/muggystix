/**
 * An empty state on a Posture page (v5.262.0) — "no scoped subnets yet", "no
 * hosts yet": icon, heading, one line, the recovery action (§13).  Left-aligned
 * under the page header, not a card: the page keeps its structure.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';

import { Button } from '../ui/button';

export interface PostureEmptyProps {
  Icon: LucideIcon;
  title: string;
  children: React.ReactNode;
  action?: { to: string; label: string };
}

export const PostureEmpty: React.FC<PostureEmptyProps> = ({ Icon, title, children, action }) => (
  <div className="flex max-w-2xl items-start gap-sm border-l-4 border-border py-xs pl-md">
    <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
    <div className="min-w-0">
      <p className="text-subheading font-semibold text-foreground">{title}</p>
      <p className="mt-xxs text-metadata text-muted-foreground">{children}</p>
      {action && (
        <Button asChild size="sm" variant="outline" className="mt-sm">
          <Link to={action.to}>{action.label}</Link>
        </Button>
      )}
    </div>
  </div>
);

export default PostureEmpty;
