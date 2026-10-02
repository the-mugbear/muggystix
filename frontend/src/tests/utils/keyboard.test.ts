/**
 * `isPageShortcutEvent` — the one "is this key press for the page?" test
 * (branch review 2026-10-01 S1).  Radix typeahead does not stop propagation,
 * so a letter typed at a Select or a menu reached every page shortcut.
 */
import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts';
import { isPageShortcutEvent, isPopupTarget, isTextEntryTarget } from '../../utils/keyboard';

const el = (tag: string, attrs: Record<string, string> = {}, parent: Element = document.body) => {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  parent.appendChild(node);
  return node;
};
const key = (target: EventTarget | null, over: Partial<KeyboardEvent> = {}) => ({
  ctrlKey: false, metaKey: false, altKey: false, repeat: false, target, ...over,
});

afterEach(() => { document.body.innerHTML = ''; });

describe('isPageShortcutEvent', () => {
  it('is the page’s for a plain key on the page', () => {
    expect(isPageShortcutEvent(key(document.body))).toBe(true);
    expect(isPageShortcutEvent(key(el('button')))).toBe(true);
    expect(isPageShortcutEvent(key(window))).toBe(true);
    expect(isPageShortcutEvent(key(null))).toBe(true);
  });

  it('is not with a modifier', () => {
    expect(isPageShortcutEvent(key(document.body, { ctrlKey: true }))).toBe(false);
    expect(isPageShortcutEvent(key(document.body, { metaKey: true }))).toBe(false);
    expect(isPageShortcutEvent(key(document.body, { altKey: true }))).toBe(false);
  });

  it('is not on auto-repeat, unless the listener moves a cursor', () => {
    expect(isPageShortcutEvent(key(document.body, { repeat: true }))).toBe(false);
    expect(isPageShortcutEvent(key(document.body, { repeat: true }), { allowRepeat: true })).toBe(true);
  });

  it('is not where text is typed', () => {
    for (const tag of ['input', 'textarea', 'select']) expect(isPageShortcutEvent(key(el(tag)))).toBe(false);
    const editable = el('div', { contenteditable: 'true' });
    expect(isPageShortcutEvent(key(el('span', {}, editable)))).toBe(false);
    expect(isTextEntryTarget(el('span', {}, editable))).toBe(true);
  });

  it.each([
    ['a Select trigger', { role: 'combobox' }],
    ['an open list', { role: 'listbox' }],
    ['a menu', { role: 'menu' }],
    ['popper content', { 'data-radix-popper-content-wrapper': '' }],
  ])('is not while %s has the key — on it or inside it', (_name, attrs) => {
    const popup = el('div', attrs);
    expect(isPageShortcutEvent(key(popup))).toBe(false);
    expect(isPageShortcutEvent(key(el('span', {}, popup)))).toBe(false);
    expect(isPopupTarget(popup)).toBe(true);
  });

  it('is not while a dialog is open, unless the listener’s surface is one', () => {
    el('div', { role: 'dialog' });
    expect(isPageShortcutEvent(key(document.body))).toBe(false);
    expect(isPageShortcutEvent(key(document.body), { allowDialog: true })).toBe(true);
    document.body.innerHTML = '';
    el('div', { role: 'alertdialog' });
    expect(isPageShortcutEvent(key(document.body))).toBe(false);
  });
});

describe('useKeyboardShortcuts — the same test', () => {
  const press = (target: EventTarget, init: KeyboardEventInit) =>
    target.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, ...init }));

  it('`g h` typed at an open list or a Select trigger goes nowhere; on the page it navigates', () => {
    const go = vi.fn();
    renderHook(() => useKeyboardShortcuts({ 'g h': go }));
    for (const role of ['listbox', 'menu', 'combobox']) {
      const popup = el('div', { role });
      press(popup, { key: 'g' });
      press(popup, { key: 'h' });
    }
    expect(go).not.toHaveBeenCalled();
    press(window, { key: 'g' });
    press(window, { key: 'h' });
    expect(go).toHaveBeenCalledTimes(1);
  });

  it('a held key does not fire a shortcut', () => {
    const help = vi.fn();
    renderHook(() => useKeyboardShortcuts({ '?': help }));
    press(window, { key: '?', repeat: true });
    expect(help).not.toHaveBeenCalled();
    press(window, { key: '?' });
    expect(help).toHaveBeenCalledTimes(1);
  });
});
