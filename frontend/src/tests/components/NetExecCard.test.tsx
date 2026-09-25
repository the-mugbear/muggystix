/**
 * NetExec shares as the inspector shows them (v5.274.0): the --shares table
 * reads as words, a spider_plus listing as a file count — neither as JSON.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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

const renderCard = (count: number) =>
  render(<MemoryRouter><NetExecCard hostId={6} count={count} /></MemoryRouter>);

describe('NetExecCard shares', () => {
  it('reads the --shares table and a spider_plus listing as words', async () => {
    getHostNetexecResults.mockResolvedValue([
      row(1, [
        { name: 'public', permissions: 'READ', remark: 'Parser lab read-only public share' },
        { name: 'restricted', permissions: null, remark: 'Parser lab authenticated-only share' },
      ]),
      row(2, { public: { 'README.txt': { size: '47 B' }, 'notes.txt': { size: '2 KB' } } }),
    ]);
    renderCard(2);
    expect(await screen.findByText('READ · Parser lab read-only public share')).toBeInTheDocument();
    expect(screen.getByText('no access · Parser lab authenticated-only share')).toBeInTheDocument();
    expect(screen.getByText('2 files listed')).toBeInTheDocument();
    expect(screen.queryByText(/"permissions"/)).not.toBeInTheDocument();
  });

  it('names the tool, the session, local-admin access and SMBv1', async () => {
    getHostNetexecResults.mockResolvedValue([
      { ...row(1, [{ name: 'public', permissions: 'READ ONLY', remark: null }]), tool: 'smbmap', username: '', auth_success: true },
      { ...row(2, null), local_admin: true, username: 'admin' },
      { ...row(3, null), auth_success: null, smbv1: true },
    ]);
    renderCard(3);
    expect(await screen.findByText('SMBMap')).toBeInTheDocument();
    expect(screen.getByText('Null session')).toBeInTheDocument();
    expect(screen.getByText('Local admin')).toBeInTheDocument();
    expect(screen.getByText('SMBv1')).toBeInTheDocument();
    expect(screen.queryByText('Auth failed')).not.toBeInTheDocument();
  });
});

describe('NetExecCard lines (v5.296.0)', () => {
  it('shows the tool line, where uninterpreted flags live, and links to what BlueStick reads', async () => {
    const vncLine = 'VNC 10.0.0.9 5900 10.0.0.9 [+] No password seems to be accepted by the server';
    getHostNetexecResults.mockResolvedValue([
      { ...row(1, null), protocol: 'vnc', port: 5900, raw_output: vncLine, username: null, auth_success: null },
      { ...row(2, { public: {} }), raw_output: 'JSON: {"public": {}}' },
    ]);
    renderCard(2);
    expect(await screen.findByText(vncLine)).toBeInTheDocument();
    // A spider_plus listing is summarised as shares, not printed as JSON.
    expect(screen.queryByText(/^JSON:/)).not.toBeInTheDocument();
    // Shares are an SMB matter.
    expect(screen.queryAllByText('· no shares enumerated')).toHaveLength(0);
    expect(screen.getByRole('link', { name: 'What BlueStick reads from NetExec' }))
      .toHaveAttribute('href', '/reference/tool-coverage?tool=netexec');
  });

  it('keeps two scans of one port apart when their lines differ', async () => {
    const vnc = { ...row(1, null), protocol: 'vnc', port: 5900, username: null, auth_success: null };
    getHostNetexecResults.mockResolvedValue([
      { ...vnc, id: 1, raw_output: 'VNC 192.168.56.22 5900 192.168.56.22 [*] RFB 3.8 (No Auth:True)', first_seen: '2026-09-25T22:09:00Z' },
      { ...vnc, id: 2, raw_output: 'VNC 192.168.56.22 5900 192.168.56.22 [*] RFB 3.8', first_seen: '2026-09-25T22:10:00Z' },
    ]);
    renderCard(2);
    expect(await screen.findByText(/\(No Auth:True\)/)).toBeInTheDocument();
    expect(screen.getByText('VNC 192.168.56.22 5900 192.168.56.22 [*] RFB 3.8')).toBeInTheDocument();
  });
});
