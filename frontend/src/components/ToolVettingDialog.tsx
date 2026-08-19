/**
 * Vetting a tool — the other half of an agent's `suggest_tool` ask.
 *
 * An agent has been able to record "I needed a tool you don't approve" since
 * backend 2.278.0, and nothing could act on it: suggestions piled up in a table
 * with no path to approval short of a SQL prompt, which is the same as not
 * capturing them.
 *
 * Approving is a status change, but rarely *only* a status change — a suggested
 * row's description is the agent's rationale, which reads badly as documentation
 * on a page humans use to learn about tools. So the prose fields are editable in
 * the same dialog, and prefilled for an existing tool.
 */
import React, { useEffect, useState } from 'react';
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
import { formatApiError } from '../utils/apiErrors';
import {
  updateToolRegistryEntry,
  type ToolRegistryEntry,
} from '../services/api/references';

interface Props {
  tool: ToolRegistryEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (updated: ToolRegistryEntry) => void;
}

type VettedStatus = 'approved' | 'reference' | 'rejected';

const STATUS_HELP: Record<VettedStatus, string> = {
  approved: 'An agent may run this against hosts in the inventory without asking each time.',
  reference: 'Documented for operators to run themselves. Agents are not offered it.',
  rejected: 'Declined. The row stays so the next agent that asks gets the same answer.',
};

const ToolVettingDialog: React.FC<Props> = ({ tool, open, onOpenChange, onSaved }) => {
  const toast = useToast();
  const [status, setStatus] = useState<VettedStatus>('reference');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('');
  const [install, setInstall] = useState('');
  const [url, setUrl] = useState('');
  const [ports, setPorts] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tool) return;
    // `suggested` is not a status an operator can set, so a pending row opens
    // on the decision they are actually here to make.
    setStatus(tool.status === 'suggested' ? 'approved' : (tool.status as VettedStatus));
    setDescription(tool.description ?? '');
    setCategory(tool.category ?? '');
    setInstall(tool.install ?? '');
    setUrl(tool.url ?? '');
    setPorts(tool.ports ?? '');
    setError(null);
  }, [tool]);

  if (!tool) return null;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateToolRegistryEntry(tool.name, {
        status,
        description: description.trim(),
        category: category.trim() || 'Uncategorised',
        install: install.trim(),
        url: url.trim(),
        ports: ports.trim(),
      });
      toast.success(`${tool.name} is now ${status}`);
      onSaved({ ...tool, ...updated });
      onOpenChange(false);
    } catch (e) {
      setError(formatApiError(e, 'Could not save this tool.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Review {tool.name}</DialogTitle>
          <DialogDescription>
            Approving a tool decides what an agent may run against the network, on every
            project in this deployment.
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
                <SelectItem value="approved">Agent-approved</SelectItem>
                <SelectItem value="reference">Reference only</SelectItem>
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
              <p className="mt-xxs text-caption text-muted-foreground">
                Prefilled with the agent&rsquo;s rationale — rewrite it as documentation
                before approving; this is what the catalogue shows.
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
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ToolVettingDialog;
