/**
 * Agendas in the browser (`utils/presentationAgenda`, plan E3): Next walks the
 * agenda's items by the anchors the SERVER put on each slide; picking a slide
 * outside the active item explores (the item frozen) and "Back to the agenda"
 * returns to it; a filter change that leaves the item explores; a newer
 * version keeps the slide when it can.  The plan's case: item 1 is finding
 * F-07, item 3 is F-07's recommendation — on item 3, Next is item 4, not
 * F-07's next page, whoever drives.
 */
import { describe, it, expect } from 'vitest';

import type { Slide } from '../../services/api';
import type { Target } from '../../utils/presentationSync';
import {
  anchorOptions, backToAgenda, itemLabel, itemPages, pickItem, pickSlide, stepTarget, switchAgenda,
  withFilterLanding, type PinnedAgenda,
} from '../../utils/presentationAgenda';

const slide = (key: string, anchors: string[], extra: Partial<Slide> = {}): Slide => ({
  key, kind: 'section', header: key, footer: '', page: 1, pages: 1, blocks: [], anchors, ...extra,
});

const SLIDES: Slide[] = [
  slide('overview:1', ['overview'], { kind: 'overview' }),
  slide('scope:1', ['scope'], { kind: 'scope' }),
  slide('f:F-07', ['finding:F-07'], { kind: 'finding', ref: 'F-07', blocks: [{ kind: 'title', md: '**Weak** TLS' }] }),
  slide('f:F-07:impact:1', ['finding:F-07', 'section:F-07:impact'], { ref: 'F-07' }),
  slide('f:F-07:recommendation:1', ['finding:F-07', 'section:F-07:recommendation'], { ref: 'F-07' }),
  slide('f:F-07:recommendation:2', ['finding:F-07', 'section:F-07:recommendation'], { ref: 'F-07' }),
  slide('f:F-07:hosts:1', ['finding:F-07', 'section:F-07:hosts'], { ref: 'F-07', kind: 'hosts' }),
  slide('f:F-08', ['finding:F-08'], { kind: 'finding', ref: 'F-08' }),
];

const AGENDA: PinnedAgenda = {
  id: 4, version: 2, items: ['finding:F-07', 'overview', 'section:F-07:recommendation', 'scope'],
};

const at = (slide_key: string, item_index: number | null, mode: 'agenda' | 'explore' = 'agenda'): Target => ({
  slide_key, host_filter: null,
  agenda: item_index == null ? null : { agenda_id: 4, version: 2, mode, item_index },
});

describe('agendas in the browser', () => {
  it('an item covers exactly the slides the server tagged with its anchor', () => {
    expect(itemPages(SLIDES, 'section:F-07:recommendation')).toEqual(['f:F-07:recommendation:1', 'f:F-07:recommendation:2']);
    expect(itemPages(SLIDES, 'finding:F-07')).toHaveLength(5);
  });

  it('Next walks the item, then the next item; Previous comes back to the last page', () => {
    expect(stepTarget(SLIDES, at('f:F-07:hosts:1', 0), AGENDA, 1)).toEqual(at('overview:1', 1));
    expect(stepTarget(SLIDES, at('overview:1', 1), AGENDA, 1)).toEqual(at('f:F-07:recommendation:1', 2));
    expect(stepTarget(SLIDES, at('f:F-07:recommendation:1', 2), AGENDA, 1)).toEqual(at('f:F-07:recommendation:2', 2));
    expect(stepTarget(SLIDES, at('overview:1', 1), AGENDA, -1)).toEqual(at('f:F-07:hosts:1', 0));
    expect(stepTarget(SLIDES, at('scope:1', 3), AGENDA, 1)).toBeNull();                 // the end
  });

  it('on item 3 (F-07 recommendation) Next is item 4 — not F-07\'s next page — for any driver', () => {
    // The state names the ACTIVE item: the same slide as item 1 would go on to F-07's endpoints.
    expect(stepTarget(SLIDES, at('f:F-07:recommendation:2', 2), AGENDA, 1)).toEqual(at('scope:1', 3));
    expect(stepTarget(SLIDES, at('f:F-07:recommendation:2', 0), AGENDA, 1)).toEqual(at('f:F-07:hosts:1', 0));
  });

  it('a slide picked outside the item explores, the item frozen, and Back returns to it', () => {
    const explored = pickSlide(SLIDES, at('overview:1', 1), AGENDA, 'f:F-08');
    expect(explored).toEqual(at('f:F-08', 1, 'explore'));
    // Exploring walks the full list and never moves the index.
    expect(stepTarget(SLIDES, explored, AGENDA, -1)).toEqual(at('f:F-07:hosts:1', 1, 'explore'));
    expect(backToAgenda(SLIDES, explored, AGENDA)).toEqual(at('overview:1', 1));
    // A slide of the active item keeps following.
    expect(pickSlide(SLIDES, at('f:F-07', 0), AGENDA, 'f:F-07:impact:1')).toEqual(at('f:F-07:impact:1', 0));
  });

  it('a filter change landing outside the item explores; inside it keeps following', () => {
    const dmz = { kind: 'site' as const, value: 'DMZ' };
    expect(withFilterLanding(SLIDES, at('f:F-07:recommendation:1', 2), AGENDA, 'f:F-07:hosts:1', dmz))
      .toEqual({ ...at('f:F-07:hosts:1', 2, 'explore'), host_filter: dmz });
    expect(withFilterLanding(SLIDES, at('f:F-07:impact:1', 0), AGENDA, 'f:F-07:hosts:1', dmz))
      .toEqual({ ...at('f:F-07:hosts:1', 0), host_filter: dmz });
  });

  it('a newer version keeps the slide in the item holding it, else goes to that place\'s item', () => {
    const v3: PinnedAgenda = { id: 4, version: 3, items: ['scope', 'section:F-07:recommendation'] };
    expect(switchAgenda(SLIDES, at('f:F-07:recommendation:2', 2), v3)).toEqual(
      { ...at('f:F-07:recommendation:2', 1), agenda: { agenda_id: 4, version: 3, mode: 'agenda', item_index: 1 } });
    expect(switchAgenda(SLIDES, at('overview:1', 1), v3)).toEqual(
      { ...at('f:F-07:recommendation:1', 1), agenda: { agenda_id: 4, version: 3, mode: 'agenda', item_index: 1 } });
    expect(pickItem(SLIDES, at('overview:1', null), AGENDA, 0)).toEqual(at('f:F-07', 0));
  });

  it('items are named from the slides, sections nested under their finding', () => {
    expect(itemLabel(SLIDES, 'finding:F-07')).toBe('F-07 · Weak TLS');
    expect(itemLabel(SLIDES, 'section:F-07:hosts')).toBe('F-07 · Affected endpoints');
    expect(anchorOptions(SLIDES).map((o) => [o.anchor, o.nested])).toEqual([
      ['overview', false], ['scope', false], ['finding:F-07', false], ['section:F-07:impact', true],
      ['section:F-07:recommendation', true], ['section:F-07:hosts', true], ['finding:F-08', false],
    ]);
  });
});
