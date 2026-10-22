/**
 * The Query page (5.405.0): pipe queries, their answers, every group's
 * drill-down, and saved / shared queries.  The committed query is the
 * address; the editor is a draft of it.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const role = vi.hoisted(() => ({ canExport: true }));
vi.mock('../../hooks/useProjectRole', () => ({ useProjectRole: () => role }));

const api = vi.hoisted(() => ({
  runQuery: vi.fn(),
  downloadQueryCsv: vi.fn(),
  getQuerySchema: vi.fn(),
  listSavedQueries: vi.fn(),
  createSavedQuery: vi.fn(),
  updateSavedQuery: vi.fn(),
  deleteSavedQuery: vi.fn(),
  listQueryHistory: vi.fn(),
  deleteQueryHistoryEntry: vi.fn(),
  clearQueryHistory: vi.fn(),
  getQueryColumnValues: vi.fn(),
  getHostQuerySchema: vi.fn(),
  suggestHostQueryValues: vi.fn(),
  parseQueryForBuilder: vi.fn(),
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
  return (
    <>
      <p data-testid="address">{params.get('q') ?? ''}</p>
      <p data-testid="search">{params.toString()}</p>
    </>
  );
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
  role.canExport = true;
  api.listSavedQueries.mockResolvedValue([]);
  api.listQueryHistory.mockResolvedValue({ items: [], total: 0 });
  api.getHostQuerySchema.mockResolvedValue({ fields: [], examples: [] });
  api.getQuerySchema.mockResolvedValue({
    datasets: [{ name: 'ports', description: 'ports', default_fields: [], examples: [],
      columns: [{ name: 'service', type: 'text', description: '' }] }],
    commands: [], caps: { rows: 500, groups: 1000, values: 20 },
  });
});

describe('Query page', () => {
  it('runs the query the address names and says how much there was', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    renderAt('/query?q=hosts');
    expect(await screen.findByTestId('query-answer')).toBeInTheDocument();
    expect(api.runQuery).toHaveBeenCalledWith(9, 'hosts', null, expect.any(AbortSignal));
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
    expect(api.runQuery).toHaveBeenCalledWith(9, 'ports | stats dc(host) as hosts by service', null, expect.any(AbortSignal));
  });

  // Q14 — an empty page teaches: three examples, from the schema, inline.
  it('an empty page shows the first example of three datasets', async () => {
    api.getQuerySchema.mockResolvedValue({
      datasets: ['hosts', 'ports', 'observations', 'findings'].map((name) => ({
        name, description: name, default_fields: [], columns: [], examples: [`${name} | stats count`, `${name} | head 1`],
      })),
      commands: [], caps: { rows: 500, groups: 1000, values: 20 },
    });
    api.runQuery.mockResolvedValue(STATS);
    renderAt('/query');
    const examples = await screen.findByTestId('query-examples');
    await waitFor(() => expect(within(examples).getAllByRole('button')).toHaveLength(3));
    expect(within(examples).getAllByRole('button').map((b) => b.textContent))
      .toEqual(['hosts | stats count', 'ports | stats count', 'observations | stats count']);
    fireEvent.click(within(examples).getByRole('button', { name: 'ports | stats count' }));
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('ports | stats count'));
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

describe('Query page — Phase 2 (5.406.0)', () => {
  it('each listing row opens its own page', async () => {
    api.runQuery.mockResolvedValue({ ...ROWS, total: 2, rows: [['10.0.0.1', null], ['10.0.0.9', null]], links: ['/findings/7', null] });
    renderAt('/query?q=findings');
    const table = await screen.findByTestId('query-answer');
    const links = within(table).getAllByRole('link', { name: 'Open' });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', '/findings/7');
  });

  it('downloads the answer as CSV — an auditor and up only', async () => {
    api.runQuery.mockResolvedValue(STATS);
    api.downloadQueryCsv.mockResolvedValue('query-ports.csv');
    const view = renderAt(`/query?q=${encodeURIComponent(STATS.query)}`);
    fireEvent.click(await screen.findByRole('button', { name: /Download CSV/ }));
    await waitFor(() => expect(api.downloadQueryCsv).toHaveBeenCalledWith(9, STATS.query, 'ports'));
    view.unmount();
    role.canExport = false;
    renderAt(`/query?q=${encodeURIComponent(STATS.query)}`);
    await screen.findByTestId('query-answer');
    expect(screen.queryByRole('button', { name: /Download CSV/ })).not.toBeInTheDocument();
  });
});

describe('Query page — the chart (5.406.0)', () => {
  it('offers a chart for one group column and a number, and keeps the view in the address', async () => {
    api.runQuery.mockResolvedValue({ ...STATS, by: ['service'] });
    renderAt(`/query?q=${encodeURIComponent(STATS.query)}`);
    await screen.findByTestId('query-answer');
    fireEvent.click(screen.getByRole('tab', { name: 'Chart' }));
    expect(await screen.findByTestId('query-chart')).toHaveTextContent('hosts by service');
    expect(screen.getByRole('img', { name: 'Bar chart of hosts by service' })).toBeInTheDocument();
    expect(screen.queryByTestId('query-answer')).not.toBeInTheDocument();
  });

  it('offers no chart for a listing, or for two group columns', async () => {
    api.runQuery.mockResolvedValue({
      ...STATS, by: ['service', 'port'],
      columns: [{ name: 'service', type: 'text' }, { name: 'port', type: 'int' }, { name: 'count', type: 'int' }],
      rows: [['ssh', 22, 3]], drilldowns: ['ports | where service="ssh" AND port=22'],
    });
    renderAt('/query?q=ports%20%7C%20stats%20count%20by%20service%2C%20port&view=chart');
    expect(await screen.findByTestId('query-answer')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Chart' })).not.toBeInTheDocument();
  });
});

describe('Query page — suggestions in the editor (5.406.0)', () => {
  it('suggests what may be typed at the caret; Tab takes it, Escape closes the list', async () => {
    renderAt('/query');
    const box = screen.getByLabelText('Query') as HTMLTextAreaElement;
    await waitFor(() => expect(api.getQuerySchema).toHaveBeenCalled());
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: 'ports | wh', selectionStart: 10, selectionEnd: 10 } });
    const list = await screen.findByRole('listbox', { name: 'Suggestions' });
    expect(within(list).getByRole('option', { name: /where/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(box, { key: 'Tab' });
    await waitFor(() => expect(box).toHaveValue('ports | where '));
    fireEvent.change(box, { target: { value: 'ports | where se', selectionStart: 16, selectionEnd: 16 } });
    expect(await screen.findByRole('option', { name: /service/ })).toBeInTheDocument();
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    // Ctrl + Enter still runs it.
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('ports | where se'));
  });
});

describe('Query page — the builder (5.406.0; one mind with the editor since 5.418.0)', () => {
  it('writes the editor’s text, no head on a listing, and the editor’s Run is the one Run', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    renderAt('/query');
    fireEvent.click(screen.getByRole('button', { name: 'Builder' }));
    const builder = await screen.findByTestId('query-builder');
    expect(api.parseQueryForBuilder).not.toHaveBeenCalled();   // nothing to read
    expect(within(builder).queryByRole('button', { name: /Run/ })).not.toBeInTheDocument();
    fireEvent.change(within(builder).getByLabelText('Hosts-language terms (optional)'), { target: { value: 'has:web' } });
    fireEvent.click(within(builder).getByRole('button', { name: 'Add a condition' }));
    expect(within(builder).getAllByTestId('builder-condition')).toHaveLength(1);
    // An unfinished condition writes nothing; a listing gets no head.
    expect(screen.getByLabelText('Query')).toHaveValue('ports has:web');
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('ports has:web'));
  });

  it('opens on the editor’s query as the server reads it', async () => {
    api.runQuery.mockResolvedValue(STATS);
    api.parseQueryForBuilder.mockResolvedValue({
      shape: {
        dataset: 'ports', terms: 'has:web', conditions: [{ column: 'service', op: '=', value: 'ssh' }],
        by: ['service'], measure: { fn: 'top', column: '', as: null }, most_first: false, head: 5,
      },
      reason: null,
    });
    renderAt(`/query?q=${encodeURIComponent('ports has:web | where service="ssh" | top limit=5 service')}`);
    await screen.findByTestId('query-answer');
    fireEvent.click(screen.getByRole('button', { name: 'Builder' }));
    // The panel reads the query first, then shows the pickers (a new element).
    await waitFor(() => expect(within(screen.getByTestId('query-builder')).getByLabelText('Hosts-language terms (optional)')).toHaveValue('has:web'));
    const builder = screen.getByTestId('query-builder');
    expect(api.parseQueryForBuilder).toHaveBeenCalledWith(9, 'ports has:web | where service="ssh" | top limit=5 service', expect.any(AbortSignal));
    expect(within(builder).getByLabelText('Top')).toHaveValue(5);
    fireEvent.change(within(builder).getByLabelText('Top'), { target: { value: '7' } });
    expect(screen.getByLabelText('Query')).toHaveValue('ports has:web | where service="ssh" | top limit=7 service');
  });

  it('says when it cannot show the query, and leaves it alone until asked to start over', async () => {
    api.runQuery.mockResolvedValue(STATS);
    api.parseQueryForBuilder.mockResolvedValue({ shape: null, reason: 'The builder has no place for an OR; edit the text instead.' });
    const q = 'ports | where service="a" OR service="b"';
    renderAt(`/query?q=${encodeURIComponent(q)}`);
    await screen.findByTestId('query-answer');
    fireEvent.click(screen.getByRole('button', { name: 'Builder' }));
    expect(await screen.findByTestId('builder-cannot')).toHaveTextContent('no place for an OR');
    expect(screen.getByLabelText('Query')).toHaveValue(q);
    fireEvent.click(screen.getByRole('button', { name: 'Start a new query in the builder' }));
    expect(screen.getByLabelText('Query')).toHaveValue('ports');
  });
});

describe('Query page — paging, pivots and values (review 2026-10-16, Q5 / Q7 / Q13)', () => {
  it('a listing past the cap pages by key, the page in the address', async () => {
    api.runQuery.mockImplementation(async (_p: number, _q: string, after: string | null) => (after
      ? { ...ROWS, total: 3, limit: 2, rows: [['10.0.0.3', null]], start: 2, next_after: null }
      : { ...ROWS, total: 3, limit: 2, rows: [['10.0.0.1', null], ['10.0.0.2', null]], start: 0, next_after: 'K2' }));
    renderAt('/query?q=hosts');
    expect(await screen.findByTestId('query-count')).toHaveTextContent('Rows 1–2 of 3');
    fireEvent.click(screen.getByRole('button', { name: 'Next 1 rows' }));
    await waitFor(() => expect(screen.getByTestId('search')).toHaveTextContent('after=K2'));
    expect(await screen.findByText('Rows 3–3 of 3', { exact: false })).toBeInTheDocument();
    expect(api.runQuery).toHaveBeenLastCalledWith(9, 'hosts', 'K2', expect.any(AbortSignal));
    // A new run starts at the first rows.
    fireEvent.click(screen.getByRole('button', { name: 'Back to the first rows' }));
    await waitFor(() => expect(screen.getByTestId('search')).not.toHaveTextContent('after='));
  });

  it('a hosts listing opens on Hosts', async () => {
    api.runQuery.mockResolvedValue({ ...ROWS, hosts_q: 'port:445 site:HQ' });
    renderAt('/query?q=hosts');
    expect(await screen.findByTestId('query-open-on-hosts'))
      .toHaveAttribute('href', `/hosts?${new URLSearchParams({ q: 'port:445 site:HQ' })}`);
  });

  it('no Hosts link when the answer is not a Hosts list', async () => {
    api.runQuery.mockResolvedValue({ ...ROWS, hosts_q: null });
    renderAt('/query?q=hosts');
    await screen.findByTestId('query-answer');
    expect(screen.queryByTestId('query-open-on-hosts')).not.toBeInTheDocument();
  });

  it('a values() cell wraps the first values, and "+N more" opens the group', async () => {
    const many = Array.from({ length: 12 }, (_, i) => `10.0.0.${i + 1}`);
    api.runQuery.mockResolvedValue({
      ...STATS, columns: [{ name: 'service', type: 'text' }, { name: 'ips', type: 'list' }],
      rows: [['ssh', many]], drilldowns: ['ports | where service="ssh"'], truncated_values: [], by: ['service'],
    });
    renderAt(`/query?q=${encodeURIComponent(STATS.query)}`);
    const cell = await screen.findByTestId('query-values-cell');
    expect(cell).toHaveTextContent(many.slice(0, 8).join(', '));
    expect(cell).not.toHaveTextContent('10.0.0.9,');
    expect(within(cell).getByRole('link', { name: '+4 more' }))
      .toHaveAttribute('href', `/query?q=${encodeURIComponent('ports | where service="ssh"')}`);
    expect(cell.firstElementChild?.className).toMatch(/line-clamp-3/);
  });
});

describe('Query page — un-sharing another member’s query (review I8)', () => {
  it('says the admin will not see it any more', async () => {
    api.listSavedQueries.mockResolvedValue([{
      id: 4, name: 'Their query', description: null, query: 'hosts', shared: true, created_by: 'dana',
      mine: false, can_modify: true, created_at: null, updated_at: null,
    }]);
    renderAt('/query');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByTestId('unshare-note')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByLabelText('Share with the project'));
    expect(within(dialog).getByTestId('unshare-note')).toHaveTextContent("Un-sharing makes it dana's private query");
  });
});

describe('Query page — external review 2026-10-16', () => {
  it('a values() list the server cut says so in its cell', async () => {
    api.runQuery.mockResolvedValue({
      ...STATS, columns: [{ name: 'service', type: 'text' }, { name: 'names', type: 'list' }],
      rows: [['ssh', ['a', 'b']], ['http', ['c']]], truncated_values: [[0, 'names']], by: ['service'],
    });
    renderAt(`/query?q=${encodeURIComponent(STATS.query)}`);
    const cells = await screen.findAllByTestId('query-values-cell');
    expect(cells[0]).toHaveTextContent('a, b +more (the list stops at 2)');
    expect(cells[1]).toHaveTextContent(/^c$/);
  });

  it('a downloaded CSV that is not the whole answer says how much it holds', async () => {
    api.runQuery.mockResolvedValue(STATS);
    api.downloadQueryCsv.mockResolvedValue({ filename: 'query-ports.csv', note: 'It holds the first 3 of 19 rows — as the query’s head asked.' });
    renderAt(`/query?q=${encodeURIComponent(STATS.query)}`);
    fireEvent.click(await screen.findByRole('button', { name: /Download CSV/ }));
    expect(await screen.findByTestId('download-note')).toHaveTextContent('It holds the first 3 of 19 rows');
    expect(toastMock.success).toHaveBeenCalledWith(
      'Saved query-ports.csv to your downloads. It holds the first 3 of 19 rows — as the query’s head asked.',
    );
  });
});

describe('Query page — second review 2026-10-16', () => {
  it('Run on the query already shown asks again (I2)', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    renderAt('/query?q=hosts');
    await screen.findByTestId('query-answer');
    expect(api.runQuery).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(api.runQuery).toHaveBeenCalledTimes(2));
  });

  it('reads the schema once for the editor, the builder and the Reference (I6)', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    renderAt('/query?q=hosts');
    await screen.findByTestId('query-answer');
    fireEvent.click(screen.getByRole('button', { name: 'Builder' }));
    await screen.findByTestId('query-builder');
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(api.runQuery).toHaveBeenCalledTimes(2));
    expect(api.getQuerySchema).toHaveBeenCalledTimes(1);
  });

  it('an empty answer says the server\'s reason when it has one (Q4)', async () => {
    const hint = '`=` is exact and case-sensitive. Ignoring case, 12 rows would match — like(service, "SSH") compares ignoring case';
    api.runQuery.mockResolvedValue({ ...ROWS, query: 'ports | where service="SSH"', rows: [], total: 0, hint });
    renderAt('/query?q=ports%20%7C%20where%20service%3D%22SSH%22');
    const empty = await screen.findByTestId('query-empty');
    // The backticks are drawn as code, never shown.
    expect(empty).toHaveTextContent(`Nothing matches this query. ${hint.replace(/`/g, '')}.`);
    expect(empty.textContent).not.toContain('`');
    expect(within(empty).getByText('=').tagName).toBe('CODE');
  });

  it('an empty answer without a hint says only that', async () => {
    api.runQuery.mockResolvedValue({ ...ROWS, rows: [], total: 0, hint: null });
    renderAt('/query?q=hosts');
    expect(await screen.findByTestId('query-empty')).toHaveTextContent(/^Nothing matches this query\.$/);
  });
});

const ENTRY = {
  id: 1, q: 'ports | stats count by service', by_agent: false, result_count: 12, error: null,
  error_position: null, run_count: 3, first_run_at: '2026-10-17T00:00:00Z', last_run_at: '2026-10-17T01:00:00Z',
};

describe('Query page — history and richer suggestions (5.408.0)', () => {
  it('lists my queries and my agent’s, labelled; a refused one says why; a query runs it', async () => {
    api.runQuery.mockResolvedValue(ROWS);
    api.listQueryHistory.mockResolvedValue({ total: 2, items: [
      ENTRY,
      { ...ENTRY, id: 2, q: 'hosts | where bogus=1', by_agent: true, result_count: null,
        error: "Unknown column 'bogus' in hosts", error_position: 14, run_count: 1 },
    ] });
    renderAt('/query?history=bogus');
    const table = await screen.findByTestId('query-history');
    const [mine, agents] = within(table).getAllByTestId('history-row');
    expect(mine).toHaveTextContent('12 results');
    expect(mine).toHaveTextContent('3 runs');
    expect(within(mine).queryByText('Agent')).not.toBeInTheDocument();
    expect(within(agents).getByText('Agent')).toBeInTheDocument();
    expect(agents).toHaveTextContent("Refused: Unknown column 'bogus' in hosts");
    // The search is the address's, sent to the server.
    expect(api.listQueryHistory).toHaveBeenCalledWith(9, { contains: 'bogus', limit: 20, offset: 0 }, expect.any(AbortSignal));
    // A query runs it, and the history's own search stays in the address.
    fireEvent.click(within(agents).getByRole('button', { name: 'hosts | where bogus=1' }));
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('hosts | where bogus=1'));
    expect(screen.getByTestId('search')).toHaveTextContent('history=bogus');
  });

  it('reads the history again once a run has answered — a refused run too', async () => {
    api.runQuery.mockRejectedValue({ response: { status: 400, data: { detail: 'Unknown dataset', position: 0 } } });
    renderAt('/query?q=widgets');
    await screen.findByTestId('query-error');
    await waitFor(() => expect(api.listQueryHistory.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('removes one entry, and Save… saves a query from it', async () => {
    api.listQueryHistory.mockResolvedValue({ total: 1, items: [ENTRY] });
    api.deleteQueryHistoryEntry.mockResolvedValue(undefined);
    renderAt('/query');
    const table = await screen.findByTestId('query-history');
    fireEvent.click(within(table).getByRole('button', { name: `Remove ${ENTRY.q} from your history` }));
    await waitFor(() => expect(api.deleteQueryHistoryEntry).toHaveBeenCalledWith(9, 1));
    fireEvent.click(within(table).getByRole('button', { name: 'Save…' }));
    expect(await screen.findByLabelText('Query', { selector: '#saved-query-text' })).toHaveValue(ENTRY.q);
  });

  it('says a failed history read is a failure, not "nothing yet"', async () => {
    api.listQueryHistory.mockRejectedValue(new Error('down'));
    renderAt('/query');
    expect(await screen.findByText(/Could not load your query history/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing yet/)).not.toBeInTheDocument();
  });

  it('offers a text column’s values from the server after where col = "', async () => {
    api.getQueryColumnValues.mockResolvedValue({ supported: true, timed_out: false, values: [{ value: 'microsoft-ds', count: 6 }] });
    renderAt('/query');
    const box = screen.getByLabelText('Query') as HTMLTextAreaElement;
    await waitFor(() => expect(api.getQuerySchema).toHaveBeenCalled());
    fireEvent.focus(box);
    const text = 'ports | where service="mi';
    fireEvent.change(box, { target: { value: text, selectionStart: text.length, selectionEnd: text.length } });
    const option = await screen.findByRole('option', { name: /microsoft-ds/ });
    expect(option).toHaveTextContent('6 rows');
    expect(api.getQueryColumnValues).toHaveBeenCalledWith(9, 'ports', 'service', 'mi', expect.any(AbortSignal));
    fireEvent.keyDown(box, { key: 'Tab' });
    await waitFor(() => expect(box).toHaveValue('ports | where service="microsoft-ds"'));
  });

  it('completes the search terms with the Hosts language’s fields', async () => {
    api.getHostQuerySchema.mockResolvedValue({ examples: [], fields: [
      { name: 'port', aliases: [], value_source: 'port', trgm: false, enum_values: [], description: 'an open port', enum_descriptions: {} },
    ] });
    renderAt('/query');
    const box = screen.getByLabelText('Query') as HTMLTextAreaElement;
    await waitFor(() => expect(api.getHostQuerySchema).toHaveBeenCalled());
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: 'ports po', selectionStart: 8, selectionEnd: 8 } });
    expect(await screen.findByRole('option', { name: /port:/ })).toHaveTextContent('an open port');
    fireEvent.keyDown(box, { key: 'Tab' });
    await waitFor(() => expect(box).toHaveValue('ports port:'));
  });
});
