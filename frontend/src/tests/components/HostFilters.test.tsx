import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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
      <MemoryRouter>
        <HostFilters
          filters={{}}
          onFiltersChange={vi.fn()}
          availableData={null}
          optionsLoading={false}
          notesToggleVisible
        />
      </MemoryRouter>,
    );

  it('drops the duplicate "Search hosts" field', () => {
    renderFilters();
    expect(screen.queryByText('Search hosts')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Search hosts')).not.toBeInTheDocument();
  });

  it('surfaces every filter in flat intent sections — no "More filters" disclosure', () => {
    renderFilters();
    // v5.66.1 — the nested disclosure is gone; all controls render directly,
    // including the formerly-advanced ones (Port states / Technologies /
    // Subnet labels).
    for (const label of [
      'Operating system', 'Ports', 'Services', 'Subnets', 'Tags',
      'Technologies', 'Subnet labels', 'Site', 'Discovered in scans',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: /More filters/i })).not.toBeInTheDocument();
  });

  it('groups controls under intent section headers', () => {
    renderFilters();
    for (const section of ['Workflow', 'Risk', 'Network exposure', 'Inventory & location', 'Discovery']) {
      expect(screen.getByText(section)).toBeInTheDocument();
    }
  });

  // §6 guided review queue — the one-click "My review queue" entry seeds the
  // assigned-to-me + not-yet-reviewed filter the analyst works through.
  it('offers a "My review queue" preset that filters to my unreviewed hosts', () => {
    const onFiltersChange = vi.fn();
    render(
      <MemoryRouter>
        <HostFilters
          filters={{}}
          onFiltersChange={onFiltersChange}
          availableData={null}
          optionsLoading={false}
          notesToggleVisible
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /My review queue/i }));
    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ assignedToMe: true, followFilter: 'none' }),
    );
  });
});
