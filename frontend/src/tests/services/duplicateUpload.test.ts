import { describe, it, expect, vi } from 'vitest';

// Keep the axios client (stubbed in setupTests) out of the import graph.
vi.mock('../../services/api/client', () => ({ api: { defaults: {} }, p: () => '/projects/1' }));

import { duplicateUploadOf } from '../../services/api/uploads';
import { formatApiError } from '../../utils/apiErrors';

// An identical re-upload is refused with 409 duplicate_scan. The Scans page
// must tell it apart from a failure (it offers "View scan" / "Import again"),
// and generic error copy must not read it as "changed by someone else".
const refused = {
  response: {
    status: 409,
    data: {
      detail: {
        code: 'duplicate_scan',
        message: 'This exact file is already imported as scan #42 (sweep.xml); uploading it again would add nothing.',
        scan_id: 42,
        job_id: null,
      },
    },
  },
};

describe('duplicate upload', () => {
  it('recognises the refusal and what the file already is', () => {
    expect(duplicateUploadOf(refused)).toEqual({
      scanId: 42,
      jobId: null,
      jobStatus: null,
      message: refused.response.data.detail.message,
    });
  });

  it('carries the waiting copy\'s status when the same file is staged', () => {
    const staged = {
      response: {
        status: 409,
        data: { detail: { code: 'duplicate_scan', message: 'waiting', scan_id: null, job_id: 7, job_status: 'staged' } },
      },
    };
    expect(duplicateUploadOf(staged)).toMatchObject({ jobId: 7, jobStatus: 'staged' });
  });

  it('ignores other conflicts and plain errors', () => {
    expect(duplicateUploadOf({ response: { status: 409, data: { detail: 'Job is already completed' } } })).toBeNull();
    expect(duplicateUploadOf(new Error('Network error during upload'))).toBeNull();
  });

  it("surfaces a structured detail's message instead of generic 409 copy", () => {
    expect(formatApiError(refused, 'Upload failed')).toBe(refused.response.data.detail.message);
  });
});
