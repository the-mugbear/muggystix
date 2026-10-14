/**
 * A short list of people edited in place — the report's assessment team and
 * its distribution list — whose ORDER is the order the report prints.
 *
 * Each row gets, when the list may be changed: a grip to drag it by (only the
 * grip is draggable, so the row's inputs select and edit as usual), Move up /
 * Move down buttons, and Remove.  The grip also takes the up and down arrow
 * keys.  A row keeps its own key through a reorder or a removal, so an input
 * never shows another row's text and the control that was pressed keeps the
 * focus — repeated presses keep moving the same row.
 *
 * The caller renders only the row's fields.
 */
import React, { useLayoutEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, GripVertical, Trash2 } from 'lucide-react';

import { cn } from '../../utils/cn';
import { moveItem } from '../../utils/reorder';
import { Button } from '../ui/button';

type Control = 'handle' | 'up' | 'down';

interface Props<T extends { name: string }> {
  items: readonly T[];
  onChange: (next: T[]) => void;
  /** What an unnamed row is called: "team member", "recipient". */
  noun: string;
  /** The list may be changed (false: the fields alone, no control at all). */
  editable: boolean;
  /** Momentarily unavailable (saving): the controls stay, inert. */
  disabled?: boolean;
  children: (item: T, index: number) => React.ReactNode;
}

/** `ids` made as long as the list: new rows get new keys, at the end. */
const fitted = (ids: number[], length: number): number[] => {
  if (ids.length >= length) return ids.slice(0, length);
  const next = ids.slice();
  let fresh = ids.reduce((max, id) => Math.max(max, id), -1) + 1;
  while (next.length < length) next.push(fresh++);
  return next;
};

