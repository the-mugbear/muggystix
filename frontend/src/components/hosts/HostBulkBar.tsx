/**
 * Bulk-action bar for the Hosts page (v2.71.0).
 *
 * Shown when one or more host rows are selected. Applies tags /
 * assignment / follow-status to the selection — or, via "select all
 * matching", to every host matching the current filters (resolved
 * server-side through GET /hosts/ids, so we never ship thousands of ids
 * up from the client).
 */
import React, { useEffect, useState } from 'react';
import { Loader2, Tag as TagIcon, UserPlus, Eye, X } from 'lucide-react';
import {
  HostTagWithCount,
  ProjectMember,
  FollowStatus,
  bulkTagHosts,
  bulkAssignHosts,
  bulkFollowHosts,
  getMatchingHostIds,
  listHostTags,
  listProjectMembers,
} from '../../services/api';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
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
  /** Total hosts matching the active filters (for "select all"). */
  totalMatching: number;
  /** Filter params for the current view — feeds GET /hosts/ids. */
  queryContext: Record<string, string | boolean | number | undefined>;
  /** Clear the selection (and exit select-all-matching). */
  onClear: () => void;
  /** Re-fetch hosts + filter data after a successful mutation. */
  onApplied: () => void;
}

const STATUS_OPTIONS: Array<{ value: FollowStatus; label: string }> = [
  { value: 'watching', label: 'Watching' },
  { value: 'in_review', label: 'In review' },
  { value: 'reviewed', label: 'Reviewed' },
];

// Selections at/above this size (or any "all-matching" selection) require a
// confirmation before the bulk mutation runs.
const CONFIRM_THRESHOLD = 25;

interface PendingAction {
  summary: string;
  run: () => Promise<void>;
}

const HostBulkBar: React.FC<HostBulkBarProps> = ({
  selectedIds,
  totalMatching,
  queryContext,
  onClear,
  onApplied,
}) => {
  const { user } = useAuth();
  const toast = useToast();
  const [allMatching, setAllMatching] = useState(false);
  const [working, setWorking] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);

  const [tags, setTags] = useState<HostTagWithCount[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [checkedTagIds, setCheckedTagIds] = useState<Set<number>>(new Set());
  const [newTagName, setNewTagName] = useState('');

  useEffect(() => {
    listHostTags().then(setTags).catch(() => setTags([]));
    listProjectMembers().then(setMembers).catch(() => setMembers([]));
  }, []);

  // Leaving select-all-matching when the page selection changes keeps the
  // displayed count honest.
  useEffect(() => {
    setAllMatching(false);
  }, [selectedIds.length]);

  const effectiveCount = allMatching ? totalMatching : selectedIds.length;
  const canSelectAll = !allMatching && totalMatching > selectedIds.length && selectedIds.length > 0;

  const resolveIds = async (): Promise<number[]> => {
    if (!allMatching) return selectedIds;
    const res = await getMatchingHostIds(queryContext);
    if (res.capped) {
      toast.warning(`Acting on the first ${res.ids.length} of ${res.total} matches (capped).`);
    }
    return res.ids;
  };

  const execute = async (
    fn: (ids: number[]) => Promise<{ affected: number }>,
    verb: string,
    after?: () => void,
  ) => {
    setWorking(true);
    try {
      const ids = await resolveIds();
      if (!ids.length) {
        toast.info('No hosts selected.');
        return;
      }
      const res = await fn(ids);
      toast.success(`${verb} ${res.affected} host${res.affected === 1 ? '' : 's'}`, { autoHideMs: 2500 });
      after?.();
      onApplied();
    } catch (err) {
      toast.error(formatApiError(err, `Bulk action failed.`));
    } finally {
      setWorking(false);
    }
  };

  // Bulk changes to a large set — or to *every* host matching the current
  // filters — are operationally risky in a security inventory, so gate them
  // behind a confirmation that names the action, count, and filter scope.
  const runAction = (
    fn: (ids: number[]) => Promise<{ affected: number }>,
    verb: string,
    actionLabel: string,
    after?: () => void,
  ) => {
    const run = () => execute(fn, verb, after);
    if (allMatching || effectiveCount > CONFIRM_THRESHOLD) {
      setPending({
        summary:
          `${actionLabel} — ${effectiveCount.toLocaleString()} host${effectiveCount === 1 ? '' : 's'}` +
          (allMatching ? ' matching the current filters' : '') +
          '.',
        run,
      });
      return;
    }
    void run();
  };

  const applyTags = (action: 'add' | 'remove') =>
    runAction(
      (ids) =>
        bulkTagHosts(ids, {
          tag_ids: Array.from(checkedTagIds),
          names: action === 'add' && newTagName.trim() ? [newTagName.trim()] : [],
          action,
        }),
      action === 'add' ? 'Tagged' : 'Untagged',
      action === 'add' ? 'Add tags' : 'Remove tags',
      () => {
        setCheckedTagIds(new Set());
        setNewTagName('');
      },
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
    <div className="flex flex-wrap items-center gap-xs rounded-control border border-primary/40 bg-primary/5 px-sm py-xs">
      <Badge variant="default">{effectiveCount.toLocaleString()} selected</Badge>

      {canSelectAll && (
        <Button size="sm" variant="ghost" onClick={() => setAllMatching(true)} disabled={working}>
          Select all {totalMatching.toLocaleString()} matching
        </Button>
      )}
      {allMatching && (
        <span className="text-caption text-muted-foreground">All hosts matching the current filters.</span>
      )}

      <div className="ml-auto flex flex-wrap items-center gap-xs">
        {working && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}

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
              <DropdownMenuItem onSelect={() => runAction((ids) => bulkAssignHosts(ids, user.id), 'Assigned', 'Assign to me')}>
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
                    onSelect={() => runAction((ids) => bulkAssignHosts(ids, m.user_id), 'Assigned', `Assign to ${name}`)}
                  >
                    {name}
                  </DropdownMenuItem>
                );
              })}
            {members.length === 0 && !user && (
              <DropdownMenuItem disabled>No members</DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Status */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline" disabled={working}>
              <Eye className="size-3.5" aria-hidden /> Status
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {STATUS_OPTIONS.map((opt) => (
              <DropdownMenuItem
                key={opt.value}
                onSelect={() => runAction((ids) => bulkFollowHosts(ids, opt.value), 'Updated', `Set status: ${opt.label}`)}
              >
                {opt.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <Button size="sm" variant="ghost" onClick={onClear} disabled={working} aria-label="Clear selection">
          <X className="size-3.5" aria-hidden /> Clear
        </Button>
      </div>

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
              onClick={async () => {
                const p = pending;
                setPending(null);
                await p?.run();
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
