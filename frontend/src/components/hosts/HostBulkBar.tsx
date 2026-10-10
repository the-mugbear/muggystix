/**
 * Bulk-action bar for the Hosts page (v2.71.0).
 *
 * Shown when one or more host rows are selected. Applies tags /
 * assignment / follow-status to the selection — or, via "select all
 * matching", to every host matching the current filters (resolved
 * server-side through GET /hosts/ids, so we never ship thousands of ids
 * up from the client).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Tag as TagIcon, UserPlus, Eye, X, Copy, Check, ClipboardList } from 'lucide-react';
import ProposeTestsDialog from './ProposeTestsDialog';
import { describeSelection } from '../../utils/hostSelection';
import {
  HostTagWithCount,
  FollowStatus,
  bulkTagHosts,
  bulkAssignHosts,
  bulkUnassignHosts,
  bulkFollowHosts,
  getMatchingHostIds,
  listHostTags,
} from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { useProjectRoster } from '../../hooks/useProjectMembers';
import { MEMBERS_LOAD_ERROR } from '../MembersLoadError';
import { useAuth } from '../../contexts/AuthContext';
import { useProjectRole } from '../../hooks/useProjectRole';
import { useToast } from '../../contexts/ToastContext';
import { invalidateReads } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import { copyToClipboard } from '../../utils/clipboard';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Checkbox } from '../ui/checkbox';
import { Label } from '../ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';

interface HostBulkBarProps {
  /** Host ids selected on the current page. */
  selectedIds: number[];
  /** IPs of the explicitly-checked rows (page-scoped) for "Copy IPs". */
  selectedIps: string[];
  /** Total hosts matching the active filters (for "select all"). */
  totalMatching: number;
  /** The most hosts "all matching" reaches — the server's cap, from the same
   *  list answer as `totalMatching` (`bulk_select_cap`).  null = not stated:
   *  "all matching" is then not offered (the bar could not say how many). */
  bulkCap: number | null;
  /** Filter params for the current view — feeds GET /hosts/ids. */
  queryContext: Record<string, string | boolean | number | string[] | undefined>;
  /** Clear the selection (and exit select-all-matching). */
  onClear: () => void;
  /** A bulk action went through (the rows and the filters' counts are
   *  re-read by the bar itself): the page drops the selection. */
  onApplied: () => void;
}

// Review stages a bulk action can set.  'watching' is retired (see
// FOLLOW_STATUS_OPTIONS) — hosts are reviewed, not followed.
const STATUS_OPTIONS: Array<{ value: FollowStatus; label: string }> = [
  { value: 'in_review', label: 'In review' },
  { value: 'reviewed', label: 'Reviewed' },
];

// Selections at/above this size (or any "all-matching" selection) require a
// confirmation before the bulk mutation runs.
const CONFIRM_THRESHOLD = 25;

interface PendingAction {
  summary: string;
  run: () => void;
}

/** What a bulk action does to the hosts it is given. */
type BulkAction =
  | { kind: 'tags'; action: 'add' | 'remove'; tagIds: number[]; names: string[] }
  | { kind: 'assign'; userId: number }
  | { kind: 'unassign' }
  | { kind: 'follow'; status: FollowStatus };

/** The past tense the result is reported in ("Tagged 12 hosts"). */
const bulkVerb = (action: BulkAction): string => {
  switch (action.kind) {
    case 'tags': return action.action === 'add' ? 'Tagged' : 'Untagged';
    case 'assign': return 'Assigned';
    case 'unassign': return 'Unassigned';
    default: return 'Updated';
  }
};

const NO_TAGS: HostTagWithCount[] = [];

