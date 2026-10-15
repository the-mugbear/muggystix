/**
 * Prepare one contact's remediation list as a document (5.342.0): the
 * findings assigned to them in ONE project, printed as the penetration test
 * report prints a finding (description, impact, evidence, recommendation)
 * with the deadline on every affected system — rendered on the report worker
 * from the `contact-report` template, a copy of the report's that admins
 * restyle on the Reports page.
 *
 * Choose what it lists and Word or HTML — and, across projects, which
 * project when the contact has work in several (a document is one
 * project's) — prepare, then download.  The document is made on
 * demand and is not kept: its file expires like any export, and preparing it
 * is recorded on each of the contact's hosts' timelines.
 */
import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Download, Loader2 } from 'lucide-react';

import {
  downloadContactReport, getContactReport, prepareContactReport, type ContactReportFormat, type ContactReportJob,
  type RemediationMount,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useJobPoll } from '../../hooks/useJobPoll';
import { NO_PROJECT, useProjectId } from '../../hooks/useProjectId';
import { HORIZONS } from './RemediationFollowUpDialog';
import { formatApiError } from '../../utils/apiErrors';
import { saveBlob } from '../../utils/download';
import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

const FORMATS: Array<{ value: ContactReportFormat; label: string }> = [
  { value: 'contact-docx', label: 'Word (.docx)' },
  { value: 'contact-html', label: 'HTML (one file)' },
];
// The worker's statuses, typed: this set once held 'running', which the server
// never sends, so a poll that landed mid-render read as a failure (5.346.0).
const WAITING = new Set<ContactReportJob['status']>(['queued', 'processing']);
const isWaiting = (job: ContactReportJob): boolean => WAITING.has(job.status);
/** How often a job still on the worker is asked about. */
const POLL_MS = 2000;

/** What the document lists: everything still open, or one of the reminder's
 *  horizons (`upcoming_days`). */
const EVERYTHING = 'open';
const SELECTIONS: Array<{ value: string; label: string; days?: number }> = [
  { value: EVERYTHING, label: 'Everything still open' },
  ...HORIZONS.map((h) => ({ value: String(h.days), label: h.label, days: h.days })),
];

