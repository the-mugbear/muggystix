import { describe, it, expect } from 'vitest';
import { hostFiltersFromUrl } from '../../utils/hostFiltersFromUrl';

const url = (search: string) => new URLSearchParams(search);

describe('hostFiltersFromUrl', () => {
  it('a shared link is authoritative — the recipient\'s session filters are not blended in', () => {
    const { filters } = hostFiltersFromUrl(url('ports=443'), {
      filters: { sites: ['3'], hasCriticalVulns: true, followFilter: 'reviewed', onlyWithNotes: true },
    });
    expect(filters).toEqual({ ports: ['443'] });
  });

  it('reads the link a scan\'s "N renamed" opens, and it makes the address authoritative', () => {
    const { filters, restoredFromSession } = hostFiltersFromUrl(
      url('scan_ids=7&renamed_in_scan=true'), { filters: { sites: ['3'] } },
    );
    expect(filters).toEqual({ scanIds: ['7'], renamedInSelectedScans: true });
    expect(restoredFromSession).toBe(false);
    expect(hostFiltersFromUrl(url('renamed_in_scan=true'), { filters: { sites: ['3'] } }).filters)
      .toEqual({ renamedInSelectedScans: true });
  });

  it('falls back to the saved session on a bare visit', () => {
    const { filters, sortBy } = hostFiltersFromUrl(url(''), {
      filters: { sites: ['3'], hasWebInterface: false, followFilter: 'in_review', onlyWithNotes: true },
    });
    expect(filters).toEqual({
      sites: ['3'], hasWebInterface: false, followFilter: 'in_review', onlyWithNotes: true,
    });
    expect(sortBy).toBeNull();
  });

  // v5.290.0 — drives the "Restored your last filters" notice.
  it('says when the filters came from the saved session rather than the URL', () => {
    const saved = { filters: { sites: ['3'] } };
    expect(hostFiltersFromUrl(url(''), saved).restoredFromSession).toBe(true);
    expect(hostFiltersFromUrl(url('reports=1'), saved).restoredFromSession).toBe(true);
    expect(hostFiltersFromUrl(url('ports=443'), saved).restoredFromSession).toBe(false);
    expect(hostFiltersFromUrl(url(''), null).restoredFromSession).toBe(false);
    // An empty saved session restores nothing worth announcing.
    expect(hostFiltersFromUrl(url(''), { filters: {} }).restoredFromSession).toBe(false);
  });

  it('a parameter that is not a host filter does not make the URL authoritative', () => {
    const { filters } = hostFiltersFromUrl(url('reports=1'), { filters: { sites: ['3'] } });
    expect(filters).toEqual({ sites: ['3'] });
  });

  it('reads every parameter shape: strings, comma lists, repeated params, booleans', () => {
    const { filters } = hostFiltersFromUrl(
      url('q=tag:prod&os_filter=Linux&ports=22,%20443&tech=nginx&orgs=Acme,%20Inc.&orgs=Globex'
        + '&has_high_vulns=true&assigned_to=me&follow_status=none&with_notes_only=true'),
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

  // Review 2026-10-23b, FEP-12: an unknown review state was dropped, so the
  // link opened the whole list with no chip saying a condition was ignored.
  // It is kept for the server to refuse (a 422 the page shows), and the
  // legacy aliases nothing writes any more are not read.
  it('keeps a review state it does not know for the server to refuse, and reads no legacy alias', () => {
    expect(hostFiltersFromUrl(url('follow_status=reviewd'), null).filters).toEqual({ followFilter: 'reviewd' });
    expect(hostFiltersFromUrl(url('follow_status=in_review_any'), null).filters).toEqual({ followFilter: 'in_review_any' });
    expect(hostFiltersFromUrl(url('out_of_scope_only=true'), null).filters).toEqual({ outOfScopeOnly: true });
    for (const alias of ['out_of_scope=true', 'follow=none', 'with_notes=true']) {
      expect(hostFiltersFromUrl(url(alias), { filters: { sites: ['3'] } }).filters).toEqual({ sites: ['3'] });
    }
  });

  it('restores the sort from sort_by, and only a sort it knows', () => {
    expect(hostFiltersFromUrl(url('sort_by=open_ports'), null).sortBy).toBe('open_ports_desc');
    expect(hostFiltersFromUrl(url('sort_by=nonsense'), null).sortBy).toBeNull();
  });
});