function ReorderableRows<T extends { name: string }>({
  items, onChange, noun, editable, disabled = false, children,
}: Props<T>): React.ReactElement | null {
  // One key per row, moved and removed with it.  The rows have no identity of
  // their own (a person typed in has no id), and keyed by position a moved
  // row would hand its focus to whichever row took its place.
  const [storedIds, setIds] = useState<number[]>(() => items.map((_, i) => i));
  let ids = storedIds;
  if (storedIds.length !== items.length) {
    // A row was added (or the list replaced) by the caller.
    ids = fitted(storedIds, items.length);
    setIds(ids);
  }

  const [drag, setDrag] = useState<{ from: number; over: number | null } | null>(null);
  const [announcement, setAnnouncement] = useState('');

  // The control to focus once the moved row is in its new place.
  const controls = useRef(new Map<string, HTMLButtonElement>());
  const focusNext = useRef<{ id: number; control: Control } | null>(null);
  useLayoutEffect(() => {
    const wanted = focusNext.current;
    if (!wanted) return;
    focusNext.current = null;
    const pressed = controls.current.get(`${wanted.id}:${wanted.control}`);
    // At the top there is no further "up": the row's grip takes the focus
    // (it moves the row with the arrow keys).
    const target = pressed && !pressed.disabled ? pressed : controls.current.get(`${wanted.id}:handle`);
    target?.focus();
  });
  const register = (id: number, control: Control) => (el: HTMLButtonElement | null) => {
    if (el) controls.current.set(`${id}:${control}`, el);
    else controls.current.delete(`${id}:${control}`);
  };

  if (items.length === 0) return null;

  const nameOf = (item: T, index: number) => item.name.trim() || `${noun} ${index + 1}`;

  const move = (from: number, to: number, control: Control | null) => {
    if (disabled) return;
    const next = moveItem(items, from, to);
    if (next === items) return;
    focusNext.current = control ? { id: ids[from], control } : null;
    setIds(moveItem(ids, from, to));
    setAnnouncement(`${nameOf(items[from], from)} moved to position ${to + 1} of ${items.length}.`);
    onChange(next);
  };
  const remove = (index: number) => {
    setIds(ids.filter((_, j) => j !== index));
    onChange(items.filter((_, j) => j !== index));
  };

  const last = items.length - 1;
  return (
    <div className="space-y-xs">
      {items.map((item, i) => {
        const name = nameOf(item, i);
        const id = ids[i];
        // Where the dragged row will land: before this one, or after it.
        const landing = drag == null || drag.over !== i || drag.from === i ? null
          : drag.from > i ? 'before' : 'after';
        return (
          <div
            key={id}
            data-reorder-row
            className={cn(
              'flex flex-wrap items-center gap-xs rounded-control',
              drag?.from === i && 'opacity-50',
              // A line on the side the row will come in from.
              landing === 'before' && 'shadow-[0_-2px_0_0_hsl(var(--primary))]',
              landing === 'after' && 'shadow-[0_2px_0_0_hsl(var(--primary))]',
            )}
            data-dragging={drag?.from === i ? '' : undefined}
            data-drop-target={landing ?? undefined}
            onDragOver={(e) => {
              // Only a row of this list being dragged: text or a file dragged
              // over the inputs is the browser's.
              if (!drag) return;
              e.preventDefault();
              if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
              if (drag.over !== i) setDrag({ from: drag.from, over: i });
            }}
            onDrop={(e) => {
              if (!drag) return;
              e.preventDefault();
              const { from } = drag;
              setDrag(null);
              move(from, i, null);
            }}
          >
            {editable && (
              <span className="inline-flex shrink-0 items-center">
                <Button
                  ref={register(id, 'handle')}
                  type="button" variant="ghost" size="icon" disabled={disabled}
                  className="size-7 cursor-grab text-muted-foreground active:cursor-grabbing"
                  aria-label={`Reorder ${name}`}
                  title="Drag to reorder, or press the up and down arrow keys"
                  draggable={!disabled}
                  onDragStart={(e) => {
                    if (disabled) { e.preventDefault(); return; }
                    // Absent in a synthetic event; Firefox starts no drag without data.
                    const transfer = e.dataTransfer as DataTransfer | undefined;
                    if (transfer) {
                      transfer.effectAllowed = 'move';
                      transfer.setData('text/plain', name);
                      const row = e.currentTarget.closest('[data-reorder-row]');
                      if (row && typeof transfer.setDragImage === 'function') transfer.setDragImage(row, 0, 0);
                    }
                    setDrag({ from: i, over: null });
                  }}
                  onDragEnd={() => setDrag(null)}
                  onKeyDown={(e) => {
                    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
                    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
                    e.preventDefault();
                    move(i, e.key === 'ArrowUp' ? i - 1 : i + 1, 'handle');
                  }}
                >
                  <GripVertical className="size-4" aria-hidden />
                </Button>
                <Button
                  ref={register(id, 'up')}
                  type="button" variant="ghost" size="icon" className="size-7"
                  disabled={disabled || i === 0}
                  aria-label={`Move ${name} up`} title="Move up"
                  onClick={() => move(i, i - 1, 'up')}
                >
                  <ChevronUp className="size-4" aria-hidden />
                </Button>
                <Button
                  ref={register(id, 'down')}
                  type="button" variant="ghost" size="icon" className="size-7"
                  disabled={disabled || i === last}
                  aria-label={`Move ${name} down`} title="Move down"
                  onClick={() => move(i, i + 1, 'down')}
                >
                  <ChevronDown className="size-4" aria-hidden />
                </Button>
              </span>
            )}
            {children(item, i)}
            {editable && (
              <Button type="button" variant="ghost" size="icon" disabled={disabled}
                aria-label={`Remove ${item.name || noun}`}
                onClick={() => remove(i)}>
                <Trash2 className="size-4" aria-hidden />
              </Button>
            )}
          </div>
        );
      })}
      {editable && <p className="sr-only" aria-live="polite">{announcement}</p>}
    </div>
  );
}

export default ReorderableRows;
