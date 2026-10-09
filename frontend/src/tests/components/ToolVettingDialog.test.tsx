/**
 * Vetting a tool an agent asked for: the SAVE.
 *
 * Pinned: what is sent (the tool's name, then the whole form as the reader
 * left it), what the reader is told on success and on a refusal, that the
 * catalogue on the page shows the saved row without being asked for again,
 * and that one tool's fields and failure never show on the next tool.
 *
 * The catalogue is the deployment's, not a project's: no project id here.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ToolRegistryEntry, ToolRegistryResponse } from '../../services/api';

const api = vi.hoisted(() => ({ updateToolRegistryEntry: vi.fn(), getToolRegistry: vi.fn() }));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import ToolVettingDialog from '../../components/ToolVettingDialog';

const tool = (over: Partial<ToolRegistryEntry> = {}): ToolRegistryEntry => ({
  name: 'ligolo-ng', description: 'Needed a tunnel to reach 10.2.0.0/16.', category: 'Remote Access',
  ports: '11601', install: 'apt install ligolo-ng', url: 'https://github.com/nicocha30/ligolo-ng', kali: true,
  status: 'suggested', phases: [], intrusive: null, requires_privileges: null, output_format: null,
  ingestible: false, suggested_rationale: 'No listed tool pivots through a jump host.', ...over,
});
const SUGGESTED = tool();
const LISTED = tool({ name: 'nmap', status: 'reference', description: 'Port scanner.', category: 'Scanning',
  ports: null, install: null, url: null, suggested_rationale: null });
const refused = (status: number, detail: string) => ({ response: { status, data: { detail } } });

/** The catalogue as the Tool reference page reads it (`['getToolRegistry']`):
 *  one line per tool, as the server last said. */
const Catalogue: React.FC = () => {
  const registry = useQuery({
    queryKey: ['getToolRegistry'],
    queryFn: ({ signal }): Promise<ToolRegistryResponse> => api.getToolRegistry(undefined, signal),
  });
  return (
    <ul aria-label="catalogue">
      {(registry.data?.tools ?? []).map((t) => (
        <li key={t.name}>{`${t.name} | ${t.status} | ${t.category} | ${t.description}`}</li>
      ))}
    </ul>
  );
};
const catalogue = (): string[] => screen.getAllByRole('listitem', { hidden: true }).map((li) => li.textContent ?? '');

const onOpenChange = vi.fn();
const show = (t: ToolRegistryEntry | null = SUGGESTED, open = true) => render(
  <>
    <Catalogue />
    <ToolVettingDialog tool={t} open={open} onOpenChange={onOpenChange} />
  </>,
);
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const chooseStatus = async (name: 'In the catalogue' | 'Declined') => {
  fireEvent.keyDown(screen.getByRole('combobox', { name: 'Status' }), { key: 'Enter' });
  fireEvent.click(await screen.findByRole('option', { name }));
};
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save' }));

beforeEach(() => {
  vi.clearAllMocks();
  api.getToolRegistry.mockResolvedValue({ count: 2, tools: [SUGGESTED, LISTED] });
  api.updateToolRegistryEntry.mockImplementation(async (name: string, update: Partial<ToolRegistryEntry>) => (
    { ...(name === 'nmap' ? LISTED : SUGGESTED), ...update }
  ));
});

