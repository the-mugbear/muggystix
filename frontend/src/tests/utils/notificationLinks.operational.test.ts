/**
 * Review 2026-10-16, B-OPS-3: the installation's own alerts (disk, import
 * backlog, a silent worker) open System settings, where they are shown.  The
 * reaper's unrecoverable-jobs alert opened nothing before.
 */
import { describe, expect, it } from 'vitest';

import { notificationHref } from '../../utils/notificationLinks';

const system = (source_type: string) =>
  notificationHref({ type: 'system', source_type, source_id: null, host_id: null, finding_id: null });

// 5.417.0 — a session's scope proposals open just those, every person's,
// where "Accept all shown" decides them together.
describe('scope proposals', () => {
  it('open the session’s scope details on the Proposals page', () => {
    expect(notificationHref({
      type: 'proposal', source_type: 'agent_session_scope', source_id: 41, host_id: null, finding_id: null,
    })).toBe('/proposals?agent_session_id=41&kind=subnet_details&scope=all');
  });
});

// Review 2026-10-10, R1c: a finding assignment opens the finding; several
// open the reader's findings.
describe('finding assignments', () => {
  it('open the finding, or the findings that are yours', () => {
    expect(notificationHref({
      type: 'assignment', source_type: 'finding', source_id: 7, host_id: null, finding_id: 7,
    })).toBe('/findings/7');
    expect(notificationHref({
      type: 'assignment', source_type: 'findings', source_id: 3, host_id: null, finding_id: null,
    })).toBe('/findings?owner=me');
  });
});

describe('operational alerts', () => {
  it('open System settings', () => {
    expect(system('operational:disk_low')).toBe('/system-settings');
    expect(system('operational:ingestion_backlog')).toBe('/system-settings');
    expect(system('operational:worker_silent:report_jobs')).toBe('/system-settings');
    expect(system('ingestion_jobs')).toBe('/system-settings');
  });
});
