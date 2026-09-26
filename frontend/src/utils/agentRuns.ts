import type { AgentSessionRow } from '../services/api';

/** v5.288.0 — an in-progress run whose session can no longer act: it will not
 *  move on its own.  Workflow state, not evidence age.  Shared by Agent Runs
 *  and Operations (5.304.0), and the backend's Blocked strip uses the same
 *  rule (`agent_session_service.runs_session_live`). */
export const isStalledRun = (row: AgentSessionRow): boolean =>
  row.kind !== 'project'
  && row.session_live === false
  && ['active', 'in_progress'].includes(row.status.toLowerCase());
