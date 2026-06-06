/**
 * /test-plans/:planId/activity — per-plan agent API call audit log.
 */
import React from 'react';
import AgentActivityLog from '../../components/AgentActivityLog';
import { useTestPlanContext } from './TestPlanLayout';

const ActivityTab: React.FC = () => {
  const { plan } = useTestPlanContext();
  return <AgentActivityLog source={{ kind: 'plan', planId: plan.id }} />;
};

export default ActivityTab;
