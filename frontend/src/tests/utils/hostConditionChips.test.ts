import { describe, it, expect } from 'vitest';
import { hostConditionChips } from '../../utils/hostConditionChips';

const labels = (filters: Parameters<typeof hostConditionChips>[0]) =>
  hostConditionChips(filters).map((c) => c.label);

// The strip is headed "Matching all of:", so a chip per VALUE lied: `Service:
// http` beside `Service: https` read as "http AND https" for a filter that
// means "http OR https".
describe('hostConditionChips', () => {
  it('fuses port / service / state into one Endpoint condition, alternatives joined with "or"', () => {
    const chips = hostConditionChips({ services: ['http', 'https'], portStates: ['open'] });
    expect(chips).toHaveLength(1);
    expect(chips[0].label).toBe('Endpoint: service http or https · state open');
    expect(chips[0].title).toMatch(/same recorded port/);
    expect(chips[0].clearKeys).toEqual(['services', 'portStates']);
  });

  it('"has open ports" alone is its own plain condition; with a port it requires that port open', () => {
    expect(labels({ hasOpenPorts: true })).toEqual(['Has open ports']);
    expect(labels({ ports: ['22'], hasOpenPorts: true })).toEqual(['Endpoint: port 22 · open']);
    // `false` is a standalone exclusion in the backend, never part of the endpoint.
    expect(labels({ ports: ['22'], hasOpenPorts: false })).toEqual(['Endpoint: port 22', 'No recorded open ports']);
  });

  it('shows the selected severities as ONE ORed condition that clears together', () => {
    const chips = hostConditionChips({ hasCriticalVulns: true, hasHighVulns: true });
    expect(chips.map((c) => c.label)).toEqual(['Scanner severity: Critical or High']);
    expect(chips[0].clearKeys).toEqual(
      expect.arrayContaining(['hasCriticalVulns', 'hasHighVulns', 'hasMediumVulns', 'hasLowVulns']),
    );
  });

  it('abbreviates a long value list and keeps every value in the tooltip', () => {
    const [chip] = hostConditionChips({ sites: ['East', 'West', 'North', 'South', 'HQ'] });
    expect(chip.label).toBe('Site: East or West or North +2');
    expect(chip.title).toBe('Site: East or West or North or South or HQ');
  });

  it('resolves ids to names when it can and falls back to the id while facets load', () => {
    expect(hostConditionChips({ tags: ['5'] }, { tag: () => 'prod' })[0].label).toBe('Tag: prod');
    expect(labels({ tags: ['5'] })).toEqual(['Tag: 5']);
    expect(labels({ scanIds: ['9'] })).toEqual(['Seen in: Scan #9']);
  });

  it('folds "first discovered" into the scan condition it depends on', () => {
    const [chip] = hostConditionChips(
      { scanIds: ['9'], firstSeenInSelectedScans: true },
      { scan: () => 'nightly.xml' },
    );
    expect(chip.label).toBe('First discovered in: nightly.xml');
    expect(chip.clearKeys).toEqual(['scanIds', 'firstSeenInSelectedScans']);
  });

  it('names the query, a legacy text search and the review state — nothing constrains invisibly', () => {
    expect(labels({ query: ' port:443 ', search: 'web', followFilter: 'none', assignedToMe: true })).toEqual([
      'Query: port:443', 'Text search: web', 'Review: Not started', 'Assigned to me',
    ]);
  });

  it('says what the evidence supports: registered owner, exploit reported, no web interface RECORDED', () => {
    expect(labels({ orgs: ['Acme, Inc.'], hasExploitAvailable: true, hasWebInterface: false })).toEqual([
      'No web interface recorded', 'Registered owner: Acme, Inc.', 'Exploit reported',
    ]);
  });

  it('is empty when nothing is applied', () => {
    expect(hostConditionChips({})).toEqual([]);
  });
});
