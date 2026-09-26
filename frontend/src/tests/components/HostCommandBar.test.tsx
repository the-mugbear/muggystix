import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  getHostQuerySchema: vi.fn(),
  validateHostQuery: vi.fn(),
  listHostQueryHistory: vi.fn(),
  recordHostQuery: vi.fn(),
  deleteHostQuery: vi.fn(),
  clearHostQueryHistory: vi.fn(),
  suggestHostQueryValues: vi.fn(),
}));

import * as api from '../../services/api';
import HostCommandBar from '../../components/hosts/HostCommandBar';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const schema = {
  fields: [
    { name: 'port', aliases: [], value_source: 'port', trgm: false, enum_values: [] },
    { name: 'has', aliases: [], value_source: 'enum', trgm: false, enum_values: ['web', 'critical'] },
    { name: 'scan', aliases: [], value_source: 'scan', trgm: false, enum_values: [] },
  ],
  examples: [{ label: 'Both ports', q: 'port:80 port:443' }],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getHostQuerySchema.mockResolvedValue(schema);
  mocked.listHostQueryHistory.mockResolvedValue([]);
  mocked.validateHostQuery.mockResolvedValue({ valid: true, match_count: 3, leaf_count: 1 });
  mocked.recordHostQuery.mockResolvedValue({ id: 1, q: 'port:80', result_count: 3, created_at: 'x' });
  // Default: the server has nothing to add, so the page's facets stand.
  mocked.suggestHostQueryValues.mockResolvedValue({ field: 'port', supported: false, values: [] });
});

function setup(overrides: Partial<React.ComponentProps<typeof HostCommandBar>> = {}) {
  const onChange = vi.fn();
  const onPin = vi.fn();
  const onCopyLink = vi.fn();
  render(
    <HostCommandBar value="" onChange={onChange} onPin={onPin} onCopyLink={onCopyLink} {...overrides} />,
  );
  return { onChange, onPin, onCopyLink };
}

