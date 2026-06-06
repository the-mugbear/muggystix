import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the axios client so we exercise the real getToolReadyOutput serializer
// without pulling in the interceptor setup (which needs the full app harness).
vi.mock('../../services/api/client', () => {
  const get = vi.fn().mockResolvedValue({ data: '' });
  return {
    api: { get },
    p: () => '/api/v1/projects/1',
    setCurrentProjectId: () => {},
    getCurrentProjectId: () => 1,
  };
});

import { getToolReadyOutput } from '../../services/api';
import { api } from '../../services/api/client';

// Regression for the Critical review finding: getToolReadyOutput used a manual
// param allowlist that silently dropped tags, tech, assigned_to, the has_*
// booleans, and subnet_labels, so scanner targets could be generated from a
// broader host set than the visible /hosts list. It now serializes the full
// query context generically.
describe('getToolReadyOutput serialization', () => {
  beforeEach(() => {
    (api.get as ReturnType<typeof vi.fn>).mockClear();
  });

  it('sends every active filter to the tool-ready endpoint', async () => {
    await getToolReadyOutput('targets', {
      q: 'port:443',
      tags: '3,7',
      tech: 'nginx',
      subnet_labels: '2',
      assigned_to: 'me',
      has_exploit_available: true,
      has_test_execution: true,
      has_web_interface: true,
      has_critical_vulns: true,
      includePorts: true,
      scanId: 9,
    });

    const url = (api.get as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain('q=port%3A443');
    expect(url).toContain('tags=3%2C7');
    expect(url).toContain('tech=nginx');
    expect(url).toContain('subnet_labels=2');
    expect(url).toContain('assigned_to=me');
    expect(url).toContain('has_exploit_available=true');
    expect(url).toContain('has_test_execution=true');
    expect(url).toContain('has_web_interface=true');
    expect(url).toContain('has_critical_vulns=true');
    // Special-cased wire names.
    expect(url).toContain('include_ports=true');
    expect(url).toContain('scan_id=9');
    // Camel-case keys must not leak through as-is.
    expect(url).not.toContain('includePorts');
    expect(url).not.toContain('scanId');
  });

  it('omits empty/undefined values', async () => {
    await getToolReadyOutput('targets', { q: 'port:80', tags: '', includePorts: false });
    const url = (api.get as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain('q=port%3A80');
    expect(url).not.toContain('tags=');
    expect(url).not.toContain('include_ports');
  });
});
