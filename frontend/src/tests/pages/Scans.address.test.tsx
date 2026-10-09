/**
 * Scans — the import history's filters, sort and view are the address's
 * (UI_STYLE_GUIDE §39), under the REAL router.
 *
 * The defect this pins: tool, search, day range, `since`, uploader, sort and
 * the Grouped / All files switch were each copied from the address into state
 * once, and an effect wrote the state back.  Operations links to
 * `/scans?since=…`; reached from an already-open /scans — or left again with
 * Back — the address changed while the controls and the requests kept the
 * old filters, and the next change wrote the old filters over the address.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// setupTests replaces useNavigate / useLocation for every file; this one
// needs the router's own.
vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

// Everything the page (and the components it mounts) takes from the barrel;
// an unlisted function resolves to an empty list (as Scans.history.test).
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
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo' }, refreshProjects: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'admin' }, hasPermission: () => true }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import Scans from '../../pages/Scans';
import { TooltipProvider } from '../../components/ui/tooltip';

const scan = (id: number, filename: string) => ({
  id, filename, tool_name: 'nmap', scan_type: 'port_scan', created_at: '2026-09-19T10:00:00Z',
  total_hosts: 3, up_hosts: 3, new_hosts: 1, updated_hosts: 2, total_ports: 4, open_ports: 4,
});

const SINCE = '2026-09-01T00:00:00.000Z';

const open = (entry: string) => {
  const router = createMemoryRouter(
    [
      { path: '/scans', element: <TooltipProvider><Scans /></TooltipProvider> },
      { path: '/elsewhere', element: <p>elsewhere</p> },
    ],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
type Router = ReturnType<typeof open>;
/** The address's query as a plain object: what is in it, whatever the order. */
const address = (router: Router) => Object.fromEntries(new URLSearchParams(router.state.location.search));

type Filters = {
  search?: string; tool?: string; createdAfter?: string; uploadedBy?: number;
  sortBy?: string; sortOrder?: string;
};
const pick = ({ search, tool, createdAfter, uploadedBy }: Filters) => ({ search, tool, createdAfter, uploadedBy });
/** The filters of each read of the grouped history. */
const historyAsked = () => api.getImportHistory.mock.calls.map(([projectId, options]) => {
  expect(projectId).toBe(1);
  return pick(options as Filters);
});
/** The filters and sort of each read of the flat ("All files") list. */
const filesAsked = () => api.getScans.mock.calls
  .filter(([, , limit]) => limit === 250)
  .map(([projectId, , , options]) => {
    expect(projectId).toBe(1);
    const o = options as Filters;
    return { ...pick(o), sortBy: o.sortBy, sortOrder: o.sortOrder };
  });
const lastOf = <T,>(list: T[]): T => list[list.length - 1];

const box = () => screen.getByLabelText('Search scan inventory') as HTMLInputElement;
const toolChooser = () => screen.getByRole('combobox', { name: 'Filter scans by tool' });
const dateChooser = () => screen.getByRole('combobox', { name: 'Filter scans by upload date' });
const viewButton = (name: string) =>
  within(screen.getByRole('group', { name: 'How the import history is listed' })).getByRole('button', { name });
/** Longer than the search box's delay: anything that was going to be written has been. */
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });

beforeEach(() => {
  vi.clearAllMocks();
  api.getRecentIngestionJobs.mockResolvedValue([]);
  api.getStagedIngestionJobs.mockResolvedValue([]);
  api.getScansSummary.mockResolvedValue({
    total_scans: 3, total_files: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 },
    uploaders: [{ user_id: 1, username: 'ana', files: 2 }, { user_id: 7, username: 'ben', files: 1 }],
  });
  api.getScanInventoryMarker.mockResolvedValue({ count: 3, latest_id: 9 });
  api.getImportHistory.mockResolvedValue({
    items: [{ kind: 'scan', id: 9, at: '2026-09-19T11:00:00Z' }, { kind: 'scan', id: 3, at: '2026-09-19T08:00:00Z' }],
    total: 2, batch_total: 0, scan_total: 2, has_more: false,
  });
  api.getScans.mockResolvedValue([scan(3, 'older.xml'), scan(9, 'newest.xml')]);
  api.getScanBatches.mockResolvedValue([]);
});

