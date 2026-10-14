/**
 * The report workspace's detail model — "What a field becomes" (proposal,
 * revised after its second review): an untouched field keeps its saved
 * state; an edit back to the saved state is no edit; `null` follows the
 * project; what is saved is the complete override map plus the keys edited.
 */
import { describe, expect, it } from 'vitest';

import {
  type DetailContext, current, customised, sectionStatus, toSave, withEdit, DETAIL_SECTIONS,
} from '../../../components/reports/workspace/detailModel';
import type { EngagementSettings } from '../../../services/api';

const followed: EngagementSettings = {
  executive_summary: 'Project summary.', client_name: 'Example Corp', classification: 'A', engagement_type: null,
  testers: [{ user_id: 1, name: 'Ana', role: 'Lead', email: null }], distribution: [{ name: 'CISO', email: null }],
  system_description: null, author_name: 'Installation Ltd', disclaimer: null,
};
const ctx = (over: Partial<DetailContext> = {}): DetailContext => ({
  saved: {}, followed, sources: { author_name: 'installation' }, pending: {}, ownOnly: [], ...over,
});
const engagement = DETAIL_SECTIONS.find((s) => s.id === 'engagement')!;

describe('what a field becomes', () => {
  it('an untouched field keeps its saved state, an override included', () => {
    const c = ctx({ saved: { classification: 'B' } });
    expect(current(c, 'classification')).toBe('B');
    expect(customised(c, 'classification')).toBe(true);
    expect(toSave(c)).toEqual({ settings: { classification: 'B' }, edited: [] });
  });

  it('typing the value it already has is no edit — the draft stays clean', () => {
    expect(withEdit(ctx(), 'client_name', ' Example Corp ')).toEqual({});
    expect(withEdit(ctx({ saved: { classification: 'B' } }), 'classification', 'B')).toEqual({});
  });

  it('a changed value is an override; changed back, it is no edit again', () => {
    let c = ctx();
    c = { ...c, pending: withEdit(c, 'client_name', 'Other') };
    expect(customised(c, 'client_name')).toBe(true);
    expect(toSave(c)).toEqual({ settings: { client_name: 'Other' }, edited: ['client_name'] });
    c = { ...c, pending: withEdit(c, 'client_name', 'Example Corp') };
    expect(c.pending).toEqual({});
  });

  it('"use the project\'s" removes a saved override, and is sent as an edit', () => {
    let c = ctx({ saved: { classification: 'B' } });
    c = { ...c, pending: withEdit(c, 'classification', null) };
    expect(current(c, 'classification')).toBe('A');
    expect(customised(c, 'classification')).toBe(false);
    expect(toSave(c)).toEqual({ settings: {}, edited: ['classification'] });
  });

  it('a cleared text follows the project once saved', () => {
    let c = ctx({ saved: { classification: 'B' } });
    c = { ...c, pending: withEdit(c, 'classification', '   ') };
    expect(toSave(c).settings).toEqual({});
  });

  it('emptying a list is an override to nobody; rows without a name are dropped', () => {
    let c = ctx();
    c = { ...c, pending: withEdit(c, 'distribution', []) };
    expect(customised(c, 'distribution')).toBe(true);           // "nobody", where the project lists the CISO
    expect(toSave(c).settings).toEqual({ distribution: [] });
    c = { ...c, pending: withEdit(c, 'testers', [{ user_id: null, name: '  Bo ', role: null, email: null }, { user_id: null, name: ' ', role: null, email: null }]) };
    expect(toSave(c).settings.testers).toEqual([{ user_id: null, name: 'Bo', role: null, email: null }]);
  });

  it('an addendum\'s summary is its own: never customised, never followed', () => {
    const c = ctx({ ownOnly: ['executive_summary'], saved: {} });
    expect(current(c, 'executive_summary')).toBeUndefined();
    const edited = { ...c, pending: withEdit(c, 'executive_summary', 'Since #1.') };
    expect(customised(edited, 'executive_summary')).toBe(false);
    expect(toSave(edited)).toEqual({ settings: { executive_summary: 'Since #1.' }, edited: ['executive_summary'] });
  });

  it('a section says whether it uses the project\'s defaults, and how many are its own', () => {
    expect(sectionStatus(ctx(), engagement)).toBe('Uses the project’s defaults');
    expect(sectionStatus(ctx({ saved: { classification: 'B', client_name: 'X' } }), engagement)).toBe('2 customised for this report');
    expect(sectionStatus(ctx({ followed: null }), engagement)).toBeNull();     // the defaults page
  });
});
