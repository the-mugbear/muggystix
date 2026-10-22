/**
 * The tool-ready export (and the out-of-scope export, until 5.416.0 — the
 * Hosts page's out-of-scope filter and this export replaced it): the text on screen
 * belongs to the format it was generated in (the same defect, and the same
 * fix, as ScopeExport — see ScopeExport.format.test.tsx).  Moving the picker
 * afterwards recounted the old text by the new format's rule and downloaded it
 * under the new format's name and extension.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getToolReadyOutput: vi.fn() }));
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

import ToolReadyOutput from '../../components/ToolReadyOutput';
import { TooltipProvider } from '../../components/ui/tooltip';

const pick = (format: string) =>
  fireEvent.change(screen.getByRole('combobox', { name: 'Output format' }), { target: { value: format } });

beforeEach(() => {
  api.getToolReadyOutput.mockReset();
  download.downloadTextFile.mockReset();
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

// Prod, 2026-10-22: a names export in a project that declares no domain
// answered an empty 200 ten times, and the dialog showed nothing — no output,
// no error — which read as a failure.
describe('ToolReadyOutput — an empty answer is said, with its reason', () => {
  const show = () => render(
    <TooltipProvider>
      <ToolReadyOutput open onClose={vi.fn()} filters={{}} totalHosts={3} selectedCount={0} />
    </TooltipProvider>,
  );

  it('names in scope only: says no in-scope name is bound and which switch widens it', async () => {
    api.getToolReadyOutput.mockResolvedValue({ output: '' });
    show();
    pick('names');
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByText(/No name a declared domain covers is bound to these hosts/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Generated Output/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download output as file' })).not.toBeInTheDocument();

    // The reason follows the request: moving the switch afterwards changes nothing said.
    fireEvent.click(screen.getByRole('switch', { name: 'In-scope names only' }));
    expect(screen.getByText(/No name a declared domain covers/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByText('Nothing to export: none of the matching hosts has anything this format lists.'))
      .toBeInTheDocument();
    expect(screen.queryByText(/No name a declared domain covers/)).not.toBeInTheDocument();
  });

  it('an empty IP list is said too', async () => {
    api.getToolReadyOutput.mockResolvedValue({ output: '' });
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByText('Nothing to export: none of the matching hosts has anything this format lists.'))
      .toBeInTheDocument();
  });
});
