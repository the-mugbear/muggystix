/**
 * Subnet labels.
 *
 *  - Manage subnet labels: a FAILED read of the catalogue is said in the
 *    dialog with Retry; it was shown as "No labels yet" under a toast.
 *  - The per-subnet editor: the selection starts from the subnet's labels when
 *    the popover OPENS and is the reader's from then on.  It was re-synced by
 *    an effect on the `currentLabels` array, and the Scope page hands a new
 *    array on every render (`subnet.labels ?? []`, a re-read of the scope) —
 *    so any render of the page took back what the reader had just changed.
 */
import { useQuery } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listSubnetLabels: vi.fn(), createSubnetLabel: vi.fn(), updateSubnetLabel: vi.fn(),
  deleteSubnetLabel: vi.fn(), replaceSubnetLabels: vi.fn(),
}));
vi.mock('../../services/api', () => api);

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));

import { SubnetLabelEditorPopover, SubnetLabelManagerDialog } from '../../components/SubnetLabelManager';
import { useListQuery } from '../../hooks/useListQuery';
import { TooltipProvider } from '../../components/ui/tooltip';

const label = (id: number, name: string) => ({
  id, project_id: 1, name, color: null, created_at: '2026-10-01T00:00:00Z', subnet_count: 1, host_count: 3,
});
const refused = (status: number, detail: string) => Object.assign(
  new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } },
);

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
});

