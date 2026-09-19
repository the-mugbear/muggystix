/**
 * The upload review step's state machine (phase C): stage → inspect →
 * ready or choose → import, with the format override sent only when it
 * means something.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// The hook reaches the API barrel only for its default dependencies (the
// test injects its own), but the barrel's HTTP client must not load in jsdom.
vi.mock('../../services/api', () => ({
  uploadFile: vi.fn(),
  getJobDetection: vi.fn(),
  startIngestionJob: vi.fn(),
  createScanBatch: vi.fn(),
}));

import { overrideFor, useUploadReview } from '../../hooks/useUploadReview';
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

const makeDeps = () => {
  const detections: Record<string, DetectionResponse> = {
    'scan.xml': ready, 'naabu.txt': filenameOnly, 'results.txt': unknown,
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
  return { uploadFile, getJobDetection, startIngestionJob, createScanBatch };
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
    expect(naabu.chosen).toBe('naabu_output'); // prefilled, still needs confirming
    expect(unknownRow.chosen).toBeNull();
    expect(result.current.readyCount).toBe(2); // ready + the prefilled choose row
    expect(result.current.chooseCount).toBe(1);
  });

  it('imports the ready rows with an override only where it means something', async () => {
    const deps = makeDeps();
    const onStarted = vi.fn();
    const { result } = renderHook(() => useUploadReview({ skipInformational: false, onStarted, deps }));
    await act(async () => {
      await result.current.addFiles([file('scan.xml'), file('naabu.txt'), file('results.txt')]);
    });
    await waitFor(() => expect(result.current.rows.map((r) => r.phase)).toEqual(['ready', 'choose', 'choose']));

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
});

describe('overrideFor', () => {
  it('sends the choice when one was required, or when it differs from detection', () => {
    expect(overrideFor({ phase: 'ready', chosen: 'nmap_xml', detection: ready })).toBeNull();
    expect(overrideFor({ phase: 'ready', chosen: 'masscan_xml', detection: ready })).toBe('masscan_xml');
    expect(overrideFor({ phase: 'choose', chosen: 'naabu_output', detection: filenameOnly })).toBe('naabu_output');
    expect(overrideFor({ phase: 'choose', chosen: null, detection: unknown })).toBeNull();
  });
});
