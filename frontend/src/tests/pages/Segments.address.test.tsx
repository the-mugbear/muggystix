/**
 * Segments — the subnet table's page and the lens live in the address, under
 * the REAL router (setupTests replaces `useNavigate` / `useLocation` for
 * every other file).
 *
 * The page number was component state: a reload, a shared link and Back from
 * a subnet's hosts all landed on the first fifty subnets.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

const subnetsMock = vi.fn();
const postureMock = vi.fn();
vi.mock('../../services/api', () => ({
  getSubnetInsights: (...a: unknown[]) => subnetsMock(...a),
  getPosture: () => postureMock(),
  downloadSystemicReport: vi.fn(),
  conditionHostsHref: (k: string, cidr?: string) => `/hosts?condition=${k}${cidr ? `&subnet=${cidr}` : ''}`,
  subnetHostsHref: (cidr: string) => `/hosts?subnet=${cidr}`,
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P' } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: () => true }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import Segments from '../../pages/Segments';
import { TooltipProvider } from '../../components/ui/tooltip';

const TOTAL = 120;
const subnet = (i: number) => ({
  subnet_id: i + 1, cidr: `10.9.${i}.0/24`, scope_name: 'Corp', site: null, site_id: null, criticality_tier: null,
  host_count: 20, usable_addresses: 254, no_coverage: false,
  exposure: { raw_score: 1, weighted_score: 1, active_findings: 1, by_severity: { critical: 0, high: 0, medium: 1, low: 0, info: 0 } },
  neglect: { unowned_active_findings: 0, unreviewed_hosts: 0 },
  hygiene: { eol_os_hosts: 0, eol_os_detail: [], cert_issue_hosts: 0, weak_auth_hosts: 0, risky_service_hosts: 0, risky_services: [] },
  recommended_action: { kind: 'ok', text: 'Nothing to do.' },
});
const site = (name: string | null) => ({
  site: name, site_id: name ? 1 : null, unassigned: !name, criticality_tier: name ? 1 : null, owner_name: null,
  host_count: 20, expected_host_count: null, coverage_gap: null,
  exposure: { active_findings: 0, by_severity: { critical: 0, high: 0, medium: 0, low: 0, info: 0 } },
  neglect: { unowned_active_findings: 0, unreviewed_hosts: 0 }, recommended_action: { kind: 'ok', text: 'Fine.' },
});

const settle = async (ms = 450) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/segments', element: <TooltipProvider><Segments /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
/** [limit, offset] of each subnet read — every one asked of project 1. */
const asked = () => subnetsMock.mock.calls.map(([projectId, limit, offset]) => {
  expect(projectId).toBe(1);
  return [limit, offset];
});
const last = () => asked()[asked().length - 1];

beforeEach(() => {
  subnetsMock.mockReset().mockImplementation(async (_p: number, limit: number, offset: number) => ({
    adopted: true, total: TOTAL, limit, offset,
    subnets: Array.from({ length: Math.max(0, Math.min(limit, TOTAL - offset)) }, (_, k) => subnet(offset + k)),
    totals: { subnet_count: TOTAL, hosts_in_scope: 2400, eol_os_hosts: 0, cert_issue_hosts: 0, weak_auth_hosts: 0,
      active_findings: TOTAL, by_severity: { critical: 0, high: 0, medium: TOTAL, low: 0, info: 0 } },
  }));
  // No named site: the page opens on the Subnet lens.
  postureMock.mockReset().mockResolvedValue({ sites: { adopted: true, items: [site(null)] } });
});

