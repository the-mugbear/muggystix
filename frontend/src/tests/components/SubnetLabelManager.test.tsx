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
  const Row = ({ onSaved = vi.fn() }: { onSaved?: (next: unknown) => void }) => (
    <SubnetLabelEditorPopover
      subnetId={7} subnetCidr="10.0.0.0/24" onSaved={onSaved}
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
          key={id} subnetId={id} subnetCidr={`10.0.${id}.0/24`} onSaved={vi.fn()}
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
