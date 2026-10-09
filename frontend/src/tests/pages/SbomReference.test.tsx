/**
 * Software Bill of Materials — what the reader sees (the list, its filters,
 * sort and paging), and where that state lives: the address.
 *
 * Under the REAL router (setupTests replaces `useNavigate` / `useLocation`):
 * the search, the filters, the sort and the page were component state, so a
 * reload or a shared link always opened on the first fifty of everything.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({ getSbom: vi.fn() }));
const downloadMock = vi.hoisted(() => vi.fn());
vi.mock('../../utils/download', () => ({ downloadTextFile: downloadMock }));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import SbomReference from '../../pages/SbomReference';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const pad = (i: number) => String(i).padStart(3, '0');
const LONG = `a-package-with-a-very-long-name-${'x'.repeat(200)}`;
// 130 components — three pages of 50.  Odd ones are backend Python, even ones
// frontend npm; every tenth is direct; licences are MIT, Apache-2.0 (every
// seventh) or missing (every thirteenth).  By name: pkg-001 … pkg-130.
const component = (i: number) => {
  const backend = i % 2 === 1;
  return {
    name: `pkg-${pad(i)}`, version: `1.${i}.0`,
    ecosystem: backend ? 'python' : 'npm', application_layer: backend ? 'backend' : 'frontend',
    declared_in: i % 10 === 0 ? 'package.json' : null,
    resolved_from: backend ? 'installed venv' : 'package-lock.json',
    direct: i % 10 === 0,
    license: i % 13 === 0 ? null : i % 7 === 0 ? 'Apache-2.0' : 'MIT',
  };
};
const COMPONENTS = Array.from({ length: 130 }, (_, k) => component(k + 1));
const sbom = (components = COMPONENTS) => ({
  generated_at: '2026-10-01T12:00:00Z', app_version: '2.475.0',
  manifests: { python: 'requirements.txt', npm: 'package-lock.json' },
  summary: { total: components.length, direct: 13, transitive: 117, backend: 65, frontend: 65 },
  components,
});

const settle = async (ms = 450) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const open = async (entry = '/sbom') => {
  const router = createMemoryRouter([{ path: '/sbom', element: <SbomReference /> }], { initialEntries: [entry] });
  render(<RouterProvider router={router} />);
  await screen.findByRole('heading', { name: 'Software Bill of Materials' });
  return router;
};
const box = () => screen.getByRole('searchbox', { name: 'Search SBOM by package name' }) as HTMLInputElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
/** The packages listed, in order. */
const names = () => within(screen.getByRole('table')).getAllByRole('row')
  .map((r) => r.textContent?.match(/pkg-\d{3}/)?.[0]).filter(Boolean);
const range = (from: number, to: number, step = 1) => {
  const out: string[] = [];
  for (let i = from; step > 0 ? i <= to : i >= to; i += step) out.push(`pkg-${pad(i)}`);
  return out;
};
const head = (label: string) => screen.getByRole('columnheader', { name: label });
const pressed = (group: string) => within(screen.getByRole('group', { name: group }))
  .getAllByRole('button').find((b) => b.getAttribute('aria-pressed') === 'true')?.textContent;

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getSbom.mockResolvedValue(sbom());
});

