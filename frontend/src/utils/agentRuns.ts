import type { AgentSessionRow } from '../services/api';
import { formatTimestamp } from './relativeTime';

/** v5.288.0 — an in-progress legacy row whose session can no longer act: it
 *  will not move on its own.  Workflow state, not evidence age.  Only
 *  pre-consolidation assist rows can be in this state now (execution runs
 *  went in 5.320.0). */
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
 *  5.312.0 so the session page says exactly what the list says.)
 *  `now` is the BROWSER's clock, compared with the server's dates: a skewed
 *  clock can call a key live or lapsed a little early or late here, but the
 *  server remains the authority — a call it refuses is what the user sees. */
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

/** The filters for "the project's active sessions" — what the sentence below
 *  is built from, on Agent Sessions and on Operations alike. */
export const LIVE_SESSION_FILTERS = { kind: 'project', status: 'active', limit: 100 } as const;

export interface LiveSessionsSummary {
  /** An agent can use the session's key right now. */
  connected: number;
  /** The key ran out inside the session's lifetime: its operator can resume it. */
  resumable: number;
  /** One sentence, with its full stop. */
  text: string;
  /** Something waits on a person (a session to resume). */
  waiting: boolean;
}

/**
 * What is live and what waits on someone, as ONE sentence (5.329.0): Agent
 * Sessions' lead and the Operations line both print this, so the two pages
 * cannot disagree — Operations used to list the stored status, where a
 * session whose key ran out read "active" beside "Resumable" on the other.
 * `active` is the project's active sessions (`LIVE_SESSION_FILTERS`) on
 * Agent Sessions, and the reader's own on Operations (`options.mine`).
 */
