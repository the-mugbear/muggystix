/**
 * The filter row every list page uses (v5.294.0, UX review).
 *
 * Hosts had a query bar and chips, Findings labelled form fields on two rows,
 * Scans unlabelled dropdowns, Names upper-case count chips, Runs chips AND a
 * toggle. One pattern now — Scans' (v5.270.0), which was already the cleanest:
 *
 *   [search] [select] [select] …                          <summary>
 *
 * on one wrapping row over a thin rule. Selects are unlabelled `h-8` triggers
 * whose first option names the dimension ("All statuses", "Any owner") and
 * carry an `aria-label`; the summary is the count of what is listed. Hosts
 * keeps its query language — it is a DSL, not a filter row — but its own row
 * uses the same control height.
 */
import React from 'react';
import { Search } from 'lucide-react';

import { Input } from './ui/input';
import { cn } from '../utils/cn';

export interface ListFilterBarProps {
  children: React.ReactNode;
  /** Right-aligned: how much is listed ("53 findings", "6 of 6 shown"). */
  summary?: React.ReactNode;
  className?: string;
}

export const ListFilterBar: React.FC<ListFilterBarProps> = ({ children, summary, className }) => (
  <div className={cn('mb-xs flex min-w-0 flex-wrap items-center gap-sm border-b border-border pb-sm', className)}>
    {children}
    {summary != null && (
      <span className="ml-auto min-w-0 truncate text-caption text-muted-foreground">{summary}</span>
    )}
  </div>
);

export interface ListFilterSearchProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** Accessible name; the placeholder is not one. */
  label: string;
  className?: string;
  inputRef?: React.Ref<HTMLInputElement>;
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>;
}

export const ListFilterSearch: React.FC<ListFilterSearchProps> = ({
  value, onChange, placeholder, label, className, inputRef, onKeyDown,
}) => (
  <div className={cn('relative w-64 min-w-0', className)}>
    <Search
      className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      aria-hidden
    />
    <Input
      ref={inputRef}
      type="search"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
      placeholder={placeholder}
      aria-label={label}
      className="h-8 pl-xl text-metadata"
    />
  </div>
);

/** The class a filter `SelectTrigger` takes inside the bar (width at the call site). */
export const FILTER_TRIGGER_CLASS = 'h-8 min-w-0 text-metadata';

export default ListFilterBar;
