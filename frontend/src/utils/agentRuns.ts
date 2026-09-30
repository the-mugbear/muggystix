import type { AgentSessionRow, SessionPhase } from '../services/api';
import { formatTimestamp } from './relativeTime';

/** v5.288.0 — an in-progress run whose session can no longer act: it will not
 *  move on its own.  Workflow state, not evidence age.  Shared by Agent Runs
 *  and Operations (5.304.0), and the backend's Blocked strip uses the same
 *  rule (`agent_session_service.runs_session_live`). */
export const isStalledRun = (row: AgentSessionRow): boolean =>
  row.kind !== 'project'
  && row.session_live === false
  && ['active', 'in_progress'].includes(row.status.toLowerCase());

export type StateTone = 'ok' | 'warn' | 'muted';
export interface StateLine { text: string; tone: StateTone }

/** v5.214.0 — one line about an active project session's key: live,
 *  lapsed-but-renewable, or gone.  "active" alone cannot tell a live agent from
 *  one that died a day ago; the key's state can.  Null for anything else, or a
 *  legacy row that carries neither date.  (Moved here from Agent Runs in
 *  5.312.0 so the session page says exactly what the list says.) */
export const keyState = (row: AgentSessionRow, now: number = Date.now()): StateLine | null => {
  if (row.kind !== 'project' || row.status !== 'active') return null;
  const exp = row.key_expires_at ? new Date(row.key_expires_at).getTime() : null;
  const cap = row.renewable_until ? new Date(row.renewable_until).getTime() : null;
  if (exp != null && exp > now) {
    return { text: `key valid until ${formatTimestamp(row.key_expires_at)}`, tone: 'ok' };
  }
  if (cap != null && cap > now) {
    return {
      text: exp == null
        ? `key revoked · resumable until ${formatTimestamp(row.renewable_until)}`
        : `key expired · resumable until ${formatTimestamp(row.renewable_until)}`,
      tone: 'warn',
    };
  }
  if (exp != null || cap != null) return { text: 'key expired · past its lifetime', tone: 'muted' };
  return null;
};

/** True while an agent can use the session right now (its key is live). */
export const agentConnected = (row: AgentSessionRow, now: number = Date.now()): boolean =>
  keyState(row, now)?.tone === 'ok';

/** v5.219.0 — how an ended project session ended and whether it said anything
 *  on the way out.  'agent' is the clean exit; the other two mean the agent
 *  never called end. */
export const endedState = (row: AgentSessionRow): StateLine | null => {
  if (row.kind !== 'project' || row.status === 'active') return null;
  const fb = row.feedback_count ?? 0;
  const fbText = fb > 0 ? `${fb} feedback` : 'no feedback';
  switch (row.end_reason) {
    case 'agent':
      return { text: `ended by agent · ${fbText}`, tone: fb > 0 ? 'ok' : 'warn' };
    case 'operator':
      return { text: `ended by operator · ${fbText}`, tone: 'warn' };
    case 'lapsed':
      return { text: `lapsed (never ended) · ${fbText}`, tone: 'warn' };
    default:
      return fb > 0 ? { text: fbText, tone: 'muted' } : null;
  }
};

/** v5.219.0 — what the operator pastes to a still-connected agent before
 *  ending from the UI. Mirrors the contract's ending steps so the agent does
 *  the clean exit itself: feedback (if none filed), phases closed, session end. */
export const WRAP_UP_PROMPT =
  'We are done with this BlueStick session. Wrap up now: (1) if you have filed no '
  + 'feedback in this session yet, call submit_feedback (POST /agent/feedback) with the '
  + 'friction you hit — one line per endpoint or tool where you retried, guessed, or worked '
  + 'around something; (2) close any execution run you have open — '
  + 'execution_complete_session; (3) call end_session (POST /agent/session/end) with a '
  + 'one-line note of what this session did. Confirm each step’s response to me.';

/** Where one session lives: the detail page for a consolidated session. */
export const agentSessionPath = (sessionId: number): string => `/agent-sessions/${sessionId}`;

/** The Agent Sessions list. */
export const SESSIONS_LIST_PATH = '/agent-activity';

/** Where a row of the sessions timeline opens, or null when it has no page:
 *  a project session opens its own page; a pre-consolidation execution or
 *  plan-generation row opens that run or plan.  A legacy assist row has no
 *  page (5.312.0 replaced it with the session page, which only project
 *  sessions have), and recon runs no longer exist (5.313.1).  The one rule
 *  for Agent Sessions, Operations and the agent rail. */
export const sessionRowPath = (row: AgentSessionRow): string | null => {
  switch (row.kind) {
    case 'project':
      return agentSessionPath(row.id);
    case 'execution':
      return `/executions/${row.id}`;
    case 'plan_generation':
      return row.test_plan_id != null ? `/test-plans/${row.test_plan_id}` : null;
    default:
      return null;
  }
};

/** The page for one piece of a session's work. */
export const phasePath = (phase: SessionPhase): string => {
  switch (phase.kind) {
    case 'execution':
      return `/executions/${phase.id}`;
    case 'plan':
    default:
      return `/test-plans/${phase.id}`;
  }
};

export const PHASE_KIND_LABEL: Record<SessionPhase['kind'], string> = {
  plan: 'Plan',
  execution: 'Execution',
};

/** 5.313.0 — the one-line tasks the per-object entry points hand to the
 *  operator's agent session (AgentTaskButton). There are no per-workflow keys:
 *  the session's agent opens the plan / execution run itself. 5.313.1 — a
 *  scan is no run: the agent reads the scope and uploads to its session. */
export const agentInstruction = {
  scanScope: (scopeId?: number): string =>
    scopeId != null
      ? `Read scope ${scopeId} in BlueStick, run your scanners on what is in scope, and upload the output to this session.`
      : 'Read this project’s scopes in BlueStick, run your scanners on what is in scope, and upload the output to this session.',
  workPlan: (planId: number): string => `Work test plan #${planId} in BlueStick.`,
  draftPlan: (hostIds?: number[], why?: string): string => {
    const base = hostIds && hostIds.length > 0
      ? `Draft a test plan in BlueStick for these hosts only (host ids): ${hostIds.join(', ')}.`
      : 'Draft a test plan in BlueStick for this project.';
    const reason = why?.trim();
    return reason ? `${base} Why these hosts: ${reason}` : base;
  },
};

/** Statuses that mean a run or plan still has work outstanding — an agent or
 *  a person to finish it. */
const OPEN_PHASE_STATUSES = new Set(['active', 'paused', 'in_progress', 'draft']);

export const isOpenPhase = (phase: SessionPhase): boolean =>
  OPEN_PHASE_STATUSES.has(phase.status.toLowerCase());
