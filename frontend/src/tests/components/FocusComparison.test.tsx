/**
 * "Where to focus" — what the reader chose, and when the choice is dropped
 * (B28).  The measure and the segment were state with two effects putting
 * them back; these pin what those effects did, so the choice can be kept
 * without them:
 *
 *  - another measure starts from ITS leading segment, and coming back to the
 *    first does too (a segment chosen under a measure is not remembered);
 *  - new data that keeps its leading measure keeps the reader's measure;
 *  - new data with another leading measure opens on that one.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({ gridCellHostsHref: () => '/hosts' }));

import FocusComparison from '../../components/posture/FocusComparison';
import type { PostureHeatmap } from '../../services/api/posture';

const cell = (segment: string, site: string, affected: number, assessed: number) => ({
  segment, affected, assessed, in_scope: assessed, eligible: assessed, eligible_assessed: assessed,
  unassessed: assessed === 0, value: assessed ? affected / assessed : 0, numerator: affected, denominator: assessed,
  drilldown_filter: { conditions: ['x'], site },
});
const SEGMENTS = [
  { key: '1', label: 'East', in_scope: 100, assessed: 100 },
  { key: '2', label: 'West', in_scope: 100, assessed: 100 },
];
const family = (key: string, label: string, east: number, west: number) => ({
  family: key, family_label: label, conditions: [key], evidence_domain: 'os_detection',
  evidence_domain_label: 'OS identification', affected_total: east + west,
  cells: [cell('1', 'East', east, 100), cell('2', 'West', west, 100)],
});
/** Lifecycle leads (East 60% against West 10%); identity is flatter. */
const LIFECYCLE_LEADS = {
  segments: SEGMENTS,
  rows: [family('lifecycle', 'Lifecycle', 60, 10), family('identity', 'Identity', 20, 30)],
} as unknown as PostureHeatmap;
/** The same shape read again: other numbers, the same leading measure. */
const LIFECYCLE_STILL_LEADS = {
  segments: SEGMENTS,
  rows: [family('lifecycle', 'Lifecycle', 61, 10), family('identity', 'Identity', 20, 30)],
} as unknown as PostureHeatmap;
/** Identity leads now (West 90% against East 20%). */
const IDENTITY_LEADS = {
  segments: SEGMENTS,
  rows: [family('lifecycle', 'Lifecycle', 12, 10), family('identity', 'Identity', 20, 90)],
} as unknown as PostureHeatmap;

const show = (heatmap: PostureHeatmap) => <MemoryRouter><FocusComparison heatmap={heatmap} /></MemoryRouter>;
const measure = (name: string) => screen.getByRole('button', { name });
const pressed = (name: string) => measure(name).getAttribute('aria-pressed');
/** The sentence under "Why it matters" starts with the selected segment. */
const sentence = () => screen.getByText(/assessed hosts affected/).textContent ?? '';

describe('FocusComparison', () => {
  it('opens on the leading measure and its leading segment', () => {
    render(show(LIFECYCLE_LEADS));
    expect(pressed('Lifecycle')).toBe('true');
    expect(pressed('Identity')).toBe('false');
    expect(sentence()).toMatch(/^East: 60 of 100/);
  });

  it('a segment is chosen under ONE measure: another measure, and coming back, start from the leading segment', () => {
    render(show(LIFECYCLE_LEADS));
    fireEvent.click(screen.getByRole('button', { name: 'West' }));
    expect(sentence()).toMatch(/^West: 10 of 100/);

    fireEvent.click(measure('Identity'));
    expect(pressed('Identity')).toBe('true');
    // Identity's own leading segment — West is first there, by its own rate.
    expect(sentence()).toMatch(/^West: 30 of 100/);
    fireEvent.click(screen.getByRole('button', { name: 'East' }));
    expect(sentence()).toMatch(/^East: 20 of 100/);

    fireEvent.click(measure('Lifecycle'));
    // Not West (chosen here before), and not East because it was chosen
    // under Identity: Lifecycle's leading segment.
    expect(sentence()).toMatch(/^East: 60 of 100/);
  });

  it('new data with the same leading measure keeps the reader’s measure', () => {
    const view = render(show(LIFECYCLE_LEADS));
    fireEvent.click(measure('Identity'));
    view.rerender(show(LIFECYCLE_STILL_LEADS));
    expect(pressed('Identity')).toBe('true');
    expect(sentence()).toMatch(/^West: 30 of 100/);
  });

  it('new data with another leading measure opens on that one — whatever was chosen', () => {
    const view = render(show(IDENTITY_LEADS));
    expect(pressed('Identity')).toBe('true');
    fireEvent.click(measure('Lifecycle'));
    expect(pressed('Lifecycle')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'West' }));

    view.rerender(show(LIFECYCLE_LEADS));
    expect(pressed('Lifecycle')).toBe('true');
    view.rerender(show(IDENTITY_LEADS));
    // The lead moved: the measure follows it, and its leading segment shows.
    expect(pressed('Identity')).toBe('true');
    expect(sentence()).toMatch(/^West: 90 of 100/);
    // …and a lead that moves back does not bring an old choice with it.
    fireEvent.click(measure('Lifecycle'));
    view.rerender(show(LIFECYCLE_LEADS));
    view.rerender(show(IDENTITY_LEADS));
    expect(pressed('Identity')).toBe('true');
  });
});
