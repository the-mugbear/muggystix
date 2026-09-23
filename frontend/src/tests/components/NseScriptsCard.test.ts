/**
 * NSE output as nmap writes it (v5.274.2): a leading newline and an indent
 * on every line.  `.trim()` removed only the first line's indent.
 */
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../services/api', () => ({}));

import { formatNseOutput } from '../../components/NseScriptsCard';

describe('formatNseOutput', () => {
  it('removes the shared indent so the first line lines up with its siblings', () => {
    // smb2-capabilities from the Parser Lab's Samba host.
    const raw = '\n  2.1: \n    Distributed File System\n    Leasing\n  3.0: \n    Distributed File System\n';
    expect(formatNseOutput(raw)).toBe('2.1: \n  Distributed File System\n  Leasing\n3.0: \n  Distributed File System');
  });

  it('leaves unindented output and empty output alone', () => {
    expect(formatNseOutput('Apache httpd')).toBe('Apache httpd');
    expect(formatNseOutput(null)).toBe('');
    expect(formatNseOutput('\n \n')).toBe('');
  });
});
