/**
 * "What BlueStick reads" (v5.296.0).
 *
 * The page exists so an analyst can tell whether BlueStick kept something
 * from their file.  Pinned: the filters narrow to the rows that answer that
 * (a level, a search for a key), links land on the right tool (`?tool=` by
 * registry name, `?format=` from Ingestion Results), gaps are stated, and
 * worst-case values stay inside the page.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ToolCoverage from '../../pages/ToolCoverage';
import type { ParserCoverageResponse, ToolCoverage as Tool } from '../../services/api';
import { countByLevel, filterCoverage, resolveFocus } from '../../utils/toolCoverage';

const getParserCoverage = vi.fn();
vi.mock('../../services/api', () => ({
  getParserCoverage: () => getParserCoverage(),
}));

const LONG = 'x'.repeat(200);

const tools = (): Tool[] => [
  {
    id: 'nmap',
    name: 'Nmap',
    formats: [{ file_type: 'nmap_xml', label: 'Nmap XML' }],
    registry_tools: ['nmap'],
    accepted_input: 'Nmap XML (-oX).',
    signals: [
      { what: 'Open ports', input: '`<port portid="445">`', level: 'field', stored_as: ['Port.port_number'], shown: 'Host inspector › Ports', note: null },
      { what: 'Other NSE scripts', input: '`<script id="vnc-info">`', level: 'text', stored_as: ['Script.output'], shown: 'Host inspector › NSE script output', note: 'VULNERABLE states raise nothing.' },
      { what: 'Traceroute hops', input: '`<trace>`', level: 'discarded', stored_as: [], shown: null, note: null },
    ],
    gaps: ['State: VULNERABLE results create no scanner observation.'],
    unverified: [],
  },
  {
    id: 'dirbuster',
    name: 'Directory brute-force',
    formats: [{ file_type: 'dirbuster_json', label: 'Directory brute-force JSON' }],
    registry_tools: ['gobuster', 'ffuf'],
    accepted_input: `ffuf JSON ${LONG}`,
    signals: [
      { what: `Discovered path ${LONG}`, input: `\`${LONG}\``, level: 'field', stored_as: [`WebPath.url_${LONG}`], shown: `Host inspector › Discovered paths ${LONG}`, note: LONG },
    ],
    gaps: [],
    unverified: ['No real capture of dirb.'],
  },
  {
    id: 'nuclei',
    name: 'Nuclei',
    formats: [{ file_type: 'nuclei_json', label: 'Nuclei JSON / JSONL' }],
    registry_tools: ['nuclei'],
    accepted_input: '-je JSON.',
    signals: [
      { what: 'Template match', input: '`template-id`', level: 'observation', stored_as: ['Vulnerability.title'], shown: 'Findings › Scanner observations', note: null },
    ],
    gaps: [],
    unverified: [],
  },
];

const payload = (): ParserCoverageResponse => ({
  levels: [
    { id: 'observation', label: 'Scanner observation', description: 'Becomes a scanner observation.' },
    { id: 'field', label: 'Field', description: 'A value BlueStick understands.' },
    { id: 'text', label: 'Raw text', description: 'Shown as-is, not interpreted.' },
    { id: 'stored', label: 'Stored, not shown', description: 'Kept but not displayed.' },
    { id: 'discarded', label: 'Discarded', description: 'Not kept.' },
  ],
  tools: tools(),
});

const renderAt = (url = '/reference/tool-coverage') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <ToolCoverage />
    </MemoryRouter>,
  );

describe('toolCoverage helpers', () => {
  it('a level filter keeps only rows at that level and drops gaps', () => {
    const out = filterCoverage(tools(), { query: '', level: 'discarded' });
    expect(out.map((t) => t.tool.id)).toEqual(['nmap']);
    expect(out[0].signals.map((s) => s.what)).toEqual(['Traceroute hops']);
    expect(out[0].gaps).toEqual([]);
  });

  it('a search matches rows by their input, and gaps by text', () => {
    const out = filterCoverage(tools(), { query: 'VULNERABLE', level: 'all' });
    expect(out).toHaveLength(1);
    expect(out[0].signals.map((s) => s.what)).toEqual(['Other NSE scripts']);
    expect(out[0].gaps).toHaveLength(1);
  });

  it('a registry name keeps the whole tool', () => {
    const out = filterCoverage(tools(), { query: 'gobuster', level: 'all' });
    expect(out.map((t) => t.tool.id)).toEqual(['dirbuster']);
    expect(out[0].signals).toHaveLength(1);
  });

  it('links resolve by id, registry name or format', () => {
    expect(resolveFocus(tools(), { tool: 'ffuf' })?.id).toBe('dirbuster');
    expect(resolveFocus(tools(), { tool: 'nmap' })?.id).toBe('nmap');
    expect(resolveFocus(tools(), { format: 'nuclei_json' })?.id).toBe('nuclei');
    expect(resolveFocus(tools(), { tool: 'unknown', format: null })).toBeNull();
  });

  it('counts rows per level', () => {
    expect(countByLevel(tools())).toEqual({ observation: 1, field: 2, text: 1, stored: 0, discarded: 1 });
  });
});

describe('ToolCoverage page', () => {
  beforeEach(() => {
    getParserCoverage.mockReset();
    getParserCoverage.mockResolvedValue(payload());
    Element.prototype.scrollIntoView = vi.fn();
  });

  it('opens with the lead, one measure per level and every tool', async () => {
    renderAt();
    expect(await screen.findByRole('heading', { name: 'What BlueStick reads' })).toBeInTheDocument();
    expect(screen.getByText(/3 tools, 3 import formats\. 1 of them turn/)).toBeInTheDocument();
    const measures = screen.getByTestId('coverage-measures');
    expect(within(measures).getByRole('link', { name: /rows at level Discarded/ })).toHaveAttribute('href', '/reference/tool-coverage?level=discarded');
    expect(screen.getByRole('heading', { name: /Nmap/ })).toBeInTheDocument();
    expect(screen.getByText('State: VULNERABLE results create no scanner observation.')).toBeInTheDocument();
  });

  it('the level filter narrows the rows', async () => {
    renderAt();
    await screen.findByText('Open ports');
    fireEvent.click(within(screen.getByRole('group', { name: 'Filter by level' })).getByRole('button', { name: 'Discarded' }));
    expect(screen.getByText('Traceroute hops')).toBeInTheDocument();
    expect(screen.queryByText('Open ports')).toBeNull();
    expect(screen.queryByRole('heading', { name: /Nuclei/ })).toBeNull();
  });

  it('says so when nothing matches, and clears', async () => {
    renderAt('/reference/tool-coverage?q=nothing-like-this');
    expect(await screen.findByText(/Nothing matches/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(await screen.findByText('Open ports')).toBeInTheDocument();
  });

  it('lands on the linked tool', async () => {
    renderAt('/reference/tool-coverage?format=nuclei_json');
    await screen.findByText('Template match');
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(document.getElementById('tool-nuclei')?.className).toMatch(/ring-2/);
  });

  it('keeps worst-case values wrapped inside fixed-layout tables', async () => {
    renderAt();
    await screen.findByText(`Discovered path ${LONG}`);
    for (const table of screen.getAllByRole('table')) {
      expect(table.style.tableLayout).toBe('fixed');
    }
    expect(screen.getByText(`Discovered path ${LONG}`).className).toMatch(/break-words/);
  });

  it('reports a failed load instead of a blank page', async () => {
    getParserCoverage.mockRejectedValue(new Error('boom'));
    renderAt();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
