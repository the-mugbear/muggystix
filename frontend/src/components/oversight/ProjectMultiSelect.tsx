/**
 * Oversight's project filter: choose any subset of projects, and every figure
 * on the page covers exactly those (none chosen = every project).
 *
 * The choice is a draft until Apply — one dashboard load per decision, not
 * one per checkbox. Search narrows the list by name; "Select all" / "Clear"
 * act on the projects the search shows.
 */
import React, { useMemo, useState } from 'react';
import { ChevronsUpDown } from 'lucide-react';

import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { formatStatusLabel } from '../../utils/statusMeta';

export interface ProjectOption {
  id: number;
  name: string;
  status?: string | null;
}

interface Props {
  options: ProjectOption[];
  /** The applied subset; empty = every project. */
  value: number[];
  onChange: (ids: number[]) => void;
  disabled?: boolean;
}

export const triggerLabel = (value: readonly number[], options: readonly ProjectOption[]): string => {
  const known = options.length ? value.filter((id) => options.some((o) => o.id === id)) : value;
  if (known.length === 0) return options.length ? `All projects (${options.length})` : 'All projects';
  if (known.length === 1) return options.find((o) => o.id === known[0])?.name ?? '1 project';
  return options.length ? `${known.length} of ${options.length} projects` : `${known.length} projects`;
};

const ProjectMultiSelect: React.FC<Props> = ({ options, value, onChange, disabled }) => {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Set<number>>(new Set(value));
  const [search, setSearch] = useState('');

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? options.filter((o) => o.name.toLowerCase().includes(q)) : options;
  }, [options, search]);

  const onOpenChange = (next: boolean) => {
    if (next) {
      setDraft(new Set(value));
      setSearch('');
    }
    setOpen(next);
  };

  const toggle = (id: number) => setDraft((d) => {
    const next = new Set(d);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const apply = () => {
    // Every project chosen is the same as none: the whole programme.
    const ids = draft.size >= options.length ? [] : [...draft].sort((a, b) => a - b);
    onChange(ids);
    setOpen(false);
  };

  const label = triggerLabel(value, options);

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="h-9 w-56 justify-between font-normal"
          aria-label={`Projects: ${label}`}
          disabled={disabled}
        >
          <span className="min-w-0 truncate" title={label}>{label}</span>
          <ChevronsUpDown className="size-3.5 shrink-0 opacity-60" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" aria-label="Choose projects">
        <div className="space-y-xs border-b border-border p-xs">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search projects…"
            aria-label="Search projects"
            className="h-8"
          />
          <div className="flex items-center justify-between gap-xs text-caption text-muted-foreground">
            <span className="tabular-nums">{draft.size} of {options.length} selected</span>
            <span className="flex gap-xxs">
              <Button
                type="button" size="sm" variant="ghost" className="h-6 px-xs"
                disabled={visible.length === 0}
                onClick={() => setDraft((d) => new Set([...d, ...visible.map((o) => o.id)]))}
              >
                Select all{search.trim() ? ' shown' : ''}
              </Button>
              <Button
                type="button" size="sm" variant="ghost" className="h-6 px-xs"
                disabled={draft.size === 0}
                onClick={() => setDraft((d) => {
                  if (!search.trim()) return new Set();
                  const next = new Set(d);
                  visible.forEach((o) => next.delete(o.id));
                  return next;
                })}
              >
                Clear{search.trim() ? ' shown' : ''}
              </Button>
            </span>
          </div>
        </div>
        <ul className="max-h-72 overflow-y-auto p-xxs" aria-label="Projects">
          {options.length === 0 ? (
            <li className="px-xs py-sm text-caption text-muted-foreground">No projects are registered.</li>
          ) : visible.length === 0 ? (
            <li className="px-xs py-sm text-caption text-muted-foreground">No project matches “{search.trim()}”.</li>
          ) : visible.map((o) => {
            const id = `oversight-project-${o.id}`;
            return (
              <li key={o.id}>
                <label htmlFor={id} className="flex min-w-0 cursor-pointer items-center gap-xs rounded-sm px-xs py-xxs hover:bg-accent">
                  <Checkbox id={id} checked={draft.has(o.id)} onCheckedChange={() => toggle(o.id)} />
                  <span className="min-w-0 flex-1 truncate text-metadata" title={o.name}>{o.name}</span>
                  {o.status && <span className="shrink-0 text-caption text-muted-foreground">{formatStatusLabel(o.status)}</span>}
                </label>
              </li>
            );
          })}
        </ul>
        <div className="flex items-center justify-between gap-xs border-t border-border p-xs">
          <span className="min-w-0 text-caption text-muted-foreground">
            {draft.size === 0 ? 'None selected = every project' : ''}
          </span>
          <span className="flex shrink-0 gap-xxs">
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="button" size="sm" onClick={apply}>Apply</Button>
          </span>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default ProjectMultiSelect;
