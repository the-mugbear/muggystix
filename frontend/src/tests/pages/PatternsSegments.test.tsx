/**
 * Patterns and Segments (5.262.0) — the Posture layout: each page opens with
 * the answer in one sentence, has no cards, and every figure keeps its link.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const systemicMock = vi.fn();
const subnetsMock = vi.fn();
const postureMock = vi.fn();
// Stand-in link builders: the real ones live beside the API client, which
// cannot load in jsdom.
vi.mock('../../services/api', () => ({
  getSystemicInsights: () => systemicMock(),
  getSubnetInsights: (...a: unknown[]) => subnetsMock(...a),
  getPosture: () => postureMock(),
  downloadSystemicReport: vi.fn(),
  conditionHostsHref: (k: string, cidr?: string) => `/hosts?condition=${k}${cidr ? `&subnet=${cidr}` : ''}`,
  familyCellHostsHref: (keys: string[]) => `/hosts?conditions=${keys.join(',')}`,
  subnetHostsHref: (cidr: string) => `/hosts?subnet=${cidr}`,
}));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import Patterns from '../../pages/Patterns';
import Segments from '../../pages/Segments';
import { TooltipProvider } from '../../components/ui/tooltip';

const wrap = (ui: React.ReactElement) => render(<MemoryRouter><TooltipProvider>{ui}</TooltipProvider></MemoryRouter>);

const condition = (key: string, label: string, classification: string, hosts: number) => ({
  key, label, vector: `${label} vector`, severity_weight: 3, recommended_action: `Fix ${label}.`,
  affected_hosts: hosts, host_fraction: hosts / 100, subnet_spread: 4, site_spread: 3, systemic_score: 999,
  example_ips: ['10.0.0.1'], is_blind_spot: classification === 'estate_wide', classification,
  family: 'identity', family_label: 'Identity & authentication',
});

describe('Patterns', () => {
  beforeEach(() => {
    systemicMock.mockReset().mockResolvedValue({
      adopted: true,
      estate: { hosts_in_scope: 100, subnets: 6, sites: 3, blind_spot_count: 1 },
      family_summary: [{
        family: 'identity', family_label: 'Identity & authentication', root_cause_hypothesis: 'No central policy.',
        recommended_control: 'Enforce signing by policy.', conditions: ['smb_signing', 'weak_auth'],
        affected_hosts: 60, host_fraction: 0.6, subnet_spread: 5, site_spread: 3, classification: 'estate_wide',
      }],
      // The server's order: the recurring one first; estate-wide must lead anyway.
      conditions: [condition('weak_auth', 'Weak authentication', 'recurring', 12), condition('smb_signing', 'SMB signing disabled', 'estate_wide', 55)],
      blind_spots: [condition('smb_signing', 'SMB signing disabled', 'estate_wide', 55)],
      segment_outliers: [], diagnostic_profiles: [],
    });
  });

  it('names the estate-wide weakness first, once, without cards or an opaque score', async () => {
    const { container } = wrap(<Patterns />);
    expect(await screen.findByText(/reaches most of the estate: SMB signing disabled\./)).toBeInTheDocument();
    const rows = container.querySelectorAll('tr[data-spread]');
    expect([...rows].map((r) => r.getAttribute('data-spread'))).toEqual(['estate_wide', 'recurring']);
    // The blind spot is a row, not also a card above the table.
    expect(screen.getAllByText('SMB signing disabled')).toHaveLength(1);
    expect(screen.queryByText('999')).not.toBeInTheDocument();
    // No Card anywhere on the page (the Card primitive is `bg-card … shadow-raised`).
    expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
    // Counts still open their hosts.
    expect(screen.getByRole('link', { name: '55' })).toHaveAttribute('href', expect.stringContaining('/hosts'));
  });

  it('says so when nothing recurs, and points at the evidence', async () => {
    systemicMock.mockResolvedValue({
      adopted: true, estate: { hosts_in_scope: 10, subnets: 1, sites: 1, blind_spot_count: 0 },
      family_summary: [], conditions: [], blind_spots: [], segment_outliers: [], diagnostic_profiles: [],
    });
    wrap(<Patterns />);
    expect(await screen.findByText('No weakness recurs across the estate.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'see Evidence' })).toHaveAttribute('href', '/posture/evidence');
  });
});

const subnet = (id: number, cidr: string, site: string | null, crit: number) => ({
  subnet_id: id, cidr, scope_name: 'Corp', site, site_id: site ? 1 : null, criticality_tier: 1,
  host_count: 20, usable_addresses: 254, no_coverage: false,
  exposure: { raw_score: 1, weighted_score: 88, active_findings: crit + 2, by_severity: { critical: crit, high: 0, medium: 2, low: 0, info: 0 } },
  neglect: { unowned_active_findings: 1, unreviewed_hosts: 4 },
  hygiene: { eol_os_hosts: 3, eol_os_detail: [], cert_issue_hosts: 0, weak_auth_hosts: 0, risky_service_hosts: 0, risky_services: [] },
  recommended_action: { kind: 'remediate', text: 'Remediate the criticals.' },
});
const subnetData = {
  adopted: true, subnets: [subnet(1, '10.1.0.0/24', 'HQ', 2), subnet(2, '10.2.0.0/24', null, 0)], total: 2, limit: 50, offset: 0,
  totals: { subnet_count: 2, hosts_in_scope: 40, eol_os_hosts: 6, cert_issue_hosts: 0, weak_auth_hosts: 0, active_findings: 6,
    by_severity: { critical: 2, high: 0, medium: 4, low: 0, info: 0 } },
};
const site = (name: string | null, crit: number) => ({
  site: name, site_id: name ? 1 : null, unassigned: !name, criticality_tier: name ? 1 : null, owner_name: null,
  host_count: 20, expected_host_count: null, coverage_gap: null,
  exposure: { active_findings: crit, by_severity: { critical: crit, high: 0, medium: 0, low: 0, info: 0 } },
  neglect: { unowned_active_findings: 0, unreviewed_hosts: 2 }, recommended_action: { kind: 'triage', text: 'Triage HQ.' },
});

describe('Segments', () => {
  beforeEach(() => {
    subnetsMock.mockReset().mockResolvedValue(subnetData);
    postureMock.mockReset().mockResolvedValue({ sites: { adopted: true, items: [site('HQ', 2), site(null, 0)] } });
  });

  it('opens on the Site lens when sites exist, and names the worst one', async () => {
    wrap(<Segments />);
    expect(await screen.findByText(/Start with/)).toHaveTextContent('Start with HQ: 2 critical active findings.');
    expect(screen.getByRole('tab', { name: 'site' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Unassigned')).toBeInTheDocument();
  });

  it('switches to the Subnet lens without an opaque score or "Do" chips', async () => {
    wrap(<Segments />);
    await screen.findByText(/Start with/);
    fireEvent.click(screen.getByRole('tab', { name: 'subnet' }));
    expect(screen.getByText(/Start with/)).toHaveTextContent(
      'Start with 10.1.0.0/24 (HQ): 2 critical active findings; 3 hosts on end-of-life OS.',
    );
    expect(screen.queryByText('·88')).not.toBeInTheDocument();
    expect(screen.queryByText('Do')).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '3 end-of-life' })[0]).toHaveAttribute('href', expect.stringContaining('10.1.0.0'));
  });

  it('opens on the Subnet lens when the project defines no sites', async () => {
    postureMock.mockResolvedValue({ sites: { adopted: true, items: [site(null, 0)] } });
    wrap(<Segments />);
    await screen.findByText(/Start with/);
    expect(screen.getByRole('tab', { name: 'subnet' })).toHaveAttribute('aria-selected', 'true');
  });
});
