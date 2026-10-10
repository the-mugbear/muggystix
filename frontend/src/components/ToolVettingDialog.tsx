/**
 * Vetting a tool — the other half of an agent's `suggest_tool` ask.
 *
 * An agent has been able to record "I needed a tool BlueStick doesn't list"
 * since backend 2.278.0. Vetting adds it to the catalogue (`reference`) or
 * declines it (`rejected`). 5.313.0 — the registry is a catalogue, not agent
 * policy: no status grants or withholds permission to run anything (the
 * `approved` status was merged into `reference`).
 *
 * Adding is a status change, but rarely *only* a status change — a suggested
 * row's description is the agent's rationale, which reads badly as documentation
 * on a page humans use to learn about tools. So the prose fields are editable in
 * the same dialog, and prefilled for an existing tool.
 */
import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { Button } from './ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Textarea } from './ui/textarea';
import { Alert, AlertDescription } from './ui/alert';
import { useToast } from '../contexts/ToastContext';
import { queryErrorText } from '../lib/query';
import {
  updateToolRegistryEntry,
  type ToolRegistryEntry,
  type ToolRegistryResponse,
  type ToolRegistryUpdate,
} from '../services/api';

interface Props {
  tool: ToolRegistryEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type VettedStatus = 'reference' | 'rejected';

const STATUS_HELP: Record<VettedStatus, string> = {
  reference: 'Listed in the catalogue, with its description and run command, for people and agents to read.',
  rejected: 'Declined. The row stays so the next agent that asks gets the same answer.',
};

/** Mounted per tool opened (the wrapper below), so the fields start from that
 *  tool's row and a save's failure never shows on the next one. */
const VettingForm: React.FC<Props & { tool: ToolRegistryEntry }> = ({ tool, open, onOpenChange }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  // `suggested` is not a status an operator can set, so a pending row opens
  // on the decision they are actually here to make.
  const [status, setStatus] = useState<VettedStatus>(
    tool.status === 'suggested' ? 'reference' : tool.status,
  );
  const [description, setDescription] = useState(tool.description ?? '');
  const [category, setCategory] = useState(tool.category ?? '');
  const [install, setInstall] = useState(tool.install ?? '');
  const [url, setUrl] = useState(tool.url ?? '');
  const [ports, setPorts] = useState(tool.ports ?? '');

  const saving = useMutation({
    mutationFn: (update: ToolRegistryUpdate) => updateToolRegistryEntry(tool.name, update),
    onSuccess: (updated, sent) => {
      // The catalogue page shows the row as the server now has it.
      queryClient.setQueryData<ToolRegistryResponse>(['getToolRegistry'], (old) => (old ? {
        ...old,
        tools: old.tools.map((t) => (t.name === tool.name ? { ...t, ...updated } : t)),
      } : old));
      toast.success(sent.status === 'reference' ? `${tool.name} is in the catalogue` : `${tool.name} was declined`);
      onOpenChange(false);
    },
  });
  const busy = saving.isPending;
  const error = queryErrorText(saving.error, 'Could not save this tool.');
  const save = () => saving.mutate({
    status,
    description: description.trim(),
    category: category.trim() || 'Uncategorised',
    install: install.trim(),
    url: url.trim(),
    ports: ports.trim(),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Review {tool.name}</DialogTitle>
          <DialogDescription>
            Add it to the tool catalogue every project in this deployment reads, or decline
            it. The catalogue documents tools; it does not decide what an agent may run —
            the operator driving the agent does.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-sm">
          {tool.status === 'suggested' && tool.suggested_rationale ? (
            <Alert variant="info">
              <AlertDescription>
                <span className="font-medium">Why an agent asked for this:</span>{' '}
                <span className="whitespace-pre-wrap break-words">
                  {tool.suggested_rationale}
                </span>
              </AlertDescription>
            </Alert>
          ) : null}

          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <div>
            <Label htmlFor="tool-status">Status</Label>
            <Select value={status} onValueChange={(v) => setStatus(v as VettedStatus)}>
              <SelectTrigger id="tool-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="reference">In the catalogue</SelectItem>
                <SelectItem value="rejected">Declined</SelectItem>
              </SelectContent>
            </Select>
            <p className="mt-xxs text-caption text-muted-foreground">{STATUS_HELP[status]}</p>
          </div>

          <div>
            <Label htmlFor="tool-description">Description</Label>
            <Textarea
              id="tool-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What the tool does and when an operator would reach for it."
            />
            {tool.status === 'suggested' ? (
              // The field starts as the row's `description`: what the agent
              // sent as one, or the server's stand-in built from its
              // rationale — not the rationale itself (that is the box above).
              <p className="mt-xxs text-caption text-muted-foreground">
                {tool.description
                  ? 'This starts as the description recorded with the agent’s suggestion. Rewrite it as documentation before adding it; this is what the catalogue shows.'
                  : 'Write it as documentation before adding the tool; this is what the catalogue shows.'}
              </p>
            ) : null}
          </div>

          <div className="grid grid-cols-2 gap-sm">
            <div>
              <Label htmlFor="tool-category">Category</Label>
              <Input
                id="tool-category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                placeholder="e.g. Remote Access"
              />
            </div>
            <div>
              <Label htmlFor="tool-ports">Ports</Label>
              <Input
                id="tool-ports"
                value={ports}
                onChange={(e) => setPorts(e.target.value)}
                placeholder="e.g. 443, 8443"
              />
            </div>
          </div>

          <div>
            <Label htmlFor="tool-install">Install command</Label>
            <Input
              id="tool-install"
              value={install}
              onChange={(e) => setInstall(e.target.value)}
              placeholder="e.g. apt install ligolo-ng"
            />
          </div>

          <div>
            <Label htmlFor="tool-url">Project URL</Label>
            <Input
              id="tool-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const ToolVettingDialog: React.FC<Props> = ({ tool, open, onOpenChange }) => (
  tool ? <VettingForm key={tool.name} tool={tool} open={open} onOpenChange={onOpenChange} /> : null
);

export default ToolVettingDialog;
