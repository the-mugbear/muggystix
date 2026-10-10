/**
 * The one sentence about the project's agent sessions (5.329.0).  Agent
 * Sessions' lead and the Operations line both print it, so the two pages
 * cannot say different things about the same sessions — Operations used to
 * print the stored status, where a session whose key had run out read
 * "active" beside "Resumable" on the other page.
 */
import { describe, expect, it } from 'vitest';

import type { AgentSessionRow } from '../../services/api';
import { keyState, liveSessionsSummary } from '../../utils/agentRuns';

const NOW = Date.parse('2026-10-02T12:00:00Z');
const HOUR = 3_600_000;
const row = (over: Partial<AgentSessionRow> = {}): AgentSessionRow => ({
  id: 1, project_id: 1, status: 'active',
  key_expires_at: new Date(NOW + HOUR).toISOString(),
  renewable_until: new Date(NOW + 24 * HOUR).toISOString(),
  ...over,
} as AgentSessionRow);
const lapsed = (id: number) => row({ id, key_expires_at: new Date(NOW - HOUR).toISOString() });

describe('liveSessionsSummary', () => {
  it('no active session', () => {
    expect(liveSessionsSummary([], NOW)).toEqual({
      connected: 0, resumable: 0, waiting: false, text: 'No agent session is live on this project.',
    });
  });

  it('counts a session live only while its key is valid', () => {
    expect(liveSessionsSummary([row(), row({ id: 2 })], NOW).text).toBe('2 sessions live now.');
    expect(liveSessionsSummary([row()], NOW).text).toBe('1 session live now.');
  });

  it('a key that ran out inside the session’s lifetime waits to be resumed — it is not live', () => {
    const one = liveSessionsSummary([row(), lapsed(2)], NOW);
    expect(one).toMatchObject({ connected: 1, resumable: 1, waiting: true });
    expect(one.text).toBe('1 session live now; 1 more waiting to be resumed (the key ran out, the session did not).');
    // "2 more", never "2 mores".
    expect(liveSessionsSummary([lapsed(2), lapsed(3)], NOW).text)
      .toBe('0 sessions live now; 2 more waiting to be resumed (the key ran out, the session did not).');
  });

  // 5.330.0 — Operations passes the reader's own sessions: the same counting,
  // worded as theirs.  Agent Sessions' team-wide wording (above) is unchanged.
  it('`mine` words the same counts as the reader’s own', () => {
    expect(liveSessionsSummary([], NOW, { mine: true }).text).toBe('You have no agent session live on this project.');
    expect(liveSessionsSummary([row(), row({ id: 2 })], NOW, { mine: true }).text).toBe('2 sessions of yours live now.');
    const one = liveSessionsSummary([row(), lapsed(2)], NOW, { mine: true });
    expect(one).toMatchObject({ connected: 1, resumable: 1, waiting: true });
    expect(one.text).toBe('1 session of yours live now; 1 more waiting to be resumed (the key ran out, the session did not).');
  });

  it('an active row past its lifetime is neither live nor resumable', () => {
    const over = row({
      key_expires_at: new Date(NOW - 48 * HOUR).toISOString(),
      renewable_until: new Date(NOW - HOUR).toISOString(),
    });
    expect(liveSessionsSummary([over], NOW)).toMatchObject({ connected: 0, resumable: 0, waiting: false });
    // 5.383.0 — this is what an active session that nothing can act on any
    // more reads as, whenever it was started: a row from before the
    // consolidation carried a flag (`session_live`) and a "Stalled" mark of
    // its own (`isStalledRun`), and the key's state was not read for it.
    expect(keyState(over, NOW)).toEqual({ text: 'key expired · past its lifetime', tone: 'muted' });
    // One whose key was revoked and never had a lifetime recorded either.
    const bare = row({ key_expires_at: null, renewable_until: new Date(NOW - HOUR).toISOString() });
    expect(keyState(bare, NOW)).toEqual({ text: 'key expired · past its lifetime', tone: 'muted' });
    expect(keyState(row({ status: 'ended' }), NOW)).toBeNull();
  });
});
