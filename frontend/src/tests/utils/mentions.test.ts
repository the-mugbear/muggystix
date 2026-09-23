import { describe, expect, it } from 'vitest';

import { activeMentionQuery, filterMentionCandidates, findMentionSpans } from '../../utils/mentions';

// The same cases as backend/tests/test_mentions_and_discussion.py::test_find_mentions —
// what the composer highlights must be what the server notifies.
const NAMES = ['eval-ana', 'eval-ben', 'ana', 'j.smith', 'anabel', 'Bob_2'];
const names = (text: string) => new Set(findMentionSpans(text, NAMES).map((s) => s.username));

describe('findMentionSpans', () => {
  it.each([
    ['@eval-ana please look', ['eval-ana']],
    ['ping @j.smith.', ['j.smith']],
    ['@ana, and @anabel', ['ana', 'anabel']],
    ['@anab is nobody', []],
    ['@ana-maria is nobody either', []],
    ['mail ana@example.com', []],
    ['(@eval-ben) @BOB_2!', ['eval-ben', 'Bob_2']],
    ['@eval-ana\n@eval-ana again', ['eval-ana']],
    ['no mention here', []],
    ['trailing @', []],
  ])('%s', (text, expected) => {
    expect(names(text)).toEqual(new Set(expected));
  });

  it('returns the exact span of each mention', () => {
    expect(findMentionSpans('hi @eval-ana.', NAMES)).toEqual([{ start: 3, end: 12, username: 'eval-ana' }]);
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
