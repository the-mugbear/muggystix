/**
 * Addresses between Query, Hosts, Scanner observations and a host's weakness
 * row (review 2026-10-16, Q7 / O4 / O7).
 */
import { describe, expect, it } from 'vitest';

import {
  issueFromWeaknessHash, observationPath, weaknessAnchorId, weaknessHash,
} from '../../utils/observationsPath';
import { hostsInQueryPath, hostsPageFor, issueInQueryPath, queryPath } from '../../utils/queryPath';

describe('queryPath', () => {
  it('names the query, and a later page when there is one', () => {
    expect(queryPath('hosts | head 3')).toBe(`/query?q=${encodeURIComponent('hosts | head 3')}`);
    expect(queryPath('hosts', 'a/b=')).toBe(`/query?q=hosts&after=${encodeURIComponent('a/b=')}`);
  });

  it('Hosts ↔ Query: `hosts <terms>` and /hosts?q=<terms> are the same list', () => {
    expect(hostsInQueryPath('port:445 site:"HQ"')).toBe(queryPath('hosts port:445 site:"HQ"'));
    expect(hostsInQueryPath('  ')).toBe(queryPath('hosts'));
    expect(hostsPageFor('port:445')).toBe('/hosts?q=port%3A445');
    expect(hostsPageFor('')).toBe('/hosts');
  });

  it('an issue in Query is a where on its key, quoted; a row: key has none', () => {
    expect(issueInQueryPath('title:say "hi"')).toBe(queryPath('observations | where issue="title:say \\"hi\\""'));
    expect(issueInQueryPath('row:12')).toBeNull();
  });
});

describe('a weakness row on the host page', () => {
  it('its anchor round-trips through the address', () => {
    const key = 'title:smb signing not required';
    expect(observationPath(key)).toBe(`/observations?issue=${encodeURIComponent(key)}`);
    expect(weaknessAnchorId(key)).toBe(`weakness-${key}`);
    expect(issueFromWeaknessHash(weaknessHash(key))).toBe(key);
    expect(issueFromWeaknessHash('#section-notes')).toBeNull();
    expect(issueFromWeaknessHash('#%E0%A4%A')).toBeNull();   // not decodable
    expect(issueFromWeaknessHash('')).toBeNull();
  });
});
