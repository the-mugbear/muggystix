/**
 * "Add hosts" on a finding: search the project's hosts by address or name,
 * pick several, and attach them in ONE request (POST /findings/:id/hosts).
 * Hosts already on the finding are shown but cannot be picked; the server
 * skips them too. Selections survive a new search, so hosts can be gathered
 * across several queries before adding.
 */
import React, { useMemo, useState } from 'react';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { Loader2, RefreshCw, Search, X } from 'lucide-react';

import { Finding, Host, addFindingHosts, getHosts } from '../services/api';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useOpeningKey } from '../hooks/useOpeningKey';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';

const PAGE = 25;

interface Picked {
  id: number;
  ip_address: string;
  hostname: string | null;
}

export interface AddFindingHostsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  finding: Finding;
  /** Called with the updated finding and the ids that were requested. */
  onAdded: (updated: Finding, requested: number[]) => void;
}

// A fresh dialog each time it opens: its search, selection and any refusal
// are the body's initial state (`useOpeningKey`).
const AddFindingHostsDialog: React.FC<AddFindingHostsDialogProps> = (props) => (
  <AddFindingHostsBody key={useOpeningKey(props.open)} {...props} />
);

const AddFindingHostsBody: React.FC<AddFindingHostsDialogProps> = ({ open, onOpenChange, finding, onAdded }) => {
  const projectId = useProjectId();
  const [query, setQuery] = useState('');
  const debounced = useDebouncedValue(query.trim(), 250);
  const [picked, setPicked] = useState<Map<number, Picked>>(new Map());

  const attached = useMemo(() => new Set(finding.hosts.map((h) => h.host_id)), [finding.hosts]);

  // The rows of the previous search stay while the next one is read.
  const search = useQuery({
    queryKey: ['getHosts', projectId, { search: debounced || undefined, limit: PAGE, include_total: false }],
    queryFn: ({ signal }) => getHosts(projectId, { search: debounced || undefined, limit: PAGE, include_total: false }, signal),
    enabled: open,
    placeholderData: keepPreviousData,
  });
  const results: Host[] = search.data?.items ?? [];
  const loading = search.isFetching;

  const add = useMutation({
    mutationFn: (ids: number[]) => addFindingHosts(projectId, finding.id, ids),
    onSuccess: (updated, ids) => {
      onAdded(updated, ids);
      onOpenChange(false);
    },
  });
  const saving = add.isPending;
  const resetAdd = add.reset;
  // One line for what went wrong: the add that was refused, else the search.
  const error = queryErrorText(add.error, 'The hosts could not be added.')
    ?? queryErrorText(search.error, 'Hosts could not be searched.');

  const toggle = (h: Host) => {
    setPicked((prev) => {
      const next = new Map(prev);
      if (next.has(h.id)) next.delete(h.id);
      else next.set(h.id, { id: h.id, ip_address: h.ip_address, hostname: h.hostname });
      return next;
    });
  };

  const submit = () => {
    if (picked.size === 0 || saving) return;
    add.mutate([...picked.keys()]);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!saving) onOpenChange(o); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add affected hosts</DialogTitle>
          <DialogDescription>
            Record that this issue affects more hosts. Each one is added as an open endpoint.
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            autoFocus
            className="pl-xl"
            placeholder="Search by IP address or hostname…"
            value={query}
            // A new search starts clean: a refused add is no longer what the list says.
            onChange={(e) => { setQuery(e.target.value); resetAdd(); }}
            aria-label="Search hosts"
          />
        </div>

        {picked.size > 0 && (
          <div className="flex max-h-24 flex-wrap gap-xs overflow-y-auto" aria-label="Selected hosts">
            {[...picked.values()].map((p) => (
              <span
                key={p.id}
                className="inline-flex min-w-0 max-w-full items-center gap-xxs rounded-control border border-border bg-muted px-xs py-0.5 text-caption"
              >
                <span className="truncate font-mono">{p.ip_address}</span>
                {p.hostname && <span className="truncate text-muted-foreground">{p.hostname}</span>}
                <button
                  type="button"
                  className="shrink-0 hover:text-foreground"
                  onClick={() => setPicked((prev) => { const n = new Map(prev); n.delete(p.id); return n; })}
                  aria-label={`Remove ${p.ip_address} from the selection`}
                >
                  <X className="size-3" aria-hidden />
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="max-h-80 min-h-32 overflow-y-auto border-y border-border" role="group" aria-label="Matching hosts">
          {error ? (
            <div className="flex flex-wrap items-center gap-sm py-md">
              <p className="min-w-0 break-words text-caption text-destructive">{error}</p>
              <Button variant="outline" size="sm" onClick={() => { resetAdd(); void search.refetch(); }}>
                <RefreshCw className="size-4" aria-hidden /> Retry
              </Button>
            </div>
          ) : loading && results.length === 0 ? (
            <div className="flex items-center gap-xs py-md text-caption text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Searching hosts…
            </div>
          ) : results.length === 0 ? (
            <p className="py-md text-caption text-muted-foreground">
              {debounced ? `No hosts match “${debounced}”.` : 'This project has no hosts yet.'}
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {results.map((h) => {
                const already = attached.has(h.id);
                const inputId = `add-host-${h.id}`;
                return (
                  <li key={h.id} className="flex min-w-0 items-center gap-sm py-xs">
                    <Checkbox
                      id={inputId}
                      checked={already || picked.has(h.id)}
                      disabled={already || saving}
                      onCheckedChange={() => toggle(h)}
                      aria-label={`Add ${h.ip_address}`}
                    />
                    <label htmlFor={inputId} className="flex min-w-0 flex-1 items-baseline gap-xs">
                      <span className="shrink-0 font-mono text-metadata">{h.ip_address}</span>
                      <span className="min-w-0 truncate text-caption text-muted-foreground" title={h.hostname ?? undefined}>
                        {h.hostname ?? ''}
                      </span>
                    </label>
                    {already && <span className="shrink-0 text-caption text-muted-foreground">already affected</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        {results.length === PAGE && (
          <p className="text-caption text-muted-foreground">Showing the first {PAGE} matches — refine the search to find others.</p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>Cancel</Button>
          <Button onClick={submit} disabled={picked.size === 0 || saving}>
            {saving && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {picked.size === 0 ? 'Add hosts' : `Add ${picked.size} host${picked.size === 1 ? '' : 's'}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default AddFindingHostsDialog;
