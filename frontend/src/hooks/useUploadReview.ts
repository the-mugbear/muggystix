/**
 * The upload review step (v5.229.0; phase C of the staged-import plan).
 *
 * Choose → review formats and options → import → results.  Each dropped
 * file is uploaded as a STAGED job (on disk, not queued), its detection is
 * fetched, and the row lands in one of two places: *ready* (one format
 * recognised by structure) or *choose* (nothing recognised, filename only,
 * or several formats match).  Import starts the ready rows and any row the
 * operator resolved; the rest stay staged (they expire in a day).  Results
 * are the page's banner, which follows each started job by id.
 *
 * The hook owns the state machine and takes its API as an injectable
 * dependency so the logic is testable without a dropzone or a module mock.
 */
import { useCallback, useMemo, useRef, useState } from 'react';

import { createScanBatch, getJobDetection, startIngestionJob, uploadFile } from '../services/api';
import type { DetectionResponse, UploadOptions } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { duplicateUploadOf, type DuplicateUpload } from '../utils/duplicateUpload';

export type ReviewPhase =
  | 'uploading'
  | 'detecting'
  | 'ready'
  | 'choose'
  | 'starting'
  | 'started'
  | 'duplicate'
  | 'error';

export interface ReviewRow {
  key: string;
  file: File;
  filename: string;
  size: number;
  phase: ReviewPhase;
  percent: number;
  jobId?: number;
  batchId?: number;
  detection?: DetectionResponse;
  /** The format the row will be imported as; null = let detection decide. */
  chosen: string | null;
  sourceTool: string;
  duplicate?: DuplicateUpload;
  error?: string;
}

export interface StartedUpload {
  key: string;
  filename: string;
  jobId: number;
  batchId?: number;
  startedAt: number;
}

export interface UploadReviewDeps {
  uploadFile: typeof uploadFile;
  getJobDetection: typeof getJobDetection;
  startIngestionJob: typeof startIngestionJob;
  createScanBatch: typeof createScanBatch;
}

const DEFAULT_DEPS: UploadReviewDeps = { uploadFile, getJobDetection, startIngestionJob, createScanBatch };

export interface UseUploadReviewOptions {
  skipInformational: boolean;
  onStarted: (started: StartedUpload) => void;
  deps?: Partial<UploadReviewDeps>;
}

/** The override to send for a row: an explicit choice on a row that needed
 *  one, or a change away from what detection would have picked. */
export const overrideFor = (row: Pick<ReviewRow, 'phase' | 'chosen' | 'detection'>): string | null => {
  if (!row.chosen) return null;
  if (row.detection?.needs_choice) return row.chosen;
  return row.chosen === row.detection?.primary ? null : row.chosen;
};

export const isImportable = (row: ReviewRow): boolean =>
  row.phase === 'ready' || (row.phase === 'choose' && !!row.chosen);

