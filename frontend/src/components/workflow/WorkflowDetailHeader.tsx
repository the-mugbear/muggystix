import * as React from 'react';
import { ArrowLeft } from 'lucide-react';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { cn } from '../../utils/cn';

/**
 * WorkflowDetailHeader — the shared title/status/action bar for the
 * agent-workflow detail pages (recon run, execution session, test plan).
 *
 * Before this existed, each detail page hand-rolled its own header: recon
 * used a `SummarySection` card, execution used `ExecutionSessionHeader`,
 * and test plans used a bare top bar + a separate action cluster lower
 * down the page.  The Abandon control lived in three different places.
 * This component standardizes the layout so all three read identically:
 *
 *   [<- Back]  Title  [status badges]            [actions]  [Abandon]
 *              subtitle / metadata
 *              {children — stat tiles, etc.}
 *
 * The `destructiveAction` slot is pinned to the far right of the action
 * group (after `actions`), which is where Abandon always lives per the
 * standardized layout.  On narrow viewports the action group wraps below
 * the title as its own row.
 */
export interface WorkflowDetailHeaderProps {
  onBack: () => void;
  /** Accessible label for the back button.  Defaults to "Back". */
  backLabel?: string;
  title: React.ReactNode;
  /** Rendered inline immediately after the title (e.g. an Edit pencil). */
  titleAdornment?: React.ReactNode;
  /** Status / stale / mode badges rendered on the title row. */
  badges?: React.ReactNode;
  /** Metadata line(s) rendered under the title. */
  subtitle?: React.ReactNode;
  /** Non-destructive actions (Refresh, Approve, Execute, Export, …). */
  actions?: React.ReactNode;
  /** Destructive/terminal action (Abandon) — pinned to the far right. */
  destructiveAction?: React.ReactNode;
  /** Extra content inside the header card, under the title row
   *  (e.g. a stat-tile grid). */
  children?: React.ReactNode;
  className?: string;
}

export const WorkflowDetailHeader: React.FC<WorkflowDetailHeaderProps> = ({
  onBack,
  backLabel = 'Back',
  title,
  titleAdornment,
  badges,
  subtitle,
  actions,
  destructiveAction,
  children,
  className,
}) => {
  const hasActions = Boolean(actions) || Boolean(destructiveAction);
  return (
    <Card className={cn('mb-md', className)}>
      <CardContent className="flex flex-col gap-md p-md">
        <div className="flex flex-wrap items-start gap-sm">
          <Button
            variant="ghost"
            size="icon"
            onClick={onBack}
            aria-label={backLabel}
            className="shrink-0"
          >
            <ArrowLeft className="size-4" aria-hidden />
          </Button>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-xs">
              <h1 className="min-w-0 break-words text-section-title font-semibold">{title}</h1>
              {titleAdornment}
              {badges}
            </div>
            {subtitle && (
              <div className="mt-xxs text-caption text-muted-foreground">{subtitle}</div>
            )}
          </div>

          {hasActions && (
            <div className="flex flex-wrap items-center gap-xs">
              {actions}
              {destructiveAction}
            </div>
          )}
        </div>

        {children}
      </CardContent>
    </Card>
  );
};

export default WorkflowDetailHeader;
