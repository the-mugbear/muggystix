import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import InventoryDownloadDialog from '../../components/InventoryDownloadDialog';
import * as api from '../../services/api';

vi.mock('../../services/api', () => ({
  downloadInventoryCsv: vi.fn(),
  enqueueInventoryJson: vi.fn(),
  downloadReportJob: vi.fn(),
  listReportJobs: vi.fn().mockResolvedValue([]),
  dismissReportJob: vi.fn(),
  retryReportJob: vi.fn(),
  cancelReportJob: vi.fn(),
}));

const mocked = api as unknown as Record<
  'downloadInventoryCsv' | 'enqueueInventoryJson' | 'downloadReportJob' | 'listReportJobs'
  | 'dismissReportJob' | 'retryReportJob' | 'cancelReportJob',
  ReturnType<typeof vi.fn>
>;
const job = (id: number, status: string, over: Record<string, unknown> = {}) => ({
  id, project_id: 1, status, format: 'json', report_type: 'comprehensive',
  created_at: '2026-10-07T10:00:00Z', ...over,
});

describe('InventoryDownloadDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listReportJobs.mockResolvedValue([]);
  });
  afterEach(() => { vi.useRealTimers(); });

  // The retirement of "Export hosts" (owner, 2026-10-07): two downloads, and
  // nothing that offers the HTML report, the Markdown bundle or the agent
  // dataset.
  it('offers the CSV and the JSON, and nothing else', async () => {
    render(<InventoryDownloadDialog open onClose={vi.fn()} filters={{}} totalHosts={3000} />);
    expect(screen.getByRole('heading', { name: 'Download inventory' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Prepare JSON' })).toBeInTheDocument();
    expect(screen.getByText(/3,000/)).toBeInTheDocument();
    expect(screen.getByText(/both files hold all of them/)).toBeInTheDocument();
    expect(screen.queryByText(/HTML|Markdown|agent dataset|dossier|Report type/i)).toBeNull();
    // No cap exists any more, so none is stated.
    expect(screen.queryByText(/includes the first|capped|incomplete/i)).toBeNull();
    await waitFor(() => expect(mocked.listReportJobs).toHaveBeenCalled());
  });

  it('names the filters that narrow both files', () => {
    render(
      <InventoryDownloadDialog
        open onClose={vi.fn()} totalHosts={1}
        filters={{ state: 'up', q: 'port:445', has_critical_vulns: true, has_high_vulns: true, orgs: ['Example, Inc.'], sort_by: 'ip_address' }}
      />,
    );
    const list = screen.getByRole('list', { name: 'Active filters' });
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Query: port:445', 'State: up', 'Scanner severity: Critical or High', 'Organisation: Example, Inc.',
    ]);
    expect(screen.getByText(/host matches/)).toBeInTheDocument();
  });

  it('downloads the CSV with the page’s filters and closes', async () => {
    mocked.downloadInventoryCsv.mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<InventoryDownloadDialog open onClose={onClose} filters={{ state: 'up' }} totalHosts={10} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    await waitFor(() => expect(mocked.downloadInventoryCsv).toHaveBeenCalledWith(1, { state: 'up' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mocked.enqueueInventoryJson).not.toHaveBeenCalled();
  });

  it('says why when the CSV cannot be downloaded, and stays open', async () => {
    mocked.downloadInventoryCsv.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 503'), { response: { status: 503, data: {} } }),
    );
    const onClose = vi.fn();
    render(<InventoryDownloadDialog open onClose={onClose} filters={{}} totalHosts={10} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/server is having trouble/i);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled();
  });

  // Review 2026-09-09 Ops-1 — a worker job must not hold the dialog hostage:
  // queue it, show it in the list, and let the reader leave.
  it('queues the JSON, shows it queued, and leaves the dialog usable', async () => {
    const queued = job(7, 'queued');
    mocked.enqueueInventoryJson.mockResolvedValue(queued);
    mocked.listReportJobs.mockResolvedValue([queued]);
    const onClose = vi.fn();

    render(<InventoryDownloadDialog open onClose={onClose} filters={{ state: 'up' }} totalHosts={10} />);
    fireEvent.click(screen.getByRole('button', { name: 'Prepare JSON' }));

    await waitFor(() => expect(mocked.enqueueInventoryJson).toHaveBeenCalledWith(1, { state: 'up' }));
    await waitFor(() => expect(screen.getByTestId('tracked-job-running')).toHaveTextContent(/queued/));
    expect(within(screen.getByTestId('inventory-job-7')).getByText('Queued')).toBeInTheDocument();
    expect(mocked.downloadReportJob).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // Nothing is "busy" client-side: the CSV is still one click away.
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onClose).toHaveBeenCalled();
  });

  // An opening starts clean of the last one's refusal — and still follows the
  // JSON that was started from this dialog.
  it('a refusal is not carried to the next opening; the JSON started here is still followed', async () => {
    const queued = job(7, 'queued');
    mocked.enqueueInventoryJson.mockResolvedValue(queued);
    mocked.listReportJobs.mockResolvedValue([queued]);
    mocked.downloadInventoryCsv.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 503'), { response: { status: 503, data: {} } }),
    );
    const dialog = (open: boolean) => (
      <InventoryDownloadDialog open={open} onClose={() => {}} filters={{}} totalHosts={10} />
    );
    const { rerender } = render(dialog(true));
    fireEvent.click(screen.getByRole('button', { name: 'Prepare JSON' }));
    await screen.findByTestId('tracked-job-running');
    fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    expect(await screen.findByText(/server is having trouble/i)).toBeInTheDocument();

    rerender(dialog(false));
    await waitFor(() => expect(screen.queryByText('Download inventory')).toBeNull());
    rerender(dialog(true));
    expect(await screen.findByTestId('tracked-job-running')).toHaveTextContent(/queued/);
    expect(screen.queryByText(/server is having trouble/i)).toBeNull();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled();
  });

  it('reopening shows a JSON prepared earlier and downloads it without closing', async () => {
    mocked.listReportJobs.mockResolvedValue([job(9, 'completed')]);
    mocked.downloadReportJob.mockResolvedValue(undefined);
    const onClose = vi.fn();

    const { rerender } = render(<InventoryDownloadDialog open={false} onClose={onClose} filters={{}} totalHosts={5} />);
    expect(screen.queryByText('Recent JSON downloads')).toBeNull();
    rerender(<InventoryDownloadDialog open onClose={onClose} filters={{}} totalHosts={5} />);
    // The list comes from the API, so the job is there without any client state.
    const row = await screen.findByTestId('inventory-job-9');
    expect(within(row).getByText('Ready')).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(mocked.downloadReportJob).toHaveBeenCalledWith(1, 9));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('says why when a listed file can no longer be downloaded', async () => {
    mocked.listReportJobs.mockResolvedValue([job(9, 'completed')]);
    mocked.downloadReportJob.mockRejectedValue(Object.assign(new Error('410'), {
      response: { status: 410, data: { detail: 'Report artifact has expired or been removed.' } },
    }));
    render(<InventoryDownloadDialog open onClose={vi.fn()} filters={{}} totalHosts={5} />);
    fireEvent.click(within(await screen.findByTestId('inventory-job-9')).getByRole('button', { name: 'Download' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Report artifact has expired or been removed.');
  });

  it('offers Download when the JSON becomes ready while the dialog is open, then closes', async () => {
    const queued = job(11, 'queued');
    mocked.enqueueInventoryJson.mockResolvedValue(queued);
    mocked.listReportJobs.mockResolvedValue([queued]);
    mocked.downloadReportJob.mockResolvedValue(undefined);
    const onClose = vi.fn();
    // The poll is the query's own interval now (it was a mocked hook whose
    // callback the test called): the clock is what brings the next refresh.
    vi.useFakeTimers({ shouldAdvanceTime: true });

    render(<InventoryDownloadDialog open onClose={onClose} filters={{}} totalHosts={5} />);
    fireEvent.click(screen.getByRole('button', { name: 'Prepare JSON' }));
    await waitFor(() => expect(screen.getByTestId('tracked-job-running')).toBeInTheDocument());

    // The next refresh (what the poll does) reports completion.
    mocked.listReportJobs.mockResolvedValue([{ ...queued, status: 'completed' }]);
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

    const ready = await screen.findByTestId('tracked-job-ready');
    fireEvent.click(within(ready).getByRole('button', { name: 'Download JSON' }));
    await waitFor(() => expect(mocked.downloadReportJob).toHaveBeenCalledWith(1, 11));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('polls only while a job is still running', async () => {
    // Pinned by the requests made as the clock runs (it read the interval
    // handed to the mocked polling hook).
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocked.listReportJobs.mockResolvedValue([job(1, 'completed'), job(2, 'failed')]);
    const finished = render(<InventoryDownloadDialog open onClose={vi.fn()} filters={{}} totalHosts={5} />);
    await screen.findByTestId('inventory-job-1');
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocked.listReportJobs).toHaveBeenCalledTimes(1);
    finished.unmount();

    mocked.listReportJobs.mockClear();
    mocked.listReportJobs.mockResolvedValue([job(3, 'processing')]);
    render(<InventoryDownloadDialog open onClose={vi.fn()} filters={{}} totalHosts={5} />);
    await screen.findByTestId('inventory-job-3');
    expect(mocked.listReportJobs).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(mocked.listReportJobs).toHaveBeenCalledTimes(2);
  });

  it('says why the JSON failed', async () => {
    const failed = job(8, 'failed', { error_message: 'render exploded' });
    mocked.enqueueInventoryJson.mockResolvedValue(failed);
    mocked.listReportJobs.mockResolvedValue([failed]);
    const onClose = vi.fn();

    render(<InventoryDownloadDialog open onClose={onClose} filters={{}} totalHosts={5} />);
    fireEvent.click(screen.getByRole('button', { name: 'Prepare JSON' }));

    await waitFor(() => expect(screen.getByTestId('tracked-job-failed')).toHaveTextContent(/render exploded/i));
    expect(mocked.downloadReportJob).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  // Worst-case text (style guide §4): a long query or a long worker error
  // must truncate inside the dialog, with the whole text on hover.
  it('truncates a long filter and a long failure, keeping the whole text on hover', async () => {
    const longQuery = `hostname:${'a'.repeat(200)}`;
    const longError = `boom ${'x'.repeat(300)}`;
    mocked.listReportJobs.mockResolvedValue([job(4, 'failed', { error_message: longError })]);
    render(<InventoryDownloadDialog open onClose={vi.fn()} filters={{ q: longQuery }} totalHosts={5} />);
    const chip = screen.getByText(`Query: ${longQuery}`);
    expect(chip).toHaveClass('truncate');
    expect(chip).toHaveAttribute('title', `Query: ${longQuery}`);
    const failure = await screen.findByText(longError);
    expect(failure).toHaveClass('truncate');
    expect(failure).toHaveAttribute('title', longError);
  });
});
