/**
 * /test-plans/:planId/runs — execution run header + picker +
 * compare-links.  Empty state when no runs exist yet.
 *
 * 5.313.0 — no per-run Resume: a run's agent is resumed from its agent
 * session (OwningSessionLink), and no approval gates the first run.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ClipboardCheck, ExternalLink } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { ExecutionSessionHeader } from '../../components/execution/ExecutionSessionHeader';
import { ExecutionSessionPicker } from '../../components/execution/ExecutionSessionPicker';
import { ExecutionCompareLinks } from '../../components/execution/ExecutionCompareLinks';
import OwningSessionLink from '../../components/agent-sessions/OwningSessionLink';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { Button } from '../../components/ui/button';
import { Card, CardContent } from '../../components/ui/card';
import { useTestPlanContext } from './TestPlanLayout';

const RunsTab: React.FC = () => {
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const {
    plan,
    allSessions,
    sessionsLoading,
    sessionsError,
    selectedSessionId,
    setSelectedSessionId,
    openReportDialog,
  } = useTestPlanContext();

  if (!plan.latest_execution_session) {
    return (
      <div className="flex flex-col gap-sm">
        {sessionsError && (
          <Alert variant="warning">
            <AlertDescription>{sessionsError}</AlertDescription>
          </Alert>
        )}
        <Card>
          <CardContent className="p-md text-metadata text-muted-foreground">
            No execution runs yet.{' '}
            {plan.entry_count > 0 && (plan.status === 'draft' || plan.status === 'in_progress') ? (
              <span>
                Use <strong>Work with your agent</strong> on the action bar above — your agent
                opens a run when it starts testing — or export a bundle for an offline run.
              </span>
            ) : plan.entry_count === 0 ? (
              <span>Add entries to the plan first; a run works through them.</span>
            ) : null}
          </CardContent>
        </Card>
      </div>
    );
  }

  const sessionsForPicker =
    allSessions ?? (plan.latest_execution_session ? [plan.latest_execution_session] : []);
  const activeSession =
    sessionsForPicker.find((s) => s.id === selectedSessionId) ?? plan.latest_execution_session;
  const totalSessionCount = plan.execution_session_count ?? sessionsForPicker.length;
  const hasMultiple = totalSessionCount > 1;

  return (
    <div className="flex flex-col gap-sm">
      {sessionsError && (
        <Alert variant="warning">
          <AlertDescription>{sessionsError}</AlertDescription>
        </Alert>
      )}
      <ExecutionSessionHeader
        session={activeSession}
        totalSessionCount={totalSessionCount}
        actions={
          <>
            {(activeSession.status === 'active' || activeSession.status === 'paused') && (
              <OwningSessionLink agentSessionId={activeSession.agent_session_id} />
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate(`/executions/${activeSession.id}`)}
            >
              Permalink
              <ExternalLink className="size-3" aria-hidden />
            </Button>
            <Button size="sm" variant="outline" onClick={openReportDialog}>
              <ClipboardCheck className="size-4" aria-hidden /> Open report
            </Button>
            {hasPermission('admin') && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => navigate(`/feedback?test_plan_id=${plan.id}`)}
              >
                Agent feedback
              </Button>
            )}
          </>
        }
      />
      {hasMultiple && (
        <div className="flex flex-col gap-sm">
          <ExecutionSessionPicker
            sessions={sessionsForPicker}
            selectedId={activeSession.id}
            onSelect={setSelectedSessionId}
            loading={sessionsLoading}
          />
          {sessionsForPicker.length >= 2 && (
            <ExecutionCompareLinks
              activeId={activeSession.id}
              sessions={sessionsForPicker}
              planId={plan.id}
            />
          )}
        </div>
      )}
    </div>
  );
};

export default RunsTab;
