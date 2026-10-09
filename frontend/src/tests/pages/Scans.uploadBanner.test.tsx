/**
 * The Scans page's upload banner: one row per file THIS TAB started, followed
 * by job id to its import result.
 *
 *   received → processing → imported | partial | failed
 *
 * What is pinned here is what the reader sees and what the server is asked —
 * not how the page keeps it.  A file reaches the banner the way a reader puts
 * it there: staged files in the queue → "Review N waiting" → Import.
 *
 * A poll's ids are asserted exactly where every tick is the same question
 * (the jobs still running).  The one-off read made when a file is started,
 * and a result lookup, are asserted as "names this id" / "one request": they
 * may also name a job already finished, or a result already known.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => {
  const named: Record<string, ReturnType<typeof vi.fn>> = {};
  return new Proxy(named, {
    get(target, prop: string) {
      if (prop === '__esModule') return true;
      if (prop === 'then') return undefined;
      if (!(prop in target)) target[prop] = vi.fn().mockResolvedValue([]);
      return target[prop];
    },
    has: () => true,
  });
});
const navigate = vi.hoisted(() => vi.fn());
vi.mock('../../services/api', () => api);
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<typeof import('react-router-dom')>('react-router-dom')),
  useNavigate: () => navigate,
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo' } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'admin' }, hasPermission: () => true }),
}));

import Scans from '../../pages/Scans';
import { TooltipProvider } from '../../components/ui/tooltip';

type Job = Record<string, unknown> & { id: number; status: string };
type ScanRow = Record<string, unknown> & { id: number };

/** The server, as far as the banner is concerned. */
const server = {
  /** Jobs by id: what `GET /upload/jobs?ids=` answers for the ids it is asked. */
  jobs: new Map<number, Job>(),
  /** Import results by scan id: what `GET /scans/?ids=` answers. */
  scans: new Map<number, ScanRow>(),
  /** Set to make the next job reads / result lookups fail. */
  jobsFail: false,
  scansFail: false,
};

const staged = (id: number, name: string) => ({
  id, status: 'staged', original_filename: name, filename: name, created_at: '2026-10-09T10:00:00Z',
  file_size: 10, message: null,
});
const job = (id: number, status: string, extra: Record<string, unknown> = {}): Job => ({
  id, status, original_filename: `f${id}.xml`, filename: `f${id}.xml`, created_at: '2026-10-09T10:00:00Z', ...extra,
});
const result = (id: number, extra: Record<string, unknown> = {}): ScanRow => ({
  id, filename: `scan-${id}.xml`, tool_name: 'nmap', scan_type: 'port_scan', created_at: '2026-10-09T10:00:00Z',
  total_hosts: 50, up_hosts: 50, new_hosts: 12, updated_hosts: 38, total_ports: 4, open_ports: 4, ...extra,
});
const detection = (jobId: number) => ({
  job_id: jobId, filename: 'f',
  candidates: [{ file_type: 'nmap_xml', label: 'Nmap XML', basis: 'structure', rank: 0 }],
  primary: 'nmap_xml', needs_choice: false, reason: null,
  preview: { raw: '<nmaprun>', sample: [] },
  formats: [{ file_type: 'nmap_xml', label: 'Nmap XML', family: 'port' }],
});

const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const TICK = 4000;

/** The ids of every job read so far, one array per request. */
const jobReads = (): number[][] => api.getIngestionJobsByIds.mock.calls.map((call) => call[1] as number[]);
/** The scan ids of every import-result lookup so far, one array per request. */
const resultReads = (): number[][] => api.getScans.mock.calls
  .map((call) => (call[3] as { ids?: number[] } | undefined)?.ids)
  .filter((ids): ids is number[] => Array.isArray(ids));

