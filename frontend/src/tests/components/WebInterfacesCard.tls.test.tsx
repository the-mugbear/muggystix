/**
 * Review 2026-10-23b, AGT-6 — the TLS line words the server's reading of the
 * tool's record (`tls_summary`, from `cert_fields.tls_summary`, which the
 * agents' web interfaces carry too). The card no longer reads `tls_info`.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getHostWebInterfaces: vi.fn(), fetchWebInterfaceScreenshot: vi.fn(), getWebInterfaceRecord: vi.fn(),
}));
vi.mock('../../services/api', () => api);

import WebInterfacesCard from '../../components/WebInterfacesCard';
import { TooltipProvider } from '../../components/ui/tooltip';

const base = {
  id: 7, source: 'httpx', url: 'https://app.example.com', protocol: 'https', port: 443,
  technologies: [], has_screenshot: false, scan_id: 3, port_id: 443,
};

const renderRow = (row: object) =>
  render(
    <MemoryRouter><TooltipProvider>
      <WebInterfacesCard rows={[row] as never} />
    </TooltipProvider></MemoryRouter>,
  );

describe('WebInterfacesCard — the TLS line', () => {
  it('words every flag the server read from the record', () => {
    renderRow({
      ...base,
      // A raw record saying something else proves the card does not read it.
      tls_info: { tls_version: 'tls10' },
      tls_summary: {
        version: 'tls13', issuer: 'Example CA', subject_cn: 'app.example.com',
        not_after: '2999-01-01T00:00:00Z', sans: ['a.example.com', 'b.example.com'],
        flags: ['self_signed', 'hostname_mismatch', 'wildcard'],
      },
    });
    expect(screen.getByText('tls13')).toBeInTheDocument();
    expect(screen.queryByText('tls10')).not.toBeInTheDocument();
    expect(screen.getByText(/Example CA/)).toBeInTheDocument();
    expect(screen.getByText('2 SANs')).toBeInTheDocument();
    for (const word of ['self-signed', 'hostname mismatch', 'wildcard']) {
      expect(screen.getByText(word)).toBeInTheDocument();
    }
  });

  it('shows no TLS line without a summary', () => {
    renderRow({ ...base, tls_info: { tls_version: 'tls13', mismatched: true }, tls_summary: null });
    expect(screen.queryByText('tls13')).not.toBeInTheDocument();
    expect(screen.queryByText('hostname mismatch')).not.toBeInTheDocument();
  });
});
