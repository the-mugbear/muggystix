import { describe, it, expect } from 'vitest';
import {
  HOST_BUILT_IN_VIEWS,
  HOST_FILTER_PRESETS,
  HOST_PORT_GROUP_PRESETS,
  togglePreset,
} from '../../components/HostFilters';

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

  it('a preset merely IMPLIED by the one being switched off holds nothing back', () => {
    // `narrow` lights up whenever `wide` is applied, but it was never chosen.
    const wide = { assignedToMe: true, followFilter: 'none' as const };
    const narrow = { followFilter: 'none' as const };
    const on = togglePreset(wide, {}, [wide, narrow]);
    expect(togglePreset(wide, on, [wide, narrow])).toEqual({});
  });
});

// The panel component these used to render was deleted in 5.251.1 (the catalog
// in HostFilterPopover replaced it; its tests are HostFilterPopover.test.tsx).
describe('host filter presets', () => {
  it('splits the presets into port groups and built-in views with none lost', () => {
    expect(HOST_PORT_GROUP_PRESETS.map((p) => p.id)).toEqual(['web_hosts', 'ssh', 'database', 'windows', 'legacy']);
    // "My review queue" must keep a host once work on it has STARTED: with
    // `followFilter: 'none'` it emptied itself, since taking a host In review
    // is what assigns it.
    expect(HOST_BUILT_IN_VIEWS.find((v) => v.id === 'my_queue')?.filters)
      .toEqual({ assignedToMe: true, query: 'NOT follow:reviewed' });
    expect(HOST_PORT_GROUP_PRESETS.length + HOST_BUILT_IN_VIEWS.length).toBe(HOST_FILTER_PRESETS.length);
  });
});
