/**
 * A Textarea that suggests project members while an `@name` is being typed.
 *
 * A drop-in for `ui/textarea` (same props, same onChange): picking a
 * suggestion writes `@username ` through the element's native value setter
 * and an `input` event, so the parent's controlled `onChange` sees it like
 * typing. ArrowUp/Down move, Enter or Tab picks, Escape closes the list (and
 * is not passed on, so it does not also cancel an edit). Options take the
 * mouse on mousedown without stealing focus from the field.
 */
import React, { useId, useRef, useState } from 'react';

import { Textarea, type TextareaProps } from './ui/textarea';
import { useProjectMembers } from '../hooks/useProjectMembers';
import { cn } from '../utils/cn';
import {
  activeMentionQuery,
  filterMentionCandidates,
  unmatchedMentionHint,
  unmatchedMentionTokens,
  type MentionCandidate,
} from '../utils/mentions';

function setNativeValue(el: HTMLTextAreaElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

export const MentionTextarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ onChange, onKeyDown, onSelect, onBlur, ...props }, ref) => {
    const members = useProjectMembers();
    const inner = useRef<HTMLTextAreaElement | null>(null);
    const [query, setQuery] = useState<{ start: number; query: string } | null>(null);
    const [active, setActive] = useState(0);
    const listId = useId();

    const suggestions: MentionCandidate[] = query ? filterMentionCandidates(members, query.query) : [];
    const open = suggestions.length > 0;
    const activeIndex = Math.min(active, Math.max(suggestions.length - 1, 0));
    const hintId = `${listId}-hint`;

    // v5.290.0 — an @word that matches no member is said BEFORE posting
    // (a mention to a non-member used to reach nobody silently). Not while
    // that word is still being typed, and not until the roster has loaded
    // (an empty roster would flag every mention).
    const text = typeof props.value === 'string' ? props.value : '';
    const hint =
      members.length > 0
        ? unmatchedMentionHint(unmatchedMentionTokens(text, members.map((m) => m.username), query?.start ?? null))
        : null;
    const describedBy = [props['aria-describedby'], hint ? hintId : null].filter(Boolean).join(' ') || undefined;

    const setRefs = (el: HTMLTextAreaElement | null) => {
      inner.current = el;
      if (typeof ref === 'function') ref(el);
      else if (ref) ref.current = el;
    };

    const refresh = (el: HTMLTextAreaElement) => {
      const next = el.selectionStart === el.selectionEnd ? activeMentionQuery(el.value, el.selectionStart) : null;
      if (next?.start === query?.start && next?.query === query?.query) return;
      setActive(0);
      setQuery(next);
    };

    const pick = (member: MentionCandidate) => {
      const el = inner.current;
      if (!el || !query) return;
      const caret = el.selectionStart;
      const inserted = `@${member.username} `;
      setNativeValue(el, el.value.slice(0, query.start) + inserted + el.value.slice(caret));
      const pos = query.start + inserted.length;
      el.setSelectionRange(pos, pos);
      el.focus();
      setQuery(null);
    };

    return (
      <div className="relative min-w-0">
        <Textarea
          ref={setRefs}
          {...props}
          aria-describedby={describedBy}
          aria-autocomplete="list"
          aria-controls={open ? listId : undefined}
          aria-activedescendant={open ? `${listId}-${activeIndex}` : undefined}
          onChange={(e) => {
            onChange?.(e);
            refresh(e.currentTarget);
          }}
          onSelect={(e) => {
            onSelect?.(e);
            refresh(e.currentTarget);
          }}
          onBlur={(e) => {
            setQuery(null);
            onBlur?.(e);
          }}
          onKeyDown={(e) => {
            if (open) {
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const step = e.key === 'ArrowDown' ? 1 : -1;
                setActive((activeIndex + step + suggestions.length) % suggestions.length);
                return;
              }
              if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault();
                pick(suggestions[activeIndex]);
                return;
              }
              if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                setQuery(null);
                return;
              }
            }
            onKeyDown?.(e);
          }}
        />
        {open && (
          <ul
            id={listId}
            role="listbox"
            aria-label="Mention a teammate"
            className="absolute left-0 top-full z-50 mt-xxs max-h-48 w-72 max-w-full overflow-y-auto rounded-control border border-border bg-popover p-xxs text-popover-foreground shadow-md"
          >
            {suggestions.map((m, i) => (
              <li
                key={m.username}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === activeIndex}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(m);
                }}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  'flex min-w-0 cursor-pointer items-baseline gap-xs rounded-sm px-xs py-xxs text-metadata',
                  i === activeIndex && 'bg-accent text-accent-foreground',
                )}
              >
                <span className="min-w-0 truncate font-medium">@{m.username}</span>
                {m.full_name && (
                  <span className="min-w-0 truncate text-caption text-muted-foreground">{m.full_name}</span>
                )}
              </li>
            ))}
          </ul>
        )}
        {hint && (
          <p id={hintId} aria-live="polite" className="mt-xxs min-w-0 break-words text-caption text-warning">
            {hint}
          </p>
        )}
      </div>
    );
  },
);
MentionTextarea.displayName = 'MentionTextarea';

export default MentionTextarea;
