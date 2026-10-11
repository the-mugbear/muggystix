/**
 * retryIngestionJob — what goes on the wire.
 *
 * A plain retry sends NO body (the route's body is optional, and "keep what a
 * cut-short file holds" is one attempt's instruction: a retry that did not ask
 * for it must not carry it).  "Import what the file holds" sends
 * `{keep_truncated: true}`.  The page tests mock this function, so this test
 * mocks only the HTTP client.
 */
import { beforeEach, describe, it, expect, vi } from 'vitest';

const { post } = vi.hoisted(() => ({
  post: vi.fn((..._args: unknown[]) => Promise.resolve({ data: { job_id: 41, status: 'queued', message: 'Job re-queued' } })),
}));
vi.mock('../../services/api/client', () => ({ api: { post }, projectPath: (id: number) => `/projects/${id}` }));

import { retryIngestionJob } from '../../services/api/uploads';

beforeEach(() => post.mockClear());

describe('retryIngestionJob', () => {
  it('a plain retry posts to the job’s retry path with no body', async () => {
    const answer = await retryIngestionJob(7, 41);
    expect(post.mock.calls).toEqual([['/projects/7/upload/jobs/41/retry']]);
    expect(answer.status).toBe('queued');
  });

  it('asked to keep a cut-short file, it says so in the body', async () => {
    await retryIngestionJob(7, 41, { keepTruncated: true });
    expect(post.mock.calls).toEqual([['/projects/7/upload/jobs/41/retry', { keep_truncated: true }]]);
  });

  it('keepTruncated false is a plain retry', async () => {
    await retryIngestionJob(7, 41, { keepTruncated: false });
    expect(post.mock.calls).toEqual([['/projects/7/upload/jobs/41/retry']]);
  });
});
