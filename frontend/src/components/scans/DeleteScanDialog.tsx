import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertCircle, AlertTriangle, CheckCircle2, Info, Loader2, Trash2 } from 'lucide-react';

import { deleteScan, getScanDeletionImpact } from '../../services/api';
import type { Scan } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useOpeningKey } from '../../hooks/useOpeningKey';
import { useProjectId } from '../../hooks/useProjectId';
import { formatApiError } from '../../utils/apiErrors';
import {
  hostsWithWorkCount,
  hostsWithWorkRefusal,
  importRunningNotice,
  importRunningRefusal,
  type HostsWithWorkRefusal,
} from '../../utils/scanDeletion';
import { Button } from '../ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import HostsWithWorkWarning from './HostsWithWorkWarning';

interface DeleteScanDialogProps {
  /** The scan the dialog is about; it stays set while the dialog closes. */
  scan: Scan | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The scan is gone: the page closes the dialog and reads its lists again. */
  onDeleted: (scan: Scan) => void;
}

/**
 * Deleting one scan (the project admin's): the server's preview of exactly
 * what goes, the hosts that carry people's work, and the delete itself.  Every
 * opening starts clean — the review tick, a refusal — so the body is keyed by
 * its opening.
 */
const DeleteScanDialog: React.FC<DeleteScanDialogProps> = (props) => (
  <DeleteScanDialogBody key={useOpeningKey(props.open)} {...props} />
);

