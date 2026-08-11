import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import ProvenanceCard from '../../components/host-inspector/ProvenanceCard';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { HostCertOrg, NetworkAttribution } from '../../services/api';

const attr = (over: Partial<NetworkAttribution> = {}): NetworkAttribution => ({
  id: 1, cidr: '198.51.100.0/24', org_name: 'Acme Corporation', asn: null,
  as_name: null, country: 'US', registry: 'ARIN', handle: 'NET-198-51-100-0-1',
  cloud_provider: null, cloud_region: null, source: 'rdap',
  looked_up_at: new Date().toISOString(), ...over,
});

const cert = (over: Partial<HostCertOrg> = {}): HostCertOrg => ({
  org: 'Acme Corporation', issuer: 'DigiCert Inc', url: 'https://a.acme.test', ...over,
});

const renderCard = (props: Parameters<typeof ProvenanceCard>[0]) =>
  render(<TooltipProvider><ProvenanceCard {...props} /></TooltipProvider>);

describe('ProvenanceCard', () => {
  it('shows the registrant and the registry handle', () => {
    renderCard({ attributions: [attr()] });
    expect(screen.getByText('Acme Corporation')).toBeInTheDocument();
    expect(screen.getByText(/NET-198-51-100-0-1/)).toBeInTheDocument();
  });

  // Most engagements are internal and a block simply may not have been looked
  // up. Neither is suspicious, so the card must not appear at all rather than
  // render an alarming empty state.
  it('renders nothing when there is no provenance', () => {
    const { container } = renderCard({ attributions: [], certOrgs: [] });
    expect(container).toBeEmptyDOMElement();
  });

  // The interesting case: an independently-validated cert naming a different
  // organisation than the self-declared registration.
  it('flags a certificate that disagrees with the registration', () => {
    renderCard({
      attributions: [attr({ org_name: 'Unrelated Hosting BV' })],
      certOrgs: [cert({ org: 'Acme Corporation' })],
    });
    expect(screen.getByText(/certificate and registration disagree/i)).toBeInTheDocument();
  });

  it('stays quiet when the two signals corroborate each other', () => {
    renderCard({
      attributions: [attr({ org_name: 'Acme Corporation' })],
      certOrgs: [cert({ org: 'Acme' })],
    });
    expect(screen.queryByText(/disagree/)).toBeNull();
  });

  // A DV certificate makes no organisational claim, so there is nothing to
  // disagree with — flagging that would be a false positive on most of the web.
  it('does not flag disagreement when there is no certificate org', () => {
    renderCard({ attributions: [attr({ org_name: 'Acme Corporation' })], certOrgs: [] });
    expect(screen.queryByText(/disagree/)).toBeNull();
  });

  it('warns when the registration lookup has gone stale', () => {
    const old = new Date(Date.now() - 400 * 86_400_000).toISOString();
    renderCard({ attributions: [attr({ looked_up_at: old })] });
    expect(screen.getByText(/re-check before citing/)).toBeInTheDocument();
  });

  it('surfaces cloud hosting when known', () => {
    renderCard({
      attributions: [attr({ cloud_provider: 'aws', cloud_region: 'eu-west-1' })],
    });
    expect(screen.getByText(/AWS · eu-west-1/)).toBeInTheDocument();
  });
});

describe('certificate status (v5.143.0)', () => {
  it('states an expiry as a deadline the operator can act on', () => {
    const soon = new Date(Date.now() + 9 * 86_400_000).toISOString();
    renderCard({ certStatus: [{ url: 'https://10.0.0.1', not_after: soon, self_signed: false }] });
    expect(screen.getByText(/expires in 9 days/)).toBeInTheDocument();
  });

  it('says a certificate has already expired rather than counting down past zero', () => {
    const past = new Date(Date.now() - 3 * 86_400_000).toISOString();
    renderCard({ certStatus: [{ url: 'https://10.0.0.1', not_after: past, self_signed: false }] });
    expect(screen.getByText(/expired 3 days ago/)).toBeInTheDocument();
  });

  it('flags a self-signed certificate', () => {
    renderCard({ certStatus: [{ url: 'https://10.0.0.1', not_after: null, self_signed: true }] });
    expect(screen.getByText(/self-signed/)).toBeInTheDocument();
  });

  it('renders for a DV cert that has an expiry but no organisation', () => {
    // The case the backend query used to drop entirely.
    const soon = new Date(Date.now() + 14 * 86_400_000).toISOString();
    renderCard({
      attributions: [],
      certOrgs: [],
      certStatus: [{ url: 'https://10.0.0.1', not_after: soon, self_signed: false, subject_org: null }],
    });
    expect(screen.getByText(/expires in 14 days/)).toBeInTheDocument();
  });
});