describe('ToolVettingDialog — what it opens on', () => {
  it('a suggested tool: the agent’s reason, "In the catalogue" preselected, the row’s own values in the fields', () => {
    show();
    expect(screen.getByRole('dialog', { name: 'Review ligolo-ng' })).toBeInTheDocument();
    expect(screen.getByText('Why an agent asked for this:')).toBeInTheDocument();
    expect(screen.getByText('No listed tool pivots through a jump host.')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveTextContent('In the catalogue');
    expect(screen.getByLabelText('Description')).toHaveValue('Needed a tunnel to reach 10.2.0.0/16.');
    expect(screen.getByLabelText('Category')).toHaveValue('Remote Access');
    expect(screen.getByLabelText('Ports')).toHaveValue('11601');
    expect(screen.getByLabelText('Install command')).toHaveValue('apt install ligolo-ng');
    expect(screen.getByLabelText('Project URL')).toHaveValue('https://github.com/nicocha30/ligolo-ng');
    expect(screen.getByText(/rewrite it as documentation/)).toBeInTheDocument();
    expect(api.updateToolRegistryEntry).not.toHaveBeenCalled();
  });

  it('a declined tool opens on "Declined"; a listed one shows no agent reason and empty fields for its nulls', () => {
    const { unmount } = show(tool({ status: 'rejected' }));
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveTextContent('Declined');
    // The reason is shown for a pending ask only.
    expect(screen.queryByText('Why an agent asked for this:')).toBeNull();
    unmount();

    show(LISTED);
    expect(screen.getByRole('combobox', { name: 'Status' })).toHaveTextContent('In the catalogue');
    expect(screen.queryByText('Why an agent asked for this:')).toBeNull();
    expect(screen.queryByText(/rewrite it as documentation/)).toBeNull();
    expect(screen.getByLabelText('Ports')).toHaveValue('');
    expect(screen.getByLabelText('Install command')).toHaveValue('');
    expect(screen.getByLabelText('Project URL')).toHaveValue('');
  });

  it('with no tool, or closed, there is no dialog and nothing is sent', () => {
    const { unmount } = show(null);
    expect(screen.queryByRole('dialog')).toBeNull();
    unmount();
    show(SUGGESTED, false);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.updateToolRegistryEntry).not.toHaveBeenCalled();
  });

  it('a 200-character tool name is the one in the title, the request and the message', async () => {
    const long = `tool-${'n'.repeat(195)}`;
    show(tool({ name: long, status: 'suggested', suggested_rationale: null }));
    expect(screen.getByRole('dialog', { name: `Review ${long}` })).toBeInTheDocument();
    // A pending ask with no reason recorded shows no empty "why" box.
    expect(screen.queryByText('Why an agent asked for this:')).toBeNull();
    save();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(`${long} is in the catalogue`));
    expect(api.updateToolRegistryEntry).toHaveBeenCalledWith(long, expect.objectContaining({ status: 'reference' }));
  });
});

