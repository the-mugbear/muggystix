/**
 * useOpeningKey (5.362.0) — a dialog or panel whose every opening starts clean
 * is keyed by its opening, instead of setting each piece of state back from
 * an effect on `open`.
 */
import { useState } from 'react';
import { fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useOpeningKey } from '../../hooks/useOpeningKey';

describe('useOpeningKey', () => {
  it('changes when it opens, not when it closes or re-renders', () => {
    const { result, rerender } = renderHook(({ open }: { open: boolean }) => useOpeningKey(open), {
      initialProps: { open: false },
    });
    const closed = result.current;
    rerender({ open: true });
    const first = result.current;
    expect(first).not.toBe(closed);
    rerender({ open: true });
    expect(result.current).toBe(first);
    // Closing keeps the key: the body stays as it was while it goes.
    rerender({ open: false });
    expect(result.current).toBe(first);
    rerender({ open: true });
    expect(result.current).not.toBe(first);
  });

  it('another record is a new opening; the record being cleared is not', () => {
    const { result, rerender } = renderHook(({ id }: { id: number | null }) => useOpeningKey(id), {
      initialProps: { id: null as number | null },
    });
    rerender({ id: 7 });
    const seven = result.current;
    rerender({ id: 8 });
    const eight = result.current;
    expect(eight).not.toBe(seven);
    rerender({ id: null });
    expect(result.current).toBe(eight);
    // The same record again, after closing, is a new opening too.
    rerender({ id: 8 });
    expect(result.current).not.toBe(eight);
  });

  it('a body keyed by it starts from its initial state at every opening, with no render of the old state', () => {
    const seenOnOpen: string[] = [];
    const Body = ({ open }: { open: boolean }) => {
      const [text, setText] = useState('');
      if (open) seenOnOpen.push(text);
      return open ? <input aria-label="draft" value={text} onChange={(e) => setText(e.target.value)} /> : null;
    };
    const Dialog = ({ open }: { open: boolean }) => <Body key={useOpeningKey(open)} open={open} />;
    const { rerender } = render(<Dialog open />);
    fireEvent.change(screen.getByLabelText('draft'), { target: { value: 'half-typed' } });
    rerender(<Dialog open={false} />);
    seenOnOpen.length = 0;
    rerender(<Dialog open />);
    expect(screen.getByLabelText('draft')).toHaveValue('');
    // An effect-based reset renders 'half-typed' once before clearing it.
    expect(seenOnOpen).not.toContain('half-typed');
  });
});
