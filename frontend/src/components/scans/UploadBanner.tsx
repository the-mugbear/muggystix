import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ChevronUp } from 'lucide-react';

import { getIngestionJobsByIds, getScans } from '../../services/api';
import { pollEvery, useLastSettled } from '../../lib/query';
import { importResultsPath } from '../../utils/importResultsPath';
import {
  anyStillRunning, bannerRows, completedScanIds, jobIdsToAsk, mergeFollowed, settledJobs,
  type BannerEntry, type BannerRow, type BannerStatus, type FollowedJobs,
} from '../../utils/uploadBanner';
import { Alert, AlertDescription } from '../ui/alert';
import { Button } from '../ui/button';
import ImportResult from './ImportResult';

export interface UploadBannerState {
  rows: BannerRow[];
  /** A file the upload review started; a key started again takes its row back. */
  start: (entry: BannerEntry) => void;
  dismiss: (key: string) => void;
}

/**
 * The results banner's state: one entry per file the review dialog (or the
 * staged queue's "Review and import") has STARTED, followed by job id to its
 * import result — received → processing → imported | partial | failed.
 *
 * STATE is only what this tab knows and the server does not: which files it
 * started, and which rows the reader dismissed.  A job's status and message
 * and a finished file's result are read by the two queries below and laid
 * over these entries while rendering (`utils/uploadBanner`).
 *
 * `onSettled` runs once for each set of jobs nothing more is expected of,
 * `onCompleted` once for each set of jobs that completed.
 */
export function useUploadBanner(
  projectId: number, onSettled: () => void, onCompleted: () => void,
): UploadBannerState {
  const [uploadEntries, setUploadEntries] = useState<BannerEntry[]>([]);

  // Following the started files is ONE request per tick (`GET
  // /upload/jobs?ids=`): at once for a file just handed over, then every 4 s
  // while the tab is visible and one of them is still running.
  //
  // The key names every job this tab started, dismissed rows included, so it
  // changes only when a file is started.  A REQUEST names fewer: a job this
  // reading already knows to be finished (or no longer returned) is not asked
  // about again — its last reading is carried into the next
  // (`jobIdsToAsk` / `mergeFollowed`).  This query's own lifecycle: once no
  // job is running it is DISABLED, not merely left unpolled, so nothing — an
  // invalidation included — asks again (`Scans.uploadBanner.test.tsx`).
  const bannerJobIds = useMemo(() => Array.from(new Set(uploadEntries.map((e) => e.jobId))), [uploadEntries]);
  // The last settled reading, whichever key it was read under: one more file
  // is a new key with nothing in it yet, and the jobs already known to be
  // finished are still not asked about (review 2026-10-23b, FEC-8).
  const lastReading = useRef<FollowedJobs | undefined>(undefined);
  // eslint-disable-next-line @tanstack/query/exhaustive-deps -- the last reading only spares asking about finished jobs again; the answer is the key's
  const followedQuery = useQuery({
    queryKey: ['getIngestionJobsByIds', projectId, bannerJobIds],
    queryFn: async ({ signal, client, queryKey }): Promise<FollowedJobs> => {
      const known = client.getQueryData<FollowedJobs>(queryKey) ?? lastReading.current;
      const ask = jobIdsToAsk(bannerJobIds, known);
      return mergeFollowed(known, ask, await getIngestionJobsByIds(projectId, ask, signal));
    },
    enabled: (query) => bannerJobIds.length > 0 && anyStillRunning(query.state.data),
    ...pollEvery((query) => (anyStillRunning(query.state.data) ? 4000 : null)),
  });
  // While the read for one more file is on its way — or fails — the files
  // already on the banner keep what was last said about them.
  const followed = useLastSettled(followedQuery.data, { resetKey: projectId });
  useEffect(() => { lastReading.current = followed; }, [followed]);

  // The import results of the finished files (the scan rows' summaries: the
  // same numbers the inventory shows), in ONE request for the files that
  // finished together.  Its own lifecycle too: a set of finished files is
  // asked about ONCE — answered or failed, it is disabled, so neither the
  // banner's poll nor the inventory's refresh (which is by the name
  // `getScans`) asks again.  A lookup that failed leaves the job's own
  // message in place of the counts.
  const resultScanIds = useMemo(() => completedScanIds(followed), [followed]);
  const resultsQuery = useQuery({
    queryKey: ['getScans', projectId, 0, resultScanIds.length, { ids: resultScanIds }],
    queryFn: ({ signal }) => getScans(projectId, 0, resultScanIds.length, { ids: resultScanIds, signal }),
    enabled: (query) => resultScanIds.length > 0 && query.state.status === 'pending',
  });
  const results = useLastSettled(resultsQuery.data, { resetKey: projectId });
  const rows = useMemo(() => bannerRows(uploadEntries, followed, results), [uploadEntries, followed, results]);

  // An import that finished put the queue — and, when it completed, the lists
  // and counts of imported files — out of date.  Keyed by the SET of jobs
  // nothing more is expected of: it changes when a job joins it, so each
  // finish refreshes once however many later polls report the same job.
  const { settled: settledJobIds, completed: completedJobIds } = useMemo(() => settledJobs(followed), [followed]);
  const settledKey = settledJobIds.join(',');
  const completedKey = completedJobIds.join(',');
  useEffect(() => {
    if (settledKey) onSettled();
  }, [settledKey, onSettled]);
  useEffect(() => {
    if (completedKey) onCompleted();
  }, [completedKey, onCompleted]);

  const start = useCallback((entry: BannerEntry) => {
    setUploadEntries((prev) => (prev.some((e) => e.key === entry.key)
      ? prev.map((e) => (e.key === entry.key ? entry : e))
      : [...prev, entry]));
  }, []);
  const dismiss = useCallback((key: string) => {
    setUploadEntries((prev) => prev.map((entry) => (entry.key === key ? { ...entry, dismissed: true } : entry)));
  }, []);
  return { rows, start, dismiss };
}

