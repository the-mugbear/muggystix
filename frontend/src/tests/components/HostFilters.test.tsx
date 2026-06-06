import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import HostFilters from '../../components/HostFilters';

// Finding 5: the legacy "Search hosts" field is gone (bare-text search lives in
// the command bar), and the common network filters (OS/ports/services/subnets/
// tags) are surfaced into the always-visible grid instead of hiding behind the
// "More filters" disclosure. This also smoke-tests that the restructured
// component mounts (the Hosts page test stubs HostFilters, so nothing else
// renders the real one).
describe('HostFilters layout', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  const renderFilters = () =>
    render(
      <HostFilters
        filters={{}}
        onFiltersChange={vi.fn()}
        availableData={null}
        optionsLoading={false}
        notesToggleVisible
      />,
    );

  it('drops the duplicate "Search hosts" field', () => {
    renderFilters();
    expect(screen.queryByText('Search hosts')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Search hosts')).not.toBeInTheDocument();
  });

  it('surfaces common network filters without opening "More filters"', () => {
    renderFilters();
    // Visible immediately (advanced section is collapsed by default).
    for (const label of ['Operating system', 'Ports', 'Services', 'Subnets', 'Tags']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // Genuinely-advanced filters stay behind the disclosure.
    expect(screen.queryByText('Min risk score')).not.toBeInTheDocument();
  });

  it('keeps less-common filters in the single "More filters" disclosure', async () => {
    const user = userEvent.setup();
    renderFilters();
    await user.click(screen.getByRole('button', { name: /More filters/i }));
    await waitFor(() => expect(screen.getByText('Min risk score')).toBeInTheDocument());
    expect(screen.getByText('Port states')).toBeInTheDocument();
    expect(screen.getByText('Technologies')).toBeInTheDocument();
  });
});