describe('SBOM — what the reader sees', () => {
  it('lists the build and its components, fifty to a page, by name', async () => {
    await open();
    expect(screen.getByText('2.475.0')).toBeInTheDocument();
    expect(screen.getByText('65 / 65')).toBeInTheDocument();
    expect(screen.getByText('Showing 130 of 130')).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 3 · 130 components')).toBeInTheDocument();
    expect(names()).toEqual(range(1, 50));
    expect(head('Name')).toHaveAttribute('aria-sort', 'ascending');
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
    expect(mocked.getSbom).toHaveBeenCalledTimes(1);

    const row = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;
    // A direct npm package: where it was declared and where it was resolved.
    expect(within(row('pkg-010')).getByText('direct')).toBeInTheDocument();
    expect(within(row('pkg-010')).getByText('declared: package.json')).toBeInTheDocument();
    expect(within(row('pkg-010')).getByText('resolved: package-lock.json')).toBeInTheDocument();
    // A transitive Python one with no licence: a dash, never "null".
    expect(within(row('pkg-013')).getByText('transitive')).toBeInTheDocument();
    expect(within(row('pkg-013')).getByText('declared: —')).toBeInTheDocument();
    expect(within(row('pkg-013')).getAllByRole('cell')[5].textContent).toBe('—');
    expect(row('pkg-013').textContent).not.toMatch(/null|undefined/);
  });

  it('says a failed read, and does not show an empty list', async () => {
    mocked.getSbom.mockRejectedValue(new Error('boom'));
    const router = createMemoryRouter([{ path: '/sbom', element: <SbomReference /> }], { initialEntries: ['/sbom'] });
    render(<RouterProvider router={router} />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.queryByText(/No components match/)).toBeNull();
  });

  it('a package with a very long name is listed whole, in a cell that wraps', async () => {
    mocked.getSbom.mockResolvedValue(sbom([{ ...component(1), name: LONG }]));
    await open();
    expect(screen.getByText(LONG).className).toMatch(/break-words/);
    expect(screen.getByText('Page 1 of 1 · 1 component')).toBeInTheDocument();
  });

  it('searches by name, whatever the case, and says when nothing matches', async () => {
    await open();
    type('PKG-13');
    await waitFor(() => expect(names()).toEqual(['pkg-130']));
    expect(screen.getByText('Showing 1 of 130')).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 1 · 1 component')).toBeInTheDocument();

    type('left-pad');
    expect(await screen.findByText(/No components match your filters\./)).toBeInTheDocument();
    expect(screen.getByText('Try searching for the package name exactly as published.')).toBeInTheDocument();
    expect(screen.getByText('Showing 0 of 130')).toBeInTheDocument();
  });

  it('filters by layer, by source and by licence — together', async () => {
    const user = userEvent.setup();
    await open();
    fireEvent.click(within(screen.getByRole('group', { name: 'Filter by application layer' })).getByRole('button', { name: 'frontend' }));
    expect(screen.getByText('Showing 65 of 130')).toBeInTheDocument();
    expect(names()).toEqual(range(2, 100, 2));

    fireEvent.click(within(screen.getByRole('group', { name: 'Filter by source (direct or transitive)' })).getByRole('button', { name: 'direct' }));
    expect(names()).toEqual(range(10, 130, 10));

    // The licences of the build, counted, the missing ones last.
    await user.click(screen.getByRole('combobox', { name: 'License' }));
    expect((await screen.findAllByRole('option')).map((o) => o.textContent)).toEqual([
      'All licenses', 'Apache-2.0 (17)', 'MIT (103)', 'Unspecified (10)',
    ]);
    await user.click(screen.getByRole('option', { name: 'Apache-2.0 (17)' }));
    await waitFor(() => expect(names()).toEqual(['pkg-070']));
  });

  it('sorts by a column; the same column again reverses it', async () => {
    await open();
    fireEvent.click(within(head('Version')).getByRole('button'));
    expect(head('Version')).toHaveAttribute('aria-sort', 'ascending');
    expect(head('Name')).toHaveAttribute('aria-sort', 'none');
    // Numeric, not text order: 1.2.0 before 1.10.0.
    expect(names()).toEqual(range(1, 50));

    fireEvent.click(within(head('Version')).getByRole('button'));
    expect(head('Version')).toHaveAttribute('aria-sort', 'descending');
    expect(names()).toEqual(range(130, 81, -1));

    // Another column starts ascending: direct before transitive, then by name.
    fireEvent.click(within(head('Source')).getByRole('button'));
    expect(head('Source')).toHaveAttribute('aria-sort', 'ascending');
    expect(names().slice(0, 14)).toEqual([...range(10, 130, 10), 'pkg-001']);
  });

  it('pages through the list, and by another page size', async () => {
    const user = userEvent.setup();
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(screen.getByText('Page 2 of 3 · 130 components')).toBeInTheDocument();
    expect(names()).toEqual(range(51, 100));
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(names()).toEqual(range(101, 130));
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(names()).toEqual(range(51, 100));

    // Another size starts from the first page.
    await user.click(screen.getByRole('combobox', { name: 'Rows per page' }));
    await user.click(await screen.findByRole('option', { name: '25' }));
    await waitFor(() => expect(screen.getByText('Page 1 of 6 · 130 components')).toBeInTheDocument());
    expect(names()).toEqual(range(1, 25));
  });

  it('a filter or a sort chosen on a later page starts from the first', async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    fireEvent.click(within(screen.getByRole('group', { name: 'Filter by application layer' })).getByRole('button', { name: 'backend' }));
    expect(screen.getByText('Page 1 of 2 · 65 components')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    fireEvent.click(within(head('Name')).getByRole('button'));
    expect(screen.getByText('Page 1 of 2 · 65 components')).toBeInTheDocument();
    expect(names()[0]).toBe('pkg-129');
  });

  it('downloads the whole SBOM, named by the build', async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: /Download JSON/ }));
    expect(downloadMock).toHaveBeenCalledWith('networkmapper-sbom-2.475.0.json', JSON.stringify(sbom(), null, 2), 'application/json');
  });

  it('explains how the list is generated only when asked', async () => {
    await open();
    expect(screen.queryByText(/live snapshot of the running build/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /How is this list generated/ }));
    expect(screen.getByText(/live snapshot of the running build/)).toBeInTheDocument();
  });
});