export const liveSessionsSummary = (
  active: AgentSessionRow[],
  now: number = Date.now(),
  /** `mine` (5.330.0): `active` is the READER'S OWN active sessions
   *  (`myActiveSessionFilters`) — Operations' line. The counting is the same;
   *  only the wording says whose they are. Agent Sessions passes nothing. */
  options: { mine?: boolean } = {},
): LiveSessionsSummary => {
  const connected = active.filter((r) => keyState(r, now)?.tone === 'ok').length;
  const resumable = active.filter((r) => keyState(r, now)?.tone === 'warn').length;
  const n = (v: number, one: string) => `${v.toLocaleString()} ${v === 1 ? one : `${one}s`}`;
  let text: string;
  if (active.length === 0) {
    text = options.mine
      ? 'You have no agent session live on this project.'
      : 'No agent session is live on this project.';
  } else {
    text = options.mine
      ? `${n(connected, 'session')} of yours live now`
      : `${n(connected, 'session')} live now`;
    if (resumable > 0) {
      text += `; ${resumable.toLocaleString()} more waiting to be resumed (the key ran out, the session did not)`;
    }
    text += '.';
  }
  return { connected, resumable, text, waiting: resumable > 0 };
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
 *  the clean exit itself: feedback (if none filed), then session end. */
export const WRAP_UP_PROMPT =
  'We are done with this BlueStick session. Wrap up now: (1) if you have filed no '
  + 'feedback in this session yet, call submit_feedback (POST /agent/feedback) with the '
  + 'friction you hit — one line per endpoint or tool where you retried, guessed, or worked '
  + 'around something; (2) call end_session (POST /agent/session/end) with a '
  + 'one-line note of what this session did. Confirm each step’s response to me.';

/** Where one session lives: the detail page for a consolidated session. */
export const agentSessionPath = (sessionId: number): string => `/agent-sessions/${sessionId}`;

/** The Agent Sessions list. */
export const SESSIONS_LIST_PATH = '/agent-activity';

/** Where a row of the sessions timeline opens. The one rule for Agent
 *  Sessions, Operations and the agent rail.
 *  5.328.0 — every row has the page: a legacy assist session is the same kind
 *  of record as a project session now (one table, one id), so its calls and
 *  notes are readable too. It returned null for those rows before. */
export const sessionRowPath = (row: AgentSessionRow): string | null =>
  agentSessionPath(row.id);

/** A session whose key still works: an agent could take a task right now.
 *  This — not the stored `status` — is what "my live sessions" means: an
 *  active session whose key ran out is resumable from Agent Sessions, but
 *  nothing is connected to it. (The list this replaced derived an `ended`
 *  status for such rows on the server.) */
export const hasLiveKey = (
  row: Pick<AgentSessionRow, 'status' | 'key_expires_at'>,
  now: number = Date.now(),
): boolean =>
  row.status === 'active'
  && row.key_expires_at != null
  && new Date(row.key_expires_at).getTime() > now;

/** The session-list filters for "this operator's sessions that may be live";
 *  the caller keeps the rows `hasLiveKey` accepts. One definition for
 *  `useMyAssistSessions` and `useAgentTask`. */
export const myActiveSessionFilters = (userId: number) =>
  ({ kind: 'project', status: 'active', user_id: userId }) as const;

/** 5.313.0 — the one-line tasks the per-object entry points hand to the
 *  operator's agent session (AgentTaskButton). There are no per-workflow keys.
 *  5.313.1 — a scan is no run: the agent reads the scope and uploads to its
 *  session. 5.320.0 — tests are proposed on hosts; there is no plan. */
export const agentInstruction = {
  scanScope: (scopeId?: number): string =>
    scopeId != null
      ? `Read scope ${scopeId} in BlueStick, run your scanners on what is in scope, and upload the output to this session.`
      : 'Read this project’s scopes in BlueStick, run your scanners on what is in scope, and upload the output to this session.',
  /** Propose tests on a fixed host list (or one host). Proposing only: the
   *  operator asks for a run separately. */
  proposeTests: (hostIds: number[], what?: string): string => {
    const base = hostIds.length > 0
      ? `Propose tests in BlueStick for these hosts only (host ids): ${hostIds.join(', ')}.`
      : 'Propose tests in BlueStick for this project’s hosts.';
    const typed = what?.trim();
    // The operator's words end as a sentence, so the next one does not run on.
    const focus = typed && !/[.!?]$/.test(typed) ? `${typed}.` : typed;
    return `${base}${focus ? ` What to test: ${focus}` : ''} Read what each host exposes and the tests `
      + 'already on it (host_tests_list) so you do not duplicate them, then use host_tests_propose: one '
      + 'test per check, each with its exact command and why it is worth running. Do not run anything yet.';
  },
  /** Propose a test that would confirm ONE scanner observation on a host
   *  (5.322.0). `vulnerability_id` links the test to the weakness, so its
   *  result is shown on it and promoting it joins the issue's finding. */
  proposeTestForObservation: (hostId: number, vulnerabilityId: number, title: string): string =>
    `Propose a test in BlueStick that would confirm or rule out this scanner observation on host ${hostId}: `
    + `"${title.replace(/"/g, "'").slice(0, 200)}" (vulnerability_id ${vulnerabilityId}). Read the observation and what the host exposes, `
    + 'check the tests already on the host (host_tests_list) so you do not duplicate one, then use '
    + `host_tests_propose with vulnerability_id ${vulnerabilityId}: the exact command, what output would confirm it, `
    + 'and why. Do not run anything yet.',
  /** Run the tests already proposed on one host and record what came back. */
  runHostTests: (hostId: number): string =>
    `Run the proposed tests on host ${hostId} in BlueStick: read them with host_tests_list, show me each `
    + 'command before you run it, record what came back with record_evidence (host_test_id), and mark each '
    + 'test done.',
  /** 5.317.0 — review a finding's write-up and write what is missing, as
   *  proposals (the finding's author accepts or rejects them). */
  reviewFinding: (findingId: number, missing: string[] = []): string => {
    const gaps = missing.length > 0 ? ` Still empty: ${missing.join(', ')}.` : '';
    // 5.318.0 — "propose improvements" produced critiques; a proposal's text
    // REPLACES the section on accept, so ask for the rewrite itself.
    return `Review finding #${findingId} in BlueStick: read its report text, hosts and evidence. For each `
      + `section that needs it, and each missing one, write the complete new section as it should read in `
      + `the client report, and propose it with propose_finding_text; accepting replaces the section word `
      + `for word. Put what you changed and why in rationale, not in the text. Leave sections that are fine `
      + `alone. Write only what the finding's data and evidence support: if a section cannot be written from `
      + `them, do not propose it or fill it with a guess or placeholder; tell me what is missing and what would `
      + `let you write it. Cite evidence you record. Do not change the finding directly.${gaps}`;
  },
};