const HostBulkBar: React.FC<HostBulkBarProps> = ({
  selectedIds,
  selectedIps,
  totalMatching,
  bulkCap,
  queryContext,
  onClear,
  onApplied,
}) => {
  const { user } = useAuth();
  // Tagging, assigning and proposing tests are a project analyst's (R32).
  // Review status is the caller's own, and copying IPs changes nothing, so a
  // viewer or auditor keeps those two.
  const { canWrite } = useProjectRole();
  const projectId = useProjectId();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [allMatching, setAllMatching] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [copiedIps, setCopiedIps] = useState(false);
  // v5.221.0 — hand the selection to the agent as a fixed list (design review
  // item 6); 5.320.0: to propose tests on those hosts (it was a test plan).
  const [planDialogOpen, setPlanDialogOpen] = useState(false);

  // Copy the explicitly-checked rows' IPs as a newline-delimited target
  // list — the core "feed these to an external tool" loop.  Page-scoped:
  // in select-all-matching mode the IPs of unfetched rows aren't on the
  // client, so this is disabled there in favour of the Tool-Ready export.
  const copyIps = async () => {
    const ok = await copyToClipboard(selectedIps.join('\n'));
    if (ok) {
      setCopiedIps(true);
      window.setTimeout(() => setCopiedIps(false), 1500);
      toast.success(`Copied ${selectedIps.length} IP${selectedIps.length === 1 ? '' : 's'}`, { autoHideMs: 2000 });
    } else {
      toast.error('Could not copy to clipboard.');
    }
  };

  // A failed read is an empty picker: a tag can still be made by name.
  const tags = useQuery({
    queryKey: ['listHostTags', projectId],
    queryFn: ({ signal }) => listHostTags(projectId, signal),
    enabled: canWrite,  // the picker this fills is not rendered otherwise
  }).data ?? NO_TAGS;
  const roster = useProjectRoster({ enabled: canWrite });
  const members = roster.members;
  const [checkedTagIds, setCheckedTagIds] = useState<Set<number>>(new Set());
  const [newTagName, setNewTagName] = useState('');

  // Leaving select-all-matching when the page selection changes keeps the
  // displayed count honest.
  useEffect(() => {
    setAllMatching(false);
  }, [selectedIds.length]);

  // The server resolves at most `bulkCap` ids — its own number, stated in the
  // list's answer beside the total (5.365.0: the bar kept a copy of it).  Above
  // it, "all matching" is not what would be acted on, so nothing here may say
  // "all": the button, the count and the confirmation all name the capped
  // number BEFORE the action (the post-hoc toast in resolveIds stays as a
  // backstop: the filters' matches can grow between the list and the click).
  const matchingIsCapped = bulkCap != null && totalMatching > bulkCap;
  const reachableMatching = bulkCap != null ? Math.min(totalMatching, bulkCap) : totalMatching;
  const capWords = (bulkCap ?? 0).toLocaleString();
  const effectiveCount = allMatching ? reachableMatching : selectedIds.length;
  const canSelectAll = bulkCap != null && !allMatching
    && totalMatching > selectedIds.length && selectedIds.length > 0;

  // "Every matching host" as ids: asked of the server when an action (or the
  // hand-off to an agent) needs them, under the filters of that moment.
  const { mutateAsync: readMatchingIds } = useMutation({
    mutationFn: () => getMatchingHostIds(projectId, queryContext),
  });
  const resolveIds = useCallback(async (): Promise<number[]> => {
    if (!allMatching) return selectedIds;
    const res = await readMatchingIds();
    if (res.capped) {
      toast.warning(`Acting on the first ${res.ids.length} of ${res.total} matches (capped).`);
    }
    return res.ids;
  }, [allMatching, selectedIds, readMatchingIds, toast]);

  // One bulk action: the selection resolved to ids, then the one request.
  // `null` when the selection turned out to hold nothing.
  const bulk = useMutation({
    mutationFn: async (action: BulkAction) => {
      // The ids are this project's, and so is the write: both requests carry
      // the project this bar was rendered in.
      const ids = await resolveIds();
      if (!ids.length) return null;
      switch (action.kind) {
        case 'tags':
          return bulkTagHosts(projectId, ids, { tag_ids: action.tagIds, names: action.names, action: action.action });
        case 'assign':
          return bulkAssignHosts(projectId, ids, action.userId);
        case 'unassign':
          return bulkUnassignHosts(projectId, ids);
        default:
          return bulkFollowHosts(projectId, ids, action.status);
      }
    },
    onSuccess: (res, action) => {
      if (!res) {
        toast.info('No hosts selected.');
        return;
      }
      toast.success(`${bulkVerb(action)} ${res.affected} host${res.affected === 1 ? '' : 's'}`, { autoHideMs: 2500 });
      if (action.kind === 'tags') {
        setCheckedTagIds(new Set());
        setNewTagName('');
      }
      // What a bulk change is read back through: the rows, the filters'
      // counts, the tag list and a host that is open in the inspector.
      void invalidateReads(queryClient, 'getHosts', 'getHostFilterData', 'listHostTags', 'getHost');
      onApplied();
    },
    onError: (err) => toast.error(formatApiError(err, `Bulk action failed.`)),
  });
  const working = bulk.isPending;

  // Bulk changes to a large set — or to *every* host matching the current
  // filters — are operationally risky in a security inventory, so gate them
  // behind a confirmation that names the action, count, and filter scope.
  const runAction = (action: BulkAction, actionLabel: string) => {
    const run = () => bulk.mutate(action);
    if (allMatching || effectiveCount > CONFIRM_THRESHOLD) {
      setPending({
        summary:
          `${actionLabel} — ${effectiveCount.toLocaleString()} host${effectiveCount === 1 ? '' : 's'}` +
          (allMatching
            ? matchingIsCapped
              ? ` — the first ${capWords} of the ${totalMatching.toLocaleString()} matching the current filters; the rest are NOT included`
              : ' matching the current filters'
            : '') +
          '.',
        run,
      });
      return;
    }
    run();
  };

  const applyTags = (action: 'add' | 'remove') =>
    runAction(
      {
        kind: 'tags',
        action,
        tagIds: Array.from(checkedTagIds),
        names: action === 'add' && newTagName.trim() ? [newTagName.trim()] : [],
      },
      action === 'add' ? 'Add tags' : 'Remove tags',
    );

  const toggleTag = (id: number) => {
    setCheckedTagIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const hasTagSelection = checkedTagIds.size > 0 || newTagName.trim().length > 0;

  return (
    // v5.290.0 — one line at a fixed height (the page reserves this slot even
    // with nothing selected, so the table never moves under the cursor).  A
    // narrow window scrolls the bar sideways instead of wrapping it taller.
    <div className="flex h-full min-w-0 flex-nowrap items-center gap-xs overflow-x-auto rounded-control border border-primary/40 bg-primary/5 px-sm py-xs">
      <Badge variant="default" className="shrink-0" aria-live="polite">
        {effectiveCount.toLocaleString()} selected
      </Badge>

      {canSelectAll && (
        <Button size="sm" variant="ghost" className="shrink-0" onClick={() => setAllMatching(true)} disabled={working}>
          {matchingIsCapped
            ? `Select the first ${capWords} of ${totalMatching.toLocaleString()} matching`
            : `Select all ${totalMatching.toLocaleString()} matching`}
        </Button>
      )}
      {allMatching && (() => {
        const note = matchingIsCapped
          ? `The first ${capWords} of ${totalMatching.toLocaleString()} matching hosts — bulk actions stop there. Narrow the filters to reach the rest.`
          : 'Every host matching the current filters, on every page.';
        return (
          <span
            className={cn('min-w-0 truncate text-caption', matchingIsCapped ? 'text-warning' : 'text-muted-foreground')}
            title={note}
          >
            {note}
          </span>
        );
      })()}
      {!allMatching && (
        <span className="shrink-0 text-caption text-muted-foreground">checked rows only</span>
      )}

      <div className="ml-auto flex shrink-0 flex-nowrap items-center gap-xs">
        {working && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}

        {/* Copy IPs — quick target-list to clipboard for external tools. */}
        <Button
          size="sm"
          variant="outline"
          onClick={copyIps}
          disabled={working || allMatching || selectedIps.length === 0}
          title={
            allMatching
              ? 'Copy works on the explicitly checked rows — use Export → Tool-Ready for all matching hosts.'
              : 'Copy the selected IPs as a newline-separated list'
          }
        >
          {copiedIps ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
          Copy IPs
        </Button>

        {canWrite && (<>
        {/* Tags */}
        <Popover>
          <PopoverTrigger asChild>
            <Button size="sm" variant="outline" disabled={working}>
              <TagIcon className="size-3.5" aria-hidden /> Tag
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-72 space-y-sm">
            <p className="text-metadata font-medium">Tags</p>
            <div className="max-h-48 space-y-xxs overflow-y-auto">
              {tags.length === 0 && (
                <p className="text-caption text-muted-foreground">No tags yet — create one below.</p>
              )}
              {tags.map((tag) => (
                <label key={tag.id} className="flex items-center gap-xs text-metadata">
                  <Checkbox
                    checked={checkedTagIds.has(tag.id)}
                    onCheckedChange={() => toggleTag(tag.id)}
                  />
                  <span className="min-w-0 flex-1 truncate">{tag.name}</span>
                  <span className="text-caption text-muted-foreground">{tag.host_count}</span>
                </label>
              ))}
            </div>
            <div className="space-y-xxs">
              <Label htmlFor="bulk-new-tag" className="text-caption">New tag</Label>
              <Input
                id="bulk-new-tag"
                value={newTagName}
                onChange={(e) => setNewTagName(e.target.value)}
                placeholder="e.g. owned"
                maxLength={60}
              />
            </div>
            <div className="flex gap-xs">
              <Button size="sm" className="flex-1" disabled={!hasTagSelection || working} onClick={() => applyTags('add')}>
                Add
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="flex-1"
                disabled={checkedTagIds.size === 0 || working}
                onClick={() => applyTags('remove')}
              >
                Remove
              </Button>
            </div>
          </PopoverContent>
        </Popover>

        {/* Assign */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline" disabled={working}>
              <UserPlus className="size-3.5" aria-hidden /> Assign
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-h-72 overflow-y-auto">
            {user && (
              <DropdownMenuItem onSelect={() => runAction({ kind: 'assign', userId: user.id }, 'Assign to me')}>
                Assign to me
              </DropdownMenuItem>
            )}
            {members
              .filter((m) => m.user_id !== user?.id)
              .map((m) => {
                const name = m.full_name || m.username || `User #${m.user_id}`;
                return (
                  <DropdownMenuItem
                    key={m.user_id}
                    onSelect={() => runAction({ kind: 'assign', userId: m.user_id }, `Assign to ${name}`)}
                  >
                    {name}
                  </DropdownMenuItem>
                );
              })}
            {roster.status === 'error' ? (
              // Stays open: the retry fills this same menu.
              <DropdownMenuItem onSelect={(e) => { e.preventDefault(); roster.retry(); }}>
                {MEMBERS_LOAD_ERROR} Retry
              </DropdownMenuItem>
            ) : roster.status === 'loading' ? (
              <DropdownMenuItem disabled>Loading members…</DropdownMenuItem>
            ) : members.length === 0 && !user && (
              <DropdownMenuItem disabled>No members</DropdownMenuItem>
            )}
            {user && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => runAction({ kind: 'unassign' }, 'Unassign me')}>
                  Unassign me
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        </>)}

        {/* Status */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline" disabled={working}>
              <Eye className="size-3.5" aria-hidden /> Review
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {STATUS_OPTIONS.map((opt) => (
              <DropdownMenuItem
                key={opt.value}
                onSelect={() => runAction({ kind: 'follow', status: opt.value }, `Set status: ${opt.label}`)}
              >
                {opt.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Propose tests — the selection becomes a fixed target list. */}
        {canWrite && (
        <Button
          size="sm"
          variant="outline"
          disabled={working || effectiveCount === 0}
          onClick={() => setPlanDialogOpen(true)}
          title="Have your agent propose tests on these hosts; they appear on each host's page"
        >
          <ClipboardList className="size-3.5" aria-hidden /> Propose tests
        </Button>
        )}

        <Button size="sm" variant="ghost" onClick={onClear} disabled={working} aria-label="Clear selection">
          <X className="size-3.5" aria-hidden /> Clear
        </Button>
      </div>

      <ProposeTestsDialog
        open={planDialogOpen}
        onOpenChange={setPlanDialogOpen}
        resolveIds={resolveIds}
        selectionSummary={describeSelection(effectiveCount, allMatching, queryContext)}
        sampleIps={allMatching ? [] : selectedIps}
      />

      <Dialog open={!!pending} onOpenChange={(v) => { if (!v) setPending(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm bulk action</DialogTitle>
            <DialogDescription>{pending?.summary}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)} disabled={working}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                const p = pending;
                setPending(null);
                p?.run();
              }}
              disabled={working}
            >
              Apply
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default HostBulkBar;
