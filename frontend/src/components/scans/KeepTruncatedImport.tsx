import React from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { retryIngestionJob } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import { useProjectRole } from '../../hooks/useProjectRole';
import { invalidateReads } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { INGESTION_JOB_READS } from '../../utils/ingestionReads';
import { Button } from '../ui/button';

/** The one sentence under the action: what pressing it does. */
export const KEEP_TRUNCATED_EXPLANATION =
  'The import will stop where the file ends, keep everything before that point, and be marked partial.';

/**
 * "Import what the file holds" — the reader's answer for a failed import
 * whose file was cut short (`utils/truncatedImport.isCutShort`).
 *
 * The failed attempt left nothing in the inventory.  Pressing this queues
 * the same job again with the instruction to stop at the cut: the complete
 * records are imported and the import finishes PARTIAL (so it stays in
 * "needs attention" until someone dismisses it).  The button is the answer —
 * there is no confirmation after it.  Analysts and above; hidden otherwise.
 * Shown only while the uploaded file is still retained: without it there is
 * nothing to read again.
 */
const KeepTruncatedImport: React.FC<{
  jobId: number;
  filename: string;
  fileRetained: boolean;
}> = ({ jobId, filename, fileRetained }) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  const { canWrite } = useProjectRole();
  const keep = useMutation({
    mutationFn: () => retryIngestionJob(projectId, jobId, { keepTruncated: true }),
    onSuccess: () => {
      toast.info(`${filename} queued — importing what the file holds`);
      // The job is queued again: the lists and counts of jobs are out of date.
      return invalidateReads(queryClient, ...INGESTION_JOB_READS);
    },
    // 409: the file is gone, or the same file was imported since (the message says which).
    onError: (err) => toast.error(formatApiError(err, 'Could not start the import')),
  });

  if (!canWrite || !fileRetained) return null;
  return (
    <div className="flex flex-col gap-xxs" data-testid="keep-truncated-import">
      <div>
        <Button
          size="sm"
          disabled={keep.isPending}
          aria-label={`Import what ${filename} holds`}
          onClick={() => keep.mutate()}
        >
          {keep.isPending && <Loader2 className="size-3 animate-spin" aria-hidden />}
          Import what the file holds
        </Button>
      </div>
      <p className="text-caption text-muted-foreground">{KEEP_TRUNCATED_EXPLANATION}</p>
    </div>
  );
};

export default KeepTruncatedImport;
