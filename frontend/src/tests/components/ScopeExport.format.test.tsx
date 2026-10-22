/**
 * Export scope: the text on screen belongs to the format it was generated in.
 * Moving the format picker afterwards used to recount the old text by the new
 * format's rule (a CSV's header line became an entry) and to download the
 * old text under the new extension.  The list stays, as in the sibling
 * export dialog — counted, named and downloaded as the format that produced
 * it.
 *
 * Since 5.416.0 the export is the DECLARED scope — networks and domains —
 * not the hosts found inside it (owner, 2026-10-22: the Hosts page exports
 * those already).
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getScopeExport: vi.fn() }));
vi.mock('../../services/api', () => api);

const download = vi.hoisted(() => ({ downloadTextFile: vi.fn() }));
vi.mock('../../utils/download', () => download);

// The Radix select does not open in jsdom: a native one with the same contract.
vi.mock('../../components/ui/select', () => {
  type SelectProps = { value?: string; onValueChange?: (v: string) => void; children?: React.ReactNode };
  return {
    Select: ({ value, onValueChange, children }: SelectProps) => (
      <select aria-label="Output format" value={value} onChange={(e) => onValueChange?.(e.target.value)}>
        {children}
      </select>
    ),
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectContent: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    SelectItem: ({ value, children }: { value: string; children?: React.ReactNode }) => (
      <option value={value}>{children}</option>
    ),
  };
});

import ScopeExport from '../../components/ScopeExport';
import { TooltipProvider } from '../../components/ui/tooltip';

const show = () => render(
  <TooltipProvider><ScopeExport open onClose={vi.fn()} /></TooltipProvider>,
);
const pick = (format: string) =>
  fireEvent.change(screen.getByRole('combobox', { name: 'Output format' }), { target: { value: format } });

beforeEach(() => {
  api.getScopeExport.mockReset();
  download.downloadTextFile.mockReset();
});

describe('ScopeExport — the declared scope, in the format that produced it', () => {
  it('opens on the networks: one CIDR per line, saved as the cidrs file', async () => {
    const cidrs = '10.0.0.0/24\n10.0.1.5/32\n';
    api.getScopeExport.mockResolvedValue(cidrs);
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: '2 networks · Networks' })).toBeInTheDocument();
    // The project's one scope: the request names the project and no scope.
    expect(api.getScopeExport).toHaveBeenCalledWith(1, 'cidrs');
    fireEvent.click(screen.getByRole('button', { name: 'Download scope export as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('project-scope_cidrs.txt', cidrs);
  });

  it('a CSV stays a CSV of 2 entries after the picker moves, and downloads as .csv', async () => {
    const csv = 'entry,labels,description,site\r\n10.0.0.0/24,dmz,Web tier,London DC\r\n*.example.com,,,\r\n';
    api.getScopeExport.mockResolvedValue(csv);
    show();
    pick('csv');
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: '2 entries · CSV' })).toBeInTheDocument();
    expect(api.getScopeExport).toHaveBeenCalledWith(1, 'csv');

    pick('cidrs');
    // Still the CSV: not recounted as a network list (3), not saved as .txt.
    expect(screen.getByRole('heading', { name: '2 entries · CSV' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download scope export as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('project-scope.csv', csv);
  });

  it('a scope that declares no domain says there is nothing to export', async () => {
    api.getScopeExport.mockResolvedValue('');
    show();
    pick('domains');
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByText('Nothing to export: the request matched no entries.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download scope export as file' })).not.toBeInTheDocument();
  });

  it('generating again takes the format picked then', async () => {
    api.getScopeExport.mockResolvedValueOnce('10.0.0.0/24\n');
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: '1 network · Networks' })).toBeInTheDocument();

    const domains = '*.example.com\nportal.example.org\n';
    api.getScopeExport.mockResolvedValueOnce(domains);
    pick('domains');
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: '2 domains · Domains' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download scope export as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('project-scope_domains.txt', domains);
  });
});
