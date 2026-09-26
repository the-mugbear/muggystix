/**
 * Discovered paths (v5.276.0): one line per path — status, port and path, size,
 * tool — instead of a capped string in the port's service extra info.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../services/api', () => ({}));

import { WebPathRow } from '../../components/WebPathsCard';

// v5.298.0 — the paths render in each service's panel.
describe('WebPathRow', () => {
  it('lists each path with its status, size and tool', () => {
    const rows = [
      { url: 'https://10.9.7.1/admin', path: '/admin', status_code: 401, size: 512, source: 'ffuf', port: 443, scans: 1 },
      { url: 'https://10.9.7.1/backup.zip', path: '/backup.zip', status_code: 200, size: 90210, source: 'gobuster', port: 443, scans: 2 },
    ];
    render(<ul>{rows.map((r) => <WebPathRow key={r.url} row={r} />)}</ul>);
    expect(screen.getByText(':443 /admin')).toHaveAttribute('href', 'https://10.9.7.1/admin');
    expect(screen.getByText('401')).toBeInTheDocument();
    expect(screen.getByText('88.1 KB')).toBeInTheDocument();
    expect(screen.getByText('gobuster · 2 scans')).toBeInTheDocument();
  });
});
