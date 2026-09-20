import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import ScopeMembershipCard from '../../components/host-inspector/ScopeMembershipCard';
import type { HostScopeMembership, HostScopeSubnetEntry } from '../../services/api';

const subnet = (over: Partial<HostScopeSubnetEntry> = {}): HostScopeSubnetEntry => ({
  id: 1, scope_id: 7, cidr: '10.1.2.0/24', description: null, site: null, labels: [], ...over,
});

const renderCard = (membership?: HostScopeMembership | null) =>
  render(<MemoryRouter><ScopeMembershipCard membership={membership} /></MemoryRouter>);

describe('ScopeMembershipCard', () => {
  // An older backend sends no block; a card asserting "out of scope" on no
  // evidence would be a confident wrong answer, so nothing renders.
  it('renders nothing without a membership block', () => {
    const { container } = renderCard(null);
    expect(container).toBeEmptyDOMElement();
  });

  // v5.240.0 — a header line, not a card: the status and the CLOSEST entry are
  // always there, the rest opens on demand.
  it('shows the status and the most specific subnet, whatever order the entries arrive in', () => {
    renderCard({
      coverage: 'subnet',
      project_has_scope: true,
      subnets: [subnet({ id: 2, cidr: '10.0.0.0/8' }), subnet({ id: 1, cidr: '10.1.2.0/24' })],
      names: [],
    });
    expect(screen.getByText('In scope')).toBeInTheDocument();
    expect(screen.getByText('10.1.2.0/24')).toHaveAttribute('href', '/scopes/7');
    expect(screen.queryByText('10.0.0.0/8')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'show entries' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('lists every subnet entry with its site, labels and a link to the scope', () => {
    renderCard({
      coverage: 'subnet',
      project_has_scope: true,
      subnets: [
        subnet({ id: 1, cidr: '10.1.2.0/24', site: 'London DC', description: 'dmz',
          labels: [{ id: 3, name: 'prod', color: '#f00' }] }),
        subnet({ id: 2, cidr: '10.0.0.0/8' }),
      ],
      names: [],
    });
    expect(screen.getByText('In scope')).toBeInTheDocument();
    expect(screen.getByText('2 subnet entries')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'show entries' }));
    expect(screen.getByText('10.1.2.0/24')).toHaveAttribute('href', '/scopes/7');
    expect(screen.getByText('10.0.0.0/8')).toBeInTheDocument();
    expect(screen.getByText('London DC')).toBeInTheDocument();
    expect(screen.getByText('dmz')).toBeInTheDocument();
    expect(screen.getByText('prod')).toBeInTheDocument();
  });

  it('says a name-only host is reachable, not subnet-scoped', () => {
    renderCard({
      coverage: 'name',
      project_has_scope: true,
      subnets: [],
      names: [{ fqdn: 'www.example.com', domain: 'example.com', include_subdomains: true }],
    });
    expect(screen.getByText('Reachable via in-scope name')).toBeInTheDocument();
    // Code review D5: the approved name is beside the status, not behind a click.
    expect(screen.getByText('www.example.com')).toBeInTheDocument();
    // The limit of what the name authorises is never behind the click.
    expect(screen.getByText(/does not put the address/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'show entries' }));
    expect(screen.getByText('www.example.com')).toBeInTheDocument();
    expect(screen.getByText(/via example\.com \(includes subdomains\)/)).toBeInTheDocument();
  });

  it('distinguishes out of scope from no scope defined', () => {
    const { unmount } = renderCard({ coverage: 'none', project_has_scope: true, subnets: [], names: [] });
    expect(screen.getByText('Out of scope')).toBeInTheDocument();
    unmount();

    renderCard({ coverage: 'none', project_has_scope: false, subnets: [], names: [] });
    expect(screen.getByText('No scope defined')).toBeInTheDocument();
    expect(screen.getByText('define scope')).toHaveAttribute('href', '/scopes');
  });

  it('truncates an unbounded description rather than widening the row', () => {
    const long = 'x'.repeat(400);
    renderCard({
      coverage: 'subnet', project_has_scope: true, names: [],
      subnets: [subnet({ description: long })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'show entries' }));
    expect(screen.getByText(long)).toHaveClass('truncate');
  });
});
