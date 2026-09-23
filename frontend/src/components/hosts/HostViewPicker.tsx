import { useState } from 'react';
import { Check, ChevronDown, Star, Trash2 } from 'lucide-react';
import type { HostFilterView } from '../../services/api';
import type { HostFilterOptions } from '../HostFilters';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu';

/**
 * One picker for every way of asking "which hosts": All hosts, the built-in
 * views and the operator's saved views.  Choosing one REPLACES the applied
 * filters — that is what makes it a view rather than a filter toggle, and the
 * menu says so.  Editing the filters afterwards shows "<name> · Modified";
 * nothing is updated in place (the API only creates and deletes views).
 */

export interface BuiltInHostView {
  id: string;
  name: string;
  description: string;
  filters: HostFilterOptions;
}

export interface HostViewPickerProps {
  builtInViews: BuiltInHostView[];
  savedViews: HostFilterView[];
  savedViewsError: boolean;
  /** Saved view whose filters are applied unmodified. */
  activeViewId: number | null;
  /** Built-in view whose filters are exactly what is applied. */
  activeBuiltInId: string | null;
  /** Last view applied — named in the trigger once its filters were edited. */
  baseViewName: string | null;
  /** Anything applied at all (query, filters, review status). */
  hasConditions: boolean;
  /** The applied view arrived as the project default, not by the operator's choice. */
  projectDefaultApplied: boolean;
  canSetProjectDefault: boolean;
  /** The project default view's name — offered on every visit, whoever saved
   *  it (it is usually not in this user's saved list). Null when none. */
  projectDefaultName?: string | null;
  /** The project default is what is applied now, unmodified. */
  projectDefaultActive?: boolean;
  onApplyProjectDefault?: () => void;
  onAllHosts: () => void;
  onApplyBuiltIn: (view: BuiltInHostView) => void;
  onApplyView: (view: HostFilterView) => void;
  /** Re-apply the base view, discarding the edits. Absent when there is none. */
  onReset?: () => void;
  onSaveView: () => void;
  onDeleteView: (view: HostFilterView) => void;
  onToggleProjectDefault: (view: HostFilterView) => void;
}

