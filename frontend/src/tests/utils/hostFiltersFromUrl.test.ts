import { describe, it, expect } from 'vitest';
import { hostFiltersFromUrl } from '../../utils/hostFiltersFromUrl';

const url = (search: string) => new URLSearchParams(search);

describe('hostFiltersFromUrl', () => {
  it('a shared link is authoritative — the recipient\'s session filters are not blended in', () => {
    const { filters } = hostFiltersFromUrl(url('ports=443'), {
      filters: { sites: ['3'], hasCriticalVulns: true },
      followFilter: 'reviewed',
      onlyWithNotes: true,
    });
    expect(filters).toEqual({ ports: ['443'] });
  });

  it('falls back to the saved session on a bare visit, including the legacy top-level keys', () => {
    const { filters, sortBy } = hostFiltersFromUrl(url(''), {
      filters: { sites: ['3'], hasWebInterface: false },
      followFilter: 'in_review',
      onlyWithNotes: true,
    });
    expect(filters).toEqual({
      sites: ['3'], hasWebInterface: false, followFilter: 'in_review', onlyWithNotes: true,
    });
    expect(sortBy).toBeNull();
  });

  it('a parameter that is not a host filter does not make the URL authoritative', () => {
    const { filters } = hostFiltersFromUrl(url('reports=1'), { filters: { sites: ['3'] } });
    expect(filters).toEqual({ sites: ['3'] });
  });

  it('reads every parameter shape: strings, comma lists, repeated params, booleans', () => {
    const { filters } = hostFiltersFromUrl(
      url('q=tag:prod&os_filter=Linux&ports=22,%20443&tech=nginx&orgs=Acme,%20Inc.&orgs=Globex'
        + '&has_high_vulns=true&assigned_to=me&follow=none&with_notes=true'),
      null,
    );
    expect(filters).toEqual({
      query: 'tag:prod',
      osFilter: 'Linux',
      ports: ['22', '443'],
      tech: ['nginx'],
      // An org name contains commas, so it is never comma-split.
      orgs: ['Acme, Inc.', 'Globex'],
      hasHighVulns: true,
      assignedToMe: true,
      followFilter: 'none',
      onlyWithNotes: true,
    });
  });

  it('keeps a negative filter from an old link (has_web_interface=false)', () => {
    expect(hostFiltersFromUrl(url('has_web_interface=false'), null).filters)
      .toEqual({ hasWebInterface: false });
  });

  it('accepts both spellings of out-of-scope and ignores an unknown follow value', () => {
    expect(hostFiltersFromUrl(url('out_of_scope=true'), null).filters).toEqual({ outOfScopeOnly: true });
    expect(hostFiltersFromUrl(url('out_of_scope_only=true'), null).filters).toEqual({ outOfScopeOnly: true });
    expect(hostFiltersFromUrl(url('follow_status=bogus'), null).filters).toEqual({});
  });

  it('restores the sort from sort_by, and only a sort it knows', () => {
    expect(hostFiltersFromUrl(url('sort_by=open_ports'), null).sortBy).toBe('open_ports_desc');
    expect(hostFiltersFromUrl(url('sort_by=nonsense'), null).sortBy).toBeNull();
  });
});
