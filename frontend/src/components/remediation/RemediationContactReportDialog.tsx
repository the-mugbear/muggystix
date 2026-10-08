/**
 * Prepare one contact's remediation list as a document (5.342.0): the
 * findings assigned to them in ONE project, printed as the penetration test
 * report prints a finding (description, impact, evidence, recommendation)
 * with the deadline on every affected system — rendered on the report worker
 * from the `contact-report` template, a copy of the report's that admins
 * restyle on the Reports page.
 *
 * Choose Word or HTML, prepare, then download.  The document is made on
 * demand and is not kept: its file expires like any export, and preparing it
 * is recorded on each of the contact's hosts' timelines.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';

import {
  downloadContactReport, getContactReport, prepareContactReport, type ContactReportFormat, type ContactReportJob,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useVisibilityPoll } from '../../hooks/useVisibilityPoll';
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
const WAITING = new Set(['queued', 'running']);

export const RemediationContactReportDialog: React.FC<{
  contactEmail: string;
  contactName?: string | null;
  /** Set on the cross-project page: the project the list is for. */
  projectId?: number;
  projectName?: string;
  onClose: () => void;
  /** After a list was prepared (the hosts' timelines gained an entry). */
  onPrepared?: () => void;
}> = ({ contactEmail, contactName, projectId, projectName, onClose, onPrepared }) => {
  const toast = useToast();
  const [format, setFormat] = useState<ContactReportFormat>('contact-docx');
  const [job, setJob] = useState<ContactReportJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  const waiting = job != null && WAITING.has(job.status);

  useVisibilityPoll(async () => {
    if (!job) return;
    try {
      const next = await getContactReport(job.id, projectId);
      if (live.current) setJob(next);
    } catch {
      // A missed poll is tried again; a job that is gone shows on the next one.
    }
  }, 2000, waiting);

  const prepare = async () => {
    setStarting(true);
    try {
      const started = await prepareContactReport({ contact_email: contactEmail, format }, projectId);
      if (!live.current) return;
      setJob(started);
      onPrepared?.();
    } catch (err) {
      toast.error(formatApiError(err, 'The list could not be prepared.'));
    } finally {
      if (live.current) setStarting(false);
    }
  };

  const download = async () => {
    if (!job) return;
    setSaving(true);
    try {
      saveBlob(await downloadContactReport(job.id, projectId), job.filename ?? 'remediation-list');
    } catch (err) {
      toast.error(formatApiError(err, 'The document could not be downloaded.'));
    } finally {
      if (live.current) setSaving(false);
    }
  };

  const who = contactName ? `${contactName} (${contactEmail})` : contactEmail;
  const failed = job != null && !waiting && !job.ready;

  return (
    <Dialog open onOpenChange={(v) => { if (!v && !starting) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="break-words">Remediation list for {who}</DialogTitle>
          <DialogDescription>
            The findings assigned to this contact{projectName ? ` in ${projectName}` : ' in this project'}, in the
            penetration test report’s format: each with its description, impact, evidence and recommendation, and
            the deadline on every affected system. Closed ones are counted, not listed.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="max-w-xs">
            <Label htmlFor="rem-report-format">Format</Label>
            <Select value={format} disabled={job != null && (waiting || starting)}
              onValueChange={(v) => { setFormat(v as ContactReportFormat); setJob(null); }}>
              <SelectTrigger id="rem-report-format"><SelectValue /></SelectTrigger>
              <SelectContent>
                {FORMATS.map((f) => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="mt-sm min-h-6 text-metadata" aria-live="polite">
            {waiting && (
              <p className="flex items-center gap-xs text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                {job?.status === 'queued' ? 'Waiting for the report worker…' : 'Preparing the document…'}
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
            {failed && (
              <p role="alert" className="break-words text-destructive">
                The document could not be prepared{job?.error ? `: ${job.error}` : '.'}
              </p>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={starting}>Close</Button>
          {job?.ready ? (
            <Button onClick={() => void download()} disabled={saving}>
              {saving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Download className="size-4" aria-hidden />} Download
            </Button>
          ) : (
            <Button onClick={() => void prepare()} disabled={starting || waiting}>
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
