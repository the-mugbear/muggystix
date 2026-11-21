import { fireEvent, render, screen } from '@testing-library/react';
import React, { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { tabListKeys } from '../../utils/tabList';

const Strip: React.FC = () => {
  const [chosen, setChosen] = useState('b');
  return (
    <div role="tablist" aria-label="Views" onKeyDown={tabListKeys}>
      {['a', 'b', 'c'].map((id) => (
        <button key={id} type="button" role="tab" aria-selected={chosen === id} onClick={() => setChosen(id)}>
          {id}
        </button>
      ))}
      <button type="button" role="tab" aria-selected={false} disabled>off</button>
    </div>
  );
};

const selected = () => screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true')?.textContent;

// Review 2026-11-20, T7 — six hand-built tab strips had the roles and none of
// the keys.
describe('tabListKeys', () => {
  it('moves to the neighbouring tab and chooses it, wrapping at the ends and skipping a disabled tab', () => {
    render(<Strip />);
    const strip = screen.getByRole('tablist');
    screen.getByRole('tab', { name: 'b' }).focus();

    fireEvent.keyDown(strip, { key: 'ArrowRight' });
    expect(selected()).toBe('c');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'c' }));
    fireEvent.keyDown(strip, { key: 'ArrowRight' });     // past the end: the first, never the disabled one
    expect(selected()).toBe('a');
    fireEvent.keyDown(strip, { key: 'ArrowLeft' });
    expect(selected()).toBe('c');
    fireEvent.keyDown(strip, { key: 'Home' });
    expect(selected()).toBe('a');
    fireEvent.keyDown(strip, { key: 'End' });
    expect(selected()).toBe('c');
  });

  // The user guide's strip is links with `aria-current`, not `role="tab"`
  // buttons: the first version found no tab there and did nothing.
  it('walks a strip of links, starting from the current page', () => {
    const opened: string[] = [];
    render(
      <div role="tablist" aria-label="Sections" onKeyDown={tabListKeys}>
        {['one', 'two', 'three'].map((id) => (
          <a key={id} href={`#${id}`} aria-current={id === 'two' ? 'page' : undefined}
            onClick={(e) => { e.preventDefault(); opened.push(id); }}>
            {id}
          </a>
        ))}
      </div>,
    );
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' });   // nothing focused yet
    expect(opened).toEqual(['three']);
    expect(document.activeElement).toBe(screen.getByText('three'));
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'Home' });
    expect(opened).toEqual(['three', 'one']);
  });

  it('leaves every other key, and a modified arrow, to the page', () => {
    render(<Strip />);
    const strip = screen.getByRole('tablist');
    screen.getByRole('tab', { name: 'b' }).focus();
    fireEvent.keyDown(strip, { key: 'j' });
    fireEvent.keyDown(strip, { key: 'ArrowRight', altKey: true });
    expect(selected()).toBe('b');
  });
});
