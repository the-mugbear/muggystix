/**
 * HostNameSelect — "on the address, or on one of its names?"
 *
 * A finding or a test on a web target reached by NAME is an endpoint
 * (host, name): the name hangs on the address it resolves to (5.429.0).  This
 * is the one picker for that choice — the host page's Add finding and Add
 * test forms and the Names page's actions all use it — over the host's
 * CURRENT names (`GET /names/by-host/{id}` `current`: the names whose latest
 * A/AAAA batch includes this address), which is exactly what the server
 * accepts (a name observed at this host; `dns_name_service.name_observed_at_host`).
 *
 * `useHostNameChoice` owns the read and the choice; the component only draws
 * it, and draws nothing for a host with no current name.
 *
 * The selection is never downgraded behind the reader's back (review
 * 2026-11-09 H1): a form opened ON a name is not ready until that name is
 * confirmed among the host's current names — not while the read is pending,
 * not after it failed, not when the answer lacks the name.  The address alone
 * is only ever the reader's explicit pick, or the default when no name was
 * asked for.
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getHostNames, HostNameBinding } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

/** The same key `HostNamesCard` reads under, so one answer serves both. */
export const hostNamesKey = (projectId: number, hostId: number) => ['getHostNames', projectId, hostId] as const;

const ADDRESS_ONLY = 'address';

export interface HostNameChoice {
  /** The host's current names (empty until read, or when it has none). */
  names: HostNameBinding[];
  /** The confirmed chosen name, or null for the bare address. */
  chosen: HostNameBinding | null;
  /** The confirmed chosen name's id, or null for the bare address. */
  nameId: number | null;
  pick: (nameId: number | null) => void;
  /** The ONE validity rule: a name was asked for (the caller's initial name,
   *  or a pick) and it is not confirmed among the host's current names —
   *  the read is pending, failed, or answered without it.  A form must not
   *  submit while this is true. */
  unresolved: boolean;
  /** `!unresolved`: the selection can be sent as it stands. */
  ready: boolean;
  /** The read has not answered yet. */
  pending: boolean;
  /** Why the read failed, or null. */
  error: string | null;
  retry: () => void;
}

/** The read and the choice.  `initialNameId` is the name the caller opened
 *  the form on (the Names page): selected until the reader picks otherwise,
 *  and sent only once confirmed. */
export const useHostNameChoice = (hostId: number, initialNameId?: number | null): HostNameChoice => {
  const projectId = useProjectId();
  const query = useQuery({
    queryKey: hostNamesKey(projectId, hostId),
    queryFn: ({ signal }) => getHostNames(projectId, hostId, signal),
  });
  const names = query.data?.current ?? [];
  // undefined = untouched (the initial name, else the address); null = the address.
  const [picked, setPicked] = useState<number | null | undefined>(undefined);
  const requested = picked === undefined ? (initialNameId ?? null) : picked;
  const chosen = requested == null ? null : names.find((n) => n.name_id === requested) ?? null;
  const unresolved = requested != null && chosen == null;
  return {
    names,
    chosen,
    nameId: chosen?.name_id ?? null,
    pick: setPicked,
    unresolved,
    ready: !unresolved,
    pending: query.isPending,
    error: queryErrorText(query.error, 'The host’s names could not be read.'),
    retry: () => { void query.refetch(); },
  };
};

export interface HostNameSelectProps {
  id: string;
  choice: HostNameChoice;
  /** The host's address, named as the first option. */
  address?: string | null;
  label?: string;
  className?: string;
}

export const HostNameSelect: React.FC<HostNameSelectProps> = ({ id, choice, address, label = 'On', className }) => {
  // A name was asked for and cannot be confirmed: say so, offer the read again
  // or the address by an explicit choice.  Never silently the address.
  if (choice.unresolved && !choice.pending) {
    return (
      <div role="alert" className={className}>
        <p className="break-words text-caption text-destructive">
          {choice.error
            ? `${choice.error} The name this was opened on cannot be confirmed at this address.`
            : 'The name this was opened on no longer resolves to this address.'}
        </p>
        <div className="mt-2xs flex flex-wrap gap-xs">
          {choice.error && (
            <Button type="button" size="sm" variant="outline" onClick={choice.retry}>Retry</Button>
          )}
          <Button type="button" size="sm" variant="outline" onClick={() => choice.pick(null)}>Use the address only</Button>
        </div>
      </div>
    );
  }
  if (choice.error) {
    return (
      <p role="status" className="break-words text-caption text-muted-foreground">
        {choice.error} The address alone is used.
      </p>
    );
  }
  if (choice.names.length === 0) return null;
  const value = choice.nameId == null ? ADDRESS_ONLY : String(choice.nameId);
  return (
    <div className={className}>
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={(v) => choice.pick(v === ADDRESS_ONLY ? null : Number(v))}>
        <SelectTrigger id={id} className="h-9 min-w-0 max-w-full"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={ADDRESS_ONLY}>{address ? `${address} (the address)` : 'The address only'}</SelectItem>
          {choice.names.map((n) => (
            <SelectItem key={n.name_id} value={String(n.name_id)}>
              <span className="font-mono">{n.fqdn}</span>
              {!n.in_scope && <span className="ml-xs text-caption text-muted-foreground">not in scope</span>}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
};
