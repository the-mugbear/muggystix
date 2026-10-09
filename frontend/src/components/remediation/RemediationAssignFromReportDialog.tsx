/**
 * Start the clock from a report: give every finding on a host that an ISSUED
 * report lists, is still open and has no assigned date, that report's day as
 * its assigned date — so its deadline starts running without anyone typing a
 * date per row.
 *
 * The server decides which rows: this dialog asks it for a dry run and says
 * the answer in plain words BEFORE anything is written; Confirm then runs the
 * same call for real.  A row that already has an assigned date is left alone.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import {
  assignRemediationFromReport, listClientReports, type ClientReport, type RemediationAssignFromReport,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { formatDate } from '../../utils/relativeTime';
import { Button } from '../ui/button';
import {
  Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';

const TITLE_MAX = 70;
const short = (text: string): string => (text.length > TITLE_MAX ? `${text.slice(0, TITLE_MAX - 1)}…` : text);

const reportLabel = (r: ClientReport): string =>
  `#${r.number ?? '?'} · ${short(r.title)} · issued ${formatDate(r.issued_at)}`;

const some = (n: number, one: string, many: string): string => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** The dry run's answer as sentences; the first says what will be written. */
export const assignPreviewSentences = (
  result: Pick<RemediationAssignFromReport, 'assigned' | 'already_assigned' | 'not_open' | 'not_in_list'>,
): string[] => {
  const out = [result.assigned > 0
    ? `${some(result.assigned, 'finding on a host', 'findings on hosts')} will get this assigned date.`
    : 'No finding on a host will get an assigned date.'];
  if (result.already_assigned > 0) {
    out.push(`${some(result.already_assigned, 'already has one and is', 'already have one and are')} left alone.`);
  }
  if (result.not_open > 0) out.push(`${some(result.not_open, 'is', 'are')} no longer open.`);
  if (result.not_in_list > 0) out.push(`${some(result.not_in_list, 'is', 'are')} no longer in the remediation list.`);
  return out;
};

export const RemediationAssignFromReportDialog: React.FC<{
  /** The SERVER's day (the list's `as_of`): an assigned date may not be after it. */
  today?: string;
  onClose: () => void;
  /** Called once the dates were written, so the page re-reads its list in place. */
  onDone: () => void;
}> = ({ today, onClose, onDone }) => {
  const toast = useToast();
  const [reports, setReports] = useState<ClientReport[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reportId, setReportId] = useState<number | null>(null);
  // '' until the dry run has said which day the report was issued on.
  const [date, setDate] = useState('');
  const [preview, setPreview] = useState<RemediationAssignFromReport | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The report + date the preview on screen answers.
  const answered = useRef('');

  useEffect(() => {
    let live = true;
    listClientReports()
      .then((list) => {
        if (!live) return;
        const issued = list.items.filter((r) => r.status === 'issued')
          .sort((a, b) => (b.issued_at ?? '').localeCompare(a.issued_at ?? ''));
        setReports(issued);
        setReportId(issued.find((r) => r.id === list.latest_issued_id)?.id ?? issued[0]?.id ?? null);
      })
      .catch((err) => { if (live) setLoadError(formatApiError(err, 'The reports could not be loaded.')); });
    return () => { live = false; };
  }, []);

  const future = !!today && !!date && date > today;

  useEffect(() => {
    if (reportId == null || future) return undefined;
    const key = `${reportId}|${date}`;
    if (answered.current === key) return undefined;
    let live = true;
    const controller = new AbortController();
    setPreview(null);
    setPreviewError(null);
    assignRemediationFromReport(
      { report_id: reportId, dry_run: true, ...(date ? { assigned_on: date } : {}) }, undefined, controller.signal,
    )
      .then((result) => {
        if (!live) return;
        // With no date sent, the server answers with the report's issue day.
        answered.current = `${reportId}|${date || result.assigned_on}`;
        if (!date) setDate(result.assigned_on);
        setPreview(result);
      })
      .catch((err) => {
        if (!live || controller.signal.aborted) return;
        setPreviewError(formatApiError(err, 'Could not work out what this would change. Nothing was written.'));
      });
    return () => { live = false; controller.abort(); };
  }, [reportId, date, future]);

  // Only a preview of exactly this report and date is shown or confirmed.
  const shown = preview != null && answered.current === `${reportId}|${date}` ? preview : null;
  const current = shown != null;

  const confirm = async () => {
    if (reportId == null || !date || !current || future) return;
    setBusy(true);
    try {
      const result = await assignRemediationFromReport({ report_id: reportId, assigned_on: date });
      toast.success(result.assigned === 0
        ? 'Nothing needed an assigned date.'
        : `Assigned date set on ${some(result.assigned, 'finding on a host', 'findings on hosts')}. Their deadlines are running.`);
      onDone();
      onClose();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not set the assigned dates. Nothing was changed.'));
      setBusy(false);
    }
  };

  const none = reports != null && reports.length === 0;

  return (
    <Dialog open onOpenChange={(v) => { if (!v && !busy) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start the clock from a report</DialogTitle>
          <DialogDescription>
            Findings on hosts the report lists, that are still open and have no assigned date, get one. A deadline counts from it.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {loadError && <p role="alert" className="break-words text-caption text-destructive">{loadError}</p>}
          {reports == null && !loadError && <p className="text-caption text-muted-foreground">Loading…</p>}
          {none && (
            <p className="text-metadata text-muted-foreground">
              This project has no issued report yet. Issue one on{' '}
              <Link to="/reports" className="text-info hover:underline">Reports</Link>, or assign findings one by one.
            </p>
          )}
          {reports != null && !none && (
            <>
              <div className="grid grid-cols-1 gap-sm sm:grid-cols-[minmax(0,1fr)_11rem]">
                <div className="min-w-0">
                  <Label htmlFor="rem-afr-report">Issued report</Label>
                  <Select value={reportId != null ? String(reportId) : undefined} disabled={busy}
                    onValueChange={(v) => { setReportId(Number(v)); setDate(''); setPreview(null); }}>
                    <SelectTrigger id="rem-afr-report" className="w-full min-w-0"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {reports.map((r) => (
                        <SelectItem key={r.id} value={String(r.id)} title={r.title}>{reportLabel(r)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="min-w-0">
                  <Label htmlFor="rem-afr-date">Assigned on</Label>
                  <Input id="rem-afr-date" type="date" value={date} max={today} disabled={busy}
                    onChange={(e) => setDate(e.target.value)} />
                </div>
              </div>
              {future ? (
                <p role="alert" className="mt-sm text-caption text-destructive">The assigned date cannot be in the future.</p>
              ) : previewError ? (
                <p role="alert" className="mt-sm break-words text-caption text-destructive">{previewError}</p>
              ) : shown == null ? (
                <p className="mt-sm text-caption text-muted-foreground">Working out what this would change…</p>
              ) : (
                <p role="status" className="mt-sm break-words text-metadata" data-testid="rem-afr-preview">
                  {assignPreviewSentences(shown).join(' ')}
                </p>
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>{none ? 'Close' : 'Cancel'}</Button>
          {!none && (
            <Button onClick={() => void confirm()}
              disabled={busy || !current || future || !date || shown == null || shown.assigned === 0}>
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Set the assigned date
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationAssignFromReportDialog;
