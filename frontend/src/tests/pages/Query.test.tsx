/**
 * The Query page (5.405.0): SPL-style queries, their answers, every group's
 * drill-down, and saved / shared queries.  The committed query is the
 * address; the editor is a draft of it.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  runQuery: vi.fn(),
  getQuerySchema: vi.fn(),
  listSavedQueries: vi.fn(),
  createSavedQuery: vi.fn(),
  updateSavedQuery: vi.fn(),
  deleteSavedQuery: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 9 } }) }));

import { TooltipProvider } from '../../components/ui/tooltip';
import Query from '../../pages/Query';

const STATS = {
  query: 'ports | stats dc(host) as hosts by service',
  dataset: 'ports', kind: 'stats', total: 2, limit: 1000, capped: false,
  columns: [{ name: 'service', type: 'text' }, { name: 'hosts', type: 'int' }],
  rows: [['ssh', 12], [null, 3]],
  drilldowns: ['ports | where service="ssh"', 'ports | where isnull(service)'],
};
const ROWS = {
  query: 'hosts', dataset: 'hosts', kind: 'rows', total: 120, limit: 500, capped: false,
  columns: [{ name: 'ip', type: 'text' }, { name: 'first_seen', type: 'datetime' }],
  rows: [['10.0.0.1', null]], drilldowns: null,
};

/** Where the address is now — the page's own writes land here. */
const Address: React.FC = () => {
  const [params] = useSearchParams();
  return <p data-testid="address">{params.get('q') ?? ''}</p>;
};

const renderAt = (url: string) => render(
  <TooltipProvider>
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/query" element={<><Query /><Address /></>} />
      </Routes>
    </MemoryRouter>
  </TooltipProvider>,
);

beforeEach(() => {
  vi.clearAllMocks();
  api.listSavedQueries.mockResolvedValue([]);
});

describe('Query page', () => {
  it('runs the query the address names and says how much there was', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    renderAt('/query?q=hosts');
    expect(await screen.findByTestId('query-answer')).toBeInTheDocument();
    expect(api.runQuery).toHaveBeenCalledWith(9, 'hosts', expect.any(AbortSignal));
    expect(screen.getByTestId('query-count')).toHaveTextContent('The first 1 of 120 rows · hosts');
    // A null is a dash, never "null".
    expect(within(screen.getByTestId('query-answer')).getByText('—')).toBeInTheDocument();
    expect(screen.getByLabelText('Query')).toHaveValue('hosts');
  });

  it('asks nothing until there is a query, and Run commits the draft to the address', async () => {
    api.runQuery.mockResolvedValue(STATS);
    renderAt('/query');
    expect(api.runQuery).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Query'), { target: { value: '  ports | stats dc(host) as hosts by service ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('ports | stats dc(host) as hosts by service'));
    expect(api.runQuery).toHaveBeenCalledWith(9, 'ports | stats dc(host) as hosts by service', expect.any(AbortSignal));
  });

  it('Ctrl + Enter runs it', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    renderAt('/query');
    const box = screen.getByLabelText('Query');
    fireEvent.change(box, { target: { value: 'hosts' } });
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('hosts'));
  });

  it('every row of a stats answer opens exactly its rows', async () => {
    api.runQuery.mockResolvedValue(STATS);
    renderAt(`/query?q=${encodeURIComponent(STATS.query)}`);
    const table = await screen.findByTestId('query-answer');
    const links = within(table).getAllByRole('link', { name: 'Open rows' });
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      `/query?q=${encodeURIComponent('ports | where service="ssh"')}`,
      `/query?q=${encodeURIComponent('ports | where isnull(service)')}`,
    ]);
    expect(screen.getByTestId('query-count')).toHaveTextContent('2 groups · ports');
  });

  it('a query the server cannot read says why and points at the place', async () => {
    api.runQuery.mockRejectedValue({ response: { status: 400, data: { detail: "Unknown column 'bogus' in hosts.", position: 14 } } });
    renderAt(`/query?q=${encodeURIComponent('hosts | where bogus=1')}`);
    const error = await screen.findByTestId('query-error');
    expect(error).toHaveTextContent("Unknown column 'bogus' in hosts. (at character 15)");
    expect(within(error).getByText('b', { selector: 'mark' })).toBeInTheDocument();
  });

  it('saves the draft, shared or not, and refreshes the list', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    api.createSavedQuery.mockResolvedValue({ id: 1, name: 'All hosts', query: 'hosts', shared: true });
    renderAt('/query?q=hosts');
    await screen.findByTestId('query-answer');
    fireEvent.click(screen.getByRole('button', { name: /Save…/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: ' All hosts ' } });
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Share with the project' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.createSavedQuery).toHaveBeenCalledWith(9, {
      name: 'All hosts', query: 'hosts', description: undefined, shared: true,
    }));
    expect(toastMock.success).toHaveBeenCalledWith('Saved “All hosts”, shared with the project');
    await waitFor(() => expect(api.listSavedQueries).toHaveBeenCalledTimes(2));
  });

  it('lists mine and the shared ones; a name runs it; only what I may change has Edit / Delete', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    api.listSavedQueries.mockResolvedValue([
      { id: 1, name: 'Mine', description: null, query: 'hosts', shared: false, created_by: 'me', mine: true, can_modify: true, created_at: null, updated_at: null },
      { id: 2, name: 'Team view', description: 'By OS', query: 'hosts | stats count by os_family', shared: true, created_by: 'Alice', mine: false, can_modify: false, created_at: null, updated_at: null },
    ]);
    renderAt('/query');
    const table = await screen.findByTestId('saved-queries');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(within(rows[0]).getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent('Shared');
    expect(rows[1]).toHaveTextContent('Alice');
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Team view' }));
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('hosts | stats count by os_family'));
    // The editor follows the address it was sent to.
    await waitFor(() => expect(screen.getByLabelText('Query')).toHaveValue('hosts | stats count by os_family'));
  });

  it('says a failed saved-queries read is a failure, not "none"', async () => {
    api.listSavedQueries.mockRejectedValue({ response: { status: 500, data: { detail: 'down' } } });
    renderAt('/query');
    expect(await screen.findByText(/down|Could not load the saved queries/)).toBeInTheDocument();
    expect(screen.queryByText(/None yet/)).not.toBeInTheDocument();
  });
});
