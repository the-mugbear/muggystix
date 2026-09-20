import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({ getHostNames: vi.fn(), getHostDnsRecords: vi.fn() }));
vi.mock('../../services/api', () => api);

import HostNamesCard from '../../components/HostNamesCard';

const binding = {
  name_id: 3, fqdn: 'db-01.example.test', kind: 'fqdn', in_scope: false,
  record_types: ['PTR'], last_observed: '2026-09-19T10:00:00Z',
};
const names = (over = {}) => ({ host_id: 1, current: [], previous: [], other: [binding], in_scope_via_names: false, ...over });
const dns = (over = {}) => ({
  items: [{ id: 1, domain: 'db-01.example.test', record_type: 'PTR', value: '10.0.0.5', ttl: null, resolver_name: null, created_at: '2026-09-19T10:00:00Z' }],
  total: 1, resolvers: [], record_types: ['PTR'], project_total: 1, ...over,
});

const renderCard = () => render(<MemoryRouter><HostNamesCard hostId={1} /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

// v5.241.0 — "DNS evidence" was a section of its own repeating the same
// name → address pairs listed under "Names at this address" a few lines away.
describe('HostNamesCard — the DNS records behind the names', () => {
  it('are one disclosure line inside the names section, not a second section', async () => {
    api.getHostNames.mockResolvedValue(names());
    api.getHostDnsRecords.mockResolvedValue(dns());
    renderCard();

    const toggle = await screen.findByRole('button', { name: /1 DNS record behind these names · show/ });
    expect(screen.queryByRole('button', { name: /DNS evidence/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/PTR \(1\)/)).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText(/PTR \(1\)/)).toBeInTheDocument();
  });

  it('add no line when this host has no records of its own', async () => {
    api.getHostNames.mockResolvedValue(names());
    api.getHostDnsRecords.mockResolvedValue(dns({ items: [], total: 0, record_types: [], project_total: 40 }));
    renderCard();
    expect(await screen.findByText('db-01.example.test')).toBeInTheDocument();
    expect(screen.queryByText(/DNS record/)).not.toBeInTheDocument();
  });

  it('stand alone when the host has no names, so records are not lost with the section', async () => {
    api.getHostNames.mockResolvedValue(names({ other: [] }));
    api.getHostDnsRecords.mockResolvedValue(dns());
    renderCard();
    expect(await screen.findByRole('button', { name: /DNS evidence/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Names at this address/ })).not.toBeInTheDocument();
  });
});
