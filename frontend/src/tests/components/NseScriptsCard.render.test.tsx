/**
 * The inspector's NSE section is the HOST's scripts only (review 2026-10-23b,
 * FEC-4: its port-scripts branch had no caller — port scripts are in each
 * service's panel — and was an unbounded render over every port).  Pins what
 * the one caller (`<NseScriptsCard host={host} />`) has always seen.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({}));

import NseScriptsCard from '../../components/NseScriptsCard';
import type { Host } from '../../services/api';

const script = (id: number, scriptId: string) => ({ id, script_id: scriptId, output: `out ${id}`, scan_id: 1 });
const host = (over: Partial<Host>) => ({
  id: 1, ip_address: '10.0.0.1', ports: [], host_scripts: [], ...over,
}) as unknown as Host;

describe('NseScriptsCard — the host scripts', () => {
  it('lists the host scripts under "Host scripts", and no port script', () => {
    render(<MemoryRouter><NseScriptsCard host={host({
      host_scripts: [script(1, 'smb-os-discovery'), script(2, 'smb2-time')],
      ports: [{ id: 9, port_number: 445, protocol: 'tcp', scripts: [script(3, 'smb-protocols')] }] as never,
    })} /></MemoryRouter>);
    expect(screen.getAllByText('Host scripts').length).toBeGreaterThan(0);
    expect(screen.getByText('SMB OS Discovery')).toBeInTheDocument();
    expect(screen.queryByText('Port scripts')).toBeNull();
    expect(screen.queryByText(/smb-protocols|SMB Protocols/i)).toBeNull();
  });

  it('renders nothing for a host whose only scripts are on its ports', () => {
    const { container } = render(<MemoryRouter><NseScriptsCard host={host({
      ports: [{ id: 9, port_number: 445, protocol: 'tcp', scripts: [script(3, 'smb-protocols')] }] as never,
    })} /></MemoryRouter>);
    expect(container).toBeEmptyDOMElement();
  });
});
