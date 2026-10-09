/**
 * SiteManagerDialog — set the criticality tier + expected host count for the
 * project's sites (the metadata the attention model weights by). Sites are
 * created by naming them on subnets (CSV col 4 / inline edit); this edits
 * their metadata, not the name.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { listSites, updateSite, type Site } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from './ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from './ui/table';

interface SiteManagerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type SitePatch = Parameters<typeof updateSite>[2];

/** What an "Expected hosts" box holds, as the count it would store. */
const expectedHostsOf = (raw: string): number | null => (raw.trim() === '' ? null : Number(raw.trim()));

export const SiteManagerDialog: React.FC<SiteManagerDialogProps> = ({ open, onOpenChange }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  // Read each time the dialog opens; closed, it asks for nothing.
  const projectId = useProjectId();
  const query = useQuery({
    queryKey: ['listSites', projectId],
    queryFn: ({ signal }) => listSites(projectId, signal),
    enabled: open,
  });
  const sites = query.data ?? [];
  const loading = query.isFetching;
  // A failed read is said here with Retry — never shown as "No sites yet".
  const loadError = queryErrorText(query.error, 'Failed to load sites.');

  // "Expected hosts" as the reader typed it, per site, over what is stored:
  // a box shows the stored count unless it holds an edit, and an edit leaves
  // only when the server has taken it (or it was typed back).
  const [expectedEdits, setExpectedEdits] = useState<Record<number, string>>({});
  // The sites whose last save of that edit failed: the row says so.
  const [expectedFailed, setExpectedFailed] = useState<Record<number, true>>({});
  const without = <T,>(map: Record<number, T>, id: number): Record<number, T> => {
    const { [id]: _gone, ...rest } = map;
    return rest;
  };

  const update = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: SitePatch }) => updateSite(projectId, id, payload),
    onSuccess: (updated, { id, payload }) => {
      queryClient.setQueryData<Site[]>(['listSites', projectId], (prev) =>
        prev?.map((s) => (s.id === updated.id ? updated : s)));
      if ('expected_host_count' in payload) {
        setExpectedEdits((prev) => without(prev, id));
        setExpectedFailed((prev) => without(prev, id));
      }
    },
    onError: (e, { id, payload }) => {
      if ('expected_host_count' in payload) setExpectedFailed((prev) => ({ ...prev, [id]: true }));
      toast.error(formatApiError(e, 'Failed to update site.'));
    },
  });
  const patch = (id: number, payload: SitePatch) => update.mutate({ id, payload });
  const savingExpected = (id: number) =>
    update.isPending && update.variables?.id === id && 'expected_host_count' in update.variables.payload;

  const saveExpected = (site: Site) => {
    const raw = expectedEdits[site.id];
    if (raw === undefined) return;
    const next = expectedHostsOf(raw);
    if (next === site.expected_host_count) {
      // Typed back to what is stored: nothing to save, nothing unsaved.
      setExpectedEdits((prev) => without(prev, site.id));
      setExpectedFailed((prev) => without(prev, site.id));
      return;
    }
    patch(site.id, { expected_host_count: next });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Manage sites</DialogTitle>
          <DialogDescription>
            Set each site's criticality tier (1 = most critical — weights its exposure on
            the Needs-attention card) and expected host count (drives coverage-gap
            detection). Sites are named by tagging subnets; this edits their metadata.
          </DialogDescription>
        </DialogHeader>
        {!loading && loadError && (
          <p role="alert" className="break-words text-caption text-destructive">
            {loadError}{' '}
            <button type="button" className="text-info hover:underline" onClick={() => { void query.refetch(); }}>
              Retry
            </button>
          </p>
        )}
        {loading ? (
          <div className="flex items-center gap-xs py-lg" role="status">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden /> Loading…
          </div>
        ) : sites.length === 0 ? (
          !loadError && (
            <p className="py-lg text-center text-metadata text-muted-foreground">
              No sites yet. Add a site to a subnet (CSV column 4 or the Site cell) to create one.
            </p>
          )
        ) : (
          <div className="overflow-x-auto">
            <Table className="table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead>Site</TableHead>
                  <TableHead className="w-20">Subnets</TableHead>
                  <TableHead className="w-28">Tier</TableHead>
                  <TableHead className="w-36">Expected hosts</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sites.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="truncate font-medium">{s.name}</TableCell>
                    <TableCell className="text-caption text-muted-foreground">{s.subnet_count}</TableCell>
                    <TableCell>
                      <Select
                        value={String(s.criticality_tier)}
                        onValueChange={(v) => patch(s.id, { criticality_tier: Number(v) })}
                      >
                        <SelectTrigger className="h-8 text-caption" aria-label={`Tier for ${s.name}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {[1, 2, 3, 4].map((t) => (
                            <SelectItem key={t} value={String(t)}>
                              Tier {t}{t === 1 ? ' (critical)' : t === 4 ? ' (low)' : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        min={0}
                        value={expectedEdits[s.id] ?? String(s.expected_host_count ?? '')}
                        aria-label={`Expected host count for ${s.name}`}
                        aria-invalid={expectedFailed[s.id] ? true : undefined}
                        onChange={(e) => setExpectedEdits((prev) => ({ ...prev, [s.id]: e.target.value }))}
                        onBlur={() => saveExpected(s)}
                      />
                      {expectedFailed[s.id] && !savingExpected(s.id) && (
                        <p role="alert" className="mt-xxs text-caption text-destructive">
                          Not saved.{' '}
                          <button type="button" className="text-info hover:underline" onClick={() => saveExpected(s)}>
                            Save
                          </button>
                        </p>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default SiteManagerDialog;
