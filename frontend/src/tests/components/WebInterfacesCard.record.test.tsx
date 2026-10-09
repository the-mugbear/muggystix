/**
 * The tool's own record behind a web page (v5.300.0, review 2026-09-25 R08):
 * testssl's OK/INFO checks, WhatWeb's plugin strings and httpx's DNS / CDN
 * data were stored and unreachable.  Fetched on request, cut visibly.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getHostWebInterfaces: vi.fn(), fetchWebInterfaceScreenshot: vi.fn(), getWebInterfaceRecord: vi.fn(),
}));
vi.mock('../../services/api', () => api);

import WebInterfacesCard from '../../components/WebInterfacesCard';
import { TooltipProvider } from '../../components/ui/tooltip';

const row = {
  id: 7, source: 'testssl', url: 'https://app.example.com:443', protocol: 'https', port: 443,
  technologies: [], has_screenshot: false, scan_id: 3, port_id: 443,
};

const renderCard = () =>
  render(
    <MemoryRouter><TooltipProvider>
      <WebInterfacesCard hostId={1} count={1} rows={[row] as never} embedded />
    </TooltipProvider></MemoryRouter>,
  );

beforeEach(() => vi.clearAllMocks());

// Code review 2026-10-09: the screenshot is an object URL made by the request;
// only the one on show was ever revoked.
describe('WebInterfacesCard — a screenshot nobody is looking at', () => {
  const shot = { ...row, has_screenshot: true };
  const renderShot = () => render(
    <MemoryRouter><TooltipProvider>
      <WebInterfacesCard hostId={1} count={1} rows={[shot] as never} embedded />
    </TooltipProvider></MemoryRouter>,
  );

  it('is released when it arrives after the card has gone', async () => {
    const revoke = vi.fn();
    Object.defineProperty(URL, 'revokeObjectURL', { value: revoke, configurable: true, writable: true });
    let answer!: (url: string) => void;
    api.fetchWebInterfaceScreenshot.mockImplementation(() => new Promise<string>((resolve) => { answer = resolve; }));
    const { unmount } = renderShot();
    fireEvent.click(screen.getByRole('button', { name: `View screenshot of ${row.url}` }));
    await waitFor(() => expect(answer).toBeDefined());
    unmount();
    answer('blob:late');
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:late'));
  });

  it('is released when the reader asked for another meanwhile', async () => {
    const revoke = vi.fn();
    Object.defineProperty(URL, 'revokeObjectURL', { value: revoke, configurable: true, writable: true });
    const answers: Array<(url: string) => void> = [];
    api.fetchWebInterfaceScreenshot.mockImplementation(() => new Promise<string>((resolve) => { answers.push(resolve); }));
    renderShot();
    const open = screen.getByRole('button', { name: `View screenshot of ${row.url}` });
    fireEvent.click(open);
    await waitFor(() => expect(answers).toHaveLength(1));
    fireEvent.click(open);
    await waitFor(() => expect(answers).toHaveLength(2));
    answers[0]('blob:first');
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:first'));
    answers[1]('blob:second');
    await new Promise((r) => setTimeout(r, 0));
    expect(revoke).not.toHaveBeenCalledWith('blob:second');
  });
});

describe('WebInterfacesCard — source record', () => {
  it('fetches the record only when asked for, and shows it as text', async () => {
    api.getWebInterfaceRecord.mockResolvedValue({
      id: 7, source: 'testssl', url: row.url, scan_filename: 'testssl-run.json',
      text: '{\n  "findings": [{"id": "cipher_order", "severity": "INFO"}]\n}', total_chars: 60, truncated: false,
    });
    renderCard();
    expect(api.getWebInterfaceRecord).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: `Show the testssl source record for ${row.url}` }));
    expect(await screen.findByText(/cipher_order/)).toBeInTheDocument();
    expect(api.getWebInterfaceRecord).toHaveBeenCalledWith(7, expect.any(AbortSignal));
    expect(screen.getByText(/As testssl reported it in testssl-run\.json/)).toBeInTheDocument();
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
  });

  it('says when the record was cut', async () => {
    api.getWebInterfaceRecord.mockResolvedValue({
      id: 7, source: 'testssl', url: row.url, text: 'x'.repeat(10), total_chars: 900000, truncated: true,
    });
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: /source record/ }));
    expect(await screen.findByText(/Showing the first 10 of 900,000 characters/)).toBeInTheDocument();
  });

  it('a failed load says so', async () => {
    api.getWebInterfaceRecord.mockRejectedValue(new Error('boom'));
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: /source record/ }));
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  });
});
