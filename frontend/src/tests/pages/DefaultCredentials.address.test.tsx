/**
 * Default credentials — the page, the page size and the filters live in the
 * address (B33; as on the SBOM page), under the REAL router.  The page number
 * was component state put back to 0 by an effect whenever a filter changed:
 * a reload or a shared link always landed on the first 25 of everything.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import DefaultCredentials from '../../pages/DefaultCredentials';
import { TooltipProvider } from '../../components/ui/tooltip';

/** 60 Acme rows and 12 Zyx rows: three pages of 25, or two of Acme alone at 50. */
const SHEET = [
  'productvendor,username,password',
  ...Array.from({ length: 60 }, (_, i) => `Acme,user${i + 1},pw${i + 1}`),
  ...Array.from({ length: 12 }, (_, i) => `Zyx,root${i + 1},toor${i + 1}`),
  '',
].join('\n');

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/default-credentials', element: <TooltipProvider><DefaultCredentials /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
const settle = async (ms = 400) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, text: async () => SHEET }));
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('Default credentials — list state lives in the address (real router)', () => {
  it('opens on the first 25 with nothing in the address, and Next writes the page (replace)', async () => {
    const router = open('/default-credentials');
    expect(await screen.findByText('Page 1 of 3 · 72 entries')).toBeInTheDocument();
    expect(screen.getByText('user1')).toBeInTheDocument();
    expect(router.state.location.search).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(await screen.findByText('Page 2 of 3 · 72 entries')).toBeInTheDocument();
    expect(screen.getByText('user26')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=2');
    expect(router.state.historyAction).toBe('REPLACE');

    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(await screen.findByText('Page 1 of 3 · 72 entries')).toBeInTheDocument();
    expect(router.state.location.search).toBe('');
  });

  it('a reload on page 3 shows page 3, and writes nothing', async () => {
    const router = open('/default-credentials?page=3');
    expect(await screen.findByText('Page 3 of 3 · 72 entries')).toBeInTheDocument();
    expect(screen.getByText('root12')).toBeInTheDocument();
    await settle();
    expect(router.state.location.search).toBe('?page=3');
  });

  it('opens on the filters the address names; a value it cannot mean is the default', async () => {
    open('/default-credentials?vendor=Zyx&search=root1&per=10&page=2');
    // root1, root10, root11, root12 — one page of ten: a page past the end shows the last.
    expect(await screen.findByText('Page 1 of 1 · 4 entries')).toBeInTheDocument();
    expect(screen.getByLabelText('Search')).toHaveValue('root1');
    expect(screen.getByText(/4 of 72 credentials/)).toBeInTheDocument();
  });

  it('an unknown vendor, page size or page is the default, not an empty list', async () => {
    open('/default-credentials?vendor=Nobody&per=7&page=abc');
    expect(await screen.findByText('Page 1 of 3 · 72 entries')).toBeInTheDocument();
  });

  it('a search is written once the typing stops and starts from the first page; Clear removes both filters in one write', async () => {
    const router = open('/default-credentials?vendor=Acme&page=2');
    expect(await screen.findByText('Page 2 of 3 · 60 entries')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'user6' } });
    // user6 and user60.
    expect(await screen.findByText('Page 1 of 1 · 2 entries')).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe('?vendor=Acme&search=user6'));
    expect(router.state.historyAction).toBe('REPLACE');

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(await screen.findByText('Page 1 of 3 · 72 entries')).toBeInTheDocument();
    expect(screen.getByLabelText('Search')).toHaveValue('');
    await settle();
    expect(router.state.location.search).toBe('');
  });

  it('a link to the same page with another filter, and Back, change the rows; nothing writes the old state back', async () => {
    const router = open('/default-credentials?page=2');
    await screen.findByText('Page 2 of 3 · 72 entries');
    await act(async () => { await router.navigate('/default-credentials?vendor=Zyx'); });
    expect(await screen.findByText('Page 1 of 1 · 12 entries')).toBeInTheDocument();
    await settle();
    expect(router.state.location.search).toBe('?vendor=Zyx');

    await act(async () => { await router.navigate(-1); });
    expect(await screen.findByText('Page 2 of 3 · 72 entries')).toBeInTheDocument();
    await settle();
    expect(router.state.location.search).toBe('?page=2');
  });
});
