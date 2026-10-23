/**
 * The one sentence about the project's agent sessions (5.329.0).  Agent
 * Sessions' lead and the Operations line both print it, so the two pages
 * cannot say different things about the same sessions — Operations used to
 * print the stored status, where a session whose key had run out read
 * "active" beside "Resumable" on the other page.
 *
 * Review 2026-10-10, R1e: whether a session is live, resumable or over is the
 * row's `key_state`, decided by the server on its clock.  The page words it
 * and compares no dates — the fixtures below carry dates that DISAGREE with
 * their `key_state` to prove the browser's clock is not read.
 */
import { describe, expect, it } from 'vitest';

import type { AgentSessionRow } from '../../services/api';
import { keyState, liveSessionsSummary } from '../../utils/agentRuns';

// Dates in the past, whatever `key_state` says: only `key_state` may decide.
const PAST = '2001-01-01T00:00:00Z';
const row = (over: Partial<AgentSessionRow> = {}): AgentSessionRow => ({
  id: 1, project_id: 1, status: 'active', key_state: 'live',
  key_expires_at: PAST, renewable_until: PAST,
  ...over,
} as AgentSessionRow);
const lapsed = (id: number) => row({ id, key_state: 'resumable' });

describe('liveSessionsSummary', () => {
  it('no active session', () => {
    expect(liveSessionsSummary([])).toEqual({
      connected: 0, resumable: 0, waiting: false, text: 'No agent session is live on this project.',
    });
  });

  it('counts a session live when the server says its key works', () => {
    expect(liveSessionsSummary([row(), row({ id: 2 })]).text).toBe('2 sessions live now.');
    expect(liveSessionsSummary([row()]).text).toBe('1 session live now.');
  });

  it('a key that ran out inside the session’s lifetime waits to be resumed — it is not live', () => {
    const one = liveSessionsSummary([row(), lapsed(2)]);
    expect(one).toMatchObject({ connected: 1, resumable: 1, waiting: true });
    expect(one.text).toBe('1 session live now; 1 more waiting to be resumed (the key ran out, the session did not).');
    // "2 more", never "2 mores".
    expect(liveSessionsSummary([lapsed(2), lapsed(3)]).text)
      .toBe('0 sessions live now; 2 more waiting to be resumed (the key ran out, the session did not).');
  });

  // 5.330.0 — Operations passes the reader's own sessions: the same counting,
  // worded as theirs.  Agent Sessions' team-wide wording (above) is unchanged.
  it('`mine` words the same counts as the reader’s own', () => {
    expect(liveSessionsSummary([], { mine: true }).text).toBe('You have no agent session live on this project.');
    expect(liveSessionsSummary([row(), row({ id: 2 })], { mine: true }).text).toBe('2 sessions of yours live now.');
    const one = liveSessionsSummary([row(), lapsed(2)], { mine: true });
    expect(one).toMatchObject({ connected: 1, resumable: 1, waiting: true });
    expect(one.text).toBe('1 session of yours live now; 1 more waiting to be resumed (the key ran out, the session did not).');
  });

  it('an active row past its lifetime is neither live nor resumable', () => {
    const over = row({ key_state: 'ended' });
    expect(liveSessionsSummary([over])).toMatchObject({ connected: 0, resumable: 0, waiting: false });
    // 5.383.0 — what an active session that nothing can act on any more
    // reads as, whenever it was started.
    expect(keyState(over)).toEqual({ text: 'key expired · past its lifetime', tone: 'muted' });
    const bare = row({ key_state: 'ended', key_expires_at: null });
    expect(keyState(bare)).toEqual({ text: 'key expired · past its lifetime', tone: 'muted' });
    expect(keyState(row({ status: 'ended', key_state: 'ended' }))).toBeNull();
  });

  it('words a resumable session whose key was revoked apart from one whose key ran out', () => {
    expect(keyState(row({ key_state: 'resumable', key_expires_at: null }))?.text).toMatch(/^key revoked · resumable until/);
    expect(keyState(row({ key_state: 'resumable' }))?.text).toMatch(/^key expired · resumable until/);
    expect(keyState(row())?.tone).toBe('ok');
  });
});
