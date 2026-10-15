/**
 * "Record retest…" — what a retest of ONE host showed (5.395.0; owner,
 * 2026-10-14: "anyone should be allowed to attempt to close it with
 * evidence").  The result is recorded as evidence on that host and the host
 * moves with it, in one call (`POST /findings/{id}/endpoints/{fh}/retest`):
 * fixed → Remediated, still there → Still present.  A standing retest
 * (Retest pending, or Still present after a hand move kept the request —
 * owner, 2026-10-15) is closed this way and no other: the state picker's
 * "Remediated" opens this.
 *
 * Keyed by its opening (`useOpeningKey`): every opening starts clean.
 */
import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import { recordEndpointRetest, type Finding, type FindingHostInfo } from '../../services/api';
import { useOpeningKey } from '../../hooks/useOpeningKey';
import { useProjectId } from '../../hooks/useProjectId';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';

interface Props {
  finding: Pick<Finding, 'id' | 'title'>;
  /** The host awaiting a retest; null keeps the dialog closed. */
  row: FindingHostInfo | null;
  onOpenChange: (open: boolean) => void;
  /** The finding as the server answered, to put on the page. */
  onRecorded: (updated: Finding, fixed: boolean) => void;
}

const RecordRetestDialog: React.FC<Props> = ({ finding, row, onOpenChange, onRecorded }) => {
  const key = useOpeningKey(row?.id ?? null);
  return (
    <Dialog open={row != null} onOpenChange={onOpenChange}>
      {row && <Body key={key} finding={finding} row={row} onOpenChange={onOpenChange} onRecorded={onRecorded} />}
    </Dialog>
  );
};

const Body: React.FC<Omit<Props, 'row'> & { row: FindingHostInfo }> = ({ finding, row, onOpenChange, onRecorded }) => {
  const projectId = useProjectId();
  const [fixed, setFixed] = useState<boolean | null>(null);
  const [tool, setTool] = useState('');
  const [command, setCommand] = useState('');
  const [summary, setSummary] = useState('');
  const [output, setOutput] = useState('');
  const save = useMutation({
    mutationFn: () => recordEndpointRetest(projectId, finding.id, row.id, {
      fixed: fixed === true, tool: tool.trim(), summary: summary.trim(),
      ...(command.trim() ? { command: command.trim() } : {}),
      ...(output.trim() ? { raw_output: output } : {}),
    }),
    onSuccess: (updated) => { onRecorded(updated, fixed === true); onOpenChange(false); },
  });
  const address = row.ip_address ?? `host ${row.host_id}`;
  const host = row.fqdn ? `${row.fqdn} (${address})` : address;
  const ready = fixed !== null && tool.trim() !== '' && summary.trim() !== '' && !save.isPending;
  const choice = (value: boolean, label: string, hint: string) => (
    <button
      type="button" role="radio" aria-checked={fixed === value}
      onClick={() => setFixed(value)}
      className={cn(
        'min-w-0 flex-1 rounded-control border px-sm py-xs text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        fixed === value ? 'border-primary bg-primary/10' : 'border-border hover:bg-muted',
      )}
    >
      <span className="block text-body font-medium">{label}</span>
      <span className="block text-caption text-muted-foreground">{hint}</span>
    </button>
  );
  return (
    <DialogContent size="lg">
      <DialogHeader>
        <DialogTitle>Record the retest</DialogTitle>
        <DialogDescription className="break-words">
          {finding.title} on <span className="font-mono">{host}</span>. The result is kept as evidence on this host,
          and the host takes its state from it.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="space-y-sm">
        <div role="radiogroup" aria-label="What the retest showed" className="flex flex-wrap gap-xs">
          {choice(true, 'Fixed', 'The issue is gone from this host: it becomes Remediated.')}
          {choice(false, 'Still present', 'The issue is still there: it goes back to Still present.')}
        </div>
        <div className="space-y-xxs">
          <Label htmlFor="retest-tool">Tool</Label>
          <Input id="retest-tool" maxLength={100} value={tool} placeholder="e.g. nmap, curl, a manual check"
            onChange={(e) => setTool(e.target.value)} />
        </div>
        <div className="space-y-xxs">
          <Label htmlFor="retest-summary">What it showed</Label>
          <Textarea id="retest-summary" rows={3} maxLength={10_000} value={summary}
            onChange={(e) => setSummary(e.target.value)} />
        </div>
        <div className="space-y-xxs">
          <Label htmlFor="retest-command">Command (optional)</Label>
          <Textarea id="retest-command" rows={2} maxLength={10_000} className="font-mono" value={command}
            onChange={(e) => setCommand(e.target.value)} />
        </div>
        <div className="space-y-xxs">
          <Label htmlFor="retest-output">Output (optional)</Label>
          <Textarea id="retest-output" rows={4} className="font-mono" value={output}
            onChange={(e) => setOutput(e.target.value)} />
        </div>
        {save.isError && (
          <p role="alert" className="break-words text-caption text-destructive">
            {formatApiError(save.error, 'Could not record the retest.')}
          </p>
        )}
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={save.isPending}>Cancel</Button>
        <Button onClick={() => save.mutate()} disabled={!ready}>
          {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />} Record the retest
        </Button>
      </DialogFooter>
    </DialogContent>
  );
};

export default RecordRetestDialog;