describe('Segments — the page of subnets lives in the address (real router)', () => {
  it('a reload on page 3 shows page 3, and asks for it alone', async () => {
    const router = open('/segments?page=3');
    expect(await screen.findByText('Showing 101–120 of 120 subnets, worst first')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '10.9.100.0/24' })).toBeInTheDocument();
    expect(asked()).toEqual([[50, 100]]);
    await settle();
    expect(router.state.location.search).toBe('?page=3');
    expect(asked()).toEqual([[50, 100]]);
  });

  it('Next and Previous write the address (replace) and the list follows', async () => {
    const router = open('/segments');
    await screen.findByText('Showing 1–50 of 120 subnets, worst first');
    expect(last()).toEqual([50, 0]);

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Showing 51–100 of 120 subnets, worst first')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=2');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(last()).toEqual([50, 50]);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('Showing 1–50 of 120 subnets, worst first')).toBeInTheDocument();
    // The first page is left out of the address.
    expect(router.state.location.search).toBe('');
  });

  it('a link to the same page with another page number, and Back, change the rows; nothing writes the old page back', async () => {
    const router = open('/segments');
    await screen.findByText('Showing 1–50 of 120 subnets, worst first');

    await act(async () => { await router.navigate('/segments?page=3'); });
    expect(await screen.findByText('Showing 101–120 of 120 subnets, worst first')).toBeInTheDocument();
    expect(last()).toEqual([50, 100]);
    await settle();
    expect(router.state.location.search).toBe('?page=3');
    expect(screen.getByRole('link', { name: '10.9.100.0/24' })).toBeInTheDocument();

    await act(async () => { await router.navigate(-1); });
    expect(await screen.findByText('Showing 1–50 of 120 subnets, worst first')).toBeInTheDocument();
    await settle();
    expect(router.state.location.search).toBe('');
    expect(screen.getByRole('link', { name: '10.9.0.0/24' })).toBeInTheDocument();
  });

  it('a page past the end steps back to the last page that exists', async () => {
    const router = open('/segments?page=40');
    expect(await screen.findByText('Showing 101–120 of 120 subnets, worst first')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=3');
  });

  it('the page on screen stays while the next one loads; a next page that fails says so with Retry (B5)', async () => {
    open('/segments');
    await screen.findByText('Showing 1–50 of 120 subnets, worst first');
    let fail: (e: unknown) => void = () => undefined;
    subnetsMock.mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    // The rows that were read stay under the page that was asked for.
    expect(await screen.findByText('Showing 51–100 of 120 subnets, worst first')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '10.9.0.0/24' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    expect(screen.queryByText('Ranking segments…')).not.toBeInTheDocument();

    await act(async () => { fail(new Error('HTTP 500')); });
    expect(await screen.findByText("Couldn't load the segments")).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '10.9.0.0/24' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByRole('link', { name: '10.9.50.0/24' })).toBeInTheDocument();
    expect(last()).toEqual([50, 50]);
  });

  it('a first read that fails says so with Retry, never an empty page', async () => {
    subnetsMock.mockRejectedValueOnce(new Error('HTTP 500'));
    open('/segments');
    expect(await screen.findByText("Couldn't load the segments")).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByText('Showing 1–50 of 120 subnets, worst first')).toBeInTheDocument();
  });

  it('a page the address cannot mean is the first', async () => {
    open('/segments?page=abc');
    expect(await screen.findByText('Showing 1–50 of 120 subnets, worst first')).toBeInTheDocument();
    expect(asked()).toEqual([[50, 0]]);
  });
});

describe('Segments — the lens lives in the address (real router)', () => {
  beforeEach(() => {
    postureMock.mockResolvedValue({ sites: { adopted: true, items: [site('HQ'), site(null)] } });
  });

  it('opens on the lens the address names, over the default', async () => {
    open('/segments?lens=subnet&page=2');
    expect(await screen.findByText('Showing 51–100 of 120 subnets, worst first')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'subnet' })).toHaveAttribute('aria-selected', 'true');
  });

  it('a lens the address cannot mean is the default — Site when sites exist — and nothing is written', async () => {
    const router = open('/segments?lens=galaxy');
    await screen.findByText(/No site carries active findings/);
    expect(screen.getByRole('tab', { name: 'site' })).toHaveAttribute('aria-selected', 'true');
    await settle();
    expect(router.state.location.search).toBe('?lens=galaxy');
  });

  it('choosing a lens writes it (replace) and keeps the subnet page; Back from another lens restores it', async () => {
    const router = open('/segments?page=2');
    await screen.findByText(/No site carries active findings/);
    fireEvent.click(screen.getByRole('tab', { name: 'subnet' }));
    expect(await screen.findByText('Showing 51–100 of 120 subnets, worst first')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=2&lens=subnet');
    expect(router.state.historyAction).toBe('REPLACE');

    await act(async () => { await router.navigate('/segments?lens=site'); });
    expect(screen.getByRole('tab', { name: 'site' })).toHaveAttribute('aria-selected', 'true');
    await act(async () => { await router.navigate(-1); });
    expect(screen.getByRole('tab', { name: 'subnet' })).toHaveAttribute('aria-selected', 'true');
    await settle();
    expect(router.state.location.search).toBe('?page=2&lens=subnet');
  });
});
