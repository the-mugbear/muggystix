/**
 * /test-plans/:planId/danger — destructive actions, fenced off behind
 * a dedicated tab so they don't sit on the always-visible action bar.
 *
 * Currently: Delete Plan.  Future additions (Archive, Reset all
 * entries to proposed, Force-unlock agent key, etc.) should land here
 * too so the dangerous-action surface stays in one place.
 *
 * The Delete confirmation dialog itself lives in `TestPlanLayout` —
 * this tab opens it via `openDeleteDialog` from `TestPlanContext`.
 * Keeping the dialog up at the layout level means the DELETE-typed-
 * name confirmation, the in-flight loading state, and the actual
 * deleteTestPlan call all stay co-located with the rest of the plan-
 * level mutations (reject, execute, etc.).
 */
import React from 'react';
import { Trash2 } from 'lucide-react';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { Button } from '../../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { useTestPlanContext } from './TestPlanLayout';

const DangerTab: React.FC = () => {
  const { plan, canManage, openDeleteDialog } = useTestPlanContext();

  const totalEntries = plan.entries.length;
  const dispositionedCount = plan.entries.filter((e) => e.status !== 'proposed').length;

  return (
    <div className="space-y-md">
      <Alert variant="warning">
        <AlertDescription>
          Actions on this tab are <strong>destructive</strong> and cannot be undone. They live here
          so they stay out of the way during normal triage — surface them only when you are sure.
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-xs">
            <Trash2 className="size-5 text-destructive" aria-hidden />
            <CardTitle className="text-destructive">Delete this plan</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-sm">
          <p className="text-metadata">
            Permanently delete <strong>{plan.title}</strong> and all of its entries, findings, and
            audit history. Execution sessions tied to this plan stay in the database but lose their
            link target.
          </p>
          {dispositionedCount > 0 ? (
            <Alert variant="destructive">
              <AlertDescription>
                <p className="font-semibold">
                  {dispositionedCount} of {totalEntries} entr
                  {dispositionedCount === 1 ? 'y has' : 'ies have'} already been reviewed.
                </p>
                <p className="mt-xxs">
                  Reviewed work is lost on delete. The confirmation dialog requires you to type
                  <strong> DELETE</strong> to proceed.
                </p>
              </AlertDescription>
            </Alert>
          ) : (
            <p className="text-caption text-muted-foreground">
              No entries on this plan have been reviewed yet, so nothing dispositioned will be lost.
            </p>
          )}
          <div className="flex justify-end">
            <Button
              variant="destructive"
              disabled={!canManage}
              onClick={openDeleteDialog}
            >
              <Trash2 className="size-4" aria-hidden />
              Delete Plan…
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default DangerTab;
