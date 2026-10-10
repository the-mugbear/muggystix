/**
 * The Scans page's two views (5.380.0).  "Ingestion Results" was a page of
 * its own over the same uploads; it is the Scans page's "Ingestion results"
 * view, chosen from the address — and only for someone who may read it
 * (its routes need analyst).
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));
let canWrite = true;
vi.mock('../../hooks/useProjectRole', () => ({ useProjectRole: () => ({ canWrite }) }));
vi.mock('../../pages/Scans', () => ({ default: () => <p>the imported scans</p> }));
vi.mock('../../pages/IngestionResults', () => ({ default: () => <p>the import results</p> }));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import ScansViewTabs from '../../components/scans/ScansViewTabs';
import ScansHub from '../../pages/ScansHub';

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/scans', element: <><ScansViewTabs /><ScansHub /></> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};

describe('the Scans page has two views', () => {
  it('opens on the imported scans, and on the import results when the address says so', () => {
    canWrite = true;
    const first = open('/scans');
    expect(screen.getByText('the imported scans')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Imported scans' })).toHaveAttribute('aria-selected', 'true');

    fireEvent.click(screen.getByRole('tab', { name: 'Ingestion results' }));
    expect(screen.getByText('the import results')).toBeInTheDocument();
    expect(first.state.location.search).toBe('?view=results');
    expect(first.state.historyAction).toBe('REPLACE');
  });

  it('a switch starts the other view clean: each has its own filters', () => {
    canWrite = true;
    const router = open('/scans?view=results&status=failed&page=3');
    expect(screen.getByText('the import results')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Imported scans' }));
    expect(router.state.location.search).toBe('');
    expect(screen.getByText('the imported scans')).toBeInTheDocument();
  });

  it('someone below analyst has the one view and no tabs, whatever the address says', () => {
    canWrite = false;
    open('/scans?view=results&status=failed');
    expect(screen.getByText('the imported scans')).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });
});
