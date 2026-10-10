/**
 * The one-line tasks handed to the operator's agent (`agentInstruction`,
 * `WRAP_UP_PROMPT`).  They name what to work on and BlueStick's own calls;
 * they never widen a selection, never name a scanner or its flags, and state
 * the steps the server actually requires.
 */
import { describe, expect, it } from 'vitest';

import { WRAP_UP_PROMPT, agentInstruction } from '../../utils/agentRuns';

describe('agentInstruction', () => {
  it('proposeTests always names a fixed id list — never "this project’s hosts"', () => {
    const text = agentInstruction.proposeTests([12, 14], 'SMB signing');
    expect(text).toContain('for these hosts only (host ids): 12, 14.');
    expect(text).toContain('What to test: SMB signing.');
    expect(text).toContain('Do not run anything yet.');
    for (const ids of [[], [7]]) {
      expect(agentInstruction.proposeTests(ids)).not.toMatch(/project’s hosts/);
      expect(agentInstruction.proposeTests(ids)).toContain('for these hosts only');
    }
  });

  it('runHostTests states the steps the routes require', () => {
    const text = agentInstruction.runHostTests(9);
    // Claimed before it runs; the evidence carries the key the server
    // requires for host-test evidence; then done.
    expect(text).toContain('in_progress');
    expect(text).toContain('host_test_id and a request_key');
    expect(text).toContain('show me the command before you run it');
    expect(text.indexOf('in_progress')).toBeLessThan(text.indexOf('record_evidence'));
    expect(text.indexOf('record_evidence')).toBeLessThan(text.indexOf('mark the test done'));
  });

  it('no task names a scanner, a flag or the tool catalogue', () => {
    const all = [
      agentInstruction.scanScope(),
      agentInstruction.scanScope(2, { subnets: 8, domains: 3 }),
      agentInstruction.proposeTests([1]),
      agentInstruction.proposeTestForObservation(1, 5, 'TLS 1.0 enabled'),
      agentInstruction.runHostTests(1),
      agentInstruction.reviewFinding(3, ['impact']),
      WRAP_UP_PROMPT,
    ].join('\n');
    expect(all).not.toMatch(/\b(nmap|nuclei|nessus|masscan|httpx|naabu|gobuster|nikto)\b/i);
    expect(all).not.toMatch(/list_tools|catalogue/i);
    expect(all).not.toMatch(/\b(test plan|execution run|recon run|phase)\b/i);
  });

  it('reviewFinding asks for evidence ids, and never a guess', () => {
    const text = agentInstruction.reviewFinding(3, ['impact']);
    expect(text).toContain('Cite the evidence ids that support each proposal.');
    expect(text).toContain('do not propose it or fill it with a guess or placeholder');
    expect(text).toContain('Still empty: impact.');
  });
});
