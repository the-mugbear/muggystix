import * as React from 'react';
import { Command as CommandPrimitive } from 'cmdk';
import { Check, ChevronsUpDown, X, Search as SearchIcon } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Popover, PopoverContent, PopoverTrigger } from './popover';

/**
 * Combobox — searchable single + multi-select on cmdk + Popover.
 *
 * Use over <Select> when:
 *  - The option list is long enough that scanning matters (> ~10 items)
 *  - Users want type-to-filter (services, ports, scans, technologies)
 *  - The selection is multi-valued
 *
 * Single-select shape:
 *
 *   <Combobox
 *     options={osOptions}
 *     value={filters.osFilter}
 *     onChange={(v) => setOsFilter(v ?? undefined)}
 *     placeholder="Operating system"
 *   />
 *
 * Multi-select shape (the common case for HostFilters):
 *
 *   <Combobox
 *     multiple
 *     options={portOptions}
 *     values={filters.ports ?? []}
 *     onValuesChange={(vs) => setPorts(vs.length ? vs : undefined)}
 *     placeholder="Ports"
 *   />
 */

export interface ComboboxOption {
  value: string;
  label: string;
  /** Optional caption rendered under the label in the dropdown row. */
  description?: string;
  /** Optional element rendered to the left of the label (e.g. icon). */
  leading?: React.ReactNode;
  /** Optional text shown at the row's trailing edge (e.g. count). */
  trailing?: React.ReactNode;
  /** Optional keywords added to the cmdk match haystack. */
  keywords?: string[];
}

type SharedProps = {
  options: ComboboxOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  contentClassName?: string;
  id?: string;
  'aria-label'?: string;
  'aria-labelledby'?: string;
};

type SingleProps = SharedProps & {
  multiple?: false;
  value?: string | null;
  onChange: (value: string | null) => void;
};

type MultiProps = SharedProps & {
  multiple: true;
  values: string[];
  onValuesChange: (values: string[]) => void;
  /** Cap visible chips in the trigger; rest collapse to "+N more". */
  maxVisibleChips?: number;
};

export type ComboboxProps = SingleProps | MultiProps;

