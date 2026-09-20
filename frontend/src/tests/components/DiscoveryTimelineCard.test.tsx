import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import DiscoveryTimelineCard from '../../components/host-inspector/DiscoveryTimelineCard';
import type { HostDiscovery } from '../../services/api';

const scan = (over: Partial<HostDiscovery>): HostDiscovery => ({
  scan_id: 1, scan_filename: 'scan.xml', scan_type: 'nmap', tool_name: 'nmap',
  scan_start: null, scan_end: null, command_line: null, discovered_at: '2026-09-09T19:22:38Z', ...over,
});

beforeEach(() => window.localStorage.clear());

// v5.241.0 — a scan was a bordered two-line box; it is one divided line.
describe('DiscoveryTimelineCard', () => {
  it('renders nothing when the host has no recorded discovery', () => {
    const { container } = render(<DiscoveryTimelineCard discoveries={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says "ingested" — not a scan window — when the tool recorded no start or end', () => {
    render(<DiscoveryTimelineCard discoveries={[scan({ scan_filename: 'subfinder_sample.txt', scan_type: 'subdomain_discovery' })]} />);
    expect(screen.getByText('subfinder_sample.txt')).toBeInTheDocument();
    const when = screen.getByText(/^ingested /);
    expect(when).toHaveAttribute('title', expect.stringContaining('did not record start/end'));
    expect(screen.queryByText(/→/)).not.toBeInTheDocument();
  });

  it('shows the scan window when there is one, and the command only when recorded', () => {
    render(<DiscoveryTimelineCard discoveries={[
      scan({ scan_id: 1, scan_start: '2026-09-01T10:00:00Z', scan_end: '2026-09-01T10:05:00Z', command_line: 'nmap -sV 10.0.0.5' }),
    ]} />);
    expect(screen.getByText(/→/)).toBeInTheDocument();
    expect(screen.queryByText(/^ingested /)).not.toBeInTheDocument();
    expect(screen.getByText('nmap -sV 10.0.0.5')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy scan command to clipboard' })).toBeInTheDocument();
  });

  it('previews the newest five and opens the rest on demand', () => {
    const many = Array.from({ length: 8 }, (_, i) =>
      scan({ scan_id: i + 1, scan_filename: `scan-${i + 1}.xml`, discovered_at: `2026-09-0${i + 1}T00:00:00Z` }));
    render(<DiscoveryTimelineCard discoveries={many} />);
    expect(screen.getByText('scan-8.xml')).toBeInTheDocument();
    expect(screen.getByText('scan-4.xml')).toBeInTheDocument();
    expect(screen.queryByText('scan-3.xml')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Show all 8 scans/ }));
    expect(screen.getByText('scan-1.xml')).toBeInTheDocument();
  });
});
