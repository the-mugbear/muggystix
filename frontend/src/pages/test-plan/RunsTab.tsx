/**
 * /test-plans/:planId/runs — execution session header + picker +
 * compare-links.  Empty state when no sessions exist yet.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ClipboardCheck, ExternalLink, RotateCcw } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import {
  ExecutionSessionHeader,
  isExecutionSessionStale,
} from '../../components/execution/ExecutionSessionHeader';
import { ExecutionSessionPicker } from '../../components/execution/ExecutionSessionPicker';
import { ExecutionCompareLinks } from '../../components/execution/ExecutionCompareLinks';
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
    canManage,
    handleResume,
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
            No execution sessions yet.{' '}
            {plan.status === 'approved' || plan.status === 'in_progress' ? (
              <span>Use <strong>Execute with AI</strong> on the action bar above to start one.</span>
            ) : (
              <span>Once the plan is approved you can start an execution session.</span>
            )}
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
            {canManage &&
              (activeSession.status === 'active' || activeSession.status === 'paused') && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    handleResume(
                      activeSession.id,
                      isExecutionSessionStale(activeSession) ||
                        activeSession.status === 'paused',
                    )
                  }
                >
                  <RotateCcw className="size-4" aria-hidden /> Resume
                </Button>
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
