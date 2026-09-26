/**
 * Assignee and tags, editable where they are read (5.303.0).
 *
 * The inspector showed "unassigned" in warning colour and the host's tags, and
 * offered no way to change either — the only path was selecting the row in the
 * table and using the bulk bar.  These call the same bulk endpoints for one
 * host, so the rules (analyst+ to assign; anyone may drop their OWN
 * assignment) are the server's.
 */
import React, { useEffect, useState } from 'react';
import { Loader2, Plus, UserPlus, X } from 'lucide-react';

import {
  bulkAssignHosts,
  bulkTagHosts,
  bulkUnassignHosts,
  listHostTags,
  listProjectMembers,
  type HostAssignee,
  type HostTagInfo,
  type HostTagWithCount,
  type ProjectMember,
} from '../../services/api';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

interface CommonProps {
  hostId: number;
  canEdit: boolean;
  /** Reload the host after a change. */
  onChanged: () => void;
}

function useRun(onChanged: () => void) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async (action: () => Promise<unknown>, failure: string) => {
    setBusy(true);
    try {
      await action();
      onChanged();
    } catch (err: unknown) {
      toast.error(formatApiError(err, failure));
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

export const AssigneeControl: React.FC<CommonProps & { assignees: HostAssignee[] }> = ({
  hostId, canEdit, onChanged, assignees,
}) => {
  const { user } = useAuth();
  const { busy, run } = useRun(onChanged);
  const [members, setMembers] = useState<ProjectMember[] | null>(null);
  const names = assignees.map((a) => a.name).join(', ');
  const mine = assignees.some((a) => a.user_id === user?.id);

  return (
    <span className="flex min-w-0 items-center gap-xs">
      {assignees.length > 0 ? (
        <span className="min-w-0 truncate text-foreground" title={names}>{names}</span>
      ) : (
        // Muted, not warning: it is a state, and the control beside it is the action.
        <span className="text-muted-foreground">unassigned</span>
      )}
      {canEdit && (
        <DropdownMenu onOpenChange={(open) => {
          if (open && members === null) listProjectMembers().then(setMembers).catch(() => setMembers([]));
        }}>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-6 shrink-0 px-xs text-caption" disabled={busy}
              aria-label="Assign this host">
              {busy ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <UserPlus className="size-3" aria-hidden />}
              Assign
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
            {user && !mine && (
              <DropdownMenuItem onSelect={() => run(() => bulkAssignHosts([hostId], user.id), 'Could not assign the host.')}>
                Assign to me
              </DropdownMenuItem>
            )}
            {members === null && <DropdownMenuItem disabled>Loading members…</DropdownMenuItem>}
            {(members ?? [])
              .filter((m) => m.user_id !== user?.id && !assignees.some((a) => a.user_id === m.user_id))
              .map((m) => {
                const name = m.full_name || m.username || `User #${m.user_id}`;
                return (
                  <DropdownMenuItem key={m.user_id}
                    onSelect={() => run(() => bulkAssignHosts([hostId], m.user_id), 'Could not assign the host.')}>
                    {name}
                  </DropdownMenuItem>
                );
              })}
            {mine && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => run(() => bulkUnassignHosts([hostId]), 'Could not remove your assignment.')}>
                  Remove my assignment
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </span>
  );
};

export const TagControl: React.FC<CommonProps & { tags: HostTagInfo[] }> = ({
  hostId, canEdit, onChanged, tags,
}) => {
  const { busy, run } = useRun(onChanged);
  const [open, setOpen] = useState(false);
  const [projectTags, setProjectTags] = useState<HostTagWithCount[] | null>(null);
  const [newName, setNewName] = useState('');

  useEffect(() => {
    if (open && projectTags === null) listHostTags().then(setProjectTags).catch(() => setProjectTags([]));
  }, [open, projectTags]);

  const add = (body: { tag_ids?: number[]; names?: string[] }) => {
    setOpen(false);
    setNewName('');
    setProjectTags(null);
    run(() => bulkTagHosts([hostId], { ...body, action: 'add' }), 'Could not tag the host.');
  };
  const available = (projectTags ?? []).filter((t) => !tags.some((h) => h.id === t.id));

  return (
    <span className="flex min-w-0 flex-wrap items-center gap-xxs">
      {tags.map((tag) => (
        <span key={tag.id}
          className="inline-flex max-w-full items-center gap-xxs rounded-chip border border-border px-xs text-caption text-foreground"
          style={tag.color ? { borderColor: tag.color, color: tag.color } : undefined}>
          <span className="truncate" title={tag.name}>{tag.name}</span>
          {canEdit && (
            <button type="button" disabled={busy} aria-label={`Remove tag ${tag.name}`}
              className="rounded-sm opacity-70 hover:opacity-100"
              onClick={() => run(() => bulkTagHosts([hostId], { tag_ids: [tag.id], action: 'remove' }), 'Could not remove the tag.')}>
              <X className="size-3" aria-hidden />
            </button>
          )}
        </span>
      ))}
      {tags.length === 0 && !canEdit && <span className="text-caption text-muted-foreground">—</span>}
      {canEdit && (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="sm" className="h-6 shrink-0 px-xs text-caption" disabled={busy}
              aria-label="Add a tag">
              {busy ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <Plus className="size-3" aria-hidden />}
              Tag
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-64 space-y-xs p-sm">
            <form
              className="flex gap-xs"
              onSubmit={(e) => {
                e.preventDefault();
                if (newName.trim()) add({ names: [newName.trim()] });
              }}
            >
              <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New tag…"
                aria-label="New tag name" className="h-8 text-caption" autoFocus />
              <Button type="submit" size="sm" className="h-8" disabled={!newName.trim()}>Add</Button>
            </form>
            {projectTags === null ? (
              <p className="text-caption text-muted-foreground">Loading tags…</p>
            ) : available.length > 0 ? (
              <ul className="max-h-48 overflow-y-auto" aria-label="Project tags">
                {available.map((t) => (
                  <li key={t.id}>
                    <button type="button" onClick={() => add({ tag_ids: [t.id] })}
                      className="flex w-full min-w-0 items-center justify-between gap-xs rounded-control px-xs py-xxs text-left text-metadata hover:bg-accent">
                      <span className="truncate">{t.name}</span>
                      <span className="shrink-0 text-caption tabular-nums text-muted-foreground">{t.host_count}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-caption text-muted-foreground">No other project tags — name a new one above.</p>
            )}
          </PopoverContent>
        </Popover>
      )}
    </span>
  );
};
