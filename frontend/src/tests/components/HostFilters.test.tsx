import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import HostFilters, { HOST_FILTER_PRESETS, togglePreset } from '../../components/HostFilters';

// Several presets write the same keys (ports / portStates / followFilter).
// Toggling used to assign and delete whole KEYS: a second port preset replaced
// the first one's ports, and switching one off removed `portStates` from under
// the preset that was still lit.
describe('togglePreset', () => {
  const preset = (id: string) => HOST_FILTER_PRESETS.find((p) => p.id === id)!.filters;

  it('adds a second port preset to the first instead of replacing its ports', () => {
    const both = togglePreset(preset('ssh'), togglePreset(preset('windows'), {}));
    expect(both.ports).toEqual(['135', '139', '445', '22']);
    expect(both.portStates).toEqual(['open']);
  });

  it('switching one off keeps what the still-lit preset needs', () => {
    const both = togglePreset(preset('ssh'), togglePreset(preset('windows'), {}));
    const windowsOnly = togglePreset(preset('ssh'), both);
    expect(windowsOnly.ports).toEqual(['135', '139', '445']);
    expect(windowsOnly.portStates).toEqual(['open']);
    expect(togglePreset(preset('windows'), windowsOnly)).toEqual({});
  });

  it('keeps ports two lit presets share (Windows and the 21/23/53/69/135/139 group)', () => {
    const both = togglePreset(preset('legacy'), togglePreset(preset('windows'), {}));
    const legacyOnly = togglePreset(preset('windows'), both);
    expect([...(legacyOnly.ports ?? [])].sort()).toEqual([...preset('legacy').ports!].sort());
  });

  it('never touches filters the operator set that no preset wrote', () => {
    const base = { sites: ['3'], ports: ['8080'] };
    const on = togglePreset(preset('ssh'), base);
    expect(on).toMatchObject({ sites: ['3'], ports: ['8080', '22'] });
    expect(togglePreset(preset('ssh'), on)).toEqual({ sites: ['3'], ports: ['8080'] });
  });

  it('switching "My review queue" off clears the review filter it implied', () => {
    const on = togglePreset(preset('my_queue'), {});
    expect(togglePreset(preset('my_queue'), on)).toEqual({});
  });
});

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
