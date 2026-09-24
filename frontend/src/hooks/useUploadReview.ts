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
 * A *choose* row may carry a `suggested` format (what a filename or an
 * ambiguous match points at).  A suggestion is never a choice: `chosen`
 * stays null — and the row stays out of the ready count — until the
 * operator confirms the suggestion or selects a format (v5.234.0; it used to
 * be preselected, so "Format chosen" appeared with nobody having chosen).
 *
 * The hook owns the state machine and takes its API as an injectable
 * dependency so the logic is testable without a dropzone or a module mock.
 */
import { useCallback, useMemo, useRef, useState } from 'react';

import {
  createScanBatch, discardIngestionJob, getJobDetection, getUploadFormats, renameScanBatch,
  startIngestionJob, uploadFile,
} from '../services/api';
import type { DetectionResponse, FormatOption, UploadOptions } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { duplicateUploadOf, type DuplicateUpload } from '../utils/duplicateUpload';
import { runLimited, STAGE_CONCURRENCY, START_CONCURRENCY } from '../utils/runLimited';
import { formatInstant } from '../utils/scanTime';

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
  /** The dropped file.  Absent on a row resumed from the queue: that file is
   *  already staged on the server, and only its job is known. */
  file?: File;
  filename: string;
  size: number;
  phase: ReviewPhase;
  percent: number;
  jobId?: number;
  batchId?: number;
  detection?: DetectionResponse;
  /** The format the row will be imported as. On a *ready* row it starts as
   *  the detected format; on a *choose* row it is null until the operator
   *  confirms the suggestion or selects one. */
  chosen: string | null;
  /** What detection points at on a row that still needs the operator:
   *  shown, never applied by itself. */
  suggested?: string | null;
  /** True when inspection itself failed (as opposed to finding nothing). */
  detectionFailed?: boolean;
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
  discardIngestionJob: typeof discardIngestionJob;
  getUploadFormats: typeof getUploadFormats;
  renameScanBatch: typeof renameScanBatch;
}

const DEFAULT_DEPS: UploadReviewDeps = {
  uploadFile, getJobDetection, startIngestionJob, createScanBatch, discardIngestionJob, getUploadFormats,
  renameScanBatch,
};

/** A staged job to resume in the review (v5.271.0): what the queue knows. */
export interface StagedJobRef {
  id: number;
  original_filename: string;
  file_size?: number | null;
  batch_id?: number | null;
}

/** The upload batch the latest multi-file drop formed. */
export interface ReviewBatch {
  id: number;
  label: string;
  /** True once the operator's name (not the generated label) is saved. */
  named: boolean;
}

/** How a candidate's basis reads in a chooser.  Only the first is recognition. */
export const BASIS_LABEL: Record<string, string> = {
  structure: 'by structure',
  filename: 'by filename only',
  fallback: 'not recognised',
};

/** The chooser's second group: every format the detection did NOT already
 *  list as a candidate (v5.290.0 — the candidates were listed twice, once
 *  annotated and again under "All formats"). */
export const otherFormats = (
  formats: FormatOption[],
  candidates: ReadonlyArray<{ file_type: string }> | undefined,
): FormatOption[] => {
  const listed = new Set((candidates ?? []).map((c) => c.file_type));
  return formats.filter((f) => !listed.has(f.file_type));
};

/** The format to SUGGEST on a row that needs the operator.  A fallback is a
 *  parser the dispatcher would merely try on an unrecognised file — offering
 *  it as a suggestion would repeat the overstatement in smaller type. */
