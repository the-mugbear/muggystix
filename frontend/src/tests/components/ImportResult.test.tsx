import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import ImportResult, { importHasGaps, importResultParts } from '../../components/scans/ImportResult';
import type { Scan } from '../../services/api';

const scan = (over: Partial<Scan> = {}): Scan =>
  ({
    id: 9,
    filename: 'now.xml',
    tool_name: 'nmap',
    total_hosts: 3,
    up_hosts: 3,
    new_hosts: 2,
    updated_hosts: 1,
    open_ports: 4,
    port_breakdown: { open_tcp_ports: 4, open_udp_ports: 0, new_open_ports: 7, open_with_service: 2 },
    ...over,
  }) as unknown as Scan;

describe('importResultParts', () => {
  it('names each reconciliation count and links it to the records it counts', () => {
    const parts = importResultParts(scan({ conflicts: 4, import_skipped: 3, import_job_id: 77 }));
    expect(parts.map((p) => p.text)).toEqual([
      '+2 hosts added',
      '1 already known',
      '4 conflicts',
      '7 new open ports',
      '3 records skipped',
    ]);
    expect(parts.find((p) => p.key === 'added')?.to).toBe('/hosts?scan_ids=9&first_seen_in_scan=true');
    expect(parts.find((p) => p.key === 'known')?.to).toBe('/hosts?scan_ids=9');
    expect(parts.find((p) => p.key === 'skipped')?.to).toBe('/scans?job_id=77');
  });

  // Owner, 2026-10-11: the hosts whose display name an import changed are a
  // figure a reader can open, beside "added" and "already known".
  it('says how many known hosts the upload renamed, as a link to exactly those hosts', () => {
    const parts = importResultParts(scan({ renamed_hosts: 1234, conflicts: 4 }));
    expect(parts.map((p) => p.text)).toEqual([
      '+2 hosts added',
      '1 already known',
      '1,234 renamed',
      '4 conflicts',
      '7 new open ports',
    ]);
    expect(parts.find((p) => p.key === 'renamed')?.to).toBe('/hosts?scan_ids=9&renamed_in_scan=true');
  });

  it('shows no renamed part for none, and none for a server that does not say', () => {
    expect(importResultParts(scan({ renamed_hosts: 0 })).map((p) => p.key)).not.toContain('renamed');
    expect(importResultParts(scan()).map((p) => p.key)).not.toContain('renamed');
    // A DNS import over known hosts: nothing added, some renamed.
    expect(importResultParts(scan({
      total_hosts: 3, new_hosts: 0, updated_hosts: 3, renamed_hosts: 2, port_breakdown: null,
    })).map((p) => p.text)).toEqual(['3 already known', '2 renamed']);
  });

  it('a gap with no job to name still opens the import history', () => {
    const parts = importResultParts(scan({ import_skipped: 3, import_partial: true }));
    expect(parts.find((p) => p.key === 'skipped')?.to).toBe('/scans');
    expect(parts.find((p) => p.key === 'partial')?.to).toBe('/scans');
  });

  it('says "no hosts" rather than nothing for an upload that recorded none', () => {
    const parts = importResultParts(scan({ total_hosts: 0, new_hosts: 0, updated_hosts: 0, port_breakdown: null }));
    expect(parts.map((p) => p.text)).toEqual(['no hosts']);
  });

  it('a truncated file is a gap, a clean import is not', () => {
    expect(importHasGaps(scan())).toBe(false);
    expect(importHasGaps(scan({ import_partial: true }))).toBe(true);
    expect(importHasGaps(scan({ import_skipped: 1 }))).toBe(true);
    expect(importResultParts(scan({ import_partial: true })).map((p) => p.key)).toContain('partial');
  });
});

describe('ImportResult', () => {
  it('renders the counts as links and the parser warnings beneath', () => {
    render(
      <MemoryRouter>
        <ImportResult
          scan={scan({ conflicts: 1, renamed_hosts: 1, import_warnings: '2 hosts had no address' })}
          showContribution={false}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: '1 renamed' })).toHaveAttribute(
      'href',
      '/hosts?scan_ids=9&renamed_in_scan=true',
    );
    expect(screen.getByRole('link', { name: '+2 hosts added' })).toHaveAttribute(
      'href',
      '/hosts?scan_ids=9&first_seen_in_scan=true',
    );
    expect(screen.getByRole('link', { name: '1 conflict' })).toBeInTheDocument();
    expect(screen.getByText('2 hosts had no address')).toBeInTheDocument();
  });

  it('shows how the file was read beside what it added', () => {
    const read = scan({
      import_detected_format: 'Nmap XML',
      import_format_override: 'Masscan XML',
      import_final_format: 'Masscan XML',
      import_source_tool: 'masscan 1.3',
    });
    const { rerender } = render(<MemoryRouter><ImportResult scan={read} showContribution={false} /></MemoryRouter>);
    expect(screen.getByLabelText('How this file was read')).toHaveTextContent(
      'Detected as Nmap XML · you chose Masscan XML · parsed by Masscan XML · source tool masscan 1.3',
    );
    // Ingestion Results prints the chain on the row itself.
    rerender(<MemoryRouter><ImportResult scan={read} showContribution={false} showFormatChain={false} /></MemoryRouter>);
    expect(screen.queryByLabelText('How this file was read')).not.toBeInTheDocument();
    // A scan imported before the chain was recorded shows no empty line.
    rerender(<MemoryRouter><ImportResult scan={scan()} showContribution={false} /></MemoryRouter>);
    expect(screen.queryByLabelText('How this file was read')).not.toBeInTheDocument();
  });

  // Screenshot 2026-09-23: "Detected as NetExec JSON · you chose NetExec JSON"
  // highlighted the choice in warning colour though it equalled detection.
  it('says a choice equal to the detection was confirmed, neutrally; highlights only a real override', () => {
    const same = scan({
      import_detected_format: 'NetExec JSON',
      import_format_override: 'NetExec JSON',
      import_final_format: 'NetExec JSON',
    });
    const { rerender } = render(<MemoryRouter><ImportResult scan={same} showContribution={false} /></MemoryRouter>);
    const chain = screen.getByLabelText('How this file was read');
    expect(chain).toHaveTextContent('Detected as NetExec JSON · confirmed by you · parsed by NetExec JSON');
    expect(chain.querySelector('.text-warning')).toBeNull();

    rerender(
      <MemoryRouter>
        <ImportResult scan={{ ...same, import_format_override: 'Masscan XML' }} showContribution={false} />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText('How this file was read').querySelector('.text-warning')).toHaveTextContent('Masscan XML');
  });
});