const DeleteScanDialogBody: React.FC<DeleteScanDialogProps> = ({ scan, open, onOpenChange, onDeleted }) => {
  const toast = useToast();
  const projectId = useProjectId();
  // Hosts the delete removes that carry people's work: the reader's tick, and
  // the server's refusal when it found such hosts the dialog had not shown.
  const [workReviewed, setWorkReviewed] = useState(false);
  const [workRefusal, setWorkRefusal] = useState<HostsWithWorkRefusal | null>(null);
  // The server's words when it refused the delete because an import is
  // running in the project; the preview says the same before the click.
  const [importRefusal, setImportRefusal] = useState<string | null>(null);
  // What the delete will remove, asked while the dialog is open — and again
  // after a refusal.  Hosts are deduplicated across scans, so this is rarely a
  // blanket wipe.  Every read un-ticks the review box: a tick is about the
  // list on screen.
  const deleteId = scan?.id ?? null;
  const impactQuery = useQuery({
    queryKey: ['getScanDeletionImpact', projectId, deleteId],
    queryFn: async ({ signal }) => {
      const impact = await getScanDeletionImpact(projectId, deleteId as number, signal);
      // An answer that is not about this scan is not a summary of it.
      if (impact?.scan_id !== deleteId) throw new Error('The removal summary is not about this scan.');
      return impact;
    },
    enabled: open && deleteId != null,
  });
  const impactLoading = impactQuery.isFetching;
  const impactError = !impactLoading && impactQuery.isError;
  // While it is read again the previous answer is not shown: the delete waits.
  const deletionImpact = !impactLoading && !impactQuery.isError ? impactQuery.data ?? null : null;
  const importRunning = importRunningNotice(deletionImpact);
  // Known from the preview; from the refusal when the preview could not be
  // read.  While the preview is loading it is not known, and the delete waits.
  const hostsWithWork = deletionImpact
    ? hostsWithWorkCount(deletionImpact)
    : !impactLoading && workRefusal
      ? workRefusal.hosts_with_work
      : 0;

  const { refetch: reloadImpact } = impactQuery;
  const deletion = useMutation({
    mutationFn: ({ scan: target, confirmed }: { scan: Scan; confirmed: boolean }) =>
      (confirmed ? deleteScan(projectId, target.id, { confirmHostsWithWork: true }) : deleteScan(projectId, target.id)),
    onSuccess: (_result, { scan: target }) => {
      toast.success(`Scan "${target.filename}" deleted.`);
      setWorkRefusal(null);
      onDeleted(target);
    },
    onError: async (err, { scan: target }) => {
      const refusal = hostsWithWorkRefusal(err);
      const importing = importRunningRefusal(err);
      if (importing) {
        // An import is running in the project (or holds its lock).  Nothing
        // was changed: say so in the server's words over a fresh preview,
        // which also says whether the import is still running.
        setImportRefusal(importing);
        setWorkReviewed(false);
        await reloadImpact();
      } else if (refusal) {
        // Hosts this scan removes carry work the reader has not confirmed
        // (added since the preview, or the preview never loaded).  Show the
        // server's words over a fresh list and ask again.
        setWorkRefusal(refusal);
        setWorkReviewed(false);
        await reloadImpact();
      } else {
        // Keep the dialog open on failure so the reader can retry, and say
        // what failed: the row is still in the list.
        toast.error(formatApiError(err, `Failed to delete scan "${target.filename}".`));
      }
    },
  });
  // Busy until the preview has been read again after a refusal.
  const deleteLoading = deletion.isPending;
  const handleDeleteConfirm = () => {
    if (!scan || deleteLoading) return;
    setImportRefusal(null);
    deletion.mutate({ scan, confirmed: workReviewed });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !deleteLoading && onOpenChange(next)}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Scan</DialogTitle>
        </DialogHeader>
        <p className="text-metadata">
          Delete the scan{' '}
          <span className="font-medium break-words">
            &quot;{scan?.filename}&quot;
          </span>
          ? This action cannot be undone.
        </p>

        {/* What gets removed: everything the scan brought that nothing else
            holds. Hosts are deduplicated across scans, so this is usually
            NOT a blanket wipe — hosts another scan also has are kept, and on
            them only what this scan alone created or reported goes. Every
            figure is the server's preview of its own delete. */}
        {impactLoading ? (
          <div className="mt-3 flex items-center gap-2 text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Calculating exactly what will be removed…
          </div>
        ) : impactError ? (
          <div className="mt-3 flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-metadata text-muted-foreground">
            <AlertCircle className="size-4 mt-0.5 shrink-0" aria-hidden />
            <span>
              Couldn&apos;t load the removal summary. The scan and everything
              <em> only</em> it brought will still be removed if you continue.
            </span>
          </div>
        ) : deletionImpact ? (
          <div className="mt-3 rounded-md border border-border p-3">
            <div className="text-metadata font-medium mb-2">
              This will permanently remove:
            </div>
            <ul className="space-y-1.5 text-metadata">
              <li className="flex items-center gap-2">
                <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                This scan record and its scan history
              </li>
              {/* Only what goes is listed: "0 hosts" is not a removal. */}
              {deletionImpact.hosts_removed > 0 && (
                <li className="flex items-baseline gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive self-center" aria-hidden />
                  <span>
                    <span className="font-medium">{deletionImpact.hosts_removed.toLocaleString()}</span>{' '}
                    {deletionImpact.hosts_removed === 1 ? 'host' : 'hosts'} seen only by this scan
                    {deletionImpact.sample_removed_ips.length > 0 && (
                      <span className="block text-muted-foreground break-words">
                        {deletionImpact.sample_removed_ips.join(', ')}
                        {deletionImpact.hosts_removed > deletionImpact.sample_removed_ips.length &&
                          `, +${(
                            deletionImpact.hosts_removed - deletionImpact.sample_removed_ips.length
                          ).toLocaleString()} more`}
                      </span>
                    )}
                  </span>
                </li>
              )}
              {deletionImpact.ports_removed > 0 && (
                <li className="flex items-center gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                  <span>
                    <span className="font-medium">{deletionImpact.ports_removed.toLocaleString()}</span>{' '}
                    {deletionImpact.ports_removed === 1 ? 'port' : 'ports'} on those hosts
                  </span>
                </li>
              )}
              {(deletionImpact.ports_removed_on_kept_hosts ?? 0) > 0 && (
                <li className="flex items-center gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                  <span>
                    <span className="font-medium">
                      {(deletionImpact.ports_removed_on_kept_hosts ?? 0).toLocaleString()}
                    </span>{' '}
                    {deletionImpact.ports_removed_on_kept_hosts === 1 ? 'port' : 'ports'} only this
                    scan found, on hosts that stay
                  </span>
                </li>
              )}
              {/* Observations on the removed hosts go with their host and are
                  not counted here; one another scan also reported stays. */}
              {(deletionImpact.vulnerabilities_removed ?? 0) > 0 && (
                <li className="flex items-center gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                  <span>
                    <span className="font-medium">
                      {deletionImpact.vulnerabilities_removed.toLocaleString()}
                    </span>{' '}
                    scanner{' '}
                    {deletionImpact.vulnerabilities_removed === 1 ? 'observation' : 'observations'}{' '}
                    only this scan reported, on hosts that stay
                  </span>
                </li>
              )}
              {deletionImpact.web_interfaces_removed > 0 && (
                <li className="flex items-center gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                  <span>
                    <span className="font-medium">
                      {deletionImpact.web_interfaces_removed.toLocaleString()}
                    </span>{' '}
                    web {deletionImpact.web_interfaces_removed === 1 ? 'interface' : 'interfaces'}{' '}
                    from this scan
                  </span>
                </li>
              )}
              {(deletionImpact.dns_records_removed ?? 0) > 0 && (
                <li className="flex items-center gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                  <span>
                    <span className="font-medium">
                      {(deletionImpact.dns_records_removed ?? 0).toLocaleString()}
                    </span>{' '}
                    DNS {deletionImpact.dns_records_removed === 1 ? 'record' : 'records'} from this scan
                  </span>
                </li>
              )}
              {(deletionImpact.dns_names_removed ?? 0) > 0 && (
                <li className="flex items-center gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                  <span>
                    <span className="font-medium">
                      {(deletionImpact.dns_names_removed ?? 0).toLocaleString()}
                    </span>{' '}
                    {deletionImpact.dns_names_removed === 1 ? 'name' : 'names'} only this scan observed
                  </span>
                </li>
              )}
            </ul>
            {(deletionImpact.vulnerabilities_kept ?? 0) > 0 && (
              <div
                data-testid="observations-kept"
                className="mt-2.5 flex items-start gap-2 border-t border-border pt-2.5 text-metadata text-muted-foreground"
              >
                <Info className="size-3.5 mt-0.5 shrink-0" aria-hidden />
                <span>
                  <span className="font-medium text-foreground">
                    {(deletionImpact.vulnerabilities_kept ?? 0).toLocaleString()}
                  </span>{' '}
                  scanner{' '}
                  {deletionImpact.vulnerabilities_kept === 1 ? 'observation' : 'observations'} only
                  this scan reported {deletionImpact.vulnerabilities_kept === 1 ? 'is' : 'are'} kept:
                  a finding or a proposal refers to{' '}
                  {deletionImpact.vulnerabilities_kept === 1 ? 'it' : 'them'}.
                </span>
              </div>
            )}
            {deletionImpact.hosts_kept > 0 && (
              <div className="mt-2.5 flex items-start gap-2 border-t border-border pt-2.5 text-metadata text-muted-foreground">
                <CheckCircle2 className="size-3.5 mt-0.5 shrink-0 text-emerald-600" aria-hidden />
                <span>
                  <span className="font-medium text-foreground">
                    {deletionImpact.hosts_kept.toLocaleString()}
                  </span>{' '}
                  {deletionImpact.hosts_kept === 1 ? 'host' : 'hosts'} another scan also has{' '}
                  {deletionImpact.hosts_kept === 1 ? 'is' : 'are'} kept.
                </span>
              </div>
            )}
            {/* The limit of "a wrong scan leaves nothing behind".  A scan
                that touched no host that stays overwrote nothing. */}
            {deletionImpact.hosts_kept > 0 && (
              <div
                data-testid="not-restored"
                className="mt-1.5 flex items-start gap-2 text-metadata text-muted-foreground"
              >
                <AlertCircle className="size-3.5 mt-0.5 shrink-0" aria-hidden />
                <span>
                  What this scan overwrote on hosts, ports and observations that were already
                  there (OS, host name, service, script output, severity) is not restored:
                  BlueStick keeps no previous value.
                </span>
              </div>
            )}
          </div>
        ) : null}

        {!impactLoading && (importRefusal || importRunning) && (
          <div
            role="alert"
            data-testid="import-running"
            className="mt-3 flex min-w-0 items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-metadata"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
            <span className="min-w-0 break-words">{importRefusal ?? importRunning}</span>
          </div>
        )}

        {!impactLoading && (
          <HostsWithWorkWarning
            count={hostsWithWork}
            sample={deletionImpact?.hosts_with_work_sample ?? []}
            message={workRefusal?.message}
            reviewed={workReviewed}
            onReviewedChange={setWorkReviewed}
            disabled={deleteLoading}
          />
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={deleteLoading}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDeleteConfirm}
            disabled={
              deleteLoading || impactLoading || !!importRunning || (hostsWithWork > 0 && !workReviewed)
            }
          >
            {deleteLoading ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Deleting…
              </>
            ) : (
              'Delete'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default DeleteScanDialog;