/** The banner's row for a file: its heading reads "<state>: <file name>". */
const rowOf = (filename: string): HTMLElement => {
  const heading = screen.getByText((text) => text.endsWith(`: ${filename}`));
  return heading.closest('[role="status"], [role="alert"]') as HTMLElement;
};
const headingOf = (filename: string): string =>
  screen.getByText((text) => text.endsWith(`: ${filename}`)).textContent ?? '';
const queryRow = (filename: string) => screen.queryByText((text) => text.endsWith(`: ${filename}`));

/** Opens the page with these files waiting in the queue and the review open on them. */
async function openReview(files: Array<{ id: number; name: string }>) {
  api.getStagedIngestionJobs.mockResolvedValue(files.map((f) => staged(f.id, f.name)));
  render(
    <MemoryRouter initialEntries={['/scans']}>
      <TooltipProvider><Scans /></TooltipProvider>
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: `Review ${files.length} waiting` }));
  await waitFor(() => expect(screen.getAllByRole('button', { name: 'Import' })).toHaveLength(files.length));
}

/** Starts every file in the review (the dialog then closes itself) and waits
 *  until each is on the banner and the first answer about them has arrived. */
async function importAll(files: Array<{ id: number; name: string }>) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^Import ${files.length} ready file`) }));
  await waitFor(() => files.forEach((f) => expect(queryRow(f.name)).not.toBeNull()));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await pass(50);
}

/** Starts ONE file from the open review, leaving the others in it. */
async function importOne(name: string) {
  const row = screen.getAllByRole('row').find((r) => within(r).queryByTitle(name)) as HTMLElement;
  fireEvent.click(within(row).getByRole('button', { name: 'Import' }));
  await waitFor(() => expect(queryRow(name)).not.toBeNull());
  await pass(50);
}

async function startFiles(files: Array<{ id: number; name: string }>) {
  await openReview(files);
  await importAll(files);
}

const A = { id: 1, name: 'a.xml' };
const B = { id: 2, name: 'b.xml' };

const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  act(() => { window.dispatchEvent(new Event('visibilitychange')); });
};

describe('Scans — the upload banner', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.clearAllMocks();
    server.jobs.clear();
    server.scans.clear();
    server.jobsFail = false;
    server.scansFail = false;
    api.getRecentIngestionJobs.mockResolvedValue([]);
    api.getStagedIngestionJobs.mockResolvedValue([]);
    api.getScansSummary.mockResolvedValue({ total_scans: 0, total_hosts: 0, up_hosts: 0, open_services: 0, tool_counts: {} });
    api.getScanInventoryMarker.mockResolvedValue({ count: 0, latest_id: null });
    api.getImportHistory.mockResolvedValue({ items: [], total: 0, batch_total: 0, scan_total: 0, has_more: false });
    api.getJobDetection.mockImplementation(async (_projectId: number, jobId: number) => detection(jobId));
    api.startIngestionJob.mockResolvedValue({});
    api.getIngestionJobsByIds.mockImplementation(async (_projectId: number, ids: number[]) => {
      if (server.jobsFail) throw new Error('jobs unavailable');
      return ids.map((id) => server.jobs.get(id)).filter((j): j is Job => !!j);
    });
    api.getScans.mockImplementation(async (_projectId: number, _skip: number, _limit: number, options?: { ids?: number[] }) => {
      if (!options?.ids) return [];
      if (server.scansFail) throw new Error('results unavailable');
      return options.ids.map((id) => server.scans.get(id)).filter((s): s is ScanRow => !!s);
    });
  });
  afterEach(() => {
    setVisibility('visible');
    vi.useRealTimers();
  });

  it('shows nothing until a file is started', async () => {
    await openReview([A]);
    expect(queryRow(A.name)).toBeNull();
    expect(api.getIngestionJobsByIds).not.toHaveBeenCalled();
  });

  it('a started file reads "received" until the server has answered about its job, and is asked about at once', async () => {
    let answer: (jobs: Job[]) => void = () => {};
    api.getIngestionJobsByIds.mockImplementation(() => new Promise<Job[]>((resolve) => { answer = resolve; }));
    await startFiles([A]);

    expect(headingOf(A.name)).toBe('Upload received, waiting for the worker: a.xml');
    expect(rowOf(A.name)).toHaveTextContent('The file is stored; parsing starts when a worker picks it up.');
    expect(within(rowOf(A.name)).queryByRole('button', { name: 'Dismiss a.xml progress' })).toBeNull();
    expect(jobReads()).toHaveLength(1);
    expect(jobReads()[0]).toContain(1);
    expect(api.getIngestionJobsByIds.mock.calls[0][0]).toBe(1);   // the project

    await act(async () => { answer([job(1, 'queued', { message: 'Waiting for a worker' })]); });
    await waitFor(() => expect(headingOf(A.name)).toBe('Processing: a.xml'));
    expect(rowOf(A.name)).toHaveTextContent('Waiting for a worker');
  });

  it('a queued or processing job reads "Processing" with the worker\'s message, and the message follows the job', async () => {
    server.jobs.set(1, job(1, 'processing', { message: 'Reading hosts 10 / 50' }));
    await startFiles([A]);
    await waitFor(() => expect(headingOf(A.name)).toBe('Processing: a.xml'));
    expect(rowOf(A.name)).toHaveTextContent('Reading hosts 10 / 50');
    expect(within(rowOf(A.name)).queryByRole('button', { name: 'Dismiss a.xml progress' })).toBeNull();

    server.jobs.set(1, job(1, 'processing', { message: 'Reading hosts 40 / 50' }));
    await pass(TICK);
    expect(rowOf(A.name)).toHaveTextContent('Reading hosts 40 / 50');
    expect(rowOf(A.name)).not.toHaveTextContent('10 / 50');

    // No message: the row says so in its own words.
    server.jobs.set(1, job(1, 'processing', { message: null }));
    await pass(TICK);
    expect(rowOf(A.name)).toHaveTextContent('Parsing…');
  });

  it('a completed job reads "Imported" with what the import did to the inventory', async () => {
    server.jobs.set(1, job(1, 'completed', { scan_id: 7, message: 'Parsed 50 hosts.' }));
    server.scans.set(7, result(7));
    await startFiles([A]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Imported: a.xml'));
    const summary = await within(rowOf(A.name)).findByLabelText('Import result');
    expect(summary).toHaveTextContent('+12 hosts added');
    expect(summary).toHaveTextContent('38 already known');
    expect(within(summary).getByRole('link', { name: '+12 hosts added' }))
      .toHaveAttribute('href', '/hosts?scan_ids=7&first_seen_in_scan=true');
    // The counts replace the job's own message.
    expect(rowOf(A.name)).not.toHaveTextContent('Parsed 50 hosts.');
    expect(within(rowOf(A.name)).getByRole('button', { name: 'Dismiss a.xml progress' })).toBeInTheDocument();
    // Asked of the project, by scan id.
    expect(resultReads()).toHaveLength(1);
    expect(resultReads()[0]).toContain(7);
    const lookup = api.getScans.mock.calls.find((call) => (call[3] as { ids?: number[] } | undefined)?.ids);
    expect(lookup?.[0]).toBe(1);
  });

  it.each([
    ['skipped lines', { skipped_count: 3 }],
    ['a truncated file', { partial: true }],
  ])('a completed job with %s reads "Imported with gaps"', async (_what, quality) => {
    server.jobs.set(1, job(1, 'completed', { scan_id: 7, ...quality }));
    server.scans.set(7, result(7, { import_skipped: 3 }));
    await startFiles([A]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Imported with gaps: a.xml'));
    expect(await within(rowOf(A.name)).findByLabelText('Import result')).toHaveTextContent('3 records skipped');
    expect(within(rowOf(A.name)).getByRole('button', { name: 'Dismiss a.xml progress' })).toBeInTheDocument();
  });

  it('a completed job with no scan asks for no result and keeps the job\'s message', async () => {
    server.jobs.set(1, job(1, 'completed', { scan_id: null, message: '6 DNS records' }));
    await startFiles([A]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Imported: a.xml'));
    expect(rowOf(A.name)).toHaveTextContent('6 DNS records');
    await pass(TICK * 2);
    expect(resultReads()).toHaveLength(0);
  });

  it.each([
    ['the parser\'s reason first', { failure_reason: 'SMBMap parser found 0 hosts', error_message: 'E', last_error: 'L', message: 'M' }, 'SMBMap parser found 0 hosts'],
    ['then the error message', { failure_reason: null, error_message: 'Failed to parse the file', last_error: 'L', message: 'M' }, 'Failed to parse the file'],
    ['then the last error', { error_message: '', last_error: 'Worker lost the job', message: 'M' }, 'Worker lost the job'],
    ['then the job\'s message', { message: 'Cancelled by the operator' }, 'Cancelled by the operator'],
  ])('a failed job reads "Import failed" with its reason — %s', async (_what, fields, shown) => {
    server.jobs.set(1, job(1, 'failed', fields));
    await startFiles([A]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Import failed: a.xml'));
    const row = rowOf(A.name);
    expect(row).toHaveAttribute('role', 'alert');
    expect(within(row).getByText(shown)).toBeInTheDocument();
    for (const other of ['E', 'L', 'M']) expect(within(row).queryByText(other)).toBeNull();
    expect(within(row).getByRole('button', { name: 'Dismiss a.xml progress' })).toBeInTheDocument();
  });

  it('a failed job that says nothing reads "Import failed", and "Why it failed" opens the job\'s results', async () => {
    server.jobs.set(1, job(1, 'failed'));
    await startFiles([A]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Import failed: a.xml'));
    expect(within(rowOf(A.name)).getByText('Import failed')).toBeInTheDocument();
    fireEvent.click(within(rowOf(A.name)).getByRole('button', { name: 'Why it failed' }));
    expect(navigate).toHaveBeenCalledWith('/parse-errors?job_id=1');
  });

  it('"Why it failed" opens the parse error when the job names one', async () => {
    server.jobs.set(1, job(1, 'failed', { failure_reason: 'Not an Nmap file', parse_error_id: 55 }));
    await startFiles([A]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Import failed: a.xml'));
    fireEvent.click(within(rowOf(A.name)).getByRole('button', { name: 'Why it failed' }));
    expect(navigate).toHaveBeenCalledWith('/parse-errors?error_id=55');
  });

  it('files that finish together have their results asked for in ONE request', async () => {
    server.jobs.set(1, job(1, 'processing'));
    server.jobs.set(2, job(2, 'processing'));
    server.scans.set(7, result(7, { new_hosts: 1, updated_hosts: 0 }));
    server.scans.set(8, result(8, { new_hosts: 0, updated_hosts: 5 }));
    await startFiles([A, B]);
    await waitFor(() => expect(headingOf(B.name)).toBe('Processing: b.xml'));
    expect(resultReads()).toHaveLength(0);

    server.jobs.set(1, job(1, 'completed', { scan_id: 7 }));
    server.jobs.set(2, job(2, 'completed', { scan_id: 8 }));
    await pass(TICK);

    expect(await within(rowOf(A.name)).findByLabelText('Import result')).toHaveTextContent('+1 host added');
    expect(await within(rowOf(B.name)).findByLabelText('Import result')).toHaveTextContent('5 already known');
    expect(resultReads()).toHaveLength(1);
    expect([...resultReads()[0]].sort()).toEqual([7, 8]);
    // …and nothing is asked afterwards: not the jobs, not the results.
    const reads = jobReads().length;
    await pass(TICK * 3);
    expect(jobReads()).toHaveLength(reads);
    expect(resultReads()).toHaveLength(1);
  });

  it('a result is asked for once, however long another file keeps the banner polling', async () => {
    server.jobs.set(1, job(1, 'completed', { scan_id: 7 }));
    server.jobs.set(2, job(2, 'processing', { message: 'Still reading' }));
    server.scans.set(7, result(7));
    await startFiles([A, B]);
    expect(await within(rowOf(A.name)).findByLabelText('Import result')).toHaveTextContent('+12 hosts added');
    expect(resultReads()).toHaveLength(1);

    await pass(TICK * 4);
    expect(resultReads()).toHaveLength(1);
    expect(headingOf(A.name)).toBe('Imported: a.xml');
    expect(within(rowOf(A.name)).getByLabelText('Import result')).toHaveTextContent('+12 hosts added');
    expect(rowOf(B.name)).toHaveTextContent('Still reading');
  });

  it('a poll names only the jobs still running: a finished one is not asked about again', async () => {
    server.jobs.set(1, job(1, 'failed', { failure_reason: 'Not an Nmap file' }));
    server.jobs.set(2, job(2, 'processing', { message: 'Reading b' }));
    await startFiles([A, B]);
    await waitFor(() => expect(headingOf(A.name)).toBe('Import failed: a.xml'));
    await waitFor(() => expect(rowOf(B.name)).toHaveTextContent('Reading b'));
    await pass(50);
    const before = jobReads().length;

    // Whatever the server would now say about the finished job, it is not asked.
    server.jobs.set(1, job(1, 'processing', { message: 'Retried elsewhere' }));
    await pass(TICK);
    await pass(TICK);
    expect(jobReads()).toHaveLength(before + 2);
    expect(jobReads()[before]).toEqual([2]);
    expect(jobReads()[before + 1]).toEqual([2]);
    expect(headingOf(A.name)).toBe('Import failed: a.xml');
    expect(rowOf(A.name)).toHaveTextContent('Not an Nmap file');
  });

  it('when the imported files are read again for another reason, a known result and a finished job are not asked about', async () => {
    server.jobs.set(1, job(1, 'completed', { scan_id: 7 }));
    server.scans.set(7, result(7));
    await startFiles([A]);
    expect(await within(rowOf(A.name)).findByLabelText('Import result')).toHaveTextContent('+12 hosts added');
    await pass(50);
    const jobsBefore = jobReads().length;
    const historyBefore = api.getImportHistory.mock.calls.length;

    // Another tab or an agent imported a file: the page's marker moves and
    // the lists and the queue are read again.
    api.getScanInventoryMarker.mockResolvedValue({ count: 1, latest_id: 99 });
    await pass(15_000);
    await waitFor(() => expect(api.getImportHistory.mock.calls.length).toBeGreaterThan(historyBefore));
    await pass(TICK);
    expect(resultReads()).toHaveLength(1);
    expect(jobReads()).toHaveLength(jobsBefore);
    expect(within(rowOf(A.name)).getByLabelText('Import result')).toHaveTextContent('+12 hosts added');
  });

  it('a result that cannot be read leaves the job\'s own message, is not asked for again, and the other rows are right', async () => {
    server.scansFail = true;
    server.jobs.set(1, job(1, 'completed', { scan_id: 7, message: 'Parsed 50 hosts.' }));
    server.jobs.set(2, job(2, 'failed', { failure_reason: 'Not an Nmap file' }));
    await startFiles([A, B]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Imported: a.xml'));
    await waitFor(() => expect(resultReads()).toHaveLength(1));
    await pass(50);
    expect(rowOf(A.name)).toHaveTextContent('Parsed 50 hosts.');
    expect(within(rowOf(A.name)).queryByLabelText('Import result')).toBeNull();
    expect(headingOf(B.name)).toBe('Import failed: b.xml');
    expect(rowOf(B.name)).toHaveTextContent('Not an Nmap file');

    server.scansFail = false;
    await pass(TICK * 3);
    expect(resultReads()).toHaveLength(1);
    expect(rowOf(A.name)).toHaveTextContent('Parsed 50 hosts.');
  });

  it('a completed job with no message and no readable result reads "Import complete."', async () => {
    server.scansFail = true;
    server.jobs.set(1, job(1, 'completed', { scan_id: 7 }));
    await startFiles([A]);

    await waitFor(() => expect(headingOf(A.name)).toBe('Imported: a.xml'));
    await waitFor(() => expect(resultReads()).toHaveLength(1));
    expect(rowOf(A.name)).toHaveTextContent('Import complete.');
  });

  it('a job the server no longer returns stops being asked about, and the queue is read again', async () => {
    // Nothing in `server.jobs`: the answer names no job.
    await startFiles([A]);
    await waitFor(() => expect(jobReads()).toHaveLength(1));
    await pass(50);
    expect(headingOf(A.name)).toBe('Upload received, waiting for the worker: a.xml');
    const queueReads = api.getRecentIngestionJobs.mock.calls.length;
    expect(queueReads).toBeGreaterThanOrEqual(2);   // the page's own read, then the one for the vanished job

    await pass(TICK * 3);
    expect(jobReads()).toHaveLength(1);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(queueReads);
    expect(headingOf(A.name)).toBe('Upload received, waiting for the worker: a.xml');
  });

  it('polls with ONE request per tick for every running job, and not at all once they are finished', async () => {
    server.jobs.set(1, job(1, 'queued'));
    server.jobs.set(2, job(2, 'processing'));
    await startFiles([A, B]);
    await waitFor(() => expect(headingOf(B.name)).toBe('Processing: b.xml'));
    const started = jobReads().length;

    await pass(TICK - 500);
    expect(jobReads()).toHaveLength(started);
    await pass(500);
    expect(jobReads()).toHaveLength(started + 1);
    expect([...jobReads()[started]].sort()).toEqual([1, 2]);
    await pass(TICK);
    expect(jobReads()).toHaveLength(started + 2);
    expect([...jobReads()[started + 1]].sort()).toEqual([1, 2]);

    server.jobs.set(1, job(1, 'failed', { failure_reason: 'Not an Nmap file' }));
    server.jobs.set(2, job(2, 'completed', { scan_id: null }));
    await pass(TICK);
    expect(jobReads()).toHaveLength(started + 3);
    expect(headingOf(A.name)).toBe('Import failed: a.xml');
    expect(headingOf(B.name)).toBe('Imported: b.xml');

    await pass(TICK * 5);
    expect(jobReads()).toHaveLength(started + 3);
  });

  it('asks nothing while the tab is hidden, and once at once when the reader returns', async () => {
    server.jobs.set(1, job(1, 'processing', { message: 'Reading' }));
    await startFiles([A]);
    await waitFor(() => expect(headingOf(A.name)).toBe('Processing: a.xml'));
    const before = jobReads().length;

    setVisibility('hidden');
    await pass(TICK * 4);
    expect(jobReads()).toHaveLength(before);

    server.jobs.set(1, job(1, 'processing', { message: 'Nearly done' }));
    setVisibility('visible');
    await pass(50);
    expect(jobReads()).toHaveLength(before + 1);
    expect(rowOf(A.name)).toHaveTextContent('Nearly done');
  });

  it('a failed poll keeps what the banner said, and the next one is waited for twice as long', async () => {
    server.jobs.set(1, job(1, 'processing', { message: 'Reading hosts 10 / 50' }));
    await startFiles([A]);
    await waitFor(() => expect(rowOf(A.name)).toHaveTextContent('Reading hosts 10 / 50'));
    const before = jobReads().length;

    server.jobsFail = true;
    await pass(TICK);
    expect(jobReads()).toHaveLength(before + 1);
    expect(headingOf(A.name)).toBe('Processing: a.xml');
    expect(rowOf(A.name)).toHaveTextContent('Reading hosts 10 / 50');

    server.jobsFail = false;
    server.jobs.set(1, job(1, 'processing', { message: 'Reading hosts 40 / 50' }));
    await pass(TICK);
    expect(jobReads()).toHaveLength(before + 1);
    await pass(TICK);
    expect(jobReads()).toHaveLength(before + 2);
    expect(rowOf(A.name)).toHaveTextContent('Reading hosts 40 / 50');
  });

  it('a file started later is asked about at once and followed with the first, still in one request per tick', async () => {
    server.jobs.set(1, job(1, 'processing', { message: 'Reading a' }));
    server.jobs.set(2, job(2, 'processing', { message: 'Reading b' }));
    await openReview([A, B]);
    await importOne(A.name);
    await waitFor(() => expect(rowOf(A.name)).toHaveTextContent('Reading a'));
    expect(queryRow(B.name)).toBeNull();
    expect(jobReads().every((ids) => !ids.includes(2))).toBe(true);
    const before = jobReads().length;

    await importOne(B.name);
    await waitFor(() => expect(rowOf(B.name)).toHaveTextContent('Reading b'));
    expect(jobReads()).toHaveLength(before + 1);
    expect([...jobReads()[before]].sort()).toEqual([1, 2]);
    // The first file's row did not go back to "received" while the second was asked about.
    expect(headingOf(A.name)).toBe('Processing: a.xml');

    await pass(TICK);
    expect(jobReads()).toHaveLength(before + 2);
    expect([...jobReads()[before + 1]].sort()).toEqual([1, 2]);
  });

  it('a file started after the first has finished is followed, and the first keeps its result without asking again', async () => {
    server.jobs.set(1, job(1, 'completed', { scan_id: 7 }));
    server.jobs.set(2, job(2, 'processing', { message: 'Reading b' }));
    server.scans.set(7, result(7));
    await openReview([A, B]);
    await importOne(A.name);
    expect(await within(rowOf(A.name)).findByLabelText('Import result')).toHaveTextContent('+12 hosts added');
    const before = jobReads().length;
    await pass(TICK * 2);
    expect(jobReads()).toHaveLength(before);

    await importOne(B.name);
    await waitFor(() => expect(rowOf(B.name)).toHaveTextContent('Reading b'));
    expect(jobReads()).toHaveLength(before + 1);
    expect(jobReads()[before]).toContain(2);
    expect(headingOf(A.name)).toBe('Imported: a.xml');
    expect(within(rowOf(A.name)).getByLabelText('Import result')).toHaveTextContent('+12 hosts added');

    await pass(TICK);
    expect(jobReads()).toHaveLength(before + 2);
    expect(resultReads()).toHaveLength(1);
  });

  it('when the first read for a file started later fails, the earlier file keeps what it said', async () => {
    server.jobs.set(1, job(1, 'processing', { message: 'Reading a' }));
    server.jobs.set(2, job(2, 'processing', { message: 'Reading b' }));
    await openReview([A, B]);
    await importOne(A.name);
    await waitFor(() => expect(rowOf(A.name)).toHaveTextContent('Reading a'));

    server.jobsFail = true;
    await importOne(B.name);
    expect(headingOf(A.name)).toBe('Processing: a.xml');
    expect(rowOf(A.name)).toHaveTextContent('Reading a');
    expect(headingOf(B.name)).toBe('Upload received, waiting for the worker: b.xml');

    // It is tried again (half as often while failing) and both rows follow.
    server.jobsFail = false;
    await pass(TICK * 2);
    expect(rowOf(B.name)).toHaveTextContent('Reading b');
    expect(rowOf(A.name)).toHaveTextContent('Reading a');
  });

  it('a finished import refreshes the queue and the imported files ONCE; later polls do not', async () => {
    server.jobs.set(1, job(1, 'processing'));
    server.jobs.set(2, job(2, 'processing'));
    server.scans.set(7, result(7));
    await startFiles([A, B]);
    await waitFor(() => expect(headingOf(A.name)).toBe('Processing: a.xml'));
    await pass(50);
    const reads = () => ({
      recent: api.getRecentIngestionJobs.mock.calls.length,
      staged: api.getStagedIngestionJobs.mock.calls.length,
      history: api.getImportHistory.mock.calls.length,
      summary: api.getScansSummary.mock.calls.length,
    });
    const before = reads();

    // Nothing finished: a poll refreshes nothing.
    await pass(TICK);
    expect(reads()).toEqual(before);

    server.jobs.set(1, job(1, 'completed', { scan_id: 7 }));
    await pass(TICK);
    await waitFor(() => expect(headingOf(A.name)).toBe('Imported: a.xml'));
    await pass(50);
    expect(reads()).toEqual({
      recent: before.recent + 1, staged: before.staged + 1, history: before.history + 1, summary: before.summary + 1,
    });

    // The other file keeps the banner polling: the finished one refreshes nothing again.
    const after = reads();
    await pass(TICK * 3);
    expect(reads()).toEqual(after);
  });

  it('a failed import refreshes the queue, not the imported files', async () => {
    server.jobs.set(1, job(1, 'processing'));
    await startFiles([A]);
    await waitFor(() => expect(headingOf(A.name)).toBe('Processing: a.xml'));
    await pass(50);
    const recent = api.getRecentIngestionJobs.mock.calls.length;
    const staged_ = api.getStagedIngestionJobs.mock.calls.length;
    const history = api.getImportHistory.mock.calls.length;
    const summary = api.getScansSummary.mock.calls.length;

    server.jobs.set(1, job(1, 'failed', { failure_reason: 'Not an Nmap file' }));
    await pass(TICK);
    await waitFor(() => expect(headingOf(A.name)).toBe('Import failed: a.xml'));
    await pass(50);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(recent + 1);
    expect(api.getStagedIngestionJobs).toHaveBeenCalledTimes(staged_ + 1);
    expect(api.getImportHistory).toHaveBeenCalledTimes(history);
    expect(api.getScansSummary).toHaveBeenCalledTimes(summary);
  });

  it('a finished row is dismissed by the reader: it goes, the others stay, nothing is asked, and it does not come back', async () => {
    server.jobs.set(1, job(1, 'completed', { scan_id: 7 }));
    server.jobs.set(2, job(2, 'processing', { message: 'Reading b' }));
    server.scans.set(7, result(7));
    await startFiles([A, B]);
    expect(await within(rowOf(A.name)).findByLabelText('Import result')).toBeInTheDocument();
    await pass(50);
    const jobsBefore = jobReads().length;
    const resultsBefore = resultReads().length;

    fireEvent.click(within(rowOf(A.name)).getByRole('button', { name: 'Dismiss a.xml progress' }));
    expect(queryRow(A.name)).toBeNull();
    expect(rowOf(B.name)).toHaveTextContent('Reading b');
    await pass(50);
    expect(jobReads()).toHaveLength(jobsBefore);
    expect(resultReads()).toHaveLength(resultsBefore);

    // The other file is still followed, one request per tick, and the
    // dismissed row stays gone.
    await pass(TICK);
    expect(jobReads()).toHaveLength(jobsBefore + 1);
    expect(queryRow(A.name)).toBeNull();
    expect(rowOf(B.name)).toHaveTextContent('Reading b');
    expect(resultReads()).toHaveLength(resultsBefore);
  });

  it('dismissing the last row removes the banner and asks nothing', async () => {
    server.jobs.set(1, job(1, 'failed', { failure_reason: 'Not an Nmap file' }));
    await startFiles([A]);
    await waitFor(() => expect(headingOf(A.name)).toBe('Import failed: a.xml'));
    await pass(50);
    const before = jobReads().length;

    fireEvent.click(within(rowOf(A.name)).getByRole('button', { name: 'Dismiss a.xml progress' }));
    expect(queryRow(A.name)).toBeNull();
    await pass(TICK * 3);
    expect(jobReads()).toHaveLength(before);
    expect(queryRow(A.name)).toBeNull();
  });
});
