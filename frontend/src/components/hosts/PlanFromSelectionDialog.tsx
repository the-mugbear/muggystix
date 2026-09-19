import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import {
  PlanFromHostsResponse,
  TestPlanSummary,
  createPlanFromHosts,
  getTestPlans,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { stashPlanSelection } from '../../utils/planSelection';
import { Alert, AlertDescription } from '../ui/alert';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import { RadioGroup, RadioGroupItem } from '../ui/radio-group';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';

/**
 * "Test plan" from the Hosts bulk bar (v5.221.0; design review item 6).
 *
 * The selection becomes a FIXED host list the moment the dialog opens —
 * resolved through `resolveIds` (the checked rows, or every host matching
 * the current filters) — and that is what the plan records.  A saved query's
 * membership can change; this list cannot, which is what makes the plan's
 * provenance reviewable.  Before anything is written, a dry run shows the
 * operator what will happen: how many entries, how many already in the
 * target draft, how many already carried by an approved or completed plan.
 *
 * Three ways out: a new draft the analyst authors by hand, an existing
 * draft, or the AI-assisted generate flow on /test-plans, which receives
 * the same fixed list.
 */

type Mode = 'new' | 'existing' | 'ai';

const PRIORITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;
const PHASES = ['reconnaissance', 'enumeration', 'exploitation', 'post_exploitation', 'reporting'] as const;

export interface PlanFromSelectionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Resolves the selection to its fixed id list (may hit the server). */
  resolveIds: () => Promise<number[]>;
  /** How the selection was made, for the plan's provenance note. */
  selectionSummary: string;
  /** IPs of the checked rows on this page, shown as a sample of the targets. */
  sampleIps: string[];
}

