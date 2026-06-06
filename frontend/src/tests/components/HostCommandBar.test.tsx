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
}));

import * as api from '../../services/api';
import HostCommandBar from '../../components/hosts/HostCommandBar';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const schema = {
  fields: [
    { name: 'port', aliases: [], value_source: 'port', trgm: false, enum_values: [] },
    { name: 'has', aliases: [], value_source: 'enum', trgm: false, enum_values: ['web', 'critical'] },
  ],
  examples: [{ label: 'Both ports', q: 'port:80 port:443' }],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getHostQuerySchema.mockResolvedValue(schema);
  mocked.listHostQueryHistory.mockResolvedValue([]);
  mocked.validateHostQuery.mockResolvedValue({ valid: true, match_count: 3, leaf_count: 1 });
  mocked.recordHostQuery.mockResolvedValue({ id: 1, q: 'port:80', result_count: 3, created_at: 'x' });
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
