import React, { useMemo, useState } from 'react';
import { Check, ChevronDown, Folder } from 'lucide-react';
import { useProject } from '../contexts/ProjectContext';
import { cn } from '../utils/cn';
import { projectYear, projectYears } from '../utils/projectYears';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const ALL = 'all';

const ProjectSelector: React.FC = () => {
  // Project settings/members live in the Settings hub (sidebar → Settings →
  // Project); the selector is for switching projects only, so it no longer
  // duplicates that link (FRX dedup — was two paths to /project-settings).
  const { projects, currentProject, selectProject, isLoading } = useProject();

  // The year filter (5.347.0): shown once the projects span more than one
  // year.  Until the reader picks one it follows the current project's year,
  // so the project they are in is always in the list they open.
  const years = useMemo(() => projectYears(projects), [projects]);
  const [picked, setPicked] = useState<string | null>(null);
  const currentYear = currentProject ? projectYear(currentProject) : null;
  const filtered = years.length > 1;
  const followed = currentYear != null ? String(currentYear) : ALL;
  // A picked year that no project is in any more (the last one was removed)
  // falls back, rather than listing nothing.
  const year = picked != null && (picked === ALL || years.some((y) => String(y.year) === picked)) ? picked : followed;
  const shown = filtered && year !== ALL ? projects.filter((p) => String(projectYear(p)) === year) : projects;

  if (isLoading) {
    return (
      <div className="px-md py-sm">
        <div className="h-9 w-full animate-pulse rounded-control bg-muted" />
      </div>
    );
  }

  if (projects.length === 0) {
    return (
      <div className="px-md py-sm">
        <p className="text-metadata text-muted-foreground">No projects</p>
      </div>
    );
  }

  const yearChip = 'w-auto shrink-0 rounded-full border border-border px-sm py-0 pl-sm text-caption tabular-nums '
    + 'data-[state=checked]:border-primary data-[state=checked]:bg-primary/10 data-[state=checked]:font-semibold '
    + '[&>span:first-child]:hidden';

  return (
    <div className="px-sm py-xs">
      {/* Closing the menu forgets the pick: it opens on the current project's year. */}
      <DropdownMenu onOpenChange={(open) => { if (!open) setPicked(null); }}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            id="project-selector-trigger"
            className={cn(
              'flex w-full min-w-0 items-center gap-sm rounded-control border border-border bg-card px-sm py-xs text-left shadow-raised',
              'transition-colors hover:bg-accent hover:border-primary/30',
              'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
            )}
          >
            <Folder className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="text-micro font-semibold uppercase tracking-wider text-muted-foreground">
                Project
              </div>
              <div className="truncate text-metadata font-semibold">
                {currentProject?.name ?? 'Select project'}
              </div>
            </div>
            <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          collisionPadding={8}
          // Any number of projects: the years stay put and the list scrolls
          // under them, in a menu never taller than the room under the
          // trigger (5.346.0).
          className="flex max-h-[min(28rem,var(--radix-dropdown-menu-content-available-height))] w-[var(--radix-dropdown-menu-trigger-width)] min-w-[14rem] flex-col overflow-hidden"
        >
          {filtered && (
            <DropdownMenuRadioGroup
              value={year}
              onValueChange={setPicked}
              aria-label="Projects by the year they start"
              className="flex shrink-0 flex-wrap gap-xxs border-b border-border px-xs pb-xs pt-xxs"
            >
              {/* A year is chosen without closing the menu. */}
              {years.map((y) => (
                <DropdownMenuRadioItem key={y.year} value={String(y.year)} className={yearChip}
                  title={`${y.count.toLocaleString()} project${y.count === 1 ? '' : 's'} starting in ${y.year}`}
                  onSelect={(e) => e.preventDefault()}>
                  {y.year}
                </DropdownMenuRadioItem>
              ))}
              <DropdownMenuRadioItem value={ALL} className={yearChip} onSelect={(e) => e.preventDefault()}>
                All
              </DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden" data-testid="project-list">
            {shown.map((project) => {
              const isCurrent = project.id === currentProject?.id;
              return (
                <DropdownMenuItem
                  key={project.id}
                  onSelect={() => {
                    if (project.id !== currentProject?.id) selectProject(project);
                  }}
                >
                  <span className="flex size-4 shrink-0 items-center justify-center">
                    {isCurrent ? (
                      <Check className="size-4 text-primary" aria-hidden />
                    ) : null}
                  </span>
                  <span className="min-w-0 truncate" title={project.name}>{project.name}</span>
                </DropdownMenuItem>
              );
            })}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
};

export default ProjectSelector;
