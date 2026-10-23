import { describe, expect, it } from 'vitest';

import {
  activeMentionQuery,
  filterMentionCandidates,
  findMentionSpans,
  mentionOutcomeMessages,
  unmatchedMentionHint,
  unmatchedMentionTokens,
} from '../../utils/mentions';

import { twinCases } from '../helpers/twinCases';

// ONE case table with the server (backend/tests/fixtures/twins/mentions.json,
// which test_mentions_and_discussion.py runs against `scan_mentions`): what
// the composer highlights must be what the server notifies (backlog
// 2026-10-22, H-d).  Add a case there, never here.
const TWIN = twinCases<{ names: string[]; cases: Array<{ text: string; matched: string[]; unmatched: string[] }> }>(
  'mentions.json',
);
const NAMES = TWIN.names;
const CASES = TWIN.cases.map((c) => [c.text, c.matched, c.unmatched] as const);
const names = (text: string) => new Set(findMentionSpans(text, NAMES).map((s) => s.username));

describe('findMentionSpans', () => {
  it.each(CASES)('%s', (text, matched) => {
    expect(names(text)).toEqual(new Set(matched));
  });

  it('returns the exact span of each mention', () => {
    expect(findMentionSpans('hi @eval-ana.', NAMES)).toEqual([{ start: 3, end: 12, username: 'eval-ana' }]);
  });
});

describe('unmatchedMentionTokens', () => {
  it.each(CASES)('%s', (text, _matched, unmatched) => {
    expect(unmatchedMentionTokens(text, NAMES)).toEqual(unmatched);
  });

  it('skips the mention still being typed', () => {
    expect(unmatchedMentionTokens('@ghost and @gho', NAMES, 11)).toEqual(['ghost']);
  });
});

describe('mention outcome copy', () => {
  it('names who was notified and who was not', () => {
    expect(mentionOutcomeMessages({ mentions_notified: [{ username: 'eval-ana', name: 'Ana Ortiz' }] })).toEqual({
      notified: 'Notified Ana Ortiz',
      unmatched: null,
    });
    expect(mentionOutcomeMessages({ mentions_notified: [], unmatched_mentions: ['eval-ana'] })).toEqual({
      notified: null,
      unmatched: "@eval-ana isn't a member of this project — nobody was notified",
    });
    expect(
      mentionOutcomeMessages({
        mentions_notified: [
          { username: 'a', name: 'A' },
          { username: 'b', name: 'B' },
          { username: 'c', name: 'C' },
        ],
        unmatched_mentions: ['x', 'y'],
      }),
    ).toEqual({
      notified: 'Notified A, B and C',
      unmatched: "@x and @y aren't members of this project — they were not notified",
    });
    expect(mentionOutcomeMessages({})).toEqual({ notified: null, unmatched: null });
    expect(unmatchedMentionHint(['ghost'])).toBe("@ghost isn't a member of this project — they won't be notified");
    expect(unmatchedMentionHint([])).toBeNull();
  });
});

describe('activeMentionQuery', () => {
  it('finds the partial name being typed at the caret', () => {
    expect(activeMentionQuery('ping @eva', 9)).toEqual({ start: 5, query: 'eva' });
    expect(activeMentionQuery('@', 1)).toEqual({ start: 0, query: '' });
    expect(activeMentionQuery('@eval-a', 7)).toEqual({ start: 0, query: 'eval-a' });
  });

  it('is null after a space, inside an e-mail address, or with no @', () => {
    expect(activeMentionQuery('@ana done', 9)).toBeNull();
    expect(activeMentionQuery('ana@exa', 7)).toBeNull();
    expect(activeMentionQuery('plain', 5)).toBeNull();
  });
});

describe('filterMentionCandidates', () => {
  const members = [
    { username: 'eval-ana', full_name: 'Ana Ortiz' },
    { username: 'eval-ben', full_name: 'Ben Okafor' },
    { username: 'cy.lee', full_name: 'Cy Lee' },
  ];
  it('ranks username and name-word prefixes before other matches', () => {
    expect(filterMentionCandidates(members, 'ana').map((m) => m.username)).toEqual(['eval-ana']);
    expect(filterMentionCandidates(members, 'eval').map((m) => m.username)).toEqual(['eval-ana', 'eval-ben']);
    expect(filterMentionCandidates(members, 'lee').map((m) => m.username)).toEqual(['cy.lee']);
    expect(filterMentionCandidates(members, 'kaf').map((m) => m.username)).toEqual(['eval-ben']);
    expect(filterMentionCandidates(members, '')).toHaveLength(3);
  });
});