describe('HostCommandBar', () => {
  it('validates a typed query and shows the live match count', async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText('Host query'), 'port:80');
    await waitFor(() => expect(mocked.validateHostQuery).toHaveBeenCalledWith('port:80', expect.anything()));
    await waitFor(() => expect(screen.getByText('3')).toBeInTheDocument());
  });

  it('debounce-commits a valid query to onChange', async () => {
    const user = userEvent.setup();
    const { onChange } = setup();
    await user.type(screen.getByLabelText('Host query'), 'port:443');
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('port:443'), { timeout: 2000 });
  });

  it('surfaces a parse error and does not commit an invalid query', async () => {
    mocked.validateHostQuery.mockResolvedValue({ valid: false, error: { message: "Expected a value after 'port:'", position: 5 } });
    const user = userEvent.setup();
    const { onChange } = setup();
    await user.type(screen.getByLabelText('Host query'), 'port:');
    await waitFor(() => expect(mocked.validateHostQuery).toHaveBeenCalled());
    // 5.303.0 — an unfinished term is not an error while it is being typed;
    // it is said once the bar loses focus.
    expect(screen.queryByText(/Expected a value/)).toBeNull();
    await user.tab();
    await waitFor(() => expect(screen.getByText(/Expected a value/)).toBeInTheDocument());
    expect(onChange).not.toHaveBeenCalledWith('port:');
  });

  it('records history on Enter', async () => {
    const user = userEvent.setup();
    const { onChange } = setup();
    const input = screen.getByLabelText('Host query');
    await user.type(input, 'has:web');
    await waitFor(() => expect(mocked.validateHostQuery).toHaveBeenCalled());
    await user.type(input, '{Enter}');
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith('has:web');
      expect(mocked.recordHostQuery).toHaveBeenCalledWith('has:web', 3);
    });
  });

  it('suggests field names while typing a bare token', async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText('Host query'), 'po');
    await waitFor(() => expect(screen.getByText('port:')).toBeInTheDocument());
  });

  it('fires copy-link and pin callbacks', async () => {
    const user = userEvent.setup();
    const { onCopyLink, onPin } = setup({ value: 'port:80' });
    await user.click(screen.getByLabelText('Copy shareable link'));
    expect(onCopyLink).toHaveBeenCalled();
    await waitFor(() => expect(mocked.validateHostQuery).toHaveBeenCalled());
    await user.click(screen.getByLabelText('Save query as view'));
    expect(onPin).toHaveBeenCalledWith('port:80');
  });

  it('exposes combobox semantics and lets the keyboard navigate + insert suggestions', async () => {
    const user = userEvent.setup();
    setup();
    const input = screen.getByLabelText('Host query');
    await user.type(input, 'po');

    const listbox = await screen.findByRole('listbox');
    expect(input).toHaveAttribute('role', 'combobox');
    expect(input).toHaveAttribute('aria-expanded', 'true');
    const option = within(listbox).getByRole('option', { name: 'port:' });
    expect(option).toBeInTheDocument();

    // Arrow highlights (aria-activedescendant tracks), Enter inserts — no mouse.
    await user.keyboard('{ArrowDown}');
    expect(input).toHaveAttribute('aria-activedescendant', option.id);
    await user.keyboard('{Enter}');
    expect(input).toHaveValue('port:');
  });

  it('degrades gracefully when validation is unavailable (Enter still submits + retry offered)', async () => {
    const user = userEvent.setup();
    mocked.validateHostQuery.mockRejectedValue(new Error('offline'));
    const { onChange } = setup();
    await user.type(screen.getByLabelText('Host query'), 'port:80');

    // A retry affordance appears instead of dead-ending the control…
    await screen.findByLabelText('Query validation unavailable — retry');
    // …and an explicit Enter still submits the query (the hosts endpoint will
    // reject it if it's actually invalid).
    await user.keyboard('{Enter}');
    expect(onChange).toHaveBeenCalledWith('port:80');
  });

  // `scan:` takes a numeric id — the one thing an operator doesn't know, since
  // they know the upload by its filename. Reported from prod.
  describe('id-valued fields are searchable by their human label', () => {
    const scanLabels = { scan: { '33': 'openvas-report.xml (openvas)', '41': 'nmap-full.xml (nmap)' } };
    const scanIds = { scan: ['33', '41'] };

    it('finds the id by typing the filename, and inserts the id', async () => {
      const user = userEvent.setup();
      const { onChange } = setup({ valueSuggestions: scanIds, valueLabels: scanLabels });
      await user.type(screen.getByLabelText('Host query'), 'scan:openvas');

      const listbox = await screen.findByRole('listbox');
      // The label is what matched; the id is what the operator needs to see.
      const option = within(listbox).getByRole('option', {
        name: '33 — openvas-report.xml (openvas)',
      });
      await user.click(option);
      await waitFor(() => expect(screen.getByLabelText('Host query')).toHaveValue('scan:33'));
      expect(onChange).not.toHaveBeenCalledWith('scan:openvas');
    });

    it('still matches on the id itself', async () => {
      const user = userEvent.setup();
      setup({ valueSuggestions: scanIds, valueLabels: scanLabels });
      await user.type(screen.getByLabelText('Host query'), 'scan:41');

      const listbox = await screen.findByRole('listbox');
      expect(
        within(listbox).getByRole('option', { name: '41 — nmap-full.xml (nmap)' }),
      ).toBeInTheDocument();
    });

    it('falls back to the bare value when no label is supplied', async () => {
      const user = userEvent.setup();
      setup({ valueSuggestions: scanIds });
      await user.type(screen.getByLabelText('Host query'), 'scan:33');

      const listbox = await screen.findByRole('listbox');
      expect(within(listbox).getByRole('option', { name: '33' })).toBeInTheDocument();
    });
  });

  // 5.291.0 — completion reads the slot at the caret, not the last word.
  describe('context-aware completion', () => {
    it('completes a field inside parentheses', async () => {
      const user = userEvent.setup();
      setup();
      await user.type(screen.getByLabelText('Host query'), '(po');
      const listbox = await screen.findByRole('listbox');
      await user.click(within(listbox).getByRole('option', { name: 'port:' }));
      expect(screen.getByLabelText('Host query')).toHaveValue('(port:');
    });

    it('offers AND / OR / NOT after a complete term and inserts the operator', async () => {
      const user = userEvent.setup();
      setup();
      await user.type(screen.getByLabelText('Host query'), 'has:web ');
      const listbox = await screen.findByRole('listbox');
      await user.click(within(listbox).getByRole('option', { name: /^OR —/ }));
      expect(screen.getByLabelText('Host query')).toHaveValue('has:web OR ');
    });

    it('asks the server for values the page facets do not have, and shows host counts', async () => {
      mocked.suggestHostQueryValues.mockImplementation(async (field: string, prefix: string) => ({
        field, supported: true, values: prefix === '84' ? [{ value: '8443', label: null, count: 2 }] : [],
      }));
      const user = userEvent.setup();
      // The page only knows the common ports.
      setup({ valueSuggestions: { port: ['80', '443'] } });
      await user.type(screen.getByLabelText('Host query'), 'port:84');
      await waitFor(() =>
        expect(mocked.suggestHostQueryValues).toHaveBeenCalledWith('port', '84', expect.anything()),
      );
      const option = await screen.findByRole('option', { name: '8443, 2 hosts' });
      await user.click(option);
      expect(screen.getByLabelText('Host query')).toHaveValue('port:8443');
    });

    it('keeps the page facets on screen when the server lookup fails', async () => {
      mocked.suggestHostQueryValues.mockRejectedValue(new Error('offline'));
      const user = userEvent.setup();
      setup({ valueSuggestions: { port: ['80', '443'] } });
      await user.type(screen.getByLabelText('Host query'), 'port:4');
      expect(await screen.findByRole('option', { name: '443' })).toBeInTheDocument();
    });

    it('Escape hides the list until the next edit (it used to stay hidden)', async () => {
      const user = userEvent.setup();
      setup();
      const input = screen.getByLabelText('Host query');
      await user.type(input, 'po');
      await screen.findByRole('listbox');
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
      await user.type(input, 'r');
      expect(await screen.findByRole('listbox')).toBeInTheDocument();
    });

    // Found in the browser: the debounced commit pushes the TRIMMED query up,
    // and the value→draft sync wrote it back over the draft, eating the space
    // typed after a term (then `port:443AND`).
    it('keeps a trailing space through the commit, so the operators stay offered', async () => {
      const user = userEvent.setup();
      let value = '';
      const onChange = vi.fn((q: string) => { value = q; });
      const { rerender } = render(
        <HostCommandBar value={value} onChange={onChange} onPin={vi.fn()} onCopyLink={vi.fn()} />,
      );
      const input = screen.getByLabelText('Host query');
      await user.type(input, 'port:443 ');
      await waitFor(() => expect(onChange).toHaveBeenCalledWith('port:443'), { timeout: 2000 });
      rerender(<HostCommandBar value={value} onChange={onChange} onPin={vi.fn()} onCopyLink={vi.fn()} />);
      expect(input).toHaveValue('port:443 ');
      expect(await screen.findByRole('option', { name: /^AND —/ })).toBeInTheDocument();
    });

    it('still replaces the draft when the query changes from outside', () => {
      const props = { onChange: vi.fn(), onPin: vi.fn(), onCopyLink: vi.fn() };
      const { rerender } = render(<HostCommandBar value="port:80" {...props} />);
      rerender(<HostCommandBar value="has:web" {...props} />);
      expect(screen.getByLabelText('Host query')).toHaveValue('has:web');
    });

    it('Tab accepts the highlighted suggestion', async () => {
      const user = userEvent.setup();
      setup();
      const input = screen.getByLabelText('Host query');
      await user.type(input, 'ha');
      await screen.findByRole('listbox');
      await user.keyboard('{ArrowDown}{Tab}');
      expect(input).toHaveValue('has:');
      expect(input).toHaveFocus();
    });
  });

  it('disables Copy while a different, invalid draft is shown (no silent substitution)', async () => {
    const user = userEvent.setup();
    mocked.validateHostQuery.mockResolvedValue({
      valid: false,
      error: { message: 'expected value', position: 5 },
    });
    setup({ value: '' });
    await user.type(screen.getByLabelText('Host query'), 'port:');
    await waitFor(() => expect(mocked.validateHostQuery).toHaveBeenCalled());
    // Draft differs from the committed query and is invalid → Copy is disabled
    // so it can't copy the committed query behind a misleading success toast.
    await waitFor(() => expect(screen.getByLabelText('Copy shareable link')).toBeDisabled());
  });
});
