import React, { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { getMatchingHostIds } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { queryErrorText } from '../../lib/query';
import { agentInstruction } from '../../utils/agentRuns';
import AgentTaskButton from '../agent-sessions/AgentTaskButton';
import { useCanStartAgentSession } from '../../hooks/useCanStartAgentSession';
import { useProjectId } from '../../hooks/useProjectId';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';

/**
 * "Propose tests" from the Hosts bulk bar (5.320.0; it was "Test plan",
 * v5.221.0).
 *
 * The selection becomes a FIXED host list the moment the dialog opens —
 * the checked rows, or every host matching the current filters as the server
 * resolves them (`GET /hosts/ids`) — and that list is what the operator's agent is
 * handed: a task naming exactly these host ids (AgentTaskButton). The agent
 * proposes individual tests on each host, which appear on the host's page;
 * nothing waits on approval and there is no plan to open.
 */

/** The most host ids an agent task may name: the task is pasted text, and
 *  past this it is a wall of numbers (10,000 ids ≈ 60 KB). */
export const AGENT_TASK_MAX_HOSTS = 200;

/** The Hosts filters, as `GET /hosts/ids` takes them. */
type HostIdsQuery = Parameters<typeof getMatchingHostIds>[1];

interface SelectionProps {
  /** The checked rows' ids — the list itself, unless `allMatching`. */
  selectedIds: number[];
  /** Every host matching `queryContext` instead: the server resolves the ids
   *  as the dialog opens. */
  allMatching: boolean;
  /** The Hosts filters the selection was made under. */
  queryContext: HostIdsQuery;
  /** How the selection was made, shown beside the count. */
  selectionSummary: string;
  /** IPs of the checked rows on this page, shown as a sample of the targets. */
  sampleIps: string[];
}

export interface ProposeTestsDialogProps extends SelectionProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const ProposeTestsDialog: React.FC<ProposeTestsDialogProps> = ({ open, onOpenChange, ...selection }) => {
  // Kept across openings: closing to adjust the selection does not cost the
  // reader what they wrote.
  const [what, setWhat] = useState('');
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Propose tests for the selection</DialogTitle>
          <DialogDescription>
            Your agent proposes tests on each of these hosts; they appear on the host&apos;s page. The
            selection is taken as a fixed list now — changing the Hosts filters later does not change it.
          </DialogDescription>
        </DialogHeader>
        {/* Mounted only while the dialog is open (the content is not rendered
            otherwise), so its read belongs to this opening. */}
        <ResolvedSelection {...selection} what={what} onWhatChange={setWhat} onCancel={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
};

/** The selection as the fixed list this opening hands over, and the hand-off. */
const ResolvedSelection: React.FC<SelectionProps & {
  what: string;
  onWhatChange: (what: string) => void;
  onCancel: () => void;
}> = ({ selectedIds, allMatching, queryContext, selectionSummary, sampleIps, what, onWhatChange, onCancel }) => {
  const canUseAgent = useCanStartAgentSession();
  const projectId = useProjectId();
  const toast = useToast();

  // "Every matching host" is read once per opening, as this component mounts:
  // each opening asks again — the list of the opening before is never shown
  // as this one's — and closing the dialog cancels a read still in flight
  // (the query's signal reaches the request).
  const matching = useQuery({
    queryKey: ['getMatchingHostIds', projectId, queryContext],
    queryFn: ({ signal }) => getMatchingHostIds(projectId, queryContext, signal),
    enabled: allMatching,
  });
  // The server's cap cut the list: said once per answer, as the bulk actions say it.
  const { data: matched } = matching;
  useEffect(() => {
    if (matched?.capped) {
      toast.warning(`Acting on the first ${matched.ids.length} of ${matched.total} matches (capped).`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per answer, whatever the toast object's identity
  }, [matched]);
  const resolving = allMatching && matching.isFetching;
  const ids = !allMatching ? selectedIds : resolving || matching.isError ? null : matched?.ids ?? null;
  const resolveError = !allMatching || resolving
    ? null
    : queryErrorText(matching.error, 'Could not resolve the selection.');

  const count = ids?.length ?? 0;
  const tooMany = count > AGENT_TASK_MAX_HOSTS;
  const shownIps = sampleIps.slice(0, 8);
  const moreIps = Math.max(count - shownIps.length, 0);

  return (
    <>
        <DialogBody className="flex flex-col gap-md">
          <div className="min-w-0 rounded-panel border border-border p-sm">
            <p className="text-metadata font-semibold">
              {resolving ? (
                <span className="inline-flex items-center gap-xs">
                  <Loader2 className="size-3.5 animate-spin" aria-hidden /> Resolving selection…
                </span>
              ) : (
                <>
                  {count.toLocaleString()} host{count === 1 ? '' : 's'}{' '}
                  <span className="break-words font-normal text-muted-foreground">· {selectionSummary}</span>
                </>
              )}
            </p>
            {shownIps.length > 0 && (
              <p className="mt-xxs break-words font-mono text-caption text-muted-foreground">
                {shownIps.join(', ')}
                {moreIps > 0 && ` and ${moreIps.toLocaleString()} more`}
              </p>
            )}
            {resolveError && (
              <p role="alert" className="mt-xxs break-words text-caption text-destructive">{resolveError}</p>
            )}
          </div>

          {tooMany && (
            <p role="alert" className="text-metadata text-warning">
              The task names every host id, so it takes at most {AGENT_TASK_MAX_HOSTS.toLocaleString()} hosts
              and this selection has {count.toLocaleString()}. Narrow it on the Hosts page — by subnet, site
              or severity — and hand it over in parts.
            </p>
          )}

          {!canUseAgent && (
            <p className="text-metadata text-muted-foreground">
              Starting an agent session needs the auditor role or higher on this project.
            </p>
          )}

          <div>
            <Label htmlFor="pt-what">What to test (optional)</Label>
            <Textarea
              id="pt-what"
              value={what}
              onChange={(e) => onWhatChange(e.target.value)}
              maxLength={2000}
              rows={3}
              placeholder="e.g. Confirm the critical vulnerabilities; check SMB signing and anonymous shares."
            />
            <p className="mt-xxs text-caption text-muted-foreground">
              Left empty, your agent proposes tests from what each host exposes.
            </p>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <AgentTaskButton
            variant="default"
            size="md"
            label="Hand to your agent"
            instruction={agentInstruction.proposeTests(ids ?? [], what)}
            title={`Propose tests for these ${count.toLocaleString()} hosts`}
            disabled={resolving || count === 0 || tooMany}
          />
        </DialogFooter>
    </>
  );
};

export default ProposeTestsDialog;