describe('SubnetLabelManagerDialog — a failed read', () => {
  const show = () => render(
    <TooltipProvider><SubnetLabelManagerDialog open onOpenChange={vi.fn()} /></TooltipProvider>,
  );

  it('says the failure with Retry, not "No labels yet", and Retry reads again', async () => {
    api.listSubnetLabels.mockRejectedValueOnce(refused(503, 'The database is restarting.'));
    api.listSubnetLabels.mockResolvedValue([label(1, 'PCI')]);
    show();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The database is restarting.');
    expect(screen.queryByText(/No labels yet/)).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('PCI')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(api.listSubnetLabels).toHaveBeenCalledTimes(2);
  });

  it('still says "No labels yet" when the read answered with none', async () => {
    api.listSubnetLabels.mockResolvedValue([]);
    show();
    expect(await screen.findByText(/No labels yet/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('SubnetLabelEditorPopover — the selection is the reader’s while it is open', () => {
  const catalogue = [label(1, 'PCI'), label(2, 'DMZ')];
  // The editor reads the project's catalogue itself (plan B22; the page used
  // to hand it down).
  beforeEach(() => { api.listSubnetLabels.mockResolvedValue(catalogue); });
  // As the Scope page does: a NEW array of the subnet's labels on every render.
  const Row = () => (
    <SubnetLabelEditorPopover
      subnetId={7} subnetCidr="10.0.0.0/24"
      currentLabels={[{ id: 1, name: 'PCI', color: null }]}
    >
      <button type="button">Edit labels</button>
    </SubnetLabelEditorPopover>
  );

  it('keeps a change through a render of the page, and saves it', async () => {
    api.replaceSubnetLabels.mockResolvedValue([]);
    const { rerender } = render(<Row />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit labels' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove PCI' }));
    expect(screen.queryByRole('button', { name: 'Remove PCI' })).not.toBeInTheDocument();

    // The page renders again (a poll, a re-read, any state of its own).
    rerender(<Row />);
    rerender(<Row />);
    expect(screen.queryByRole('button', { name: 'Remove PCI' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.replaceSubnetLabels).toHaveBeenCalledWith(1, 7, []));
  });

  it('starts from the subnet’s labels again each time it opens', async () => {
    render(<Row />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit labels' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove PCI' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Labels for 10.0.0.0/24')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit labels' }));
    expect(await screen.findByRole('button', { name: 'Remove PCI' })).toBeInTheDocument();
  });
});

// A save shows on the subnet's row at once, and costs no re-read of the scope:
// the PUT answers with the subnet's labels, and they are put on its row where
// it is in the Scope page's list — a "Show more" list (`useListQuery` of
// `getDefaultScope`), read here by the real hook so that what is written is
// what the page shows, on whichever loaded page the row is.
describe('SubnetLabelEditorPopover — a save shows on the subnet’s row at once', () => {
  interface Subnet { id: number; cidr: string; labels?: Array<{ id: number; name: string; color: string | null }> }
  const PCI = { id: 1, name: 'PCI', color: null };
  const DMZ = { id: 2, name: 'DMZ', color: null };
  const subnets: Subnet[] = [
    { id: 1, cidr: '10.0.1.0/24', labels: [PCI] }, { id: 2, cidr: '10.0.2.0/24' },
    { id: 3, cidr: '10.0.3.0/24', labels: [PCI, DMZ] },
  ];
  const getScope = vi.fn((offset: number, limit: number) => Promise.resolve({
    id: 5, items: subnets.slice(offset, offset + limit), total: subnets.length,
  }));
  // The Scope page's read of its subnets, two to a page.
  const ScopeRows = () => {
    const list = useListQuery<Subnet>(
      'getDefaultScope', ({ offset, limit }) => getScope(offset, limit), [1, { subnetsSearch: '' }], { pageSize: 2 },
    );
    return (
      <>
        {(list.rows ?? []).map((s) => (
          <div key={s.id}>
            <span data-testid={`labels-${s.id}`}>{(s.labels ?? []).map((l) => l.name).join(', ') || 'none'}</span>
            {/* The editor writes the row itself: the page hands it no `onSaved`
                (it did, and put the answer on the row with `mapRows`). */}
            <SubnetLabelEditorPopover subnetId={s.id} subnetCidr={s.cidr} currentLabels={s.labels ?? []}>
              <button type="button">Edit labels for {s.id}</button>
            </SubnetLabelEditorPopover>
          </div>
        ))}
        <button type="button" onClick={() => { void list.loadMore(); }}>Load more</button>
      </>
    );
  };

  it('on the loaded page the row is on, with no re-read of the scope', async () => {
    api.listSubnetLabels.mockResolvedValue([label(1, 'PCI'), label(2, 'DMZ')]);
    api.replaceSubnetLabels.mockResolvedValue([DMZ]);
    getScope.mockClear();
    render(<ScopeRows />);
    await screen.findByTestId('labels-1');
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByTestId('labels-3')).toHaveTextContent('PCI, DMZ');
    expect(getScope).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole('button', { name: 'Edit labels for 3' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove PCI' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.replaceSubnetLabels).toHaveBeenCalledWith(1, 3, [2]));
    // The server's answer, on that row only — and the editor has closed.
    await waitFor(() => expect(screen.getByTestId('labels-3')).toHaveTextContent(/^DMZ$/));
    expect(screen.getByTestId('labels-1')).toHaveTextContent(/^PCI$/);
    expect(screen.getByTestId('labels-2')).toHaveTextContent('none');
    await waitFor(() => expect(screen.queryByText('Labels for 10.0.3.0/24')).not.toBeInTheDocument());
    // Not by reading the scope again (every loaded page of it).
    expect(getScope).toHaveBeenCalledTimes(2);
    // Opened again, the editor starts from the row's labels as they now are.
    fireEvent.click(screen.getByRole('button', { name: 'Edit labels for 3' }));
    expect(await screen.findByRole('button', { name: 'Remove DMZ' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove PCI' })).not.toBeInTheDocument();
  });

  it('a refused save changes no row', async () => {
    api.listSubnetLabels.mockResolvedValue([label(1, 'PCI'), label(2, 'DMZ')]);
    api.replaceSubnetLabels.mockRejectedValue(refused(403, 'Analyst role required.'));
    getScope.mockClear();
    render(<ScopeRows />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit labels for 1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove PCI' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Analyst role required.'));
    expect(screen.getByTestId('labels-1')).toHaveTextContent(/^PCI$/);
    expect(getScope).toHaveBeenCalledTimes(1);
  });
});

// Plan B22 — every row's editor reads the catalogue's key itself.  That must
// not cost a request per row: the page's read is the one that asks, rows that
// mount later take what is there, and a change to a label reaches them all.
describe('SubnetLabelEditorPopover — a table of rows and the one catalogue', () => {
  // The Scope page's own read of the catalogue (its "Apply label…" menu).
  const PageRead = () => {
    useQuery({ queryKey: ['listSubnetLabels', 1], queryFn: () => api.listSubnetLabels(1) });
    return null;
  };
  const Table = ({ rows }: { rows: number[] }) => (
    <>
      <PageRead />
      {rows.map((id) => (
        <SubnetLabelEditorPopover
          key={id} subnetId={id} subnetCidr={`10.0.${id}.0/24`}
          currentLabels={[{ id: 2, name: 'DMZ', color: null }]}
        >
          <button type="button">Edit labels for {id}</button>
        </SubnetLabelEditorPopover>
      ))}
    </>
  );

  it('asks once for the page and its rows, not again for a row that appears later, and follows a change', async () => {
    api.listSubnetLabels.mockResolvedValue([label(1, 'PCI'), label(2, 'DMZ')]);
    api.replaceSubnetLabels.mockResolvedValue([]);
    const { rerender } = render(<Table rows={[1, 2, 3]} />);
    await waitFor(() => expect(api.listSubnetLabels).toHaveBeenCalledTimes(1));

    // "Load more" brought another subnet.
    rerender(<Table rows={[1, 2, 3, 4]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit labels for 4' }));
    expect(await screen.findByRole('button', { name: 'Remove DMZ' })).toBeInTheDocument();
    expect(api.listSubnetLabels).toHaveBeenCalledTimes(1);

    // A save changes the catalogue's counts: it is read again — once, not per row.
    api.listSubnetLabels.mockResolvedValue([label(1, 'PCI'), label(2, 'Perimeter')]);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.replaceSubnetLabels).toHaveBeenCalledWith(1, 4, [2]));
    await waitFor(() => expect(api.listSubnetLabels).toHaveBeenCalledTimes(2));
    // …and a row opened afterwards names the label as it now is.
    fireEvent.click(screen.getByRole('button', { name: 'Edit labels for 1' }));
    expect(await screen.findByRole('button', { name: 'Remove Perimeter' })).toBeInTheDocument();
    expect(api.listSubnetLabels).toHaveBeenCalledTimes(2);
  });
});