export function useUploadReview({ skipInformational, onStarted, deps }: UseUploadReviewOptions) {
  const api = useMemo<UploadReviewDeps>(() => ({ ...DEFAULT_DEPS, ...(deps ?? {}) }), [deps]);
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const startedAtRef = useRef<number>(Date.now());

  const patch = useCallback((key: string, change: Partial<ReviewRow> | ((r: ReviewRow) => Partial<ReviewRow>)) => {
    setRows((prev) =>
      prev.map((r) => (r.key === key ? { ...r, ...(typeof change === 'function' ? change(r) : change) } : r)),
    );
  }, []);

  const stageOne = useCallback(
    async (row: ReviewRow, options: UploadOptions) => {
      try {
        const res = await api.uploadFile(
          row.file,
          (percent) => patch(row.key, { percent }),
          { ...options, stage: true, skipInformational },
        );
        patch(row.key, { phase: 'detecting', percent: 100, jobId: res.job_id });
        try {
          const detection = await api.getJobDetection(res.job_id);
          patch(row.key, {
            detection,
            chosen: detection.primary,
            phase: detection.needs_choice ? 'choose' : 'ready',
          });
        } catch (err) {
          // Detection is advice; the operator can still choose a format.
          patch(row.key, { phase: 'choose', error: formatApiError(err, 'Could not inspect the file.') });
        }
      } catch (err) {
        const duplicate = duplicateUploadOf(err);
        patch(row.key, {
          phase: duplicate ? 'duplicate' : 'error',
          duplicate: duplicate ?? undefined,
          error: duplicate ? undefined : formatApiError(err, 'Upload failed'),
        });
      }
    },
    [api, patch, skipInformational],
  );

  const addFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      const startedAt = Date.now();
      startedAtRef.current = startedAt;
      const fresh: ReviewRow[] = files.map((file) => ({
        key: `${startedAt}-${Math.random().toString(36).slice(2, 8)}-${file.name}`,
        file,
        filename: file.name,
        size: file.size,
        phase: 'uploading',
        percent: 0,
        chosen: null,
        sourceTool: '',
      }));
      setRows((prev) => [...prev, ...fresh]);
      // Several files dropped together are one batch on /scans; if the batch
      // cannot be created the files are still staged, ungrouped.
      let batchId: number | undefined;
      if (files.length > 1) {
        try {
          batchId = (await api.createScanBatch(`${files.length} files · ${new Date(startedAt).toLocaleString()}`)).id;
        } catch (err) {
          console.error('Could not start an upload batch; staging ungrouped:', err);
        }
      }
      if (batchId != null) setRows((prev) => prev.map((r) => (fresh.some((f) => f.key === r.key) ? { ...r, batchId } : r)));
      await Promise.allSettled(fresh.map((row) => stageOne({ ...row, batchId }, { batchId })));
    },
    [api, stageOne],
  );

  const importAgain = useCallback(
    (key: string) => {
      const row = rows.find((r) => r.key === key);
      if (!row) return;
      patch(key, { phase: 'uploading', percent: 0, duplicate: undefined, error: undefined });
      void stageOne(row, { batchId: row.batchId, allowDuplicate: true });
    },
    [rows, patch, stageOne],
  );

  const setChoice = useCallback((key: string, fileType: string | null) => patch(key, { chosen: fileType }), [patch]);
  const setSourceTool = useCallback((key: string, value: string) => patch(key, { sourceTool: value }), [patch]);
  const remove = useCallback((key: string) => setRows((prev) => prev.filter((r) => r.key !== key)), []);

  const importOne = useCallback(
    async (row: ReviewRow) => {
      if (row.jobId == null || !isImportable(row)) return;
      patch(row.key, { phase: 'starting', error: undefined });
      try {
        await api.startIngestionJob(row.jobId, {
          formatOverride: overrideFor(row),
          sourceTool: row.sourceTool.trim() || null,
        });
        patch(row.key, { phase: 'started' });
        onStarted({
          key: row.key,
          filename: row.filename,
          jobId: row.jobId,
          batchId: row.batchId,
          startedAt: startedAtRef.current,
        });
      } catch (err) {
        patch(row.key, {
          phase: row.detection?.needs_choice ? 'choose' : 'ready',
          error: formatApiError(err, 'Could not start the import.'),
        });
      }
    },
    [api, patch, onStarted],
  );

  const importReady = useCallback(async () => {
    const targets = rows.filter(isImportable);
    await Promise.allSettled(targets.map((row) => importOne(row)));
  }, [rows, importOne]);

  const readyCount = rows.filter(isImportable).length;
  const chooseCount = rows.filter((r) => r.phase === 'choose' && !r.chosen).length;
  const allStarted = rows.length > 0 && rows.every((r) => r.phase === 'started');
  const busy = rows.some((r) => r.phase === 'uploading' || r.phase === 'detecting' || r.phase === 'starting');

  return {
    rows,
    addFiles,
    importAgain,
    importOne,
    importReady,
    setChoice,
    setSourceTool,
    remove,
    readyCount,
    chooseCount,
    allStarted,
    busy,
  };
}