export default function HostViewPicker({
  builtInViews,
  savedViews,
  savedViewsError,
  activeViewId,
  activeBuiltInId,
  baseViewName,
  hasConditions,
  projectDefaultApplied,
  canSetProjectDefault,
  projectDefaultName = null,
  projectDefaultActive = false,
  onApplyProjectDefault,
  onAllHosts,
  onApplyBuiltIn,
  onApplyView,
  onReset,
  onSaveView,
  onDeleteView,
  onToggleProjectDefault,
}: HostViewPickerProps) {
  const [manageOpen, setManageOpen] = useState(false);

  const activeSaved = savedViews.find((v) => v.id === activeViewId) ?? null;
  const activeBuiltIn = builtInViews.find((v) => v.id === activeBuiltInId) ?? null;
  // The project default may be a colleague's view, which is not in MY saved
  // list — it is still the view that is applied, by the name it was given.
  const current = activeSaved?.name
    ?? (activeViewId !== null || projectDefaultApplied ? baseViewName : null)
    ?? activeBuiltIn?.name
    ?? (!hasConditions ? 'All hosts' : baseViewName ? `${baseViewName} · Modified` : 'Custom filters');

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="min-w-0 max-w-[20rem]" aria-label={`View: ${current}`}>
            <span className="shrink-0 text-muted-foreground">View:</span>
            <span className="truncate">{current}</span>
            {(projectDefaultApplied || activeSaved?.is_project_default) && (
              <span
                className="inline-flex shrink-0"
                title="Project default view — applied because you arrived with no filters of your own. Choose All hosts to see everything."
              >
                <Star className="size-3 fill-current text-warning" aria-label="Project default" />
              </span>
            )}
            <ChevronDown className="size-4 shrink-0" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-[70vh] w-[22rem] overflow-y-auto">
          <DropdownMenuLabel className="text-caption font-normal text-muted-foreground">
            Choosing a view replaces the filters that are applied now.
          </DropdownMenuLabel>
          <DropdownMenuItem onSelect={onAllHosts}>
            <Check className={hasConditions ? 'size-4 opacity-0' : 'size-4'} aria-hidden />
            All hosts
          </DropdownMenuItem>
          {projectDefaultName && onApplyProjectDefault && (
            <DropdownMenuItem onSelect={onApplyProjectDefault}>
              <Check className={projectDefaultActive ? 'size-4 shrink-0' : 'size-4 shrink-0 opacity-0'} aria-hidden />
              <span className="min-w-0 flex-1 truncate">{projectDefaultName}</span>
              <span className="inline-flex shrink-0 items-center gap-xxs text-caption text-muted-foreground">
                <Star className="size-3 fill-current text-warning" aria-hidden />
                project default
              </span>
            </DropdownMenuItem>
          )}
          {onReset && baseViewName && !(projectDefaultName && baseViewName === projectDefaultName) && (
            <DropdownMenuItem onSelect={onReset}>
              <span className="size-4" aria-hidden />
              <span className="truncate">Reset to “{baseViewName}”</span>
            </DropdownMenuItem>
          )}

          <DropdownMenuSeparator />
          <DropdownMenuLabel>Built-in views</DropdownMenuLabel>
          {builtInViews.map((view) => (
            <DropdownMenuItem key={view.id} onSelect={() => onApplyBuiltIn(view)} className="items-start">
              <Check
                className={view.id === activeBuiltIn?.id ? 'mt-0.5 size-4 shrink-0' : 'mt-0.5 size-4 shrink-0 opacity-0'}
                aria-hidden
              />
              <span className="min-w-0">
                <span className="block truncate">{view.name}</span>
                <span className="block text-caption text-muted-foreground break-words">{view.description}</span>
              </span>
            </DropdownMenuItem>
          ))}

          <DropdownMenuSeparator />
          <DropdownMenuLabel>Saved views</DropdownMenuLabel>
          {savedViewsError && savedViews.length === 0 && (
            <p className="px-sm py-xs text-caption text-muted-foreground">
              Couldn't load saved views — refresh to retry.
            </p>
          )}
          {!savedViewsError && savedViews.length === 0 && (
            <p className="px-sm py-xs text-caption text-muted-foreground">
              None yet — apply some filters, then save them as a view.
            </p>
          )}
          {savedViews.map((view) => (
            <DropdownMenuItem key={view.id} onSelect={() => onApplyView(view)}>
              <Check className={view.id === activeViewId ? 'size-4 shrink-0' : 'size-4 shrink-0 opacity-0'} aria-hidden />
              <span className="min-w-0 flex-1 truncate">{view.name}</span>
              {view.is_project_default && (
                <span className="inline-flex shrink-0 items-center gap-xxs text-caption text-muted-foreground">
                  <Star className="size-3 fill-current text-warning" aria-hidden />
                  project default
                </span>
              )}
            </DropdownMenuItem>
          ))}

          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!hasConditions} onSelect={onSaveView}>
            <span className="size-4" aria-hidden />
            Save as new view…
          </DropdownMenuItem>
          <DropdownMenuItem disabled={savedViews.length === 0} onSelect={() => setManageOpen(true)}>
            <span className="size-4" aria-hidden />
            Manage saved views…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={manageOpen} onOpenChange={setManageOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Saved views</DialogTitle>
            <DialogDescription>
              {canSetProjectDefault
                ? 'Delete a view, or make one the project default — it is applied for everyone who opens Hosts with no filters of their own.'
                : 'Delete a view you no longer need.'}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <ul className="divide-y divide-border">
              {savedViews.map((view) => (
                <li key={view.id} className="flex min-w-0 items-center gap-xs py-xs">
                  <span className="min-w-0 flex-1 truncate" title={view.name}>{view.name}</span>
                  {view.is_project_default && !canSetProjectDefault && (
                    <span className="shrink-0 text-caption text-muted-foreground">project default</span>
                  )}
                  {canSetProjectDefault && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="shrink-0"
                      onClick={() => onToggleProjectDefault(view)}
                      aria-label={view.is_project_default ? 'Clear project default' : `Set "${view.name}" as project default`}
                    >
                      <Star className={view.is_project_default ? 'size-3.5 fill-current text-warning' : 'size-3.5'} aria-hidden />
                      {view.is_project_default ? 'Project default' : 'Make default'}
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="shrink-0"
                    onClick={() => onDeleteView(view)}
                    aria-label={`Delete saved view ${view.name}`}
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                    Delete
                  </Button>
                </li>
              ))}
            </ul>
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}
