/**
 * /findings/:id — the canonical finding workspace.
 *
 * Closes the multi-host dead-end the list view had (host #1 + "+N" in a
 * tooltip): here every affected host is listed with its own disposition and
 * a link, the evidence thread is one click away, the disposition history is
 * inline, and status/owner are editable in place. The place My Work,
 * reports, and notifications link a finding to.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, ExternalLink, Loader2, Trash2 } from 'lucide-react';

import {
  Finding,
  FindingSeverity,
  FindingStatus,
  FindingStatusHistoryEntry,
  Annotation,
  getFinding,
  getFindingHistory,
  setFindingStatus,
  updateFinding,
  removeFindingHost,
  addFindingHosts,
  getHostNotes,
} from '../services/api';
import NoteAttachments from '../components/host-inspector/NoteAttachments';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';
import { DetailSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog';
import { Textarea } from '../components/ui/textarea';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { safeFallback } from '../utils/uiStyles';
import { STATUS_LABEL, TERMINAL_STATUSES } from '../utils/findingStatus';

const SEVERITY_VARIANT: Record<FindingSeverity, string> = {
  critical: 'severity-critical', high: 'severity-high', medium: 'severity-medium',
  low: 'severity-low', info: 'muted',
};
// Severity is an editable attribute (not a lifecycle transition, so it isn't
// in the status history) — surfaced here so a mis-set severity from
// promotion (e.g. medium that should be low) can be reclassified in place.
const SEVERITY_LABEL: Record<FindingSeverity, string> = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info',
};
const histLabel = (s: string | null) => (s ? STATUS_LABEL[s as FindingStatus] ?? s : '—');

const FindingDetail: React.FC = () => {
  const { findingId } = useParams<{ findingId: string }>();
  const id = Number(findingId);
  const toast = useToast();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  // Findings routes admit viewers (read-only); analyst+ may dispose/detach.
  // Gate the write affordances so viewers see history without 403-bait controls.
  const canManage = hasPermission('analyst');
  const [confirmDialog, confirm] = useConfirm();

  const [finding, setFinding] = useState<Finding | null>(null);
  const [history, setHistory] = useState<FindingStatusHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Terminal-disposition "why" prompt — mirrors the /findings list so a
  // status change on this page also captures the audit rationale.
  const [summaryPrompt, setSummaryPrompt] = useState<{ status: FindingStatus } | null>(null);
  const [summaryText, setSummaryText] = useState('');
  // The note thread this finding was promoted from — body + image evidence,
  // shown inline (the page previously only linked out to it).
  const [evidenceThread, setEvidenceThread] = useState<Annotation[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [f, h] = await Promise.all([getFinding(id), getFindingHistory(id)]);
      setFinding(f);
      setHistory(h);
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Failed to load finding.'));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const evidenceHref = useMemo(() => {
    if (!finding || finding.source !== 'note' || !finding.evidence_annotation_id) return null;
    const host = finding.hosts[0];
    return host ? `/hosts/${host.host_id}#note-${finding.evidence_annotation_id}` : null;
  }, [finding]);

  const evidenceHostId = finding?.hosts[0]?.host_id ?? null;

  // Fetch the source note thread (root + replies, with image attachments) so
  // the finding shows the actual evidence inline, not just a link out.
  useEffect(() => {
    const rootId = finding?.evidence_annotation_id;
    if (!finding || finding.source !== 'note' || !rootId || !evidenceHostId) {
      setEvidenceThread([]);
      return;
    }
    let cancelled = false;
    getHostNotes(evidenceHostId)
      .then((notes) => {
        if (cancelled) return;
        // Walk the subtree from the root note via parent_id links.
        const inThread = new Set<number>([rootId]);
        let changed = true;
        while (changed) {
          changed = false;
          for (const n of notes) {
            if (!inThread.has(n.id) && n.parent_id != null && inThread.has(n.parent_id)) {
              inThread.add(n.id);
              changed = true;
            }
          }
        }
        setEvidenceThread(notes.filter((n) => inThread.has(n.id)).sort((a, b) => a.id - b.id));
      })
      .catch(() => {
        if (!cancelled) setEvidenceThread([]);
      });
    return () => {
      cancelled = true;
    };
  }, [finding, evidenceHostId]);

  const applyStatus = async (status: FindingStatus, summary?: string) => {
    if (!finding) return;
    try {
      await setFindingStatus(finding.id, status, summary);
      await load(); // refresh status + history trail together
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update status.'));
    }
  };

  const handleStatus = (status: FindingStatus) => {
    if (!finding || status === finding.status) return;
    // Terminal dispositions get the same "why" prompt the /findings list
    // shows — the summary is the audit rationale on the history trail.
    // Defer the open a tick so the modal doesn't race the Radix Select's
    // dismiss layer (which can otherwise leave body pointer-events:none).
    if (TERMINAL_STATUSES.has(status)) {
      setSummaryText('');
      setTimeout(() => setSummaryPrompt({ status }), 0);
    } else {
      void applyStatus(status);
    }
  };

  const handleSeverity = async (severity: FindingSeverity) => {
    if (!finding || severity === finding.severity) return;
    try {
      await updateFinding(finding.id, { severity });
      await load(); // refresh so the headline badge + rollups reflect the change
      toast.success(`Severity reclassified to ${SEVERITY_LABEL[severity]}.`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update severity.'));
    }
  };

  const handleRemoveHost = async (hostId: number) => {
    if (!finding) return;
    const host = finding.hosts.find((h) => h.host_id === hostId);
    const label = host?.ip_address || host?.hostname || `Host ${hostId}`;
    // Detaching deletes the finding↔host link (no in-place re-add UI), so
    // confirm first and offer an immediate Undo via addFindingHosts.
    const ok = await confirm({
      title: 'Detach host from finding?',
      body: `"${label}" will be removed from this finding.`,
      resourceName: label,
      severity: 'danger',
      confirmLabel: 'Detach',
    });
    if (!ok) return;
    try {
      const updated = await removeFindingHost(finding.id, hostId);
      setFinding(updated);
      toast.success(`Detached ${label}.`, {
        action: {
          label: 'Undo',
          onClick: () => {
            addFindingHosts(finding.id, [hostId])
              .then((reverted) => { setFinding(reverted); toast.success(`Re-attached ${label}.`); })
              .catch((err) => toast.error(formatApiError(err, 'Failed to undo detach.')));
          },
        },
      });
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to remove host.'));
    }
  };

  if (loading) return <DetailSkeleton />;
  if (error || !finding) {
    return (
      <div className="p-md md:p-lg">
        <Button variant="ghost" size="sm" onClick={() => navigate('/findings')}>
          <ArrowLeft className="size-4" aria-hidden /> Findings
        </Button>
        <p className="mt-md text-destructive">{error || 'Finding not found.'}</p>
      </div>
    );
  }

  return (
    <div className="p-md md:p-lg">
      <Button variant="ghost" size="sm" onClick={() => navigate('/findings')} className="mb-sm">
        <ArrowLeft className="size-4" aria-hidden /> Findings
      </Button>

      <div className="mb-md flex flex-wrap items-start gap-sm">
        <Badge variant={SEVERITY_VARIANT[finding.severity] as never}>
          {finding.severity[0].toUpperCase() + finding.severity.slice(1)}
        </Badge>
        <h1 className="min-w-0 flex-1 break-words text-page-title font-semibold">{finding.title}</h1>
      </div>

      <div className="mb-md flex flex-wrap items-center gap-md text-metadata">
        <div className="flex items-center gap-xs">
          <span className="text-muted-foreground">Status</span>
          {canManage ? (
            <Select value={finding.status} onValueChange={(v) => handleStatus(v as FindingStatus)}>
              <SelectTrigger className="h-7 w-[10rem] text-caption" aria-label="Finding status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Badge variant="muted">{STATUS_LABEL[finding.status]}</Badge>
          )}
        </div>
        <div className="flex items-center gap-xs">
          <span className="text-muted-foreground">Severity</span>
          {canManage ? (
            <Select value={finding.severity} onValueChange={(v) => handleSeverity(v as FindingSeverity)}>
              <SelectTrigger className="h-7 w-[8rem] text-caption" aria-label="Finding severity">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(SEVERITY_LABEL) as FindingSeverity[]).map((s) => (
                  <SelectItem key={s} value={s}>{SEVERITY_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Badge variant="muted">{SEVERITY_LABEL[finding.severity]}</Badge>
          )}
        </div>
        <span><span className="text-muted-foreground">Owner</span> {safeFallback(finding.owner_name, 'Unassigned')}</span>
        <span><span className="text-muted-foreground">Source</span> {finding.source}</span>
        {evidenceHref && (
          <Link to={evidenceHref} className="inline-flex items-center gap-xxs text-info hover:underline">
            Evidence thread <ExternalLink className="size-3" aria-hidden />
          </Link>
        )}
      </div>

      {evidenceThread.length > 0 && (
        <Card className="mb-md">
          <CardHeader><CardTitle>Evidence note</CardTitle></CardHeader>
          <CardContent className="space-y-md">
            {evidenceThread.map((note) => (
              <div key={note.id} className={note.parent_id ? 'border-l-2 border-border pl-sm' : ''}>
                <div className="mb-xxs flex flex-wrap items-center gap-xs">
                  <span className="text-metadata font-semibold text-foreground">
                    {note.author_name || 'Unknown analyst'}
                  </span>
                  <span className="text-caption text-muted-foreground">
                    {new Date(note.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="whitespace-pre-wrap text-body">{note.body}</p>
                {note.attachments && note.attachments.length > 0 && evidenceHostId && (
                  <NoteAttachments
                    hostId={evidenceHostId}
                    noteId={note.id}
                    attachments={note.attachments}
                    canManage={false}
                    onChanged={() => {}}
                  />
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card className="mb-md">
        <CardHeader><CardTitle>Affected hosts ({finding.host_count})</CardTitle></CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table className="table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead>Host</TableHead>
                  <TableHead className="w-16" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {finding.hosts.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={2} className="py-lg text-center text-muted-foreground">
                      No hosts attached.
                    </TableCell>
                  </TableRow>
                ) : (
                  finding.hosts.map((h) => (
                    <TableRow key={h.host_id}>
                      <TableCell className="truncate">
                        <Link to={`/hosts/${h.host_id}`} className="font-mono text-info hover:underline">
                          {h.ip_address || `Host ${h.host_id}`}
                        </Link>
                        {h.hostname && <span className="ml-xs text-caption text-muted-foreground">{h.hostname}</span>}
                      </TableCell>
                      <TableCell>
                        {canManage && (
                          <Button
                            variant="ghost" size="icon"
                            onClick={() => handleRemoveHost(h.host_id)}
                            aria-label={`Detach ${h.ip_address || h.host_id} from finding`}
                          >
                            <Trash2 className="size-4" aria-hidden />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Disposition history</CardTitle></CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <p className="text-caption text-muted-foreground">No status changes recorded yet.</p>
          ) : (
            <ul className="flex flex-col gap-sm">
              {history.map((r) => (
                <li key={r.id} className="border-l-2 border-border pl-sm">
                  <div className="text-metadata">
                    <span className="text-muted-foreground">{histLabel(r.from_status)}</span>
                    {' → '}<span className="font-medium">{histLabel(r.to_status)}</span>
                  </div>
                  <div className="text-caption text-muted-foreground">
                    {safeFallback(r.changed_by_name, 'Unknown')} · {new Date(r.created_at).toLocaleString()}
                  </div>
                  {r.summary && <p className="mt-xxs whitespace-pre-wrap text-caption">{r.summary}</p>}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Terminal-disposition "why" prompt — same policy as the /findings
          list; the summary lands on the finding's disposition history. */}
      <Dialog open={summaryPrompt !== null} onOpenChange={(v) => { if (!v) setSummaryPrompt(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark {summaryPrompt ? STATUS_LABEL[summaryPrompt.status] : ''}</DialogTitle>
            <DialogDescription>
              Optionally record why — this is kept on the finding's disposition history.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            rows={3}
            autoFocus
            placeholder="e.g. confirmed false positive — scanner flagged the backport, not the CVE"
            value={summaryText}
            onChange={(e) => setSummaryText(e.target.value)}
            aria-label="Disposition reason"
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                const p = summaryPrompt;
                setSummaryPrompt(null);
                if (p) void applyStatus(p.status);
              }}
            >
              Skip
            </Button>
            <Button
              onClick={() => {
                const p = summaryPrompt;
                setSummaryPrompt(null);
                if (p) void applyStatus(p.status, summaryText.trim() || undefined);
              }}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {confirmDialog}
    </div>
  );
};

export default FindingDetail;