export const Combobox = React.forwardRef<HTMLDivElement, ComboboxProps>((props, ref) => {
  const {
    options,
    placeholder = 'Select…',
    searchPlaceholder = 'Search…',
    emptyMessage = 'No matches.',
    disabled,
    className,
    triggerClassName,
    contentClassName,
    id,
  } = props;
  const ariaLabel = props['aria-label'];
  // The trigger is a div[role=combobox], which a sibling `<label htmlFor>`
  // cannot name (htmlFor only names labelable form controls).  When the
  // caller gives no explicit aria name, default to a `${id}-label`
  // element so a `<Label id="${id}-label">` next to the combobox provides
  // the accessible name.  Falls back to nothing (unnamed, as before) when
  // no such element exists, so non-adopting callers don't regress.
  const ariaLabelledBy =
    props['aria-labelledby'] ?? (!ariaLabel && id ? `${id}-label` : undefined);

  const [open, setOpen] = React.useState(false);

  const optionMap = React.useMemo(() => {
    const map = new Map<string, ComboboxOption>();
    options.forEach((opt) => map.set(opt.value, opt));
    return map;
  }, [options]);

  const isMulti = props.multiple === true;
  const selectedValues: string[] = isMulti
    ? (props as MultiProps).values
    : (props as SingleProps).value
      ? [(props as SingleProps).value as string]
      : [];
  const selectedSet = React.useMemo(() => new Set(selectedValues), [selectedValues]);

  const toggle = (value: string) => {
    if (isMulti) {
      const current = (props as MultiProps).values;
      const next = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value];
      (props as MultiProps).onValuesChange(next);
    } else {
      const current = (props as SingleProps).value ?? null;
      const next = current === value ? null : value;
      (props as SingleProps).onChange(next);
      setOpen(false);
    }
  };

  const clearAll = () => {
    if (isMulti) {
      (props as MultiProps).onValuesChange([]);
    } else {
      (props as SingleProps).onChange(null);
    }
  };

  const removeOne = (value: string) => {
    if (isMulti) {
      const next = (props as MultiProps).values.filter((v) => v !== value);
      (props as MultiProps).onValuesChange(next);
    }
  };

  const removeLast = () => {
    if (isMulti && (props as MultiProps).values.length > 0) {
      const values = (props as MultiProps).values;
      (props as MultiProps).onValuesChange(values.slice(0, -1));
    }
  };

  const maxVisibleChips = isMulti ? (props as MultiProps).maxVisibleChips ?? 3 : 0;
  const visibleSelected = selectedValues.slice(0, maxVisibleChips);
  const overflowCount = Math.max(selectedValues.length - maxVisibleChips, 0);

  /**
   * Trigger key handling — the audit (beta.4) called out that the
   * combobox was mouse-only.  Real fix:
   *   - Enter / Space / ↓  -> open the popover (matches Listbox pattern)
   *   - Backspace          -> if multi-select and there's no search,
   *                            remove the last selected chip (Linear /
   *                            GitHub / Headless UI MultiSelect parity)
   * The popover's own search input handles its own arrows/Enter/Esc
   * once it's open and focused.
   */
  const handleTriggerKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      return;
    }
    if (event.key === 'Backspace' && isMulti && (props as MultiProps).values.length > 0) {
      event.preventDefault();
      removeLast();
    }
  };

  return (
    <div className={cn('relative w-full', className)}>
      {/* Selected chip list lives OUTSIDE the combobox root.  ARIA
          forbids interactive descendants (the chip-remove <button>s)
          inside a role="combobox" container, so we render the chip
          list as a sibling <ul role="list"> above the trigger.  The
          combobox itself is now purely a single keyboard widget
          (input-like trigger + popover listbox).  Single-select shows
          its label inside the trigger as before; only multi-select
          uses the external chip list. */}
      {isMulti && selectedValues.length > 0 && (
        <ul
          role="list"
          aria-label="Selected items"
          className="mb-xxs flex flex-wrap items-center gap-xxs"
        >
          {visibleSelected.map((value) => {
            const opt = optionMap.get(value);
            return (
              <li
                key={value}
                className="inline-flex max-w-[12rem] items-center gap-xxs rounded-chip bg-accent px-xs py-px text-caption text-accent-foreground"
              >
                <span className="truncate">{opt?.label ?? value}</span>
                <button
                  type="button"
                  aria-label={`Remove ${opt?.label ?? value}`}
                  disabled={disabled}
                  onClick={() => removeOne(value)}
                  className="-mr-1 inline-flex size-4 items-center justify-center rounded-sm hover:bg-accent-foreground/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
                >
                  <X className="size-3" aria-hidden />
                </button>
              </li>
            );
          })}
          {overflowCount > 0 && (
            <li className="inline-flex items-center rounded-chip bg-muted px-xs py-px text-caption text-muted-foreground">
              +{overflowCount} more
            </li>
          )}
          {!disabled && (
            <li>
              <button
                type="button"
                aria-label="Clear selection"
                onClick={clearAll}
                className="inline-flex items-center gap-xxs rounded-chip px-xs py-px text-caption text-muted-foreground hover:bg-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-3" aria-hidden />
                Clear
              </button>
            </li>
          )}
        </ul>
      )}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          {/* Trigger is a <div role="combobox"> — input-like element
              only.  Interactive descendants are forbidden here by ARIA,
              so chip-remove + clear-all controls live OUTSIDE the
              combobox (chip list above, clear-all rendered as a
              sibling button below).  Click + keyboard semantics are
              wired explicitly (handleTriggerKeyDown + Popover's
              built-in click-to-toggle). */}
          <div
            ref={ref}
            id={id}
            role="combobox"
            tabIndex={disabled ? -1 : 0}
            aria-expanded={open}
            aria-haspopup="listbox"
            aria-label={ariaLabel}
            aria-labelledby={ariaLabelledBy}
            aria-disabled={disabled || undefined}
            onKeyDown={handleTriggerKeyDown}
            className={cn(
              // v4.7.14 — min-h-10 to match the Input/Select 40px
              // density (raised in 4.7.13).  It stays `min-h` (not a
              // fixed height) because a multi-select combobox grows
              // with selected-value chips.  Pre-fix this was min-h-8
              // and sat 8px short of the Selects beside it in filter
              // rows (the /hosts "oddly spaced" report).
              'flex min-h-10 w-full cursor-pointer items-center justify-between gap-xs rounded-control border border-input bg-card px-sm py-xs text-left text-metadata text-foreground',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
              'aria-disabled:cursor-not-allowed aria-disabled:opacity-50',
              'aria-[invalid=true]:border-destructive',
              triggerClassName,
            )}
          >
            <span className="flex min-w-0 flex-1 flex-wrap items-center gap-xxs">
              {selectedValues.length === 0 && (
                <span className="text-muted-foreground">{placeholder}</span>
              )}
              {isMulti
                ? selectedValues.length > 0 && (
                    <span className="truncate text-muted-foreground">
                      {selectedValues.length} selected
                    </span>
                  )
                : selectedValues.length > 0 && (
                    <span className="truncate">
                      {optionMap.get(selectedValues[0])?.label ?? selectedValues[0]}
                    </span>
                  )}
            </span>
            <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </div>
        </PopoverTrigger>
        <PopoverContent
          className={cn('w-[var(--radix-popover-trigger-width)] p-0', contentClassName)}
          align="start"
          sideOffset={4}
        >
          <CommandPrimitive className="flex w-full flex-col" loop shouldFilter>
            <div className="flex items-center gap-xs border-b border-border px-sm">
              <SearchIcon className="size-4 text-muted-foreground" aria-hidden />
              <CommandPrimitive.Input
                placeholder={searchPlaceholder}
                className="flex h-8 w-full bg-transparent text-metadata text-foreground placeholder:text-muted-foreground focus:outline-none"
              />
            </div>
            <CommandPrimitive.List className="max-h-72 overflow-y-auto py-xxs">
              <CommandPrimitive.Empty className="px-sm py-xs text-caption text-muted-foreground">
                {emptyMessage}
              </CommandPrimitive.Empty>
              {options.map((option) => {
                const isSelected = selectedSet.has(option.value);
                return (
                  <CommandPrimitive.Item
                    key={option.value}
                    value={option.value}
                    keywords={[option.label, ...(option.keywords ?? [])]}
                    onSelect={() => toggle(option.value)}
                    className={cn(
                      'flex cursor-pointer select-none items-start gap-xs rounded-control px-sm py-xs text-metadata text-foreground',
                      'data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground',
                      'data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50',
                    )}
                  >
                    <span className="flex size-4 shrink-0 items-center justify-center pt-px">
                      {isSelected ? (
                        <Check className="size-4 text-primary" aria-hidden />
                      ) : null}
                    </span>
                    {option.leading && <span className="mt-px shrink-0">{option.leading}</span>}
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate">{option.label}</span>
                      {option.description && (
                        <span className="truncate text-caption text-muted-foreground">
                          {option.description}
                        </span>
                      )}
                    </span>
                    {option.trailing && (
                      <span className="ml-auto shrink-0 text-caption text-muted-foreground">
                        {option.trailing}
                      </span>
                    )}
                  </CommandPrimitive.Item>
                );
              })}
            </CommandPrimitive.List>
          </CommandPrimitive>
        </PopoverContent>
      </Popover>
    </div>
  );
});
Combobox.displayName = 'Combobox';
