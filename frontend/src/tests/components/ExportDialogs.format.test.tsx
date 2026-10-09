/**
 * The out-of-scope export and the tool-ready export: the text on screen
 * belongs to the format it was generated in (the same defect, and the same
 * fix, as ScopeExport — see ScopeExport.format.test.tsx).  Moving the picker
 * afterwards recounted the old text by the new format's rule and downloaded it
 * under the new format's name and extension.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getOutOfScopeHostList: vi.fn(), getToolReadyOutput: vi.fn() }));
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

import OutOfScopeExport from '../../components/OutOfScopeExport';
import ToolReadyOutput from '../../components/ToolReadyOutput';
import { TooltipProvider } from '../../components/ui/tooltip';

const pick = (format: string) =>
  fireEvent.change(screen.getByRole('combobox', { name: 'Output format' }), { target: { value: format } });

beforeEach(() => {
  api.getOutOfScopeHostList.mockReset();
  api.getToolReadyOutput.mockReset();
  download.downloadTextFile.mockReset();
});

describe('OutOfScopeExport — the output keeps the format that produced it', () => {
  it('a CSV stays a CSV of 2 hosts after the picker moves, and downloads as .csv', async () => {
    const csv = 'ip,hostname\n10.0.0.1,a\n10.0.0.2,b\n';
    api.getOutOfScopeHostList.mockResolvedValue(csv);
    render(<TooltipProvider><OutOfScopeExport open onClose={vi.fn()} /></TooltipProvider>);
    pick('csv');
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByRole('heading', { name: /^2 hosts/ })).toBeInTheDocument();
    expect(api.getOutOfScopeHostList).toHaveBeenCalledWith(1, 'csv');

    pick('txt');
    // Still the CSV: not recounted as an IP list (3 lines), not saved as .txt.
    expect(screen.getByRole('heading', { name: /^2 hosts · CSV/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download output as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('out_of_scope_hosts.csv', csv);
  });
});

describe('ToolReadyOutput — the output keeps the format that produced it', () => {
  it('a JSON output is still headed and saved as JSON after the picker moves', async () => {
    const json = '[{"ip":"10.0.0.1"}]';
    api.getToolReadyOutput.mockResolvedValue({ output: json, host_count: 1 });
    render(
      <TooltipProvider>
        <ToolReadyOutput open onClose={vi.fn()} filters={{}} totalHosts={1} selectedCount={0} />
      </TooltipProvider>,
    );
    pick('json');
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByRole('heading', { name: 'Generated Output (JSON)' })).toBeInTheDocument();
    // (the project, then the format)
    expect(api.getToolReadyOutput.mock.calls[0].slice(0, 2)).toEqual([1, 'json']);

    pick('nmap');
    expect(screen.getByRole('heading', { name: 'Generated Output (JSON)' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download output as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('json-targets.json', json);
  });
});
