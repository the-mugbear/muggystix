/**
 * AddFindingForm — write a finding on a host by hand: a title, a severity,
 * whether it is confirmed, and (5.429.0) which of the host's names it is on,
 * for a web target reached by name.  Its report text is written on the
 * finding's own page.
 *
 * The host page's Findings section shows it inline; the Names page opens it
 * in a dialog on one of a name's current addresses, with that name chosen.
 * One form, one request (`POST /findings`, `host_ids: [host]`, `name_id?`).
 */
import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { createFinding, Finding, FindingSeverity, FindingStatus } from '../../services/api';
import { SEVERITY_LABEL, SEVERITY_ORDER } from '../../utils/severity';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { HostNameSelect, useHostNameChoice } from '../names/HostNameSelect';

/** What a finding written by hand may start as: the two a proposed finding
 *  may (`proposal_service.NEW_FINDING_STATUSES`). */
const NEW_STATUSES: Array<{ value: FindingStatus; label: string }> = [
  { value: 'open', label: 'Under investigation' },
  { value: 'confirmed', label: 'Confirmed' },
];

export interface AddFindingFormProps {
  hostId: number;
  /** The host's address, for the endpoint picker's first option. */
  address?: string | null;
  /** Open the form ON this name (the Names page): preselected, changeable. */
  initialNameId?: number | null;
  onAdded: (made: Finding) => void;
  onCancel: () => void;
}

export const AddFindingForm: React.FC<AddFindingFormProps> = ({ hostId, address, initialNameId, onAdded, onCancel }) => {
  const [title, setTitle] = useState('');
  const [severity, setSeverity] = useState<FindingSeverity>('medium');
  const [status, setStatus] = useState<FindingStatus>('open');
  const choice = useHostNameChoice(hostId, initialNameId);
  const projectId = useProjectId();
  const queryClient = useQueryClient();
  const adding = useMutation({
    mutationFn: () => createFinding(projectId, {
      title: title.trim(),
      severity,
      status,
      host_ids: [hostId],
      ...(choice.nameId != null ? { name_id: choice.nameId } : {}),
    }),
    onSuccess: (made) => {
      void queryClient.invalidateQueries({ queryKey: ['listFindings'] });
      onAdded(made);
    },
  });
  // Busy until the form goes: a finding that was made is not offered again.
  // A name asked for and not yet confirmed (pending, failed, or gone from the
  // host) holds the form: the address alone is never sent in its place.
  const busy = adding.isPending || adding.isSuccess || !choice.ready;
  const error = queryErrorText(adding.error, 'Could not add the finding.');

  return (
    <form
      className="mb-sm space-y-xs rounded-panel border border-border p-xs"
      onSubmit={(e) => { e.preventDefault(); if (!busy && title.trim()) adding.mutate(); }}
    >
      <div className="flex min-w-0 flex-wrap items-end gap-xs">
        <div className="min-w-0 flex-1 basis-64">
          <Label htmlFor="add-finding-title">Finding title</Label>
          <Input id="add-finding-title" value={title} maxLength={500} autoFocus onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="add-finding-severity">Severity</Label>
          <Select value={severity} onValueChange={(v) => setSeverity(v as FindingSeverity)}>
            <SelectTrigger id="add-finding-severity" className="h-9 w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SEVERITY_ORDER.map((sev) => <SelectItem key={sev} value={sev}>{SEVERITY_LABEL[sev]}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="add-finding-status">State</Label>
          <Select value={status} onValueChange={(v) => setStatus(v as FindingStatus)}>
            <SelectTrigger id="add-finding-status" className="h-9 w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              {NEW_STATUSES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <HostNameSelect id="add-finding-name" choice={choice} address={address} className="min-w-0 flex-1 basis-64" />
      </div>
      <p className="text-caption text-muted-foreground">
        For an issue no scanner row or test result on this host stands for. Write its report text, and add further hosts, on the finding.
      </p>
      <div className="flex flex-wrap gap-xs">
        <Button type="submit" size="sm" disabled={busy || title.trim().length === 0}>
          {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Add finding
        </Button>
        <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCancel}>Cancel</Button>
      </div>
      {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
    </form>
  );
};
