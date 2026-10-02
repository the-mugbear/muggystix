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

  // Review 2026-10-01 S1 — a Select's or a menu's typeahead owns the letters.
  it.each(['listbox', 'menu', 'combobox'])('leaves a key pressed inside a %s alone', (role) => {
    const { result } = renderHook(() => useListCursor(3, vi.fn()));
    const popup = document.createElement('div');
    popup.setAttribute('role', role);
    const inner = document.createElement('span');
    popup.appendChild(inner);
    document.body.appendChild(popup);
    press('j', inner);
    expect(result.current.cursor).toBe(-1);
    press('j');
    expect(result.current.cursor).toBe(0);
  });

  it('walks on a held j, but a held Enter opens nothing', () => {
    const onOpen = vi.fn();
    const { result } = renderHook(() => useListCursor(3, onOpen));
    const held = (key: string) => act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, repeat: true }));
    });
    held('j'); held('j');
    expect(result.current.cursor).toBe(1);
    held('Enter');
    expect(onOpen).not.toHaveBeenCalled();
  });

  // Review 2026-10-01 S2 — with `getId` the cursor follows its row.
  describe('anchored by id', () => {
    const setup = (first: number[]) => renderHook(
      ({ ids }) => useListCursor(ids.length, vi.fn(), { getId: (i) => ids[i] }),
      { initialProps: { ids: first } },
    );

    it('stays on its row when rows arrive above it', () => {
      const { result, rerender } = setup([10, 20, 30]);
      press('j'); press('j');
      expect(result.current.cursorId).toBe(20);
      rerender({ ids: [99, 98, 10, 20, 30] });
      // Already right in the render that brought the new rows.
      expect(result.current.cursor).toBe(3);
      expect(result.current.cursorId).toBe(20);
      press('j');
      expect(result.current.cursorId).toBe(30);
    });

    it('moves to the row that took its place when its row is gone, and anchors there', () => {
      const { result, rerender } = setup([10, 20, 30]);
      press('j'); press('j');
      rerender({ ids: [10, 30] });
      expect(result.current.cursorId).toBe(30);
      rerender({ ids: [99, 10, 30] });
      expect(result.current.cursorId).toBe(30);
    });

    it('finds its row again after the list was empty while it reloaded', () => {
      const { result, rerender } = setup([10, 20, 30]);
      press('j'); press('j');
      rerender({ ids: [] });
      expect(result.current.cursor).toBe(-1);
      rerender({ ids: [5, 10, 20, 30] });
      expect(result.current.cursorId).toBe(20);
    });

    it('without getId the cursor is the index, clamped to the list', () => {
      const { result, rerender } = renderHook(({ n }) => useListCursor(n, vi.fn()), { initialProps: { n: 3 } });
      press('j'); press('j'); press('j');
      rerender({ n: 2 });
      expect(result.current.cursor).toBe(1);
      expect(result.current.cursorId).toBeNull();
    });
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
