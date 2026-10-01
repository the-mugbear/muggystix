/**
 * useListCursor — the Hosts keyboard model on the other lists (review
 * 2026-09-23 B-UI-6): j/k and ↓/↑ move, Enter opens, typing and dialogs are
 * left alone, a new result set clears the cursor.
 */
import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

import { LIST_CURSOR_CLASS, useListCursor } from '../../hooks/useListCursor';

const press = (key: string, target: EventTarget = window) => {
  act(() => { target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true })); });
};

afterEach(() => { document.body.innerHTML = ''; });

// Walkthrough 2026-10-01 — the cursor row was `bg-accent ring-1`: `--accent`
// carries its own alpha and tailwind.config wraps it in `hsl(… / <alpha>)`, so
// the fill was an invalid colour the browser dropped and only a 1px ring was
// left.  The highlight must be built from tokens that are opaque in index.css.
describe('LIST_CURSOR_CLASS', () => {
  it('paints with opaque theme tokens and a ring wider than a hairline', () => {
    const css = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8');
    const tokens = LIST_CURSOR_CLASS.split(/\s+/)
      .map((c) => /^(?:bg|ring)-([a-z-]+?)(?:\/\d+)?$/.exec(c)?.[1])
      .filter((t): t is string => !!t && t !== 'inset');
    expect(tokens.length).toBeGreaterThanOrEqual(2);
    for (const token of tokens) {
      const value = new RegExp(`--${token}:\\s*([^;]+);`).exec(css)?.[1];
      expect(value, `--${token} is defined`).toBeTruthy();
      expect(value, `--${token} must not carry its own alpha`).not.toContain('/');
    }
    expect(LIST_CURSOR_CLASS).toMatch(/\bbg-/);
    expect(LIST_CURSOR_CLASS).toMatch(/\bring-2\b/);
  });
});

describe('useListCursor', () => {
  it('moves with j/k and the arrows, clamps at both ends, and opens with Enter', () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() => useListCursor(3, onOpen));
    expect(result.current.cursor).toBe(-1);
    press('Enter');
    expect(onOpen).not.toHaveBeenCalled();

    press('j'); press('ArrowDown'); press('j'); press('j');
    expect(result.current.cursor).toBe(2);
    press('k'); press('ArrowUp'); press('k');
    expect(result.current.cursor).toBe(0);
    press('j');
    press('Enter');
    expect(onOpen).toHaveBeenCalledWith(1);
    expect(result.current.cursorRowProps(1, 'align-top')).toMatchObject({ 'data-list-cursor': 'true' });
    expect(result.current.cursorRowProps(1, 'align-top').className).toContain('align-top');
    expect(result.current.cursorRowProps(0, 'align-top')).toEqual({ className: 'align-top' });
  });

  it('leaves typing and open dialogs alone', () => {
    const { result } = renderHook(() => useListCursor(3, vi.fn()));
    const input = document.createElement('input');
    document.body.appendChild(input);
    press('j', input);
    expect(result.current.cursor).toBe(-1);

    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    document.body.appendChild(dialog);
    press('j');
    expect(result.current.cursor).toBe(-1);
  });

  it('clears the cursor when the rows are replaced', () => {
    const { result, rerender } = renderHook(({ k }) => useListCursor(3, vi.fn(), { resetKey: k }), {
      initialProps: { k: 'page-1' },
    });
    press('j'); press('j');
    expect(result.current.cursor).toBe(1);
    rerender({ k: 'page-2' });
    expect(result.current.cursor).toBe(-1);
  });
});
