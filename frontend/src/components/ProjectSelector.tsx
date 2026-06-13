import React from 'react';
import { Check, ChevronDown, Folder } from 'lucide-react';
import { useProject } from '../contexts/ProjectContext';
import { cn } from '../utils/cn';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const ProjectSelector: React.FC = () => {
  // Project settings/members live in the Settings hub (sidebar → Settings →
  // Project); the selector is for switching projects only, so it no longer
  // duplicates that link (FRX dedup — was two paths to /project-settings).
  const { projects, currentProject, selectProject, isLoading } = useProject();

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

  return (
    <div className="px-sm py-xs">
      <DropdownMenu>
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
          className="w-[var(--radix-dropdown-menu-trigger-width)] min-w-[14rem]"
        >
          {projects.map((project) => {
            const isCurrent = project.id === currentProject?.id;
            return (
              <DropdownMenuItem
                key={project.id}
                onSelect={() => {
                  if (project.id !== currentProject?.id) selectProject(project);
                }}
              >
                <span className="flex size-4 items-center justify-center">
                  {isCurrent ? (
                    <Check className="size-4 text-primary" aria-hidden />
                  ) : null}
                </span>
                <span className="truncate">{project.name}</span>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
};

export default ProjectSelector;
