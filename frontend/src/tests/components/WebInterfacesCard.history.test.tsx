/**
 * Code review 2026-09-19, findings 20 + 21.
 *
 * 20 — collapsing a re-scanned URL to its latest row discarded ACCESS to the
 *      others: "seen in 2 scans" was a count with nothing behind it, and a
 *      screenshot only the older scan took could no longer be opened.
 * 21 — "latest" was chosen by `last_seen`, the row's write time, so importing
 *      an OLD scan later made the old evidence the visible row.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({ getHostWebInterfaces: vi.fn(), fetchWebInterfaceScreenshot: vi.fn() }));
vi.mock('../../services/api', () => api);

import WebInterfacesCard from '../../components/WebInterfacesCard';
import { TooltipProvider } from '../../components/ui/tooltip';

const row = (over: Record<string, unknown>) => ({
  id: 1, source: 'eyewitness', url: 'https://10.0.0.5:8443/', protocol: 'https', port: 8443,
  status_code: 200, title: 'Admin Console', server_header: 'nginx', technologies: [],
  has_screenshot: false, scan_id: 1, port_id: 72, ...over,
});

const renderCard = (rows: unknown[]) => {
  api.getHostWebInterfaces.mockResolvedValue(rows);
  return render(
    <MemoryRouter><TooltipProvider><WebInterfacesCard hostId={1} count={rows.length} /></TooltipProvider></MemoryRouter>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  URL.revokeObjectURL = vi.fn();
  api.fetchWebInterfaceScreenshot.mockResolvedValue('blob:shot');
});

describe('WebInterfacesCard — history stays reachable', () => {
  const august = row({
    id: 5, scan_id: 19, status_code: 401, title: 'Old console', has_screenshot: true,
    observed_at: '2026-08-07T18:00:00Z', observed_at_basis: 'scan', first_seen: '2026-08-07T18:12:00Z',
  });
  const september = row({
    id: 41, scan_id: 109, status_code: 200, title: 'Admin Console', has_screenshot: false,
    observed_at: '2026-09-10T02:00:00Z', observed_at_basis: 'scan', first_seen: '2026-09-10T02:22:00Z',
  });

  it('shows the latest, and says an earlier observation holds a screenshot the latest lacks', async () => {
    renderCard([august, september]);
    expect(await screen.findByText('Admin Console')).toBeInTheDocument();
    expect(screen.queryByText('Old console')).not.toBeInTheDocument();
    // The latest scan took no screenshot: there is no big screenshot button…
    expect(screen.queryByRole('button', { name: /^View screenshot of/ })).not.toBeInTheDocument();
    // …and the row says where one is, instead of reading as "no screenshot".
    expect(screen.getByRole('button', { name: /1 earlier observation \(with a screenshot\) · show/ })).toBeInTheDocument();
  });

  it('opens the earlier observation with its own status, scan and screenshot', async () => {
    renderCard([august, september]);
    fireEvent.click(await screen.findByRole('button', { name: /1 earlier observation/ }));

    const history = screen.getByRole('list', { name: /Earlier observations of/ });
    expect(within(history).getByText('Old console')).toBeInTheDocument();
    expect(within(history).getByText('401')).toBeInTheDocument();
    expect(within(history).getByText('scan #19')).toBeInTheDocument();

    fireEvent.click(within(history).getByRole('button', { name: /View the screenshot from scan #19/ }));
    // The OLDER row's screenshot, by its own id — not the latest row's.
    await waitFor(() => expect(api.fetchWebInterfaceScreenshot).toHaveBeenCalledWith(5));
  });
});

describe('WebInterfacesCard — "latest" means latest OBSERVED', () => {
  it('an old scan imported later does not replace the newer evidence', async () => {
    // September's scan was imported first; August's was uploaded afterwards, so
    // its row has the LATER write time — which is what used to win.
    const septemberFirst = row({
      id: 10, scan_id: 1, title: 'September state',
      observed_at: '2026-09-10T02:00:00Z', observed_at_basis: 'scan',
      first_seen: '2026-09-10T03:00:00Z', last_seen: '2026-09-10T03:00:00Z',
    });
    const augustLater = row({
      id: 11, scan_id: 2, title: 'August state',
      observed_at: '2026-08-07T18:00:00Z', observed_at_basis: 'scan',
      first_seen: '2026-09-15T09:00:00Z', last_seen: '2026-09-15T09:00:00Z',
    });
    renderCard([septemberFirst, augustLater]);
    expect(await screen.findByText('September state')).toBeInTheDocument();
    expect(screen.queryByText('August state')).not.toBeInTheDocument();
  });

  it('says "imported", not "observed", when the tool recorded no scan time', async () => {
    renderCard([row({ id: 1, observed_at: '2026-09-10T03:00:00Z', observed_at_basis: 'import' })]);
    const when = await screen.findByText(/^imported /);
    expect(when).toHaveAttribute('title', expect.stringContaining('not when the site was seen'));
    expect(screen.queryByText(/^observed /)).not.toBeInTheDocument();
  });

  it('still works against a backend that sends no observed_at', async () => {
    renderCard([
      row({ id: 1, title: 'older', first_seen: '2026-08-01T00:00:00Z' }),
      row({ id: 2, title: 'newer', first_seen: '2026-09-01T00:00:00Z' }),
    ]);
    expect(await screen.findByText('newer')).toBeInTheDocument();
    expect(screen.queryByText('older')).not.toBeInTheDocument();
  });
});