const PlanFromSelectionDialog: React.FC<PlanFromSelectionDialogProps> = ({
  open,
  onOpenChange,
  resolveIds,
  selectionSummary,
  sampleIps,
}) => {
  const toast = useToast();
  const navigate = useNavigate();

  const [mode, setMode] = useState<Mode>('new');
  const [title, setTitle] = useState('');
  const [rationale, setRationale] = useState('');
  const [priority, setPriority] = useState<(typeof PRIORITIES)[number]>('medium');
  const [phase, setPhase] = useState<(typeof PHASES)[number]>('enumeration');
  const [draftId, setDraftId] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<TestPlanSummary[]>([]);

  const [ids, setIds] = useState<number[] | null>(null);
  const [resolvedAt, setResolvedAt] = useState<string | null>(null);
  const [preview, setPreview] = useState<PlanFromHostsResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Resolve the fixed list once per opening, and load the drafts.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setIds(null);
    setPreview(null);
    setPreviewError(null);
    setError(null);
    setResolving(true);
    resolveIds()
      .then((resolved) => {
        if (cancelled) return;
        setIds(resolved);
        setResolvedAt(new Date().toISOString());
      })
      .catch((err) => {
        if (!cancelled) setPreviewError(formatApiError(err, 'Could not resolve the selection.'));
      })
      .finally(() => {
        if (!cancelled) setResolving(false);
      });
    getTestPlans({ status: 'draft', limit: 100 })
      .then((rows) => {
        if (!cancelled) setDrafts(rows);
      })
      .catch(() => {
        if (!cancelled) setDrafts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, resolveIds]);

  // Dry run against the chosen target so the numbers shown are the numbers
  // that will apply.  Re-runs when the target draft changes.
  useEffect(() => {
    if (!open || !ids || ids.length === 0 || mode === 'ai') return;
    if (mode === 'existing' && draftId == null) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    createPlanFromHosts({
      host_ids: ids,
      rationale: '(preview)',
      title: mode === 'new' ? '(preview)' : undefined,
      plan_id: mode === 'existing' ? draftId ?? undefined : undefined,
      dry_run: true,
    })
      .then((res) => {
        if (!cancelled) {
          setPreview(res);
          setPreviewError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setPreviewError(formatApiError(err, 'Could not preview the selection.'));
      });
    return () => {
      cancelled = true;
    };
  }, [open, ids, mode, draftId]);

  const count = ids?.length ?? 0;
  const canSubmit =
    !resolving &&
    !submitting &&
    count > 0 &&
    rationale.trim().length > 0 &&
    (mode === 'new' ? title.trim().length > 0 : mode === 'existing' ? draftId != null : true);

  const submit = async () => {
    if (!ids || !canSubmit) return;
    setError(null);
    if (mode === 'ai') {
      const ok = stashPlanSelection({
        host_ids: ids,
        rationale: rationale.trim(),
        summary: selectionSummary,
        taken_at: resolvedAt ?? new Date().toISOString(),
      });
      if (!ok) {
        setError('Could not hand the selection to the generate dialog (browser storage unavailable).');
        return;
      }
      onOpenChange(false);
      navigate('/test-plans?generate=1&source=selection');
      return;
    }
    setSubmitting(true);
    try {
      const res = await createPlanFromHosts({
        host_ids: ids,
        rationale: rationale.trim(),
        title: mode === 'new' ? title.trim() : undefined,
        plan_id: mode === 'existing' ? draftId ?? undefined : undefined,
        priority,
        test_phase: phase,
        selection_summary: selectionSummary,
      });
      const planTitle = res.plan?.title ?? 'plan';
      toast.success(
        `${res.created_plan ? 'Created' : 'Updated'} “${planTitle}” with ${res.added} host${res.added === 1 ? '' : 's'}` +
          (res.already_in_plan ? ` (${res.already_in_plan} already in it)` : ''),
        { autoHideMs: 4000 },
      );
      onOpenChange(false);
      if (res.plan) navigate(`/test-plans/${res.plan.id}`);
    } catch (err) {
      setError(formatApiError(err, 'Could not create the plan.'));
    } finally {
      setSubmitting(false);
    }
  };

  const shownIps = sampleIps.slice(0, 8);
  const moreIps = Math.max(count - shownIps.length, 0);

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Test plan from selection</DialogTitle>
          <DialogDescription>
            The selection is taken as a fixed list now. Changing the Hosts filters later will not change
            the plan.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-md">
          {/* Targets */}
          <div className="rounded-panel border border-border p-sm">
            <p className="text-metadata font-semibold">
              {resolving ? (
                <span className="inline-flex items-center gap-xs">
                  <Loader2 className="size-3.5 animate-spin" aria-hidden /> Resolving selection…
                </span>
              ) : (
                <>
                  {count.toLocaleString()} host{count === 1 ? '' : 's'}{' '}
                  <span className="font-normal text-muted-foreground">· {selectionSummary}</span>
                </>
              )}
            </p>
            {shownIps.length > 0 && (
              <p className="mt-xxs break-words font-mono text-caption text-muted-foreground">
                {shownIps.join(', ')}
                {moreIps > 0 && ` and ${moreIps.toLocaleString()} more`}
              </p>
            )}
            {previewError && (
              <p className="mt-xxs text-caption text-destructive break-words">{previewError}</p>
            )}
            {preview && (
              <ul className="mt-xs flex flex-col gap-xxs text-caption" aria-label="What will happen">
                <li>
                  <strong>{preview.added.toLocaleString()}</strong> entr{preview.added === 1 ? 'y' : 'ies'} will be
                  added{mode === 'existing' ? ' to the draft' : ' to the new plan'}.
                </li>
                {preview.already_in_plan > 0 && (
                  <li className="text-muted-foreground">
                    {preview.already_in_plan.toLocaleString()} already in that draft — skipped.
                  </li>
                )}
                {preview.not_in_project > 0 && (
                  <li className="text-muted-foreground">
                    {preview.not_in_project.toLocaleString()} not in this project — excluded.
                  </li>
                )}
                {preview.planned_elsewhere > 0 && (
                  <li className="text-warning">
                    {preview.planned_elsewhere.toLocaleString()} already carried by an approved, running or completed
                    plan — check before duplicating that work.
                  </li>
                )}
              </ul>
            )}
          </div>

          {/* Where it goes */}
          <RadioGroup value={mode} onValueChange={(v) => setMode(v as Mode)} className="flex flex-col gap-xs">
            <label className="flex items-start gap-xs text-metadata">
              <RadioGroupItem value="new" id="pfs-new" className="mt-px" />
              <span>
                <span className="font-semibold">New draft plan</span>
                <span className="block text-caption text-muted-foreground">
                  One entry per host; you write the tests on the plan.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-xs text-metadata">
              <RadioGroupItem value="existing" id="pfs-existing" className="mt-px" disabled={drafts.length === 0} />
              <span>
                <span className="font-semibold">Add to an existing draft</span>
                <span className="block text-caption text-muted-foreground">
                  {drafts.length === 0 ? 'No draft plans in this project.' : 'Hosts already in the draft are skipped.'}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-xs text-metadata">
              <RadioGroupItem value="ai" id="pfs-ai" className="mt-px" />
              <span>
                <span className="font-semibold">Generate with AI from these hosts</span>
                <span className="block text-caption text-muted-foreground">
                  Opens the generate dialog with the agent restricted to this list.
                </span>
              </span>
            </label>
          </RadioGroup>

          {mode === 'new' && (
            <div>
              <Label htmlFor="pfs-title">Plan title</Label>
              <Input
                id="pfs-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={200}
                placeholder="e.g. DMZ web tier — admin interfaces"
              />
            </div>
          )}

          {mode === 'existing' && (
            <div>
              <Label htmlFor="pfs-draft">Draft plan</Label>
              <select
                id="pfs-draft"
                className="flex h-9 w-full rounded-control border border-input bg-background px-sm text-metadata"
                value={draftId ?? ''}
                onChange={(e) => setDraftId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Choose a draft…</option>
                {drafts.map((d) => (
                  <option key={d.id} value={d.id}>
                    #{d.id} · {d.title}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <Label htmlFor="pfs-rationale">Why these hosts</Label>
            <Textarea
              id="pfs-rationale"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              maxLength={4096}
              rows={3}
              placeholder="What about this selection is worth testing time. Recorded on every entry."
            />
          </div>

          {mode !== 'ai' && (
            <div className="grid grid-cols-2 gap-sm">
              <div>
                <Label htmlFor="pfs-priority">Entry priority</Label>
                <select
                  id="pfs-priority"
                  className="flex h-9 w-full rounded-control border border-input bg-background px-sm text-metadata"
                  value={priority}
                  onChange={(e) => setPriority(e.target.value as (typeof PRIORITIES)[number])}
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
              <div>
                <Label htmlFor="pfs-phase">Test phase</Label>
                <select
                  id="pfs-phase"
                  className="flex h-9 w-full rounded-control border border-input bg-background px-sm text-metadata"
                  value={phase}
                  onChange={(e) => setPhase(e.target.value as (typeof PHASES)[number])}
                >
                  {PHASES.map((ph) => (
                    <option key={ph} value={ph}>{ph.replace('_', ' ')}</option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!canSubmit}>
            {submitting && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            {mode === 'ai' ? 'Continue to generate' : mode === 'existing' ? 'Add to draft' : 'Create draft plan'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default PlanFromSelectionDialog;
