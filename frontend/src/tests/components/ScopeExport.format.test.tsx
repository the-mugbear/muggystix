/**
 * Export scope: the text on screen belongs to the format it was generated in.
 * Moving the format picker afterwards used to recount the old text by the new
 * format's rule (a CSV's header line became a host; an IP list read as JSON
 * became 0) and to download the old text under the new extension.  The list
 * stays, as in the sibling export dialogs — counted, named and downloaded as
 * the format that produced it.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getScopeHostList: vi.fn() }));
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
  <TooltipProvider><ScopeExport open onClose={vi.fn()} scopeId={3} scopeName="DMZ east" /></TooltipProvider>,
);
const pick = (format: string) =>
  fireEvent.change(screen.getByRole('combobox', { name: 'Output format' }), { target: { value: format } });

beforeEach(() => {
  api.getScopeHostList.mockReset();
  download.downloadTextFile.mockReset();
});

describe('ScopeExport — the output keeps the format that produced it', () => {
  it('a CSV stays a CSV of 2 hosts after the picker moves, and downloads as .csv', async () => {
    const csv = 'ip,hostname,state\n10.0.0.1,a,up\n10.0.0.2,b,up\n';
    api.getScopeHostList.mockResolvedValue(csv);
    show();
    pick('csv');
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: /^2 hosts/ })).toBeInTheDocument();
    expect(api.getScopeHostList).toHaveBeenCalledWith(3, 'csv');

    pick('txt');
    // Still the CSV: not recounted as an IP list (3), not saved as .txt.
    expect(screen.getByRole('heading', { name: /^2 hosts/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download scope export as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('DMZ_east_hosts.csv', csv);
    expect(screen.getByRole('heading', { name: '2 hosts · CSV' })).toBeInTheDocument();

    pick('json');
    expect(screen.getByRole('heading', { name: '2 hosts · CSV' })).toBeInTheDocument();
  });

  it('generating again takes the format picked then', async () => {
    api.getScopeHostList.mockResolvedValueOnce('10.0.0.1\n10.0.0.2\n10.0.0.3\n');
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: '3 hosts · IP List' })).toBeInTheDocument();

    const json = JSON.stringify([{ ip: '10.0.0.1' }]);
    api.getScopeHostList.mockResolvedValueOnce(json);
    pick('json');
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: '1 host · JSON' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download scope export as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('DMZ_east_hosts.json', json);
  });
});
