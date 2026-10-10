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
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import {
  assignRemediationFromReport, listClientReports, type ClientReport, type RemediationAssignFromReport,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { formatDate } from '../../utils/relativeTime';
import { invalidateRemediationReads } from '../../utils/remediation';
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
}> = ({ today, onClose }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  // The project page's dialog: this project's reports, this project's rows.
  const projectId = useProjectId();
  const list = useQuery({
    queryKey: ['listClientReports', projectId], queryFn: ({ signal }) => listClientReports(projectId, signal),
  });
  const loadError = queryErrorText(list.error, 'The reports could not be loaded.');
  const reports = useMemo((): ClientReport[] | null => (list.data
    ? list.data.items.filter((r) => r.status === 'issued')
      .sort((a, b) => (b.issued_at ?? '').localeCompare(a.issued_at ?? ''))
    : null), [list.data]);

  // The report the reader chose; until they choose, the latest issued one.
  const [chosen, setChosen] = useState<number | null>(null);
  const reportId = chosen
    ?? reports?.find((r) => r.id === list.data?.latest_issued_id)?.id ?? reports?.[0]?.id ?? null;
  // The day the reader typed; until they type one, the server's answer.
  const [typed, setTyped] = useState('');
  const future = !!today && !!typed && typed > today;

  // The dry run, keyed by exactly what it asks: only the answer for this
  // report and this date is ever shown or confirmed.  With no date sent, the
  // server answers with the report's issue day.
  const issueDayRun = { report_id: reportId as number, dry_run: true };
  const onIssueDay = useQuery({
    queryKey: ['assignRemediationFromReport', projectId, issueDayRun],
    queryFn: ({ signal }) => assignRemediationFromReport(projectId, issueDayRun, undefined, signal),
    enabled: reportId != null,
  });
  // A typed day that IS the issue day the server answered with is the answer
  // already here: it is not asked for a second time under another key (B30).
  const otherDay = typed && typed !== onIssueDay.data?.assigned_on ? typed : '';
  const otherDayRun = { ...issueDayRun, assigned_on: otherDay };
  const onOtherDay = useQuery({
    queryKey: ['assignRemediationFromReport', projectId, otherDayRun],
    queryFn: ({ signal }) => assignRemediationFromReport(projectId, otherDayRun, undefined, signal),
    enabled: reportId != null && !!otherDay && !future,
  });
  const preview = otherDay ? onOtherDay : onIssueDay;
  const shown = preview.data ?? null;
  const previewError = queryErrorText(preview.error, 'Could not work out what this would change. Nothing was written.');
  // '' until the dry run has said which day the report was issued on.
  const date = typed || shown?.assigned_on || '';

  const assigning = useMutation({
    mutationFn: (body: { report_id: number; assigned_on: string }) => assignRemediationFromReport(projectId, body),
    onSuccess: (result) => {
      toast.success(result.assigned === 0
        ? 'Nothing needed an assigned date.'
        : `Assigned date set on ${some(result.assigned, 'finding on a host', 'findings on hosts')}. Their deadlines are running.`);
      // The list is re-read in place: the reader keeps their page.
      void invalidateRemediationReads(queryClient);
      onClose();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not set the assigned dates. Nothing was changed.')),
  });
  const busy = assigning.isPending;
  const confirm = () => {
    if (reportId == null || !date || shown == null || future) return;
    assigning.mutate({ report_id: reportId, assigned_on: date });
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
                    onValueChange={(v) => { setChosen(Number(v)); setTyped(''); }}>
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
                    onChange={(e) => setTyped(e.target.value)} />
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
            <Button onClick={confirm}
              disabled={busy || future || !date || shown == null || shown.assigned === 0}>
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Set the assigned date
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RemediationAssignFromReportDialog;
