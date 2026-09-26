/**
 * An import's uninterpreted lines as redacted shapes (v5.301.0): fetched when
 * opened, labelled by what happened to them, copyable for a parser fix.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getUninterpretedLines: vi.fn() }));
vi.mock('../../services/api', () => api);
const clip = vi.hoisted(() => ({ copyToClipboard: vi.fn() }));
vi.mock('../../utils/clipboard', () => clip);

import UninterpretedLines from '../../components/scans/UninterpretedLines';

const receipt = {
  job_id: 9, original_filename: 'nxc.txt', tool_name: 'netexec', total: 5, distinct: 2,
  shapes: [
    { kind: 'text_only', shape: 'RDP <IP> 3389 <HOST> [*] Windows 10 (nla:False)', count: 4 },
    { kind: 'module_as_login', shape: 'ZEROLOGON <IP> 445 <HOST> [+] VULNERABLE', count: 1 },
  ],
};

beforeEach(() => vi.clearAllMocks());

describe('UninterpretedLines', () => {
  it('loads the shapes on request and labels what happened to each', async () => {
    api.getUninterpretedLines.mockResolvedValue(receipt);
    render(<MemoryRouter><UninterpretedLines jobId={9} total={5} distinct={2} formatKey="netexec_txt" /></MemoryRouter>);
    expect(api.getUninterpretedLines).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '5 lines not interpreted (2 shapes) · show' }));
    expect(await screen.findByText('RDP <IP> 3389 <HOST> [*] Windows 10 (nla:False)')).toBeInTheDocument();
    expect(screen.getByText('×4')).toBeInTheDocument();
    expect(screen.getByText('kept as text')).toBeInTheDocument();
    expect(screen.getByText('module, read as a login')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'What BlueStick reads from this format' }))
      .toHaveAttribute('href', '/reference/tool-coverage?format=netexec_txt');
  });

  it('copies every shape tab-separated, with a header', async () => {
    api.getUninterpretedLines.mockResolvedValue(receipt);
    clip.copyToClipboard.mockResolvedValue(true);
    render(<MemoryRouter><UninterpretedLines jobId={9} total={5} distinct={2} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /not interpreted/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'copy all (tab-separated)' }));
    expect(await screen.findByText('copied')).toBeInTheDocument();
    const text = clip.copyToClipboard.mock.calls[0][0] as string;
    expect(text.split('\n')).toEqual([
      '# netexec — 5 line(s) not interpreted, 2 shape(s); values redacted',
      '4\ttext_only\tRDP <IP> 3389 <HOST> [*] Windows 10 (nla:False)',
      '1\tmodule_as_login\tZEROLOGON <IP> 445 <HOST> [+] VULNERABLE',
    ]);
  });

  it('a failed load says so', async () => {
    api.getUninterpretedLines.mockRejectedValue(new Error('boom'));
    render(<MemoryRouter><UninterpretedLines jobId={9} total={5} distinct={2} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /not interpreted/ }));
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  });
});
