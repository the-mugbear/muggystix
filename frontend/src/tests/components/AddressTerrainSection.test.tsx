import React from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';

import AddressTerrainSection, { TERRAIN_OPEN_KEY } from '../../components/operations/AddressTerrainSection';
import * as api from '../../services/api';

vi.mock('../../services/api', () => ({ getAddressTerrain: vi.fn() }));
// The scene is three.js in its own chunk: loading it is what "Show the map"
// costs, so a test can see whether the closed section asked for it.
const sceneLoads = vi.hoisted(() => ({ n: 0 }));
vi.mock('../../components/operations/TerrainScene', () => {
  sceneLoads.n += 1;
  return { default: () => null };
});
const mocked = vi.mocked(api);

const BLOCKS = [
  { cidr: '10.0.0.0/24', hosts: 250, tested: 3, planned: 25, worked: 30, untouched: 192, critical: 15, critical_untouched: 12 },
  { cidr: '10.0.1.0/24', hosts: 40, tested: 0, planned: 0, worked: 0, untouched: 40, critical: 0, critical_untouched: 0 },
];
const TERRAIN = { blocks: BLOCKS, total_hosts: 290, unplaced_hosts: 0, truncated: false };

const renderIt = () => render(<MemoryRouter><AddressTerrainSection /></MemoryRouter>);
const q = (el: HTMLElement) => new URL(el.getAttribute('href') ?? '', 'https://x').searchParams.get('q');

describe('AddressTerrainSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem(TERRAIN_OPEN_KEY);
    mocked.getAddressTerrain.mockResolvedValue(TERRAIN);
  });

  it('leads with the untouched critical exposure and where most of it is', async () => {
    renderIt();
    expect(await screen.findByText(/12 untouched hosts carry a critical scanner observation/)).toBeInTheDocument();
    expect(screen.getByText(/The team has reached/)).toHaveTextContent('The team has reached 58 of 290 hosts (3 tested)');
  });

  // 5.329.0 — the finding is the sentence and the hot block; the 460 px scene
  // opens on demand.
  it('closed by default: the sentence, the hot block with its hosts link, and the control', async () => {
    renderIt();
    const hot = await screen.findByRole('complementary', { name: 'Most untouched critical exposure' });
    expect(within(hot).getByRole('heading', { name: '10.0.0.0/24' })).toBeInTheDocument();
    expect(q(within(hot).getByRole('link', { name: 'Open 250 hosts' }))).toBe('subnet:"10.0.0.0/24"');
    expect(q(within(hot).getByRole('link', { name: /12 untouched with a critical observation/ })))
      .toBe('subnet:"10.0.0.0/24" has:untouched has:critical');
    // jsdom has no WebGL, so what opens is the table.
    const control = screen.getByRole('button', { name: 'Show the table' });
    expect(control).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'View' })).not.toBeInTheDocument();
    // Nothing asked for the scene's chunk.
    expect(sceneLoads.n).toBe(0);
  });

  it('opened: the table view, every count a link to exactly its hosts; the choice is remembered', async () => {
    renderIt();
    await userEvent.click(await screen.findByRole('button', { name: 'Show the table' }));
    const table = await screen.findByRole('table');
    expect(screen.getByRole('button', { name: /Map/ })).toBeDisabled();
    const first = within(table).getAllByRole('row')[1];
    expect(q(within(first).getByRole('link', { name: '10.0.0.0/24' }))).toBe('subnet:"10.0.0.0/24"');
    expect(q(within(first).getByRole('link', { name: '12' }))).toBe('subnet:"10.0.0.0/24" has:untouched has:critical');
    expect(q(within(first).getByRole('link', { name: '25' }))).toBe('subnet:"10.0.0.0/24" has:planned AND NOT has:tested');
    // The hot block is still shown beside it.
    expect(screen.getByRole('complementary', { name: 'Most untouched critical exposure' })).toBeInTheDocument();
    expect(localStorage.getItem(TERRAIN_OPEN_KEY)).toBe('1');

    await userEvent.click(screen.getByRole('button', { name: 'Hide the table' }));
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(localStorage.getItem(TERRAIN_OPEN_KEY)).toBeNull();
  });

  it('with WebGL, the scene’s chunk is fetched only when the map is opened', async () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => ({})) as never);
    try {
      renderIt();
      const control = await screen.findByRole('button', { name: 'Show the map' });
      // The data is here (the sentence is built from it); the scene is not.
      expect(screen.getByText(/The team has reached/)).toBeInTheDocument();
      expect(sceneLoads.n).toBe(0);
      await userEvent.click(control);
      await waitFor(() => expect(sceneLoads.n).toBe(1));
      expect(screen.getByRole('application')).toBeInTheDocument();
      // Nothing pointed at yet: the readout says how to read a block.
      expect(screen.getByText(/Point at a block/)).toBeInTheDocument();
    } finally {
      getContext.mockRestore();
    }
  });

  it('a block chosen with the keyboard is of the answer it was chosen in: a re-read clears it (B24)', async () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => ({})) as never);
    const client = createQueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider>
    );
    try {
      render(<AddressTerrainSection />, { wrapper });
      await userEvent.click(await screen.findByRole('button', { name: 'Show the map' }));
      const map = await screen.findByRole('application');
      fireEvent.keyDown(map, { key: 'ArrowRight' });
      expect(await screen.findByText('Selected block')).toBeInTheDocument();
      // The same blocks read again are a new answer: an index into the old
      // one says nothing about it.
      mocked.getAddressTerrain.mockResolvedValue({ ...TERRAIN, blocks: [BLOCKS[1], BLOCKS[0]] });
      await act(async () => { await client.invalidateQueries(); });
      await waitFor(() => expect(mocked.getAddressTerrain).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(screen.queryByText('Selected block')).not.toBeInTheDocument());
      expect(screen.getByText(/Point at a block/)).toBeInTheDocument();
      // …and the keyboard starts from the first block again.
      fireEvent.keyDown(map, { key: 'ArrowRight' });
      expect(await screen.findByText('Selected block')).toBeInTheDocument();
      fireEvent.keyDown(map, { key: 'Escape' });
      await waitFor(() => expect(screen.queryByText('Selected block')).not.toBeInTheDocument());
    } finally {
      getContext.mockRestore();
    }
  });

  it('a viewer who left it open finds it open', async () => {
    localStorage.setItem(TERRAIN_OPEN_KEY, '1');
    renderIt();
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide the table' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('works when storage refuses: closed, and the control still opens it', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    try {
      renderIt();
      await userEvent.click(await screen.findByRole('button', { name: 'Show the table' }));
      expect(await screen.findByRole('table')).toBeInTheDocument();
    } finally {
      get.mockRestore();
      set.mockRestore();
    }
  });

  it('a failed load is unavailable with a retry, never an empty map', async () => {
    mocked.getAddressTerrain.mockRejectedValueOnce(new Error('503'));
    renderIt();
    expect(await screen.findByText('The address map could not be loaded.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Retry/ }));
    await waitFor(() => expect(screen.getByText(/The team has reached/)).toBeInTheDocument());
  });
});