describe('Scans — the filters are the address (real router)', () => {
  it('opens on what the address says: every control, and the history asked with those filters', async () => {
    const router = open('/scans?search=dmz&tool=NMAP&days=7&uploaded_by=7');
    await screen.findByText('newest.xml');
    expect(box().value).toBe('dmz');
    expect(toolChooser()).toHaveTextContent('nmap (3)');
    expect(dateChooser()).toHaveTextContent('Last 7 days');
    expect(screen.getByRole('combobox', { name: 'Filter scans by uploader' })).toHaveTextContent('ben (1)');
    expect(viewButton('Grouped by upload')).toHaveAttribute('aria-pressed', 'true');

    expect(historyAsked()).toHaveLength(1);
    const asked = historyAsked()[0];
    expect(asked).toMatchObject({ search: 'dmz', tool: 'NMAP', uploadedBy: 7 });
    // "The last 7 days", as a moment.
    const age = Date.now() - Date.parse(asked.createdAfter as string);
    expect(Math.abs(age - 7 * 24 * 60 * 60 * 1000)).toBeLessThan(60_000);
    expect(api.getScansSummary).toHaveBeenCalledWith(1, expect.objectContaining({ search: 'dmz', tool: 'NMAP', uploadedBy: 7 }));

    // Opening it changed nothing in the address, then or later.
    await settle();
    expect(router.state.location.search).toBe('?search=dmz&tool=NMAP&days=7&uploaded_by=7');
    expect(historyAsked()).toHaveLength(1);
  });

  // Found walking the rebuild (2026-10-09): the list matches a tool whatever
  // its case, but the select's options carry the server's spelling (`NMAP`),
  // so a link written `?tool=nmap` filtered the list under a BLANK select.
  it('a tool named in another case shows in the select, and the address is left as written', async () => {
    const router = open('/scans?tool=nmap');
    await screen.findByText('newest.xml');
    expect(toolChooser()).toHaveTextContent('nmap (3)');
    expect(lastOf(historyAsked())).toMatchObject({ tool: 'nmap' });
    await settle();
    expect(router.state.location.search).toBe('?tool=nmap');
  });

  it('opens on the view and sort the address says, and on `since`', async () => {
    open(`/scans?batch_files=show&sort_by=filename&sort_order=asc&since=${encodeURIComponent(SINCE)}`);
    await screen.findByText('newest.xml');
    expect(viewButton('All files')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Sort by Scan, currently sorted ascending' })).toBeInTheDocument();
    expect(dateChooser()).toHaveTextContent(/^Since /);
    expect(filesAsked()).toEqual([{
      search: undefined, tool: undefined, createdAfter: SINCE, uploadedBy: undefined, sortBy: 'filename', sortOrder: 'asc',
    }]);
    expect(api.getImportHistory).not.toHaveBeenCalled();
  });

  it('a value the address cannot mean is the default', async () => {
    open('/scans?days=soon&sort_by=colour&sort_order=sideways&uploaded_by=-3&since=yesterday&batch_files=yes');
    await screen.findByText('newest.xml');
    expect(dateChooser()).toHaveTextContent('Any time');
    expect(viewButton('Grouped by upload')).toHaveAttribute('aria-pressed', 'true');
    expect(historyAsked()).toEqual([{ search: undefined, tool: undefined, createdAfter: undefined, uploadedBy: undefined }]);
  });

  it('the view switch and the sort write the address (replaced; a default is left out) and ask with them', async () => {
    const router = open('/scans?tool=NMAP');
    await screen.findByText('newest.xml');

    fireEvent.click(viewButton('All files'));
    await waitFor(() => expect(lastOf(filesAsked())).toMatchObject({ tool: 'NMAP', sortBy: 'created_at', sortOrder: 'desc' }));
    expect(address(router)).toEqual({ tool: 'NMAP', batch_files: 'show' });
    expect(router.state.historyAction).toBe('REPLACE');

    // Another column: that column, newest / largest first.
    fireEvent.click(await screen.findByRole('button', { name: /^Sort by Scan, currently not sorted/ }));
    await waitFor(() => expect(lastOf(filesAsked())).toMatchObject({ tool: 'NMAP', sortBy: 'filename', sortOrder: 'desc' }));
    expect(address(router)).toEqual({ tool: 'NMAP', batch_files: 'show', sort_by: 'filename' });

    // The same column again: the other way round.
    fireEvent.click(screen.getByRole('button', { name: 'Sort by Scan, currently sorted descending' }));
    await waitFor(() => expect(lastOf(filesAsked())).toMatchObject({ sortBy: 'filename', sortOrder: 'asc' }));
    expect(address(router)).toEqual({ tool: 'NMAP', batch_files: 'show', sort_by: 'filename', sort_order: 'asc' });

    // A third column from an ascending sort starts descending again.
    fireEvent.click(screen.getByRole('button', { name: /^Sort by New hosts, currently not sorted/ }));
    await waitFor(() => expect(lastOf(filesAsked())).toMatchObject({ sortBy: 'new_hosts', sortOrder: 'desc' }));
    expect(address(router)).toEqual({ tool: 'NMAP', batch_files: 'show', sort_by: 'new_hosts' });

    fireEvent.click(viewButton('Grouped by upload'));
    await waitFor(() => expect(address(router)).toEqual({ tool: 'NMAP', sort_by: 'new_hosts' }));
    expect(lastOf(historyAsked())).toMatchObject({ tool: 'NMAP' });
  });

  it('a row’s tool badge sets the tool in the address, and again clears it', async () => {
    const router = open('/scans?batch_files=show');
    await screen.findByText('newest.xml');
    fireEvent.click(screen.getAllByTitle('Show only NMAP scans')[0]);
    await waitFor(() => expect(lastOf(filesAsked())).toMatchObject({ tool: 'NMAP' }));
    expect(address(router)).toEqual({ batch_files: 'show', tool: 'NMAP' });
    expect(toolChooser()).toHaveTextContent('nmap (3)');

    fireEvent.click(screen.getAllByTitle('Clear NMAP filter')[0]);
    await waitFor(() => expect(lastOf(filesAsked()).tool).toBeUndefined());
    expect(address(router)).toEqual({ batch_files: 'show' });
  });

  it('choosing a day range replaces `since`; "Any time" leaves both out', async () => {
    const user = userEvent.setup({ skipHover: true });
    const router = open(`/scans?since=${encodeURIComponent(SINCE)}&tool=NMAP`);
    await screen.findByText('newest.xml');
    expect(lastOf(historyAsked())).toMatchObject({ createdAfter: SINCE });

    await user.click(dateChooser());
    await user.click(await screen.findByRole('option', { name: 'Last 30 days' }));
    await waitFor(() => expect(address(router)).toEqual({ tool: 'NMAP', days: '30' }));
    expect(dateChooser()).toHaveTextContent('Last 30 days');
    await waitFor(() => expect(lastOf(historyAsked()).createdAfter).not.toBe(SINCE));
    expect(lastOf(historyAsked()).createdAfter).toEqual(expect.any(String));

    await user.click(dateChooser());
    await user.click(await screen.findByRole('option', { name: 'Any time' }));
    await waitFor(() => expect(address(router)).toEqual({ tool: 'NMAP' }));
    await waitFor(() => expect(lastOf(historyAsked()).createdAfter).toBeUndefined());
  });

  it('choosing a tool and an uploader from their lists writes the address and asks', async () => {
    const user = userEvent.setup({ skipHover: true });
    const router = open('/scans');
    await screen.findByText('newest.xml');

    await user.click(toolChooser());
    await user.click(await screen.findByRole('option', { name: 'nmap (3)' }));
    await waitFor(() => expect(address(router)).toEqual({ tool: 'NMAP' }));
    await waitFor(() => expect(lastOf(historyAsked())).toMatchObject({ tool: 'NMAP' }));

    await user.click(screen.getByRole('combobox', { name: 'Filter scans by uploader' }));
    await user.click(await screen.findByRole('option', { name: 'ben (1)' }));
    await waitFor(() => expect(address(router)).toEqual({ tool: 'NMAP', uploaded_by: '7' }));
    await waitFor(() => expect(lastOf(historyAsked())).toMatchObject({ tool: 'NMAP', uploadedBy: 7 }));

    await user.click(screen.getByRole('combobox', { name: 'Filter scans by uploader' }));
    await user.click(await screen.findByRole('option', { name: 'Uploaded by anyone' }));
    await waitFor(() => expect(address(router)).toEqual({ tool: 'NMAP' }));
  });

  it('a link to the page with other filters (Operations’ "since"), and Back, re-seed the controls and the list; nothing writes the old filters back', async () => {
    const start = '/scans?search=first&tool=NMAP&days=7&batch_files=show&sort_by=filename&uploaded_by=7';
    const router = open(start);
    await screen.findByText('newest.xml');
    expect(lastOf(filesAsked())).toMatchObject({ search: 'first', tool: 'NMAP', uploadedBy: 7, sortBy: 'filename' });

    const link = `/scans?since=${encodeURIComponent(SINCE)}`;
    await act(async () => { await router.navigate(link); });
    await waitFor(() => expect(lastOf(historyAsked())).toEqual({
      search: undefined, tool: undefined, createdAfter: SINCE, uploadedBy: undefined,
    }));
    expect(box().value).toBe('');
    expect(toolChooser()).toHaveTextContent('All tools (3)');
    expect(dateChooser()).toHaveTextContent(/^Since /);
    expect(screen.getByRole('combobox', { name: 'Filter scans by uploader' })).toHaveTextContent('Uploaded by anyone');
    expect(viewButton('Grouped by upload')).toHaveAttribute('aria-pressed', 'true');
    expect(api.getScansSummary).toHaveBeenLastCalledWith(1, expect.objectContaining({ createdAfter: SINCE, tool: undefined }));
    await settle();
    expect(router.state.location.search).toBe(`?since=${encodeURIComponent(SINCE)}`);
    expect(lastOf(historyAsked())).toMatchObject({ createdAfter: SINCE, tool: undefined });

    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(viewButton('All files')).toHaveAttribute('aria-pressed', 'true'));
    await waitFor(() => expect(lastOf(filesAsked())).toMatchObject({
      search: 'first', tool: 'NMAP', uploadedBy: 7, sortBy: 'filename', sortOrder: 'desc',
    }));
    expect(lastOf(filesAsked()).createdAfter).not.toBe(SINCE);
    expect(box().value).toBe('first');
    expect(toolChooser()).toHaveTextContent('nmap (3)');
    expect(dateChooser()).toHaveTextContent('Last 7 days');
    expect(screen.getByRole('combobox', { name: 'Filter scans by uploader' })).toHaveTextContent('ben (1)');
    await settle();
    expect(router.state.location.pathname + router.state.location.search).toBe(start);
  });

  it('typing asks once, after the typing stops, and the search goes into the address', async () => {
    const router = open('/scans?tool=NMAP');
    await screen.findByText('newest.xml');
    api.getImportHistory.mockClear();
    api.getScansSummary.mockClear();

    fireEvent.change(box(), { target: { value: 'd' } });
    fireEvent.change(box(), { target: { value: 'dm' } });
    fireEvent.change(box(), { target: { value: 'dmz' } });
    // Still typing: nothing asked, nothing written.
    expect(historyAsked()).toHaveLength(0);
    expect(address(router)).toEqual({ tool: 'NMAP' });

    await settle();
    expect(historyAsked()).toEqual([{ search: 'dmz', tool: 'NMAP', createdAfter: undefined, uploadedBy: undefined }]);
    expect(api.getScansSummary).toHaveBeenCalledTimes(1);
    expect(address(router)).toEqual({ tool: 'NMAP', search: 'dmz' });
    expect(box().value).toBe('dmz');
  });

  it('"Clear filters" takes the search, tool, range and uploader out of the address — half-typed text too — and keeps the view and sort', async () => {
    api.getImportHistory.mockResolvedValue({ items: [], total: 0, batch_total: 0, scan_total: 0, has_more: false });
    api.getScans.mockResolvedValue([]);
    const router = open(`/scans?search=nothing&tool=NMAP&since=${encodeURIComponent(SINCE)}&uploaded_by=7&batch_files=show&sort_by=filename`);
    const clear = await screen.findByRole('button', { name: 'Clear filters' });
    // Something half-typed goes too: it is not committed a moment later.
    fireEvent.change(box(), { target: { value: 'nothing at a' } });

    fireEvent.click(clear);
    await waitFor(() => expect(address(router)).toEqual({ batch_files: 'show', sort_by: 'filename' }));
    await waitFor(() => expect(lastOf(filesAsked())).toEqual({
      search: undefined, tool: undefined, createdAfter: undefined, uploadedBy: undefined, sortBy: 'filename', sortOrder: 'desc',
    }));
    // Nothing filtered and nothing listed: the page says the project is empty.
    expect(await screen.findByText('No scans uploaded yet')).toBeInTheDocument();
    // The half-typed text is not committed a moment later.
    await settle();
    expect(address(router)).toEqual({ batch_files: 'show', sort_by: 'filename' });
    expect(lastOf(filesAsked()).search).toBeUndefined();
  });
});