describe('ToolVettingDialog — saving', () => {
  it('adding to the catalogue sends the tool’s name and the form as edited, trimmed', async () => {
    show();
    await waitFor(() => expect(catalogue()).toHaveLength(2));
    type('Description', '  Tunnels through a jump host without SOCKS.  ');
    type('Category', '  Pivoting ');
    type('Ports', ' 11601, 443 ');
    type('Install command', ' go install github.com/nicocha30/ligolo-ng@latest ');
    type('Project URL', ' https://example.test/ligolo ');
    save();

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('ligolo-ng is in the catalogue'));
    expect(api.updateToolRegistryEntry).toHaveBeenCalledTimes(1);
    expect(api.updateToolRegistryEntry).toHaveBeenCalledWith('ligolo-ng', {
      status: 'reference',
      description: 'Tunnels through a jump host without SOCKS.',
      category: 'Pivoting',
      ports: '11601, 443',
      install: 'go install github.com/nicocha30/ligolo-ng@latest',
      url: 'https://example.test/ligolo',
    });
    expect(onOpenChange).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('declining sends "rejected" and says the tool was declined', async () => {
    show();
    await chooseStatus('Declined');
    expect(screen.getByText(/Declined\. The row stays so the next agent that asks gets the same answer\./)).toBeInTheDocument();
    save();

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('ligolo-ng was declined'));
    expect(api.updateToolRegistryEntry).toHaveBeenCalledWith('ligolo-ng', expect.objectContaining({ status: 'rejected' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('the catalogue on the page shows the row as the server saved it, without being read again', async () => {
    // The server's answer is what is shown — not what was typed.
    api.updateToolRegistryEntry.mockResolvedValue(
      { ...SUGGESTED, status: 'reference', category: 'Pivoting', description: 'As the server stored it.' },
    );
    show();
    await waitFor(() => expect(catalogue()).toEqual([
      'ligolo-ng | suggested | Remote Access | Needed a tunnel to reach 10.2.0.0/16.',
      'nmap | reference | Scanning | Port scanner.',
    ]));
    type('Description', 'What was typed.');
    save();

    await waitFor(() => expect(catalogue()).toEqual([
      'ligolo-ng | reference | Pivoting | As the server stored it.',
      // The other rows are left alone.
      'nmap | reference | Scanning | Port scanner.',
    ]));
    expect(api.getToolRegistry).toHaveBeenCalledTimes(1);
  });

  it('an untouched form still saves: the row’s own values, a null as an empty text, the decision as shown', async () => {
    show(LISTED);
    save();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('nmap is in the catalogue'));
    expect(api.updateToolRegistryEntry).toHaveBeenCalledWith('nmap', {
      status: 'reference', description: 'Port scanner.', category: 'Scanning', ports: '', install: '', url: '',
    });
  });

  it('an emptied category is sent as "Uncategorised", never blank', async () => {
    show();
    type('Category', '   ');
    save();
    await waitFor(() => expect(api.updateToolRegistryEntry).toHaveBeenCalledWith(
      'ligolo-ng', expect.objectContaining({ category: 'Uncategorised' }),
    ));
  });

  it('while the save is out, neither Save nor Cancel acts, and it is sent once', async () => {
    let answer: (value: ToolRegistryEntry) => void = () => {};
    api.updateToolRegistryEntry.mockReturnValue(new Promise<ToolRegistryEntry>((resolve) => { answer = resolve; }));
    show();
    save();

    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    save();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();

    answer({ ...SUGGESTED, status: 'reference' });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(api.updateToolRegistryEntry).toHaveBeenCalledTimes(1);
  });

  it('Cancel sends nothing and tells the page to close', () => {
    show();
    type('Description', 'Half an edit.');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(api.updateToolRegistryEntry).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe('ToolVettingDialog — a refused save', () => {
  it('is said in the dialog, which stays open with the edits; nothing is announced or changed; a retry sends them', async () => {
    api.updateToolRegistryEntry.mockRejectedValueOnce(refused(403, 'Only a global administrator can vet a tool.'));
    show();
    await waitFor(() => expect(catalogue()).toHaveLength(2));
    type('Description', 'Tunnels through a jump host.');
    save();

    expect(await screen.findByText('Only a global administrator can vet a tool.')).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Review ligolo-ng' })).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Description')).toHaveValue('Tunnels through a jump host.');
    expect(catalogue()[0]).toBe('ligolo-ng | suggested | Remote Access | Needed a tunnel to reach 10.2.0.0/16.');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();

    save();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('ligolo-ng is in the catalogue'));
    expect(api.updateToolRegistryEntry).toHaveBeenCalledTimes(2);
    expect(api.updateToolRegistryEntry).toHaveBeenLastCalledWith(
      'ligolo-ng', expect.objectContaining({ status: 'reference', description: 'Tunnels through a jump host.' }),
    );
    expect(screen.queryByText('Only a global administrator can vet a tool.')).toBeNull();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('a failure with no message from the server still says the save failed', async () => {
    api.updateToolRegistryEntry.mockRejectedValue(new Error('boom'));
    show();
    save();
    expect(await screen.findByText('Could not save this tool.')).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('the next tool opened starts from its own row, without the previous tool’s failure or edits', async () => {
    api.updateToolRegistryEntry.mockRejectedValueOnce(refused(422, 'Description is too long.'));
    const page = show();
    type('Description', 'An edit on ligolo.');
    save();
    expect(await screen.findByText('Description is too long.')).toBeInTheDocument();

    page.rerender(
      <>
        <Catalogue />
        <ToolVettingDialog tool={LISTED} open onOpenChange={onOpenChange} />
      </>,
    );
    expect(screen.getByRole('dialog', { name: 'Review nmap' })).toBeInTheDocument();
    expect(screen.queryByText('Description is too long.')).toBeNull();
    expect(screen.getByLabelText('Description')).toHaveValue('Port scanner.');
    expect(screen.getByLabelText('Category')).toHaveValue('Scanning');

    save();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('nmap is in the catalogue'));
    expect(api.updateToolRegistryEntry).toHaveBeenLastCalledWith('nmap', expect.objectContaining({ description: 'Port scanner.' }));
  });
});
