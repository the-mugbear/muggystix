import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ExecutionSessionSummary } from '../../services/api/test-plans';
import { Button } from '../ui/button';

export interface ExecutionCompareLinksProps {
  activeId: number;
  sessions: ExecutionSessionSummary[];
  planId: number;
}

export const ExecutionCompareLinks: React.FC<ExecutionCompareLinksProps> = ({
  activeId,
  sessions,
  planId,
}) => {
  const navigate = useNavigate();
  const others = sessions.filter((s) => s.id !== activeId);
  if (others.length === 0) return null;
  const preferred = others[0];

  return (
    <div className="mt-sm flex flex-wrap items-center gap-xs">
      <span className="mr-xs text-caption text-muted-foreground">
        Compare this run with another:
      </span>
      <Button
        size="sm"
        onClick={() => navigate(`/test-plans/${planId}/compare?a=${activeId}&b=${preferred.id}`)}
      >
        Compare with #{preferred.id}
        {preferred.generated_by_model && ` · ${preferred.generated_by_model}`}
      </Button>
      {others.length > 1 && (
        <Button size="sm" variant="outline" onClick={() => navigate('/executions')}>
          Pick from all {others.length + 1}
        </Button>
      )}
    </div>
  );
};