export const suggestionOf = (detection: DetectionResponse): string | null => {
  if (!detection.needs_choice) return null;
  const first = detection.candidates[0];
  return first && first.basis !== 'fallback' ? first.file_type : null;
};

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
  // The rows as last rendered, for a resume that must not add a job twice.
  const rowsRef = useRef<ReviewRow[]>(rows);
  rowsRef.current = rows;
  const startedAtRef = useRef<number>(Date.now());
  // The batch is created with a generated label the moment files are dropped
  // (its id has to travel with each upload), so naming it is a rename.
  const [batch, setBatch] = useState<ReviewBatch | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

  const patch = useCallback((key: string, change: Partial<ReviewRow> | ((r: ReviewRow) => Partial<ReviewRow>)) => {
    setRows((prev) =>
      prev.map((r) => (r.key === key ? { ...r, ...(typeof change === 'function' ? change(r) : change) } : r)),
    );
  }, []);

  // The chooser's list, independent of any one file's detection: a failed
  // inspection returns nothing, and that is when the list is needed.  Taken
  // from the first detection that succeeds, fetched only when one fails.
  const [formats, setFormats] = useState<FormatOption[]>([]);
  const formatsRequestedRef = useRef(false);
  const ensureFormats = useCallback(() => {
    if (formatsRequestedRef.current) return;
    formatsRequestedRef.current = true;
    api.getUploadFormats()
      .then((list) => setFormats((prev) => (prev.length > 0 ? prev : list)))
      .catch(() => { formatsRequestedRef.current = false; });
  }, [api]);

  const detectOne = useCallback(
    async (key: string, jobId: number) => {
      patch(key, { phase: 'detecting', error: undefined, detectionFailed: false });
      try {
        const detection = await api.getJobDetection(jobId);
        setFormats((prev) => (prev.length > 0 ? prev : detection.formats));
        patch(key, {
          detection,
          // Ready = recognised by structure: the detected format stands.  A
          // row that needs the operator starts with NO choice; what detection
          // points at is a suggestion they confirm or replace.
          chosen: detection.needs_choice ? null : detection.primary,
          suggested: suggestionOf(detection),
          phase: detection.needs_choice ? 'choose' : 'ready',
        });
      } catch (err) {
        // Detection is advice; the operator can retry it or choose by hand.
        ensureFormats();
        patch(key, {
          phase: 'choose',
          detectionFailed: true,
          error: formatApiError(err, 'Could not inspect the file.'),
        });
      }
    },
    [api, patch, ensureFormats],
  );

  // One controller per upload in flight, so a 1–2 GB file can be cancelled:
  // the dialog could not be closed while anything uploaded, and nothing
  // could stop it (review 2026-09-23 B-UI-4).
  const uploadsRef = useRef<Map<string, AbortController>>(new Map());

  /** Stages a dropped file; true when the server kept it. */
  const stageOne = useCallback(
    async (row: ReviewRow, options: UploadOptions): Promise<boolean> => {
      if (!row.file) return false;
      const controller = new AbortController();
      uploadsRef.current.set(row.key, controller);
      try {
        const res = await api.uploadFile(
          row.file,
          (percent) => patch(row.key, { percent }),
          { ...options, stage: true, skipInformational, signal: controller.signal },
        );
        uploadsRef.current.delete(row.key);
        patch(row.key, { percent: 100, jobId: res.job_id });
        await detectOne(row.key, res.job_id);
        return true;
      } catch (err) {
        uploadsRef.current.delete(row.key);
        // Cancelled: the row was already removed by cancelUpload.
        if (controller.signal.aborted) return false;
        const duplicate = duplicateUploadOf(err);
        patch(row.key, {
          phase: duplicate ? 'duplicate' : 'error',
          duplicate: duplicate ?? undefined,
          error: duplicate ? undefined : formatApiError(err, 'Upload failed'),
        });
        return false;
      }
    },
    [api, patch, skipInformational, detectOne],
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
          // "uploaded", in the table's time format: the batch row's own count
          // is what IMPORTED (a re-processed file later joins the batch), so
          // the generated name must not read as the same figure.
          const created = await api.createScanBatch(
            `${files.length} files uploaded · ${formatInstant(new Date(startedAt))}`,
          );
          batchId = created.id;
          setBatch({ id: created.id, label: created.label, named: false });
        } catch (err) {
          console.error('Could not start an upload batch; staging ungrouped:', err);
        }
      }
      if (batchId != null) setRows((prev) => prev.map((r) => (fresh.some((f) => f.key === r.key) ? { ...r, batchId } : r)));
      // Bounded: every file at once was 200 simultaneous uploads for a
      // 200-file drop. Rows wait at "Uploading 0%" until a slot frees.
      const staged = await runLimited(fresh, STAGE_CONCURRENCY, (row) => stageOne({ ...row, batchId }, { batchId }));
      // Every file refused (all duplicates): the batch holds nothing, so
      // there is nothing to name.  The history skips an empty batch too.
      if (batchId != null && !staged.some((r) => r.status === 'fulfilled' && r.value)) {
        setBatch((current) => (current?.id === batchId ? null : current));
      }
    },
    [api, stageOne],
  );

  // v5.271.0 — files already staged on the server (closed review, a refused
  // re-drop) come back into the review: detection is fetched again, and the
  // row is imported or discarded like a freshly dropped one.
  const addStaged = useCallback(
    async (jobs: StagedJobRef[]) => {
      const known = new Set(rowsRef.current.map((r) => r.jobId).filter((id): id is number => id != null));
      const fresh: ReviewRow[] = jobs
        .filter((job) => !known.has(job.id))
        .map((job) => ({
          key: `staged-${job.id}`,
          filename: job.original_filename,
          size: job.file_size ?? 0,
          phase: 'detecting',
          percent: 100,
          jobId: job.id,
          batchId: job.batch_id ?? undefined,
          chosen: null,
          sourceTool: '',
        }));
      if (fresh.length === 0) return;
      startedAtRef.current = Date.now();
      setRows((prev) => [...prev, ...fresh]);
      await runLimited(fresh, STAGE_CONCURRENCY, (row) => detectOne(row.key, row.jobId!));
    },
    [detectOne],
  );

  // A dropped file refused because the same file waits staged: review that
  // copy here instead of leaving a dead end (or a second upload).
  const reviewWaitingCopy = useCallback(
    (key: string) => {
      const row = rows.find((r) => r.key === key);
      const jobId = row?.duplicate?.jobStatus === 'staged' ? row.duplicate.jobId : null;
      if (!row || jobId == null) return;
      if (rows.some((r) => r.key !== key && r.jobId === jobId)) {
        // Already a row of this review (dropped twice): keep that one.
        setRows((prev) => prev.filter((r) => r.key !== key));
        return;
      }
      patch(key, { jobId, duplicate: undefined, batchId: undefined, percent: 100 });
      void detectOne(key, jobId);
    },
    [rows, patch, detectOne],
  );

  const nameBatch = useCallback(
    async (label: string): Promise<boolean> => {
      const name = label.trim();
      if (!batch || !name || name === batch.label) return false;
      setBatchError(null);
      try {
        const saved = await api.renameScanBatch(batch.id, name);
        setBatch({ id: saved.id, label: saved.label, named: true });
        return true;
      } catch (err) {
        setBatchError(formatApiError(err, 'Could not name this upload.'));
        return false;
      }
    },
    [api, batch],
  );

  const importAgain = useCallback(
    (key: string) => {
      const row = rows.find((r) => r.key === key);
      if (!row?.file) return;
      patch(key, { phase: 'uploading', percent: 0, duplicate: undefined, error: undefined });
      void stageOne(row, { batchId: row.batchId, allowDuplicate: true });
    },
    [rows, patch, stageOne],
  );

  const setChoice = useCallback((key: string, fileType: string | null) => patch(key, { chosen: fileType }), [patch]);
  const confirmSuggestion = useCallback(
    (key: string) => patch(key, (r) => (r.suggested ? { chosen: r.suggested } : {})),
    [patch],
  );
  const setSourceTool = useCallback((key: string, value: string) => patch(key, { sourceTool: value }), [patch]);

  const retryDetection = useCallback(
    (key: string) => {
      const row = rows.find((r) => r.key === key);
      if (!row || row.jobId == null) return;
      void detectOne(key, row.jobId);
    },
    [rows, detectOne],
  );

  // A staged row has a file on the server.  Dropping only the row left that
  // file in the queue, and made the next attempt at the same file a
  // duplicate — so removing a staged row discards the staged job.  Rows with
  // nothing on the server (a failed upload, a refused duplicate) just go.
  const remove = useCallback(
    async (key: string) => {
      const row = rows.find((r) => r.key === key);
      if (!row) return;
      const staged = row.jobId != null && (row.phase === 'ready' || row.phase === 'choose');
      if (staged) {
        try {
          await api.discardIngestionJob(row.jobId!);
        } catch (err) {
          patch(key, { error: formatApiError(err, 'Could not discard the staged file.') });
          return;
        }
      }
      setRows((prev) => prev.filter((r) => r.key !== key));
    },
    [rows, api, patch],
  );

  /** Stop an upload in flight and drop its row.  Nothing was staged for it
   *  (a request cut off in the last instant may still have been stored; an
   *  unstarted staged job expires after 24 h). */
  const cancelUpload = useCallback((key: string) => {
    const controller = uploadsRef.current.get(key);
    if (!controller) return;
    controller.abort();
    uploadsRef.current.delete(key);
    setRows((prev) => prev.filter((r) => r.key !== key));
  }, []);

  // Started rows are the banner's now.  Left in place they kept `allStarted`
  // true, so the dialog closed itself the moment it was reopened.
  const clearStarted = useCallback(
    () => setRows((prev) => (prev.some((r) => r.phase === 'started') ? prev.filter((r) => r.phase !== 'started') : prev)),
    [],
  );

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
          // No detection at all (a failed inspection) is a row that needed a choice.
          phase: row.detection && !row.detection.needs_choice ? 'ready' : 'choose',
          error: formatApiError(err, 'Could not start the import.'),
        });
      }
    },
    [api, patch, onStarted],
  );

  const importReady = useCallback(async () => {
    const targets = rows.filter(isImportable);
    await runLimited(targets, START_CONCURRENCY, (row) => importOne(row));
  }, [rows, importOne]);

  const readyCount = rows.filter(isImportable).length;
  const chooseCount = rows.filter((r) => r.phase === 'choose' && !r.chosen).length;
  const allStarted = rows.length > 0 && rows.every((r) => r.phase === 'started');
  const busy = rows.some((r) => r.phase === 'uploading' || r.phase === 'detecting' || r.phase === 'starting');

  return {
    rows,
    addFiles,
    addStaged,
    reviewWaitingCopy,
    importAgain,
    importOne,
    importReady,
    setChoice,
    confirmSuggestion,
    setSourceTool,
    retryDetection,
    remove,
    cancelUpload,
    clearStarted,
    formats,
    batch,
    batchError,
    nameBatch,
    readyCount,
    chooseCount,
    allStarted,
    busy,
  };
}
