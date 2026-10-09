/**
 * Manage sites.
 *
 *  - A FAILED read of the sites is said in the dialog with Retry; it was shown
 *    as "No sites yet" under a toast.
 *  - "Expected hosts" is the reader's edit over the stored count: after a
 *    failed save the row says the typed value is not saved (it stayed in the
 *    box as if stored), and after a save the box shows what the server stored.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listSites: vi.fn(), updateSite: vi.fn() }));
vi.mock('../../services/api', () => api);

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import SiteManagerDialog from '../../components/SiteManagerDialog';

const site = (over: Record<string, unknown> = {}) => ({
  id: 1, name: 'HQ', criticality_tier: 2, owner_id: null, owner_name: null,
  expected_host_count: 10, subnet_count: 2, ...over,
});
const refused = (status: number, detail: string) => Object.assign(
  new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } },
);
const show = () => render(<SiteManagerDialog open onOpenChange={vi.fn()} />);
const expectedBox = () => screen.getByLabelText('Expected host count for HQ') as HTMLInputElement;

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
});

describe('SiteManagerDialog — a failed read', () => {
  it('says the failure with Retry, not "No sites yet", and Retry reads again', async () => {
    api.listSites.mockRejectedValueOnce(refused(503, 'The database is restarting.'));
    api.listSites.mockResolvedValue([site()]);
    show();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The database is restarting.');
    expect(screen.queryByText(/No sites yet/)).not.toBeInTheDocument();
    // Said in place, not as a toast from an effect.
    expect(toast.error).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('HQ')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(api.listSites).toHaveBeenCalledTimes(2);
  });

  it('still says "No sites yet" when the read answered with none', async () => {
    api.listSites.mockResolvedValue([]);
    show();
    expect(await screen.findByText(/No sites yet/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('SiteManagerDialog — Expected hosts', () => {
  it('after a failed save keeps what was typed, says it is not saved, and saves it from there', async () => {
    api.listSites.mockResolvedValue([site()]);
    api.updateSite.mockRejectedValueOnce(refused(500, 'boom'));
    show();
    await screen.findByText('HQ');

    fireEvent.change(expectedBox(), { target: { value: '25' } });
    fireEvent.blur(expectedBox());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Not saved.');
    expect(expectedBox().value).toBe('25');
    expect(toast.error).toHaveBeenCalledWith('boom');

    api.updateSite.mockResolvedValue(site({ expected_host_count: 25 }));
    fireEvent.click(within(alert).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(api.updateSite).toHaveBeenLastCalledWith(1, 1, { expected_host_count: 25 });
    expect(expectedBox().value).toBe('25');
  });

  it('after a save shows the count the server stored', async () => {
    api.listSites.mockResolvedValue([site()]);
    // The server's answer is what is stored — here not the number typed.
    api.updateSite.mockResolvedValue(site({ expected_host_count: 20 }));
    show();
    await screen.findByText('HQ');

    fireEvent.change(expectedBox(), { target: { value: '25' } });
    fireEvent.blur(expectedBox());

    await waitFor(() => expect(expectedBox().value).toBe('20'));
    expect(api.updateSite).toHaveBeenCalledWith(1, 1, { expected_host_count: 25 });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('sends nothing when the box is left as stored, and null when emptied', async () => {
    api.listSites.mockResolvedValue([site()]);
    api.updateSite.mockResolvedValue(site({ expected_host_count: null }));
    show();
    await screen.findByText('HQ');

    fireEvent.blur(expectedBox());
    fireEvent.change(expectedBox(), { target: { value: '11' } });
    fireEvent.change(expectedBox(), { target: { value: '10' } });
    fireEvent.blur(expectedBox());
    expect(api.updateSite).not.toHaveBeenCalled();

    fireEvent.change(expectedBox(), { target: { value: '' } });
    fireEvent.blur(expectedBox());
    await waitFor(() => expect(api.updateSite).toHaveBeenCalledWith(1, 1, { expected_host_count: null }));
    await waitFor(() => expect(expectedBox().value).toBe(''));
  });
});