export const RemediationContactReportDialog: React.FC<{
  contactEmail: string;
  contactName?: string | null;
  /** The cross-project page: the projects the document can be for — the
   *  chosen one alone, or every project where the contact still has work.  A
   *  document is ONE project's, so with several the dialog asks which. */
  projects?: Array<{ project_id: number; name: string }>;
  onClose: () => void;
}> = ({ contactEmail, contactName, projects, onClose }) => {
  const toast = useToast();
  // Given projects (the cross-project page): the chosen one, through the
  // overview mount, which also serves an archived project.  Not given: the
  // project on screen, through its own routes.
  const currentProjectId = useProjectId();
  const across = projects != null;
  const [chosen, setChosen] = useState<number | null>(projects?.length === 1 ? projects[0].project_id : null);
  const project = across ? projects.find((p) => p.project_id === chosen) ?? null : null;
  const projectId = across ? project?.project_id ?? null : currentProjectId;
  const mount: RemediationMount | undefined = across ? 'overview' : undefined;
  const [format, setFormat] = useState<ContactReportFormat>('contact-docx');
  const [selection, setSelection] = useState(EVERYTHING);
  const upcomingDays = SELECTIONS.find((s) => s.value === selection)?.days;
  // The job this dialog follows, as the worker took it.
  const [started, setStarted] = useState<ContactReportJob | null>(null);

  // Asked about every two seconds while it is queued or rendering, starting
  // from what the request answered; a finished job is not asked about again.
  // A missed poll is tried again; a job that is gone shows on the next one.
  // No job exists before a project is chosen, so nothing is asked of "no project".
  const asked = projectId ?? NO_PROJECT;
  const { job, running: waiting, error: pollError } = useJobPoll({
    queryKey: ['getContactReport', asked, started?.id, mount],
    queryFn: (of, signal) => getContactReport(asked, of.id, mount, signal),
    job: started,
    interval: POLL_MS,
    isDone: (read) => !isWaiting(read),
  });

  const preparing = useMutation({
    mutationFn: () => prepareContactReport(
      asked,
      { contact_email: contactEmail, format, ...(upcomingDays != null ? { upcoming_days: upcomingDays } : {}) },
      mount,
    ),
    onSuccess: (job) => setStarted(job),
    onError: (err) => toast.error(formatApiError(err, 'The list could not be prepared.')),
  });
  const starting = preparing.isPending;

  const downloading = useMutation({
    mutationFn: async (ready: ContactReportJob) =>
      saveBlob(await downloadContactReport(asked, ready.id, mount), ready.filename ?? 'remediation-list'),
    onError: (err) => toast.error(formatApiError(err, 'The document could not be downloaded.')),
  });
  const saving = downloading.isPending;

  const who = contactName ? `${contactName} (${contactEmail})` : contactEmail;
  // Held while a document is being asked for or prepared — the FIRST one
  // included, which has no job yet (second review 2026-10-14, nitpick: the
  // choices could be changed while it was being asked for).
  const locked = starting || (job != null && waiting);
  const where = !across ? ' in this project' : project ? ` in ${project.name}` : ' in one project';
  const missing = job?.ready ? job.without_recommendation ?? 0 : 0;
  // Finished without a file to download: the render failed (the worker says
  // why), or it completed and its file is gone (expired, or not readable here).
  const gone = job?.status === 'completed' && !job.ready;
  const failed = job != null && !waiting && !job.ready;

  return (
    <Dialog open onOpenChange={(v) => { if (!v && !starting) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="break-words">Remediation list for {who}</DialogTitle>
          <DialogDescription>
            The findings assigned to this contact{where}, most urgent first, in the penetration test report’s
            format: each with its description, impact, evidence and recommendation, and the deadline on every
            affected system. Those reported fixed are counted, not listed.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="grid grid-cols-1 gap-sm">
            {across && projects.length > 1 && (
              <div className="min-w-0">
                <Label htmlFor="rem-report-project">Project</Label>
                <Select value={chosen != null ? String(chosen) : ''} disabled={locked}
                  onValueChange={(v) => { setChosen(Number(v)); setStarted(null); }}>
                  <SelectTrigger id="rem-report-project" aria-describedby="rem-report-project-hint">
                    <SelectValue placeholder="Choose a project" />
                  </SelectTrigger>
                  <SelectContent>
                    {projects.map((p) => <SelectItem key={p.project_id} value={String(p.project_id)}>{p.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                <p id="rem-report-project-hint" className="mt-xxs text-caption text-muted-foreground">
                  A document is one project’s. This contact has open findings in {projects.length.toLocaleString()} projects:
                  prepare one for each.
                </p>
              </div>
            )}
            <div className="min-w-0">
              <Label htmlFor="rem-report-selection">What it lists</Label>
              <Select value={selection} disabled={locked}
                onValueChange={(v) => { setSelection(v); setStarted(null); }}>
                <SelectTrigger id="rem-report-selection"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SELECTIONS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="max-w-xs">
              <Label htmlFor="rem-report-format">Format</Label>
              <Select value={format} disabled={locked}
                onValueChange={(v) => { setFormat(v as ContactReportFormat); setStarted(null); }}>
                <SelectTrigger id="rem-report-format"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {FORMATS.map((f) => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="mt-sm min-h-6 text-metadata" aria-live="polite">
            {waiting && (
              <p className="flex items-center gap-xs text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                {job?.status === 'queued' ? 'Waiting for the report worker…' : 'Preparing the document…'}
              </p>
            )}
            {/* The last reading stays; the reader is told it may be old (as the
                inventory download does), and it is asked again by itself. */}
            {waiting && pollError != null && (
              <p role="status" className="break-words text-caption text-warning">
                Status may be stale — {formatApiError(pollError, 'Could not refresh the status.')}
              </p>
            )}
            {job?.ready && <p className="break-words">Ready: <span className="font-medium">{job.filename}</span></p>}
            {job?.ready && job.images_withheld > 0 && (
              <p className="text-muted-foreground">
                {job.images_withheld === 1
                  ? '1 image was left out because its finding also affects other contacts’ systems.'
                  : `${job.images_withheld.toLocaleString()} images were left out because their findings also affect other contacts’ systems.`}
              </p>
            )}
            {/* Said before it is sent: the document tells its reader to ask
                for a recommendation nobody has written. */}
            {missing > 0 && (
              <p role="status" className="break-words text-warning">
                {missing === (job?.findings ?? 0) && missing > 1
                  ? `None of its ${missing.toLocaleString()} findings has a recommendation written`
                  : `${missing.toLocaleString()} of its ${(job?.findings ?? missing).toLocaleString()} ${(job?.findings ?? missing) === 1 ? 'finding has' : 'findings have'} no recommendation written`}
                : the document tells the contact to ask the assessment team. Write {missing === 1 ? 'it' : 'them'} on
                the finding{missing === 1 ? '' : 's'} and prepare the document again.
              </p>
            )}
            {failed && (
              <p role="alert" className="break-words text-destructive">
                {gone
                  ? 'The document was prepared, but its file is no longer available. Prepare it again.'
                  : `The document could not be prepared: ${job?.error || 'the report worker gave no reason'}.`}
              </p>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={starting}>Close</Button>
          {job?.ready ? (
            <Button onClick={() => downloading.mutate(job)} disabled={saving}>
              {saving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Download className="size-4" aria-hidden />} Download
            </Button>
          ) : (
            <Button onClick={() => preparing.mutate()} disabled={starting || waiting || projectId == null}>
              {(starting || waiting) && <Loader2 className="size-4 animate-spin" aria-hidden />}
              {failed ? 'Try again' : 'Prepare'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationContactReportDialog;
