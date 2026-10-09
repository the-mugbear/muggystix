/**
 * The jump bar of a long page: one entry per section that renders something,
 * a click scrolls to it, and the section in view is marked.  A few sections
 * are buttons; many are one picker.  A jump is linkable through the address's
 * `#id` unless the page opts out.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SectionJumpBar, {
  JUMP_PICKER_ABOVE, JumpEntry, SectionJumpBarProps, jumpTargetStyle, sectionId,
} from '../../components/SectionJumpBar';

const scrollIntoView = vi.fn();
Element.prototype.scrollIntoView = scrollIntoView;

// cmdk measures its list; jsdom has no ResizeObserver.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

type Callback = (entries: Array<{ target: Element; isIntersecting: boolean }>) => void;
let observers: Array<{ callback: Callback; options?: IntersectionObserverInit; observed: Element[] }> = [];

beforeEach(() => {
  vi.clearAllMocks();
  observers = [];
  window.history.replaceState(null, '', '/');
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
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});

const ENTRIES: JumpEntry[] = [
  { id: 'sec-proposals', label: 'Proposals', count: '2' },
  { id: 'sec-hosts', label: 'Affected hosts', count: '151' },
  { id: 'sec-evidence', label: 'Test evidence' },
  { id: 'sec-text', label: 'Report text', count: '2 empty' },
  { id: 'sec-history', label: 'Disposition history' },
];

/** Thirty tools, as on "What BlueStick reads". */
const MANY: JumpEntry[] = Array.from({ length: 30 }, (_, i) => ({
  id: `tool-t${i + 1}`, label: i === 6 ? 'Nuclei' : `Tool ${i + 1}`, count: String(i + 1),
}));

type PageProps = { filled: string[]; entries?: JumpEntry[] } & Omit<SectionJumpBarProps, 'entries'>;
const Page: React.FC<PageProps> = ({ filled, entries = ENTRIES, ...bar }) => (
  <div>
    <SectionJumpBar entries={entries} {...bar} />
    {entries.map((e) => (
      <div key={e.id} id={e.id} style={jumpTargetStyle}>
        {filled.includes(e.id) && <section>{e.label} content</section>}
      </div>
    ))}
  </div>
);

const names = () => screen.getAllByRole('button').map((b) => b.textContent);
const allOf = (entries: JumpEntry[]) => entries.map((e) => e.id);

