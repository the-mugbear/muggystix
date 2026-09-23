/**
 * NetExec shares as the inspector shows them (v5.274.0): the --shares table
 * reads as words, a spider_plus listing as a file count — neither as JSON.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const getHostNetexecResults = vi.fn();
vi.mock('../../services/api', () => ({
  getHostNetexecResults: (...a: unknown[]) => getHostNetexecResults(...a),
}));

import NetExecCard from '../../components/NetExecCard';

const row = (id: number, shares: unknown) => ({
  id, scan_id: id, protocol: 'smb', port: 445, auth_success: true, username: 'guest',
  hostname: 'LABSMB', domain_name: 'LABSMB', shares, first_seen: `2026-09-23T05:1${id}:00Z`,
});

describe('NetExecCard shares', () => {
  it('reads the --shares table and a spider_plus listing as words', async () => {
    getHostNetexecResults.mockResolvedValue([
      row(1, [
        { name: 'public', permissions: 'READ', remark: 'Parser lab read-only public share' },
        { name: 'restricted', permissions: null, remark: 'Parser lab authenticated-only share' },
      ]),
      row(2, { public: { 'README.txt': { size: '47 B' }, 'notes.txt': { size: '2 KB' } } }),
    ]);
    render(<NetExecCard hostId={6} count={2} />);
    expect(await screen.findByText('READ · Parser lab read-only public share')).toBeInTheDocument();
    expect(screen.getByText('no access · Parser lab authenticated-only share')).toBeInTheDocument();
    expect(screen.getByText('2 files listed')).toBeInTheDocument();
    expect(screen.queryByText(/"permissions"/)).not.toBeInTheDocument();
  });
});
