/**
 * Discovered paths (v5.276.0): one line per path — status, port and path, size,
 * tool — instead of a capped string in the port's service extra info.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const getHostWebPaths = vi.fn();
vi.mock('../../services/api', () => ({
  getHostWebPaths: (...a: unknown[]) => getHostWebPaths(...a),
}));

import WebPathsCard from '../../components/WebPathsCard';

describe('WebPathsCard', () => {
  it('lists each path with its status, size and tool', async () => {
    getHostWebPaths.mockResolvedValue([
      { url: 'https://10.9.7.1/admin', path: '/admin', status_code: 401, size: 512, source: 'ffuf', port: 443, scans: 1 },
      { url: 'https://10.9.7.1/backup.zip', path: '/backup.zip', status_code: 200, size: 90210, source: 'gobuster', port: 443, scans: 2 },
    ]);
    render(<WebPathsCard hostId={6} count={2} />);
    expect(await screen.findByText(':443 /admin')).toHaveAttribute('href', 'https://10.9.7.1/admin');
    expect(screen.getByText('401')).toBeInTheDocument();
    expect(screen.getByText('88.1 KB')).toBeInTheDocument();
    expect(screen.getByText('gobuster · 2 scans')).toBeInTheDocument();
  });

  it('renders nothing for a host without discovered paths', () => {
    const { container } = render(<WebPathsCard hostId={6} count={0} />);
    expect(container).toBeEmptyDOMElement();
    expect(getHostWebPaths).not.toHaveBeenCalledWith(6, expect.anything());
  });
});