const STATUS_LABEL: Record<BannerStatus, string> = {
  received: 'Upload received, waiting for the worker',
  processing: 'Processing',
  imported: 'Imported',
  partial: 'Imported with gaps',
  failed: 'Import failed',
};

/** Files the operator has started, followed to their import result —
 *  aria-live so screen readers announce progress when the dialog has closed. */
export const UploadBanner: React.FC<{ banner: UploadBannerState }> = ({ banner }) => {
  const navigate = useNavigate();
  if (banner.rows.length === 0) return null;
  return (
    <div className="mb-sm flex flex-col gap-xs" aria-live="polite" aria-atomic="false">
      {banner.rows.map((p) => {
        const variant =
          p.status === 'failed'
            ? 'destructive'
            : p.status === 'partial'
            ? 'warning'
            : p.status === 'imported'
            ? 'success'
            : 'info';
        const terminal =
          p.status === 'imported' || p.status === 'partial' || p.status === 'failed';
        return (
          <Alert key={p.key} variant={variant}>
            <AlertDescription className="flex flex-col gap-xxs">
              <div className="flex items-baseline justify-between gap-sm">
                <span className="truncate font-semibold">
                  {STATUS_LABEL[p.status]}: {p.filename}
                </span>
                {terminal && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-6 shrink-0"
                    aria-label={`Dismiss ${p.filename} progress`}
                    onClick={() => banner.dismiss(p.key)}
                  >
                    <ChevronUp className="size-3" aria-hidden />
                  </Button>
                )}
              </div>
              {p.status === 'failed' ? (
                <div className="flex flex-wrap items-center gap-xs">
                  <span className="min-w-0 break-words">{p.error || 'Import failed'}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      navigate(
                        p.parseErrorId != null
                          ? importResultsPath({ error_id: p.parseErrorId })
                          : importResultsPath({ job_id: p.jobId }),
                      )
                    }
                  >
                    Why it failed
                  </Button>
                </div>
              ) : p.status === 'imported' || p.status === 'partial' ? (
                p.result ? (
                  <ImportResult scan={p.result} />
                ) : (
                  <span className="text-caption text-muted-foreground">
                    {p.jobMessage || 'Import complete.'}
                  </span>
                )
              ) : (
                <span className="text-caption text-muted-foreground">
                  {p.status === 'received'
                    ? 'The file is stored; parsing starts when a worker picks it up.'
                    : p.jobMessage || 'Parsing…'}
                </span>
              )}
            </AlertDescription>
          </Alert>
        );
      })}
    </div>
  );
};
