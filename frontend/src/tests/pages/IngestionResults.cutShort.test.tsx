/**
 * Ingestion results — a failed import whose file was cut short (owner
 * decision 2026-10-11: "Ask the user if it's a partial file whether they
 * want to keep partial results").
 *
 * The row says so in plain words, with how much the file holds; it offers
 * "Import what the file holds" beside the retry, with one sentence about what
 * that does; pressing it IS the answer (no confirmation) and re-queues the
 * job with the instruction.  The offer follows the server's KIND
 * (`failure_kind: 'truncated_file'`), never the wording of a message; an
 * invalid file gets none; a viewer or auditor sees none.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

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
const who = vi.hoisted(() => ({ projectRole: 'analyst' as string, accountRole: 'member' as string }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));

vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: who.projectRole } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: who.accountRole }, hasPermission: () => who.accountRole === 'admin' }),
}));
vi.mock('../../components/scans/ImportResult', () => ({ default: () => null }));

import IngestionResults from '../../pages/IngestionResults';
import { TooltipProvider } from '../../components/ui/tooltip';
import { KEEP_TRUNCATED_EXPLANATION } from '../../components/scans/KeepTruncatedImport';

const CUT = 'This whatweb JSON file was cut short: it ends after 28,253 complete records, before the end of the document.';

const row = (over: Record<string, unknown>) => ({
  id: 1, parse_error_id: null, original_filename: 'scan.xml', status: 'completed',
  file_size: 1024, tool_name: 'whatweb', scan_type: null, scan_id: null,
  created_at: '2026-10-11T10:00:00Z', started_at: null, completed_at: null,
  duration_seconds: 171, progress: null, stats: null, error: null, ...over,
});

const cutShort = (over: Record<string, unknown> = {}) => row({
  id: 41, status: 'failed', original_filename: 'whatweb-all.json', parse_error_id: 7,
  file_retained: true, failure_kind: 'truncated_file', failure_reason: CUT,
  error: {
    error_type: 'truncated_file', error_message: CUT,
    user_message: `${CUT} Nothing from this file was imported.`,
  },
  ...over,
});

const invalid = row({
  id: 42, status: 'failed', original_filename: 'broken.json', parse_error_id: 8,
  file_retained: true, failure_kind: 'parsing_error',
  failure_reason: 'Invalid whatweb JSON during streaming: lexical error: invalid char in json text.',
  error: {
    error_type: 'parsing_error',
    error_message: 'Invalid whatweb JSON during streaming: lexical error: invalid char in json text.',
    user_message: 'Invalid whatweb JSON during streaming: lexical error: invalid char in json text.',
  },
});

const response = (items: unknown[]) => ({
  items,
  total: items.length,
  summary: {
    total_needs_attention: items.length, total_staged: 0, total_completed: 0, total_failed: items.length,
    total_queued: 0, total_processing: 0,
    total_hosts: 0, total_hosts_up: 0, total_ports: 0, total_open_ports: 0,
  },
});

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/scans?view=results']}>
      <TooltipProvider>
        <Routes>
          <Route path="/scans" element={<IngestionResults />} />
        </Routes>
      </TooltipProvider>
    </MemoryRouter>,
  );

const expand = async (filename: string) => {
  const tableRow = (await screen.findByText(filename)).closest('tr')!;
  fireEvent.click(within(tableRow).getByRole('button', { name: 'Expand details' }));
  return tableRow;
};

beforeEach(() => {
  vi.clearAllMocks();
  who.projectRole = 'analyst';
  who.accountRole = 'member';
  api.getIngestionResults.mockResolvedValue(response([cutShort()]));
  api.retryIngestionJob.mockResolvedValue({ job_id: 41, status: 'queued', message: 'Job re-queued' });
});

describe('Ingestion results — a file that was cut short', () => {
  it('says so on the row without expanding it, with how much the file holds', async () => {
    renderPage();
    const tableRow = (await screen.findByText('whatweb-all.json')).closest('tr')!;
    expect(within(tableRow).getByText('failed')).toBeInTheDocument();
    expect(within(tableRow).getByTestId('failure-reason')).toHaveTextContent(CUT);
  });

  it('says what happened and offers to import what the file holds, beside the retry', async () => {
    renderPage();
    await expand('whatweb-all.json');

    expect(await screen.findByText('The file was cut short — nothing from it was imported')).toBeInTheDocument();
    // Not the raw kind as a heading, and the sentence once (not the server's
    // two renderings of it one under the other).
    expect(screen.queryByText(/Error type:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing from this file was imported\./)).not.toBeInTheDocument();
    expect(screen.getByText(/A retry reads the same file and fails the same way/)).toBeInTheDocument();

    const offer = screen.getByTestId('keep-truncated-import');
    expect(within(offer).getByRole('button', { name: 'Import what whatweb-all.json holds' }))
      .toHaveTextContent('Import what the file holds');
    expect(within(offer).getByText(KEEP_TRUNCATED_EXPLANATION)).toBeInTheDocument();
    expect(KEEP_TRUNCATED_EXPLANATION).toBe(
      'The import will stop where the file ends, keep everything before that point, and be marked partial.',
    );
    // The existing actions stay.
    expect(screen.getByRole('button', { name: 'Review format and retry' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
  });

  it('the button is the answer: it re-queues the job with the instruction and re-reads the list', async () => {
    renderPage();
    await expand('whatweb-all.json');
    const before = api.getIngestionResults.mock.calls.length;

    fireEvent.click(await screen.findByRole('button', { name: 'Import what whatweb-all.json holds' }));

    // No confirmation dialog in between.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(api.retryIngestionJob).toHaveBeenCalledTimes(1));
    // The project the page was showing, the job, and the instruction.
    expect(api.retryIngestionJob).toHaveBeenCalledWith(1, 41, { keepTruncated: true });
    await waitFor(() => expect(api.getIngestionResults.mock.calls.length).toBeGreaterThan(before));
    expect(toast.info).toHaveBeenCalledWith('whatweb-all.json queued — importing what the file holds');
  });

  it('says why when the server refuses, in the server\'s words', async () => {
    api.retryIngestionJob.mockRejectedValue({
      response: { status: 409, data: { detail: 'The uploaded file is no longer on disk — please re-upload to retry.' } },
    });
    renderPage();
    await expand('whatweb-all.json');
    fireEvent.click(await screen.findByRole('button', { name: 'Import what whatweb-all.json holds' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'The uploaded file is no longer on disk — please re-upload to retry.',
    ));
  });

  it('follows the kind, not the wording: an invalid file is never offered', async () => {
    // A message that happens to say "cut short" under another kind offers nothing either.
    api.getIngestionResults.mockResolvedValue(response([
      invalid,
      cutShort({ id: 43, original_filename: 'first-record.json', failure_kind: 'parsing_error',
        failure_reason: 'This whatweb JSON file was cut short before its first record was complete: there is nothing in it to import.' }),
    ]));
    renderPage();
    await expand('broken.json');
    expect(await screen.findByText('Error type: parsing_error')).toBeInTheDocument();
    expect(screen.queryByTestId('keep-truncated-import')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review format and retry' })).toBeInTheDocument();
  });

  it('offers nothing once the uploaded file is no longer retained', async () => {
    api.getIngestionResults.mockResolvedValue(response([cutShort({ file_retained: false })]));
    renderPage();
    await expand('whatweb-all.json');
    expect(await screen.findByText('The file was cut short — nothing from it was imported')).toBeInTheDocument();
    expect(screen.queryByTestId('keep-truncated-import')).not.toBeInTheDocument();
    expect(screen.getByText(/File no longer retained/)).toBeInTheDocument();
  });

  it.each(['viewer', 'auditor'])('hides the action from a project %s (hidden, not disabled)', async (role) => {
    who.projectRole = role;
    renderPage();
    await expand('whatweb-all.json');
    // They still read what happened…
    expect(await screen.findByText('The file was cut short — nothing from it was imported')).toBeInTheDocument();
    // …and are shown no way to answer.
    expect(screen.queryByTestId('keep-truncated-import')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Import what/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review format and retry' })).not.toBeInTheDocument();
  });

  it('the kept import reads as partial, with the warning that names the count', async () => {
    const kept = `${CUT} Those records were imported; anything the tool wrote after that point is not in the file.`;
    api.getIngestionResults.mockResolvedValue(response([row({
      id: 41, status: 'completed', original_filename: 'whatweb-all.json', partial: true,
      skipped_count: 0, parser_warnings: kept, file_retained: true, failure_kind: null,
    })]));
    renderPage();
    const tableRow = await expand('whatweb-all.json');
    expect(within(tableRow).getByText('completed')).toBeInTheDocument();
    expect(within(tableRow).getByText('partial')).toBeInTheDocument();
    expect(await screen.findByText('Partial import — part of this file is not in the inventory')).toBeInTheDocument();
    expect(screen.getByText(kept)).toBeInTheDocument();
    // Nothing left to ask; it can be dismissed like any partial import.
    expect(screen.queryByTestId('keep-truncated-import')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
  });
});
