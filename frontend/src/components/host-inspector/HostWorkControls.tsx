/**
 * Assignee and tags, editable where they are read (5.303.0).
 *
 * The inspector showed "unassigned" in warning colour and the host's tags, and
 * offered no way to change either — the only path was selecting the row in the
 * table and using the bulk bar.  These call the same bulk endpoints for one
 * host, so the rules (analyst+ to assign; anyone may drop their OWN
 * assignment) are the server's.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus, UserPlus, X } from 'lucide-react';

import {
  bulkAssignHosts,
  bulkTagHosts,
  bulkUnassignHosts,
  listHostTags,
  type HostAssignee,
  type HostTagInfo,
  type HostTagWithCount,
} from '../../services/api';
import { useProjectRoster } from '../../hooks/useProjectMembers';
import { MEMBERS_LOAD_ERROR } from '../MembersLoadError';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { invalidateReads } from '../../lib/query';
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
}

/** One write to this host.  The host is read again behind it (the inspector's
 *  `getHost`), so the control shows what the server stored — and so is the
 *  Hosts list when the inspector is its side sheet: the row behind it and the
 *  filters' counts (an assignee, a tag) are this host's too. */
function useHostWrite<V>(
  hostId: number, { mutationFn, failure }: { mutationFn: (value: V) => Promise<unknown>; failure: string },
) {
  const toast = useToast();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['getHost', hostId] });
      void invalidateReads(queryClient, 'getHosts', 'getHostFilterData');
    },
    onError: (err) => toast.error(formatApiError(err, failure)),
  });
}

export const AssigneeControl: React.FC<CommonProps & { assignees: HostAssignee[] }> = ({
  hostId, canEdit, assignees,
}) => {
  const { user } = useAuth();
  const assign = useHostWrite(hostId, {
    mutationFn: (userId: number) => bulkAssignHosts([hostId], userId), failure: 'Could not assign the host.',
  });
  const unassign = useHostWrite<void>(hostId, {
    mutationFn: () => bulkUnassignHosts([hostId]), failure: 'Could not remove your assignment.',
  });
  const busy = assign.isPending || unassign.isPending;
  // Asked for when the menu is first opened; shared with every other picker.
  const [wanted, setWanted] = useState(false);
  const roster = useProjectRoster({ enabled: wanted });
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
        <DropdownMenu onOpenChange={(open) => { if (open) setWanted(true); }}>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-6 shrink-0 px-xs text-caption" disabled={busy}
              aria-label="Assign this host">
              {busy ? <Loader2 className="size-3 animate-spin" aria-hidden /> : <UserPlus className="size-3" aria-hidden />}
              Assign
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
            {user && !mine && (
              <DropdownMenuItem onSelect={() => assign.mutate(user.id)}>
                Assign to me
              </DropdownMenuItem>
            )}
            {roster.status === 'loading' && <DropdownMenuItem disabled>Loading members…</DropdownMenuItem>}
            {roster.status === 'error' && (
              // Stays open: the retry fills this same menu.
              <DropdownMenuItem onSelect={(e) => { e.preventDefault(); roster.retry(); }}>
                {MEMBERS_LOAD_ERROR} Retry
              </DropdownMenuItem>
            )}
            {roster.members
              .filter((m) => m.user_id !== user?.id && !assignees.some((a) => a.user_id === m.user_id))
              .map((m) => {
                const name = m.full_name || m.username || `User #${m.user_id}`;
                return (
                  <DropdownMenuItem key={m.user_id} onSelect={() => assign.mutate(m.user_id)}>
                    {name}
                  </DropdownMenuItem>
                );
              })}
            {mine && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => unassign.mutate()}>
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
  hostId, canEdit, tags,
}) => {
  const queryClient = useQueryClient();
  const addTag = useHostWrite(hostId, {
    mutationFn: (body: { tag_ids?: number[]; names?: string[] }) => bulkTagHosts([hostId], { ...body, action: 'add' }),
    failure: 'Could not tag the host.',
  });
  const removeTag = useHostWrite(hostId, {
    mutationFn: (tagId: number) => bulkTagHosts([hostId], { tag_ids: [tagId], action: 'remove' }),
    failure: 'Could not remove the tag.',
  });
  const busy = addTag.isPending || removeTag.isPending;
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState('');

  // The project's tags, read when the picker is first opened and again only
  // after a tag was added here (its count, or the tag itself, is new).
  const tagsQuery = useQuery({
    queryKey: ['listHostTags'],
    queryFn: ({ signal }) => listHostTags(signal),
    enabled: open,
    staleTime: Infinity,
  });
  // A failed read offers no tag to pick; a new one can still be named.
  const projectTags: HostTagWithCount[] | null = tagsQuery.isError ? [] : tagsQuery.data ?? null;

  const add = (body: { tag_ids?: number[]; names?: string[] }) => {
    setOpen(false);
    setNewName('');
    addTag.mutate(body, {
      onSuccess: () => { void invalidateReads(queryClient, 'listHostTags'); },
    });
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
              onClick={() => removeTag.mutate(tag.id)}>
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
