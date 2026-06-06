import React from 'react';
import { Bot, Compass, ShieldCheck, TerminalSquare } from 'lucide-react';
import HubLanding from './HubLanding';

const WorkflowsHub: React.FC = () => (
  <HubLanding
    title="Workflows"
    subtitle="Agent-coordinated work against the inventory — discovery (recon), planning (test plans), execution, and the full audit timeline of every agent session."
    links={[
      {
        label: 'Recon Runs',
        path: '/recon/runs',
        description: 'Each agent-driven discovery session — what scopes were probed, what hosts surfaced, with cross-session compare.',
        requiredRole: 'viewer',
        Icon: Compass,
      },
      {
        label: 'Test Plans',
        path: '/test-plans',
        description: 'Structured test plans drafted by agents and approved by analysts.  Per-host entries with proposed tests.',
        requiredRole: 'viewer',
        Icon: ShieldCheck,
      },
      {
        label: 'Executions',
        path: '/executions',
        description: 'Per-test results from running a plan, with sanity-check gating and side-by-side compare across executions.',
        requiredRole: 'viewer',
        Icon: TerminalSquare,
      },
      {
        label: 'Agent Runs',
        path: '/agent-activity',
        description: 'Unified timeline of every agent session — recon, plan generation, and execution — with model + tool + user attribution.',
        requiredRole: 'viewer',
        Icon: Bot,
      },
    ]}
  />
);

export default WorkflowsHub;