describe('SectionJumpBar — a few sections are buttons', () => {
  it('lists the sections that render something, in page order, with their counts', () => {
    render(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} label="Sections of this finding" />);
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

describe('SectionJumpBar — many sections are one picker', () => {
  it('up to the threshold is buttons, one more is the picker', () => {
    const at = MANY.slice(0, JUMP_PICKER_ABOVE);
    const { unmount } = render(<Page entries={at} filled={allOf(at)} />);
    expect(screen.getByRole('navigation')).toHaveAttribute('data-presentation', 'buttons');
    expect(screen.getAllByRole('button')).toHaveLength(JUMP_PICKER_ABOVE);
    unmount();

    const over = MANY.slice(0, JUMP_PICKER_ABOVE + 1);
    render(<Page entries={over} filled={allOf(over)} />);
    expect(screen.getByRole('navigation')).toHaveAttribute('data-presentation', 'picker');
    // One control, not one button per section.
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.getByRole('combobox', { name: 'Jump to a section' })).toHaveTextContent('Jump to…');
  });

  it('a page can fix the presentation, so the control keeps its shape under the page’s own filter', () => {
    const { unmount } = render(<Page filled={['sec-hosts', 'sec-history']} presentation="picker" />);
    expect(screen.getByRole('combobox', { name: 'Jump to a section' })).toBeInTheDocument();
    unmount();
    render(<Page entries={MANY} filled={allOf(MANY)} presentation="buttons" />);
    expect(screen.getAllByRole('button')).toHaveLength(30);
  });

  it('filters by typing and jumps on choose', async () => {
    const user = userEvent.setup();
    render(<Page entries={MANY} filled={allOf(MANY)} />);
    await user.click(screen.getByRole('combobox', { name: 'Jump to a section' }));
    const list = await screen.findByRole('listbox');
    expect(within(list).getAllByRole('option')).toHaveLength(30);
    await user.type(screen.getByPlaceholderText('Find a section…'), 'nucl');
    await waitFor(() => expect(within(screen.getByRole('listbox')).getAllByRole('option')).toHaveLength(1));
    // The entry carries the count the page knows.
    expect(within(screen.getByRole('listbox')).getByRole('option')).toHaveTextContent('Nuclei7');
    await user.click(within(screen.getByRole('listbox')).getByRole('option'));

    const targets = scrollIntoView.mock.instances;
    expect(targets[targets.length - 1]).toBe(document.getElementById('tool-t7'));
    expect(scrollIntoView).toHaveBeenLastCalledWith({ block: 'start' });
    // The picker closes and shows where the reader is.
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
    expect(screen.getByRole('combobox', { name: 'Jump to a section' })).toHaveTextContent('Nuclei');
  });

  it('shows the section in view', () => {
    render(<Page entries={MANY} filled={allOf(MANY)} />);
    const [observer] = observers.filter((o) => o.observed.length > 0);
    act(() => observer.callback([{ target: document.getElementById('tool-t3')!, isIntersecting: true }]));
    expect(screen.getByRole('combobox', { name: 'Jump to a section' })).toHaveTextContent('Tool 3');
  });

  it('lists only the sections that render something', async () => {
    const user = userEvent.setup();
    render(<Page entries={MANY} filled={allOf(MANY.slice(0, 12))} />);
    await user.click(screen.getByRole('combobox', { name: 'Jump to a section' }));
    expect(within(await screen.findByRole('listbox')).getAllByRole('option')).toHaveLength(12);
  });
});

describe('SectionJumpBar — a section can be linked', () => {
  it('a jump writes the section to the address without adding a history entry', () => {
    const before = window.history.length;
    window.history.replaceState({ idx: 4, key: 'router' }, '', '/reference/tool-coverage?q=smb');
    render(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} />);
    fireEvent.click(screen.getByRole('button', { name: /Report text/ }));
    expect(window.location.hash).toBe('#sec-text');
    // The page's own query is kept, and so is the router's state.
    expect(window.location.search).toBe('?q=smb');
    expect(window.history.state).toEqual({ idx: 4, key: 'router' });
    fireEvent.click(screen.getByRole('button', { name: /Disposition history/ }));
    expect(window.location.hash).toBe('#sec-history');
    expect(window.history.length).toBe(before);
  });

  it('a page opened with a section in the address lands on it, once', () => {
    window.history.replaceState(null, '', '/reference/mcp#sec-text');
    const { rerender } = render(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} />);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.instances[0]).toBe(document.getElementById('sec-text'));
    expect(screen.getByRole('button', { name: /Report text/ })).toHaveAttribute('aria-current', 'location');
    // Sections coming and going later do not pull the reader back.
    rerender(<Page filled={['sec-proposals', 'sec-hosts', 'sec-text', 'sec-history']} />);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it('waits for a section whose data arrives after the page', async () => {
    window.history.replaceState(null, '', '/reference/mcp#sec-evidence');
    const { rerender } = render(<Page filled={['sec-hosts', 'sec-history']} />);
    expect(scrollIntoView).not.toHaveBeenCalled();
    rerender(<Page filled={['sec-hosts', 'sec-evidence', 'sec-history']} />);
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(scrollIntoView.mock.instances[0]).toBe(document.getElementById('sec-evidence'));
  });

  it('an id that is no section of the page is ignored, and a later jump is not mistaken for a link', async () => {
    window.history.replaceState(null, '', '/reference/mcp#not-a-section');
    const { rerender } = render(<Page filled={['sec-hosts', 'sec-history']} />);
    expect(scrollIntoView).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Disposition history/ }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    rerender(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} />);
    await waitFor(() => expect(names()).toHaveLength(3));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it('a page with its own deep link opts out: no address written, none read', () => {
    window.history.replaceState(null, '', '/findings/7?endpoint=41#sec-text');
    render(<Page filled={['sec-hosts', 'sec-text', 'sec-history']} hash={false} />);
    expect(scrollIntoView).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Disposition history/ }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(`${window.location.search}${window.location.hash}`).toBe('?endpoint=41#sec-text');
  });
});

describe('sectionId', () => {
  it('makes a stable element id from a heading', () => {
    expect(sectionId('SMB / NetBIOS', 'category')).toBe('category-smb-netbios');
    expect(sectionId('Web Content Discovery')).toBe('section-web-content-discovery');
    expect(sectionId('  ???  ')).toBe('section-untitled');
  });
});