describe('SBOM — the list state lives in the address (real router)', () => {
  it('opens on what the address says: search, filters, sort, page size and page', async () => {
    await open('/sbom?search=pkg&layer=backend&source=transitive&license=MIT&sort=version&dir=desc&per=25&page=2');
    expect(box().value).toBe('pkg');
    expect(pressed('Filter by application layer')).toBe('backend');
    expect(pressed('Filter by source (direct or transitive)')).toBe('transitive');
    expect(screen.getByRole('combobox', { name: 'License' })).toHaveTextContent('MIT (103)');
    expect(screen.getByRole('combobox', { name: 'Rows per page' })).toHaveTextContent('25');
    expect(head('Version')).toHaveAttribute('aria-sort', 'descending');
    // Backend (odd), MIT (not a multiple of 7 or 13), newest version first.
    const expected = range(129, 1, -2).filter((n) => { const i = Number(n.slice(4)); return i % 7 !== 0 && i % 13 !== 0; });
    expect(screen.getByText(`Page 2 of ${Math.ceil(expected.length / 25)} · ${expected.length} components`)).toBeInTheDocument();
    expect(names()).toEqual(expected.slice(25, 50));
  });

  it('a reload on page 3 shows page 3 and writes nothing', async () => {
    const router = await open('/sbom?page=3');
    expect(screen.getByText('Page 3 of 3 · 130 components')).toBeInTheDocument();
    expect(names()).toEqual(range(101, 130));
    await settle();
    expect(router.state.location.search).toBe('?page=3');
    expect(names()).toEqual(range(101, 130));
  });

  it('what the address cannot mean is the default; a page past the end is the last', async () => {
    await open('/sbom?layer=kernel&source=x&sort=colour&dir=sideways&per=7&page=40');
    expect(pressed('Filter by application layer')).toBe('all');
    expect(pressed('Filter by source (direct or transitive)')).toBe('all');
    expect(head('Name')).toHaveAttribute('aria-sort', 'ascending');
    expect(screen.getByText('Page 3 of 3 · 130 components')).toBeInTheDocument();
    expect(names()).toEqual(range(101, 130));
  });

  it('the pager writes the address (replace); the first page is left out', async () => {
    const router = await open('/sbom?layer=backend');
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(router.state.location.search).toBe('?layer=backend&page=2');
    expect(router.state.historyAction).toBe('REPLACE');
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(router.state.location.search).toBe('?layer=backend');
  });

  it('a typed search is written once the typing stops — trimmed, page dropped, the rest kept', async () => {
    const router = await open('/sbom?layer=backend&page=2');
    type('  pkg-1 ');
    await waitFor(() => expect(router.state.location.search).toBe('?layer=backend&search=pkg-1'));
    expect(router.state.historyAction).toBe('REPLACE');
    expect(names()).toEqual(range(101, 129, 2));
    type('');
    await waitFor(() => expect(router.state.location.search).toBe('?layer=backend'));
  });

  it('a filter and a sort write the address and drop the page; a default is left out', async () => {
    const router = await open('/sbom?page=3');
    fireEvent.click(within(screen.getByRole('group', { name: 'Filter by application layer' })).getByRole('button', { name: 'frontend' }));
    expect(router.state.location.search).toBe('?layer=frontend');
    expect(router.state.historyAction).toBe('REPLACE');

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    fireEvent.click(within(screen.getByRole('group', { name: 'Filter by source (direct or transitive)' })).getByRole('button', { name: 'transitive' }));
    expect(router.state.location.search).toBe('?layer=frontend&source=transitive');

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    fireEvent.click(within(head('License')).getByRole('button'));
    expect(router.state.location.search).toBe('?layer=frontend&source=transitive&sort=license');
    fireEvent.click(within(head('License')).getByRole('button'));
    expect(router.state.location.search).toBe('?layer=frontend&source=transitive&sort=license&dir=desc');
    // Back to the default order: by name, ascending — nothing in the address.
    fireEvent.click(within(head('Name')).getByRole('button'));
    expect(router.state.location.search).toBe('?layer=frontend&source=transitive');

    fireEvent.click(within(screen.getByRole('group', { name: 'Filter by application layer' })).getByRole('button', { name: 'all' }));
    expect(router.state.location.search).toBe('?source=transitive');
  });

  it('a link to the same page with other state, and Back, change the list; nothing writes the old state back', async () => {
    const router = await open('/sbom?search=pkg-00');
    expect(box().value).toBe('pkg-00');
    expect(names()).toEqual(range(1, 9));

    await act(async () => { await router.navigate('/sbom?layer=frontend&sort=version&dir=desc&page=2'); });
    expect(box().value).toBe('');
    expect(pressed('Filter by application layer')).toBe('frontend');
    expect(head('Version')).toHaveAttribute('aria-sort', 'descending');
    expect(screen.getByText('Page 2 of 2 · 65 components')).toBeInTheDocument();
    expect(names()).toEqual(range(30, 2, -2));
    await settle();
    expect(router.state.location.search).toBe('?layer=frontend&sort=version&dir=desc&page=2');
    expect(names()).toEqual(range(30, 2, -2));

    await act(async () => { await router.navigate(-1); });
    expect(box().value).toBe('pkg-00');
    expect(pressed('Filter by application layer')).toBe('all');
    expect(head('Name')).toHaveAttribute('aria-sort', 'ascending');
    expect(names()).toEqual(range(1, 9));
    await settle();
    expect(router.state.location.search).toBe('?search=pkg-00');
    // The list was read once, whatever the address did.
    expect(mocked.getSbom).toHaveBeenCalledTimes(1);
  });
});
