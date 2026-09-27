import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AddressTerrainSection from '../../components/operations/AddressTerrainSection';
import * as api from '../../services/api';

vi.mock('../../services/api', () => ({ getAddressTerrain: vi.fn() }));
const mocked = vi.mocked(api);

const BLOCKS = [
  { cidr: '10.0.0.0/24', hosts: 250, tested: 3, planned: 25, worked: 30, untouched: 192, critical: 15, critical_untouched: 12 },
  { cidr: '10.0.1.0/24', hosts: 40, tested: 0, planned: 0, worked: 0, untouched: 40, critical: 0, critical_untouched: 0 },
];

const renderIt = () => render(<MemoryRouter><AddressTerrainSection /></MemoryRouter>);

describe('AddressTerrainSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('without WebGL shows the table view, every count a link to exactly its hosts', async () => {
    mocked.getAddressTerrain.mockResolvedValue({ blocks: BLOCKS, total_hosts: 290, unplaced_hosts: 0, truncated: false });
    renderIt();
    const table = await screen.findByRole('table');
    expect(screen.getByRole('button', { name: /Map/ })).toBeDisabled();
    const first = within(table).getAllByRole('row')[1];
    const q = (el: HTMLElement) => new URL(el.getAttribute('href') ?? '', 'https://x').searchParams.get('q');
    expect(q(within(first).getByRole('link', { name: '10.0.0.0/24' }))).toBe('subnet:"10.0.0.0/24"');
    expect(q(within(first).getByRole('link', { name: '12' }))).toBe('subnet:"10.0.0.0/24" has:untouched has:critical');
    expect(q(within(first).getByRole('link', { name: '25' }))).toBe('subnet:"10.0.0.0/24" has:planned AND NOT has:tested');
  });

  it('leads with the untouched critical exposure and where most of it is', async () => {
    mocked.getAddressTerrain.mockResolvedValue({ blocks: BLOCKS, total_hosts: 290, unplaced_hosts: 0, truncated: false });
    renderIt();
    expect(await screen.findByText(/12 untouched hosts carry a critical scanner observation/)).toBeInTheDocument();
    expect(screen.getByText(/The team has reached/)).toHaveTextContent('The team has reached 58 of 290 hosts (3 tested)');
  });

  it('a failed load is unavailable with a retry, never an empty map', async () => {
    mocked.getAddressTerrain.mockRejectedValueOnce(new Error('503'));
    renderIt();
    expect(await screen.findByText('The address map could not be loaded.')).toBeInTheDocument();
    mocked.getAddressTerrain.mockResolvedValue({ blocks: BLOCKS, total_hosts: 290, unplaced_hosts: 0, truncated: false });
    await userEvent.click(screen.getByRole('button', { name: /Retry/ }));
    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument());
  });
});
