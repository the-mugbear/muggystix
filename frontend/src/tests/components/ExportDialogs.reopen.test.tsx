/**
 * The three export dialogs (scope, out-of-scope, tool-ready) are one dialog
 * (`GeneratedTextDialog`, plan B2a).  What they did alike, pinned here for
 * each of them, beside `ScopeExport.format.test.tsx`,
 * `ExportDialogs.format.test.tsx` and `ExportDialogs.errors.test.tsx`:
 *
 *   - an opening starts with no output and no failure — but with the format
 *     chosen last time (kept ON PURPOSE: an analyst exports the same way
 *     again and again);
 *   - Copy takes the whole text, and so does Download;
 *   - while the request is out the button says so and cannot be pressed twice.
 *
 * And what only the tool-ready export has: its two switches travel with the
 * request, the capped-export notice, and a preview that is cut while Copy and
 * Download are not.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getScopeHostList: vi.fn(), getOutOfScopeHostList: vi.fn(), getToolReadyOutput: vi.fn(),
}));
vi.mock('../../services/api', () => api);

const download = vi.hoisted(() => ({ downloadTextFile: vi.fn() }));
vi.mock('../../utils/download', () => download);
const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn() }));
vi.mock('../../utils/clipboard', () => clipboard);

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
import ScopeExport from '../../components/ScopeExport';
import ToolReadyOutput from '../../components/ToolReadyOutput';
import { TooltipProvider } from '../../components/ui/tooltip';

const picker = () => screen.getByRole('combobox', { name: 'Output format' }) as HTMLSelectElement;
const pick = (format: string) => fireEvent.change(picker(), { target: { value: format } });

const refused = Object.assign(new Error('Request failed with status code 403'), { response: { status: 403, data: {} } });

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  Object.values(api).forEach((m) => m.mockReset());
  download.downloadTextFile.mockReset();
  clipboard.copyToClipboard.mockReset().mockResolvedValue(true);
});

interface Case {
  name: string;
  dialog: (open: boolean) => React.ReactElement;
  request: ReturnType<typeof vi.fn>;
  /** A format other than the one the dialog opens on, and an answer in it. */
  format: string;
  answer: unknown;
  text: string;
  generate: string;
  heading: string;
  copy: string;
  download: string;
  file: string;
}

const csv = 'ip,hostname,state\n10.0.0.1,a,up\n10.0.0.2,b,up\n';
const cases: Case[] = [
  {
    name: 'ScopeExport',
    dialog: (open) => <ScopeExport open={open} onClose={vi.fn()} scopeId={3} scopeName="DMZ east" />,
    request: api.getScopeHostList,
    format: 'csv', answer: csv, text: csv,
    generate: 'Generate list', heading: '2 hosts · CSV',
    copy: 'Copy scope export to clipboard', download: 'Download scope export as file', file: 'DMZ_east_hosts.csv',
  },
  {
    name: 'OutOfScopeExport',
    dialog: (open) => <OutOfScopeExport open={open} onClose={vi.fn()} />,
    request: api.getOutOfScopeHostList,
    format: 'csv', answer: csv, text: csv,
    generate: 'Generate list', heading: '2 hosts · CSV',
    copy: 'Copy output to clipboard', download: 'Download output as file', file: 'out_of_scope_hosts.csv',
  },
  {
    name: 'ToolReadyOutput',
    dialog: (open) => <ToolReadyOutput open={open} onClose={vi.fn()} filters={{ state: 'up' }} totalHosts={2} />,
    request: api.getToolReadyOutput,
    format: 'nmap', answer: { output: '10.0.0.1 10.0.0.2' }, text: '10.0.0.1 10.0.0.2',
    generate: 'Generate output', heading: 'Generated Output (Nmap)',
    copy: 'Copy output to clipboard', download: 'Download output as file', file: 'nmap-targets.txt',
  },
];

