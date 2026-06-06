import React from 'react';
import { ExecutionSessionSummary } from '../../services/api/test-plans';
import { Badge } from '../ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

export interface ExecutionSessionPickerProps {
  sessions: ExecutionSessionSummary[];
  selectedId: number | null;
  onSelect: (sessionId: number) => void;
  loading?: boolean;
  label?: string;
}

export const ExecutionSessionPicker: React.FC<ExecutionSessionPickerProps> = ({
  sessions,
  selectedId,
  onSelect,
  loading = false,
  label,
}) => {
  if (sessions.length <= 1) return null;

  const headerText =
    label ?? `Pick a session to view per-entry results from ${loading ? '(loading…)' : 'a specific run'}:`;

  return (
    <div>
      <p className="mb-xs text-caption text-muted-foreground">{headerText}</p>
      <div className="flex flex-wrap gap-xs" role="group" aria-label="Execution session picker">
        {sessions.map((s) => {
          const isActive = s.id === selectedId;
          const labelParts: string[] = [`#${s.id}`];
          if (s.generated_by_model) labelParts.push(s.generated_by_model);
          else if (s.agent_name) labelParts.push(s.agent_name);
          if (s.started_by_username) labelParts.push(s.started_by_username);
          const tooltipText = [
            s.started_at && `Started ${new Date(s.started_at).toLocaleString()}`,
            s.generated_by_tool && `Tool: ${s.generated_by_tool}`,
            s.environment_os_family && `Host: ${s.environment_os_family}`,
          ]
            .filter(Boolean)
            .join(' · ');
          return (
            <Tooltip key={s.id}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => onSelect(s.id)}
                  className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Badge variant={isActive ? 'default' : 'outline'} className="cursor-pointer">
                    {labelParts.join(' · ')}
                  </Badge>
                </button>
              </TooltipTrigger>
              {tooltipText && <TooltipContent>{tooltipText}</TooltipContent>}
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
};
