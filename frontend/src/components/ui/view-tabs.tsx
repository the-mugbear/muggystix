/**
 * A page's VIEW switch: two or three ways of looking at one page, shown as a
 * small segmented control under its title (Findings | Scanner observations;
 * Imported scans | Ingestion results).  The caller owns which view is on —
 * normally the address — and what a switch does to it.
 *
 * Not for a hub's sub-pages (the tab strip in the shell), a filter (chips), or
 * tab panels of one record (`ui/tabs`).
 */
import { cn } from '../../utils/cn';

interface ViewTabsProps<T extends string> {
  /** What the control chooses, for assistive technology ("Findings view"). */
  label: string;
  value: T;
  options: readonly (readonly [T, string])[];
  onChange: (next: T) => void;
  className?: string;
}

export function ViewTabs<T extends string>({ label, value, options, onChange, className }: ViewTabsProps<T>) {
  return (
    <div role="tablist" aria-label={label} className={cn('inline-flex rounded-control border border-border p-[2px]', className)}>
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          onClick={() => onChange(key)}
          className={cn(
            'rounded-control px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            value === key ? 'bg-primary text-primary-foreground' : 'text-foreground hover:bg-accent',
          )}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export default ViewTabs;
