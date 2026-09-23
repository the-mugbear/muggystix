/**
 * The upload review step's state machine (phase C): stage → inspect →
 * ready or choose → import, with the format override sent only when it
 * means something.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { STAGE_CONCURRENCY } from '../../utils/runLimited';

// The hook reaches the API barrel only for its default dependencies (the
// test injects its own), but the barrel's HTTP client must not load in jsdom.
vi.mock('../../services/api', () => ({
  uploadFile: vi.fn(),
  getJobDetection: vi.fn(),
  startIngestionJob: vi.fn(),
  createScanBatch: vi.fn(),
  discardIngestionJob: vi.fn(),
  getUploadFormats: vi.fn(),
  renameScanBatch: vi.fn(),
}));

import { overrideFor, suggestionOf, useUploadReview } from '../../hooks/useUploadReview';
import type { DetectionResponse, UploadOptions } from '../../services/api/uploads';

const file = (name: string) => new File(['x'], name, { type: 'text/plain' });

const detection = (over: Partial<DetectionResponse>): DetectionResponse => ({
  job_id: 1,
  filename: 'f',
  candidates: [],
  primary: null,
  needs_choice: true,
  reason: 'No distinctive signature was recognised.',
  preview: { raw: '10.0.0.5:443', sample: ['10.0.0.5:443'] },
  formats: [{ file_type: 'naabu_output', label: 'Naabu host:port text', family: 'port' }],
  ...over,
});

const ready = detection({
  job_id: 11,
  candidates: [{ file_type: 'nmap_xml', label: 'Nmap XML', basis: 'structure', rank: 0 }],
  primary: 'nmap_xml',
  needs_choice: false,
  reason: null,
});
const filenameOnly = detection({
  job_id: 12,
  candidates: [{ file_type: 'naabu_output', label: 'Naabu host:port text', basis: 'filename', rank: 0 }],
  primary: 'naabu_output',
  needs_choice: true,
  reason: 'Recognised from the filename only; the content did not confirm it.',
});
const unknown = detection({ job_id: 13 });
// An unrelated .xml: the dispatcher would TRY these, having recognised nothing.
const fallbackOnly = detection({
  job_id: 14,
  candidates: [
    { file_type: 'nmap_xml', label: 'Nmap XML', basis: 'fallback', rank: 0 },
    { file_type: 'nessus_xml', label: 'Nessus', basis: 'fallback', rank: 1 },
  ],
  primary: 'nmap_xml',
  needs_choice: true,
});

const makeDeps = () => {
  const detections: Record<string, DetectionResponse> = {
    'scan.xml': ready, 'naabu.txt': filenameOnly, 'results.txt': unknown, 'inventory.xml': fallbackOnly,
  };
  let nextJob = 100;
  const jobsByName: Record<number, string> = {};
  const uploadFile = vi.fn(async (f: File, onProgress?: (p: number) => void, _options?: UploadOptions) => {
    onProgress?.(100);
    const id = nextJob++;
    jobsByName[id] = f.name;
    return { job_id: id, filename: f.name, status: 'staged', message: 'staged', scan_id: null };
  });
  const getJobDetection = vi.fn(async (jobId: number) => ({ ...detections[jobsByName[jobId]], job_id: jobId }));
  const startIngestionJob = vi.fn(async (jobId: number) => ({ id: jobId, status: 'queued' }) as never);
  const createScanBatch = vi.fn(async () => ({ id: 7, label: 'b' }));
  const discardIngestionJob = vi.fn(async (jobId: number) => ({ id: jobId, status: 'failed' }) as never);
  const getUploadFormats = vi.fn(async () => [
    { file_type: 'nmap_xml', label: 'Nmap XML', family: 'port' },
    { file_type: 'naabu_output', label: 'Naabu host:port text', family: 'port' },
  ]);
  const renameScanBatch = vi.fn(async (id: number, label: string) => ({ id, label }));
  return {
    uploadFile, getJobDetection, startIngestionJob, createScanBatch, discardIngestionJob, getUploadFormats,
    renameScanBatch,
  };
};

describe('useUploadReview', () => {
  it('stages each file, inspects it, and sorts rows into ready and choose', async () => {
    const deps = makeDeps();
    const onStarted = vi.fn();
    const { result } = renderHook(() => useUploadReview({ skipInformational: true, onStarted, deps }));

    await act(async () => {
      await result.current.addFiles([file('scan.xml'), file('naabu.txt'), file('results.txt')]);
    });

    // Three files dropped together are one batch; every upload is STAGED.
    expect(deps.createScanBatch).toHaveBeenCalledTimes(1);
    expect(deps.uploadFile).toHaveBeenCalledTimes(3);
    expect(deps.uploadFile.mock.calls[0][2]).toMatchObject({ stage: true, batchId: 7, skipInformational: true });

    await waitFor(() => expect(result.current.rows.map((r) => r.phase)).toEqual(['ready', 'choose', 'choose']));
    const [xml, naabu, unknownRow] = result.current.rows;
    expect(xml.chosen).toBe('nmap_xml');
    // A filename-only match is a SUGGESTION: shown, not applied.  It used to
    // be prefilled, which counted the row as ready and labelled it "Format
    // chosen" though nobody had chosen anything.
    expect(naabu.chosen).toBeNull();
    expect(naabu.suggested).toBe('naabu_output');
    expect(unknownRow.chosen).toBeNull();
    expect(unknownRow.suggested).toBeNull();
    expect(result.current.readyCount).toBe(1);
    expect(result.current.chooseCount).toBe(2);

    // Confirming the suggestion is what makes the row importable.
    act(() => result.current.confirmSuggestion(naabu.key));
    expect(result.current.rows[1].chosen).toBe('naabu_output');
    expect(result.current.readyCount).toBe(2);
  });

  it('a file nothing recognised is never ready, and its fallbacks are not suggested', async () => {
    const deps = makeDeps();
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('inventory.xml')]);
    });
    const row = result.current.rows[0];
    expect(row.phase).toBe('choose');
    expect(row.chosen).toBeNull();
    expect(row.suggested).toBeNull();
    expect(result.current.readyCount).toBe(0);
    expect(suggestionOf(fallbackOnly)).toBeNull();
    expect(suggestionOf(filenameOnly)).toBe('naabu_output');
    expect(suggestionOf(ready)).toBeNull();
  });

  it('a failed inspection can be retried, and a format chosen by hand meanwhile', async () => {
    const deps = makeDeps();
    deps.getJobDetection.mockRejectedValueOnce(new Error('inspect failed'));
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml')]);
    });
    expect(result.current.rows[0]).toMatchObject({ phase: 'choose', detectionFailed: true, chosen: null });
    expect(result.current.rows[0].detection).toBeUndefined();
    // The chooser's list arrives without a detection.
    await waitFor(() => expect(result.current.formats.map((f) => f.file_type)).toContain('nmap_xml'));
    expect(deps.getUploadFormats).toHaveBeenCalledTimes(1);

    // By hand: importable, and the choice is sent as the override.
    act(() => result.current.setChoice(result.current.rows[0].key, 'nmap_xml'));
    expect(result.current.readyCount).toBe(1);
    expect(overrideFor(result.current.rows[0])).toBe('nmap_xml');

    // Or retry the inspection, which now succeeds.
    await act(async () => {
      result.current.retryDetection(result.current.rows[0].key);
    });
    await waitFor(() => expect(result.current.rows[0].phase).toBe('ready'));
    expect(result.current.rows[0]).toMatchObject({ detectionFailed: false, error: undefined, chosen: 'nmap_xml' });
    expect(deps.getJobDetection).toHaveBeenCalledTimes(2);
  });

  it('removing a staged row discards the staged file, and keeps the row if that fails', async () => {
    const deps = makeDeps();
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml'), file('results.txt')]);
    });
    await waitFor(() => expect(result.current.rows.map((r) => r.phase)).toEqual(['ready', 'choose']));

    deps.discardIngestionJob.mockRejectedValueOnce(new Error('nope'));
    await act(async () => {
      await result.current.remove(result.current.rows[0].key);
    });
    expect(result.current.rows).toHaveLength(2);
    expect(result.current.rows[0].error).toBeTruthy();

    await act(async () => {
      await result.current.remove(result.current.rows[0].key);
    });
    expect(deps.discardIngestionJob).toHaveBeenLastCalledWith(100);
    expect(result.current.rows.map((r) => r.filename)).toEqual(['results.txt']);
  });

  it('a multi-file drop forms a batch the operator can name; a single file forms none', async () => {
    const deps = makeDeps();
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml')]);
    });
    expect(result.current.batch).toBeNull();

    await act(async () => {
      await result.current.addFiles([file('scan.xml'), file('results.txt')]);
    });
    expect(result.current.batch).toMatchObject({ id: 7, named: false });

    // Blank, or the label it already has, is not a rename.
    await act(async () => {
      expect(await result.current.nameBatch('   ')).toBe(false);
    });
    expect(deps.renameScanBatch).not.toHaveBeenCalled();

    await act(async () => {
      expect(await result.current.nameBatch('  DMZ sweep, week 2 ')).toBe(true);
    });
    expect(deps.renameScanBatch).toHaveBeenCalledWith(7, 'DMZ sweep, week 2');
    expect(result.current.batch).toMatchObject({ label: 'DMZ sweep, week 2', named: true });

    deps.renameScanBatch.mockRejectedValueOnce(new Error('nope'));
    await act(async () => {
      expect(await result.current.nameBatch('another')).toBe(false);
    });
    expect(result.current.batchError).toBeTruthy();
    expect(result.current.batch?.label).toBe('DMZ sweep, week 2');
  });

  it('clearStarted drops what the banner owns and keeps unresolved rows', async () => {
    const deps = makeDeps();
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml'), file('results.txt')]);
    });
    await act(async () => {
      await result.current.importReady();
    });
    expect(result.current.rows.map((r) => r.phase)).toEqual(['started', 'choose']);
    act(() => result.current.clearStarted());
    expect(result.current.rows.map((r) => r.filename)).toEqual(['results.txt']);
    expect(result.current.allStarted).toBe(false);
  });

  it('imports the ready rows with an override only where it means something', async () => {
    const deps = makeDeps();
    const onStarted = vi.fn();
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted, deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml'), file('naabu.txt'), file('results.txt')]);
    });
    await waitFor(() => expect(result.current.rows.map((r) => r.phase)).toEqual(['ready', 'choose', 'choose']));

    act(() => result.current.confirmSuggestion(result.current.rows[1].key));
    await act(async () => {
      await result.current.importReady();
    });

    // scan.xml: detected by structure, no override. naabu.txt: confirmed
    // filename-only choice IS the override. results.txt: untouched, stays staged.
    expect(deps.startIngestionJob).toHaveBeenCalledTimes(2);
    expect(deps.startIngestionJob).toHaveBeenCalledWith(100, { formatOverride: null, sourceTool: null });
    expect(deps.startIngestionJob).toHaveBeenCalledWith(101, { formatOverride: 'naabu_output', sourceTool: null });
    expect(onStarted).toHaveBeenCalledTimes(2);
    expect(onStarted.mock.calls[0][0]).toMatchObject({ filename: 'scan.xml', jobId: 100, batchId: 7 });
    expect(result.current.rows.map((r) => r.phase)).toEqual(['started', 'started', 'choose']);
    expect(result.current.allStarted).toBe(false);

    // Resolve the last one with a source tool and import it.
    act(() => {
      result.current.setChoice(result.current.rows[2].key, 'naabu_output');
      result.current.setSourceTool(result.current.rows[2].key, 'naabu 2.3');
    });
    await act(async () => {
      await result.current.importOne(result.current.rows[2]);
    });
    expect(deps.startIngestionJob).toHaveBeenLastCalledWith(102, { formatOverride: 'naabu_output', sourceTool: 'naabu 2.3' });
    expect(result.current.allStarted).toBe(true);
  });

  it('a duplicate refusal is a row state with a way to import anyway', async () => {
    const deps = makeDeps();
    deps.uploadFile.mockRejectedValueOnce({
      response: { status: 409, data: { detail: { code: 'duplicate_scan', scan_id: 5, message: 'Already imported as scan #5.' } } },
    });
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml')]);
    });
    expect(result.current.rows[0].phase).toBe('duplicate');
    expect(result.current.rows[0].duplicate?.scanId).toBe(5);
    expect(deps.createScanBatch).not.toHaveBeenCalled(); // one file: no batch

    await act(async () => {
      result.current.importAgain(result.current.rows[0].key);
    });
    await waitFor(() => expect(result.current.rows[0].phase).toBe('ready'));
    expect(deps.uploadFile.mock.calls[1][2]).toMatchObject({ stage: true, allowDuplicate: true });
  });

  // v5.271.0 — a closed review left 26 files staged, and the page could only
  // review them one per dialog, with the 26th out of the queue's reach.
  it('resumes staged files: inspected again, imported, and never added twice', async () => {
    const deps = makeDeps();
    deps.getJobDetection.mockImplementation(async (jobId: number) =>
      (jobId === 41 ? { ...ready, job_id: 41 } : { ...filenameOnly, job_id: jobId }));
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    const jobs = [
      { id: 41, original_filename: 'nmap.xml', file_size: 76_000, batch_id: 7 },
      { id: 42, original_filename: 'naabu.txt', file_size: 300, batch_id: 7 },
    ];

    await act(async () => {
      await result.current.addStaged(jobs);
    });
    expect(deps.uploadFile).not.toHaveBeenCalled(); // the files are already on the server
    expect(result.current.rows.map((r) => [r.jobId, r.phase])).toEqual([[41, 'ready'], [42, 'choose']]);
    expect(result.current.rows[1].suggested).toBe('naabu_output'); // still only a suggestion

    await act(async () => {
      await result.current.addStaged(jobs);
    });
    expect(result.current.rows).toHaveLength(2);

    await act(async () => {
      await result.current.importReady();
    });
    expect(deps.startIngestionJob).toHaveBeenCalledTimes(1);
    expect(deps.startIngestionJob).toHaveBeenCalledWith(41, { formatOverride: null, sourceTool: null });
  });

  it('a drop the server refused entirely leaves no batch to name', async () => {
    const deps = makeDeps();
    deps.uploadFile.mockRejectedValue({
      response: { status: 409, data: { detail: { code: 'duplicate_scan', job_id: 3, job_status: 'staged', message: 'waiting' } } },
    });
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml'), file('results.txt')]);
    });
    expect(deps.createScanBatch).toHaveBeenCalledTimes(1);
    expect(result.current.rows.every((r) => r.phase === 'duplicate')).toBe(true);
    expect(result.current.batch).toBeNull();
  });

  it('a duplicate of a staged file reviews that waiting copy instead', async () => {
    const deps = makeDeps();
    deps.uploadFile.mockRejectedValueOnce({
      response: { status: 409, data: { detail: { code: 'duplicate_scan', job_id: 9, job_status: 'staged', message: 'waiting' } } },
    });
    deps.getJobDetection.mockImplementation(async (jobId: number) => ({ ...ready, job_id: jobId }));
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml')]);
    });
    expect(result.current.rows[0].duplicate).toMatchObject({ jobId: 9, jobStatus: 'staged' });

    await act(async () => {
      result.current.reviewWaitingCopy(result.current.rows[0].key);
    });
    await waitFor(() => expect(result.current.rows[0].phase).toBe('ready'));
    expect(result.current.rows[0].jobId).toBe(9);
    expect(deps.getJobDetection).toHaveBeenLastCalledWith(9);
    expect(deps.uploadFile).toHaveBeenCalledTimes(1); // not uploaded a second time
  });

  // v5.248.0 — code review finding 10. Every selected file used to start
  // uploading at once; over HTTP/2 nothing in the browser caps that.
  it('stages a large selection a few files at a time, and still stages them all', async () => {
    const deps = makeDeps();
    let uploading = 0;
    let peak = 0;
    deps.uploadFile.mockImplementation(async (f: File) => {
      uploading += 1;
      peak = Math.max(peak, uploading);
      await new Promise((r) => setTimeout(r, 5));
      uploading -= 1;
      return { job_id: 500 + Number(f.name.replace(/\D/g, '')), filename: f.name, status: 'staged', message: 'staged', scan_id: null };
    });
    deps.getJobDetection.mockImplementation(async (jobId: number) => ({ ...ready, job_id: jobId }));
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));

    await act(async () => {
      await result.current.addFiles(Array.from({ length: 12 }, (_, i) => file(`scan-${i}.xml`)));
    });

    expect(deps.uploadFile).toHaveBeenCalledTimes(12);
    expect(peak).toBeLessThanOrEqual(STAGE_CONCURRENCY);
    expect(peak).toBeGreaterThan(1); // bounded, not serialised
    expect(result.current.rows.filter((r) => r.phase === 'ready')).toHaveLength(12);
  });

  it('cancels an upload in flight: aborts the request and drops the row', async () => {
    const deps = makeDeps();
    let seenSignal: AbortSignal | undefined;
    deps.uploadFile.mockImplementation((_f: File, _p?: (n: number) => void, options?: UploadOptions) =>
      new Promise((_resolve, reject) => {
        seenSignal = options?.signal;
        options?.signal?.addEventListener('abort', () => {
          const err = new Error('cancelled');
          err.name = 'AbortError';
          reject(err);
        });
      }));
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted: vi.fn(), deps }));

    let adding: Promise<void> | undefined;
    act(() => { adding = result.current.addFiles([file('big.xml')]); });
    await waitFor(() => expect(result.current.rows.map((r) => r.phase)).toEqual(['uploading']));
    expect(seenSignal?.aborted).toBe(false);

    await act(async () => {
      result.current.cancelUpload(result.current.rows[0].key);
      await adding;
    });
    expect(seenSignal?.aborted).toBe(true);
    expect(result.current.rows).toEqual([]);
    expect(deps.getJobDetection).not.toHaveBeenCalled();
  });
});

describe('overrideFor', () => {
  it('sends the choice when one was required, or when it differs from detection', () => {
    expect(overrideFor({ phase: 'ready', chosen: 'nmap_xml', detection: ready })).toBeNull();
    expect(overrideFor({ phase: 'ready', chosen: 'masscan_xml', detection: ready })).toBe('masscan_xml');
    expect(overrideFor({ phase: 'choose', chosen: 'naabu_output', detection: filenameOnly })).toBe('naabu_output');
    expect(overrideFor({ phase: 'choose', chosen: null, detection: unknown })).toBeNull();
  });
});
