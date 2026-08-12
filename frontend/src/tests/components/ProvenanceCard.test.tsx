import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import ProvenanceCard, {
  provenanceExceedsSummary,
  attributionIsStale,
  certRegistrationDisagree,
} from '../../components/host-inspector/ProvenanceCard';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { HostCertOrg, HostCertStatus, NetworkAttribution } from '../../services/api';

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

// The header shows a one-line owner summary; the card renders only when it adds
// more. provenanceExceedsSummary is that gate — keep it honest.
describe('provenanceExceedsSummary', () => {
  const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();
  const status = (over: Partial<HostCertStatus> = {}): HostCertStatus =>
    ({ url: 'https://10.0.0.1', not_after: null, self_signed: false, subject_org: null, ...over });

  it('is false for a single fresh attribution (header covers it)', () => {
    expect(provenanceExceedsSummary([attr()], [], [])).toBe(false);
  });

  it('is true when a certificate organisation is present', () => {
    expect(provenanceExceedsSummary([attr()], [cert()], [])).toBe(true);
  });

  it('is true when certificate expiry/self-signed status is present', () => {
    expect(provenanceExceedsSummary([attr()], [], [status()])).toBe(true);
  });

  it('is true with more than one attributed block', () => {
    expect(provenanceExceedsSummary([attr(), attr({ id: 2, cidr: '203.0.113.0/24' })], [], [])).toBe(true);
  });

  it('is true when the lookup is stale (a signal to re-verify)', () => {
    expect(provenanceExceedsSummary([attr({ looked_up_at: daysAgo(200) })], [], [])).toBe(true);
  });

  it('is false when there is nothing at all', () => {
    expect(provenanceExceedsSummary([], [], [])).toBe(false);
  });
});

describe('attribution helpers', () => {
  it('attributionIsStale trips past 180 days, not before', () => {
    const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();
    expect(attributionIsStale(daysAgo(200))).toBe(true);
    expect(attributionIsStale(daysAgo(10))).toBe(false);
    expect(attributionIsStale(null)).toBe(false);
  });

  it('certRegistrationDisagree only fires when both sides name an org and they differ', () => {
    expect(certRegistrationDisagree([attr({ org_name: 'Acme' })], [cert({ org: 'Acme Corp' })])).toBe(false);
    expect(certRegistrationDisagree([attr({ org_name: 'Acme' })], [cert({ org: 'Evil BV' })])).toBe(true);
    expect(certRegistrationDisagree([attr({ org_name: null })], [cert({ org: 'Evil BV' })])).toBe(false);
    expect(certRegistrationDisagree([attr({ org_name: 'Acme' })], [])).toBe(false);
  });
});
