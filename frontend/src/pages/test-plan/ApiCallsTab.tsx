/**
 * /test-plans/:planId/api-calls — write-mode preset over the same
 * agent API call audit AgentActivityLog renders for /activity.  The
 * default Method filter is `POST`, which surfaces the agent's plan
 * mutations (status changes, findings updates, session creates).  The
 * full Method select stays usable so you can flip to PATCH / PUT /
 * DELETE without leaving the tab.
 *
 * Why split from /activity: the broader audit log includes GET-only
 * read traffic that's noise when you're auditing what the agent
 * *changed*.  Reviewers triaging an off-rails run want to see writes
 * first; the /activity tab is still there when they need the broader
 * view.
 */
import React from 'react';
import AgentActivityLog from '../../components/AgentActivityLog';
import { useTestPlanContext } from './TestPlanLayout';

const ApiCallsTab: React.FC = () => {
  const { plan } = useTestPlanContext();
  return (
    <AgentActivityLog
      source={{ kind: 'plan', planId: plan.id }}
      title="Agent write requests"
      subtitle="Write requests (POST / PATCH / PUT / DELETE) the agent made for this plan. Switch the Method filter to inspect a different verb, or pop back to the Agent activity tab for the full read + write log."
      defaultMethodFilter="POST"
    />
  );
};

export default ApiCallsTab;