describe.each(cases)('$name — one export dialog', (c) => {
  const show = (open = true) => render(<TooltipProvider>{c.dialog(open)}</TooltipProvider>);

  it('an opening starts with no output, and keeps the format chosen last time', async () => {
    c.request.mockResolvedValue(c.answer);
    const { rerender } = show();
    pick(c.format);
    fireEvent.click(screen.getByRole('button', { name: c.generate }));
    expect(await screen.findByRole('heading', { name: c.heading })).toBeInTheDocument();

    rerender(<TooltipProvider>{c.dialog(false)}</TooltipProvider>);
    expect(screen.queryByRole('heading', { name: c.heading })).not.toBeInTheDocument();
    rerender(<TooltipProvider>{c.dialog(true)}</TooltipProvider>);
    await waitFor(() => expect(screen.queryByRole('heading', { name: c.heading })).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: c.copy })).not.toBeInTheDocument();
    // …but the picker is where the reader left it, and that is what is asked for.
    expect(picker().value).toBe(c.format);
    expect(c.request).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: c.generate }));
    expect(await screen.findByRole('heading', { name: c.heading })).toBeInTheDocument();
    expect(c.request).toHaveBeenCalledTimes(2);
  });

  it('a failure is not carried into the next opening', async () => {
    c.request.mockRejectedValue(refused);
    const { rerender } = show();
    fireEvent.click(screen.getByRole('button', { name: c.generate }));
    expect(await screen.findByText('You do not have permission to perform this action.')).toBeInTheDocument();
    rerender(<TooltipProvider>{c.dialog(false)}</TooltipProvider>);
    rerender(<TooltipProvider>{c.dialog(true)}</TooltipProvider>);
    await waitFor(() => expect(screen.queryByText('You do not have permission to perform this action.')).not.toBeInTheDocument());
  });

  it('Copy and Download take the whole text, under the name of the format that made it', async () => {
    c.request.mockResolvedValue(c.answer);
    show();
    pick(c.format);
    fireEvent.click(screen.getByRole('button', { name: c.generate }));
    await screen.findByRole('heading', { name: c.heading });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: c.copy })); });
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith(c.text);
    fireEvent.click(screen.getByRole('button', { name: c.download }));
    expect(download.downloadTextFile).toHaveBeenCalledWith(c.file, c.text);
    expect(screen.getByText(c.text, { selector: 'pre', normalizer: (s) => s })).toBeInTheDocument();
  });

  it('while the request is out the button says so and cannot be pressed twice', async () => {
    let answer!: (value: unknown) => void;
    c.request.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    show();
    fireEvent.click(screen.getByRole('button', { name: c.generate }));
    const busy = await screen.findByRole('button', { name: 'Generating…' });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(c.request).toHaveBeenCalledTimes(1);
    await act(async () => { answer(c.answer); });
    expect(await screen.findByRole('button', { name: c.generate })).toBeEnabled();
  });

  it('Close asks the page to close it', () => {
    const onClose = vi.fn();
    render(<TooltipProvider>{React.cloneElement(c.dialog(true), { onClose })}</TooltipProvider>);
    // (the footer's button; the dialog's own "×" is named Close too)
    screen.getAllByRole('button', { name: 'Close' }).forEach((button) => fireEvent.click(button));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('ToolReadyOutput — what only it has', () => {
  const show = (props: Partial<React.ComponentProps<typeof ToolReadyOutput>> = {}) => render(
    <TooltipProvider>
      <ToolReadyOutput open onClose={vi.fn()} filters={{ state: 'up' }} totalHosts={1200} selectedCount={2} {...props} />
    </TooltipProvider>,
  );

  it('names the population: the view, not the checked rows', () => {
    show();
    expect(screen.getByText(/all 1,200 hosts matching the applied filters, on every page/)).toBeInTheDocument();
    expect(screen.getByText(/The 2 rows you have checked\s+do not narrow it/)).toBeInTheDocument();
  });

  it('the page’s filters and the port switch travel with the request; the names switch only with a format that uses names', async () => {
    api.getToolReadyOutput.mockResolvedValue({ output: '10.0.0.1' });
    show();
    // An IP list has no names: no switch, and no names scope is sent.
    expect(screen.queryByRole('switch', { name: /names/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Include detailed port information' }));
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    await waitFor(() => expect(api.getToolReadyOutput).toHaveBeenCalledWith(1, 'ip-list', { state: 'up', includePorts: true }));

    pick('names');
    // In scope by default — the rule the agent's scope guardrail applies.
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    await waitFor(() => expect(api.getToolReadyOutput).toHaveBeenLastCalledWith(
      1, 'names', { state: 'up', includePorts: true, namesScope: 'in_scope' },
    ));
    fireEvent.click(screen.getByRole('switch', { name: 'In-scope names only' }));
    expect(screen.getByRole('switch', { name: 'All bound names' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    await waitFor(() => expect(api.getToolReadyOutput).toHaveBeenLastCalledWith(
      1, 'names', { state: 'up', includePorts: true, namesScope: 'all' },
    ));
  });

  it('says when the export stopped at the server’s limit, and how many hosts it was built from', async () => {
    api.getToolReadyOutput.mockResolvedValue({ output: 'a\nb\n', limit: 5000, returned: 5000, total: 42000 });
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByText(/Built from the first 5,000 of\s+42,000 matching hosts/)).toBeInTheDocument();
    expect(screen.getByText('Built from 5,000 hosts')).toBeInTheDocument();
  });

  it('counts the lines when the server sent no count', async () => {
    api.getToolReadyOutput.mockResolvedValue({ output: '10.0.0.1\n10.0.0.2\n\n10.0.0.3\n' });
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByText('3 entries generated')).toBeInTheDocument();
  });

  it('a very long output is previewed from its start; Copy and Download take all of it', async () => {
    const long = `${'10.0.0.1\n'.repeat(12_000)}THE-END`;
    expect(long.length).toBeGreaterThan(100_000);
    api.getToolReadyOutput.mockResolvedValue({ output: long, returned: 12_001 });
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByText(/the preview shows the start; Copy and Download include everything/)).toBeInTheDocument();
    expect(document.querySelector('pre')?.textContent).toHaveLength(100_000);
    expect(document.querySelector('pre')?.textContent).not.toContain('THE-END');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy output to clipboard' })); });
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith(long);
    fireEvent.click(screen.getByRole('button', { name: 'Download output as file' }));
    expect(download.downloadTextFile).toHaveBeenCalledWith('ip list-targets.txt', long);
  });
});
