/**
 * The report workspace's detail model — "What a field becomes" (proposal,
 * revised after its second review): an untouched field keeps its saved
 * state; an edit back to the saved state is no edit; `null` follows the
 * project; what is saved is the complete override map plus the keys edited.
 */
import { describe, expect, it } from 'vitest';

import {
  type DetailContext, current, customised, edited, sectionStatus, shown, toSave, withEdit, withPending, DETAIL_SECTIONS,
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

// Third review 2026-10-15, H6/H7: the edit was judged by its raw value, not
// by what a save stores.
describe('an edit is judged by what a save stores', () => {
  it('a blank recipient row on an empty followed list is no edit: not customised, not sent, not unsaved', () => {
    const c0 = ctx({ followed: { ...followed, distribution: [] } });
    const c = { ...c0, pending: withEdit(c0, 'distribution', [{ name: '', email: null }]) };
    expect(current(c, 'distribution')).toEqual([{ name: '', email: null }]);   // the editor keeps the row
    expect(customised(c, 'distribution')).toBe(false);
    expect(edited(c, 'distribution')).toBe(false);
    expect(toSave(c)).toEqual({});
    expect(shown(c, 'distribution')).toEqual([]);
  });

  it('a blank row beside a named one sends the named one only', () => {
    const c = { ...ctx(), pending: withEdit(ctx(), 'distribution', [{ name: ' CEO ', email: null }, { name: '', email: null }]) };
    expect(toSave(c)).toEqual({ distribution: [{ name: 'CEO', email: null }] });
  });

  it('clearing a followed text is no edit, and the read view shows what it follows', () => {
    const c = { ...ctx(), pending: withEdit(ctx(), 'client_name', '') };
    expect(current(c, 'client_name')).toBe('');                                // the field is empty while edited
    expect(edited(c, 'client_name')).toBe(false);
    expect(toSave(c)).toEqual({});
    expect(shown(c, 'client_name')).toBe('Example Corp');
  });

  it('clearing a text the draft overrides follows the project again', () => {
    const c0 = ctx({ saved: { client_name: 'Mine' } });
    const c = { ...c0, pending: withEdit(c0, 'client_name', '  ') };
    expect(edited(c, 'client_name')).toBe(true);
    expect(toSave(c)).toEqual({ client_name: null });
    expect(shown(c, 'client_name')).toBe('Example Corp');
  });
});

describe('what a field becomes', () => {
  it('an untouched field keeps its saved state, an override included', () => {
    const c = ctx({ saved: { classification: 'B' } });
    expect(current(c, 'classification')).toBe('B');
    expect(customised(c, 'classification')).toBe(true);
    // Not sent: a save never repeats an override it did not edit, so a stale
    // page cannot revert someone else's (review 2026-10-14, H1).
    expect(toSave(c)).toEqual({});
    expect(withPending(c)).toEqual({ classification: 'B' });
  });

  it('typing the value it already has is no edit — the draft stays clean', () => {
    expect(withEdit(ctx(), 'client_name', ' Example Corp ')).toEqual({});
    expect(withEdit(ctx({ saved: { classification: 'B' } }), 'classification', 'B')).toEqual({});
  });

  it('a changed value is an override; changed back, it is no edit again', () => {
    let c = ctx();
    c = { ...c, pending: withEdit(c, 'client_name', 'Other') };
    expect(customised(c, 'client_name')).toBe(true);
    expect(toSave(c)).toEqual({ client_name: 'Other' });
    c = { ...c, pending: withEdit(c, 'client_name', 'Example Corp') };
    expect(c.pending).toEqual({});
  });

  it('"use the project\'s" removes a saved override, and is sent as an edit', () => {
    let c = ctx({ saved: { classification: 'B' } });
    c = { ...c, pending: withEdit(c, 'classification', null) };
    expect(current(c, 'classification')).toBe('A');
    expect(customised(c, 'classification')).toBe(false);
    expect(toSave(c)).toEqual({ classification: null });
  });

  it('a cleared text follows the project once saved', () => {
    let c = ctx({ saved: { classification: 'B' } });
    c = { ...c, pending: withEdit(c, 'classification', '   ') };
    expect(toSave(c)).toEqual({ classification: null });
  });

  it('emptying a list is an override to nobody; rows without a name are dropped', () => {
    let c = ctx();
    c = { ...c, pending: withEdit(c, 'distribution', []) };
    expect(customised(c, 'distribution')).toBe(true);           // "nobody", where the project lists the CISO
    expect(toSave(c)).toEqual({ distribution: [] });
    c = { ...c, pending: withEdit(c, 'testers', [{ user_id: null, name: '  Bo ', role: null, email: null }, { user_id: null, name: ' ', role: null, email: null }]) };
    expect(toSave(c).testers).toEqual([{ user_id: null, name: 'Bo', role: null, email: null }]);
  });

  it('an empty list is customised even where the project lists nobody too — the server keeps it (R9)', () => {
    let c = ctx({ followed: { ...ctx().followed!, distribution: [] } });
    c = { ...c, pending: { distribution: [] } };
    expect(customised(c, 'distribution')).toBe(true);
  });

  it('an addendum\'s summary is its own: never customised, never followed', () => {
    const c = ctx({ ownOnly: ['executive_summary'], saved: {} });
    expect(current(c, 'executive_summary')).toBeUndefined();
    const edited = { ...c, pending: withEdit(c, 'executive_summary', 'Since #1.') };
    expect(customised(edited, 'executive_summary')).toBe(false);
    expect(toSave(edited)).toEqual({ executive_summary: 'Since #1.' });
  });

  it('a section says whether it uses the project\'s defaults, and how many are its own', () => {
    expect(sectionStatus(ctx(), engagement)).toBe('Uses the project’s defaults');
    expect(sectionStatus(ctx({ saved: { classification: 'B', client_name: 'X' } }), engagement)).toBe('2 customised for this report');
    expect(sectionStatus(ctx({ followed: null }), engagement)).toBeNull();     // the defaults page
  });
});
