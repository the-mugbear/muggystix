/**
 * The finding page's jump bar: one entry per section that renders something,
 * a click scrolls to it, and the section in view is marked.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import FindingJumpBar, { JumpEntry, jumpTargetStyle } from '../../components/findings/FindingJumpBar';

const scrollIntoView = vi.fn();
Element.prototype.scrollIntoView = scrollIntoView;

type Callback = (entries: Array<{ target: Element; isIntersecting: boolean }>) => void;
let observers: Array<{ callback: Callback; options?: IntersectionObserverInit; observed: Element[] }> = [];

beforeEach(() => {
  vi.clearAllMocks();
  observers = [];
  vi.stubGlobal('IntersectionObserver', class {
    private record: (typeof observers)[number];

    constructor(callback: Callback, options?: IntersectionObserverInit) {
      this.record = { callback, options, observed: [] };
      observers.push(this.record);
    }

    observe(el: Element) { this.record.observed.push(el); }

    disconnect() { this.record.observed = []; }
  });
});
afterEach(() => { vi.unstubAllGlobals(); });

const ENTRIES: JumpEntry[] = [
  { id: 'sec-proposals', label: 'Proposals', count: '2' },
  { id: 'sec-hosts', label: 'Affected hosts', count: '151' },
  { id: 'sec-evidence', label: 'Test evidence' },
  { id: 'sec-text', label: 'Report text', count: '2 empty' },
  { id: 'sec-history', label: 'Disposition history' },
];

const Page: React.FC<{ filled: string[]; entries?: JumpEntry[] }> = ({ filled, entries = ENTRIES }) => (
  <div>
    <FindingJumpBar entries={entries} />
    {entries.map((e) => (
      <div key={e.id} id={e.id} style={jumpTargetStyle}>
        {filled.includes(e.id) && <section>{e.label} content</section>}
      </div>
    ))}
  </div>
);

const names = () => screen.getAllByRole('button').map((b) => b.textContent);

describe('FindingJumpBar', () => {
  it('lists the sections that render something, in page order, with their counts', () => {
    render(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} />);
    expect(screen.getByRole('navigation', { name: 'Sections of this finding' })).toBeInTheDocument();
    expect(names()).toEqual(['Affected hosts151', 'Report text2 empty', 'Disposition history']);
  });

  it('adds a section when it appears and drops it when it goes', async () => {
    const { rerender } = render(<Page filled={['sec-hosts', 'sec-history']} />);
    expect(names()).toEqual(['Affected hosts151', 'Disposition history']);
    rerender(<Page filled={['sec-proposals', 'sec-hosts', 'sec-evidence', 'sec-history']} />);
    await waitFor(() => expect(names()).toEqual(['Proposals2', 'Affected hosts151', 'Test evidence', 'Disposition history']));
    rerender(<Page filled={['sec-hosts', 'sec-history']} />);
    await waitFor(() => expect(names()).toEqual(['Affected hosts151', 'Disposition history']));
  });

  it('is not drawn for a single section', () => {
    render(<Page filled={['sec-hosts']} />);
    expect(screen.queryByRole('navigation')).toBeNull();
  });

  it('a click scrolls its section to the top, below the chrome and the bar', () => {
    render(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} />);
    fireEvent.click(screen.getByRole('button', { name: /Report text/ }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.instances[0]).toBe(document.getElementById('sec-text'));
    // A jump, not an animation (a smooth scroll does not finish in a background tab).
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'start' });
    // The target's scroll margin clears the fixed chrome plus the bar itself.
    expect(document.getElementById('sec-text')!.style.scrollMarginTop).toMatch(/var\(--topbar-h.*3\.75rem/);
    expect(screen.getByRole('button', { name: /Report text/ })).toHaveAttribute('aria-current', 'location');
  });

  it('marks the first section in view as the reader scrolls, without a scroll listener', () => {
    const listen = vi.spyOn(window, 'addEventListener');
    render(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} />);
    expect(listen.mock.calls.filter(([type]) => type === 'scroll')).toHaveLength(0);
    const [observer] = observers.filter((o) => o.observed.length > 0);
    expect(observer.observed.map((el) => el.id)).toEqual(['sec-hosts', 'sec-text', 'sec-history']);
    const el = (id: string) => document.getElementById(id)!;
    const current = () => screen.getAllByRole('button').filter((b) => b.getAttribute('aria-current')).map((b) => b.textContent);

    act(() => observer.callback([{ target: el('sec-hosts'), isIntersecting: true }]));
    expect(current()).toEqual(['Affected hosts151']);
    // Two sections in the band: the earlier one is where the reader is.
    act(() => observer.callback([{ target: el('sec-text'), isIntersecting: true }]));
    expect(current()).toEqual(['Affected hosts151']);
    act(() => observer.callback([{ target: el('sec-hosts'), isIntersecting: false }]));
    expect(current()).toEqual(['Report text2 empty']);
    // In a gap between sections the last one stays marked.
    act(() => observer.callback([{ target: el('sec-text'), isIntersecting: false }]));
    expect(current()).toEqual(['Report text2 empty']);
  });

  it('is pinned below the app chrome', () => {
    render(<Page filled={['sec-hosts', 'sec-history']} />);
    const bar = screen.getByRole('navigation');
    expect(bar.className).toMatch(/sticky/);
    expect(bar.style.top).toMatch(/var\(--topbar-h/);
  });
});
