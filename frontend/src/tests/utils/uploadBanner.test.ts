/**
 * The upload banner's derivation: an entry this tab made + what the server
 * said about its job + its import result → what the row shows; and which
 * jobs a poll still names.
 */
import { describe, expect, it } from 'vitest';

import type { IngestionJob, Scan } from '../../services/api';
import {
  anyStillRunning, bannerRow, bannerRows, completedScanIds, jobIdsToAsk, mergeFollowed, settledJobs,
  type BannerEntry, type FollowedJobs,
} from '../../utils/uploadBanner';

const entry = (jobId: number, extra: Partial<BannerEntry> = {}): BannerEntry => ({
  key: `k${jobId}`, filename: `f${jobId}.xml`, jobId, ...extra,
});
const job = (id: number, status: string, extra: Partial<IngestionJob> = {}): IngestionJob => ({
  id, status, filename: `f${id}.xml`, original_filename: `f${id}.xml`, created_at: '2026-10-09T10:00:00Z', ...extra,
});
const scan = (id: number) => ({ id, filename: `s${id}.xml` }) as Scan;
const followed = (jobs: IngestionJob[], gone: number[] = []): FollowedJobs => ({ jobs, gone });

describe('bannerRow', () => {
  it('is "received" until the server has said something the banner has a word for', () => {
    expect(bannerRow(entry(1), undefined, undefined)).toEqual({
      key: 'k1', filename: 'f1.xml', jobId: 1, status: 'received', jobMessage: null, result: null, parseErrorId: null,
    });
    expect(bannerRow(entry(1), job(1, 'staged'), undefined).status).toBe('received');
  });

  it('is "processing" with the worker\'s message while the job is queued or processing', () => {
    expect(bannerRow(entry(1), job(1, 'queued', { message: 'Waiting' }), undefined))
      .toMatchObject({ status: 'processing', jobMessage: 'Waiting' });
    expect(bannerRow(entry(1), job(1, 'processing', { message: 'Reading 3 / 9' }), undefined))
      .toMatchObject({ status: 'processing', jobMessage: 'Reading 3 / 9' });
    expect(bannerRow(entry(1), job(1, 'processing'), undefined).jobMessage).toBeNull();
  });

  it('is "imported" with the scan\'s result once completed, "partial" with skipped records or a truncated file', () => {
    expect(bannerRow(entry(1), job(1, 'completed', { scan_id: 7, message: 'Done' }), scan(7)))
      .toMatchObject({ status: 'imported', jobMessage: 'Done', result: { id: 7 } });
    expect(bannerRow(entry(1), job(1, 'completed', { skipped_count: 2 }), undefined))
      .toMatchObject({ status: 'partial', result: null });
    expect(bannerRow(entry(1), job(1, 'completed', { partial: true }), undefined).status).toBe('partial');
    expect(bannerRow(entry(1), job(1, 'completed', { skipped_count: 0, partial: false }), undefined).status).toBe('imported');
  });

  it('is "failed" with the first reason the job gives, and its parse error', () => {
    const reason = (fields: Partial<IngestionJob>) => bannerRow(entry(1), job(1, 'failed', fields), undefined).error;
    expect(reason({ failure_reason: 'F', error_message: 'E', last_error: 'L', message: 'M' })).toBe('F');
    expect(reason({ failure_reason: null, error_message: 'E', last_error: 'L', message: 'M' })).toBe('E');
    expect(reason({ error_message: '', last_error: 'L', message: 'M' })).toBe('L');
    expect(reason({ message: 'M' })).toBe('M');
    expect(reason({})).toBe('Import failed');
    expect(bannerRow(entry(1), job(1, 'failed', { parse_error_id: 55 }), undefined).parseErrorId).toBe(55);
    expect(bannerRow(entry(1), job(1, 'failed'), undefined).parseErrorId).toBeNull();
    // A result is a completed import's: a failed job shows none.
    expect(bannerRow(entry(1), job(1, 'failed', { scan_id: 7 }), scan(7)).result).toBeNull();
  });
});

describe('bannerRows', () => {
  it('lists the started files in order, without the dismissed ones, each with its own job and result', () => {
    const rows = bannerRows(
      [entry(1), entry(2, { dismissed: true }), entry(3), entry(4)],
      followed([job(3, 'completed', { scan_id: 9 }), job(1, 'processing', { message: 'Reading' })], [4]),
      [scan(9), scan(8)],
    );
    expect(rows.map((r) => [r.key, r.status])).toEqual([['k1', 'processing'], ['k3', 'imported'], ['k4', 'received']]);
    expect(rows[1].result).toEqual(scan(9));
  });

  it('shows every file as received before any answer, and a completed one without counts before its result', () => {
    expect(bannerRows([entry(1)], undefined, undefined).map((r) => r.status)).toEqual(['received']);
    const [row] = bannerRows([entry(1)], followed([job(1, 'completed', { scan_id: 7, message: 'Done' })]), undefined);
    expect(row).toMatchObject({ status: 'imported', result: null, jobMessage: 'Done' });
  });
});

describe('what a poll asks, and when it stops', () => {
  it('asks about every started job first, then only those not finished or gone', () => {
    expect(jobIdsToAsk([1, 2, 3, 4], undefined)).toEqual([1, 2, 3, 4]);
    const known = followed([job(1, 'completed'), job(2, 'processing'), job(3, 'failed')], [4]);
    expect(jobIdsToAsk([1, 2, 3, 4, 5], known)).toEqual([2, 5]);
  });

  it('carries the finished jobs into the next reading and marks an unanswered id as gone', () => {
    const first = mergeFollowed(undefined, [1, 2, 3], [job(1, 'completed', { scan_id: 7 }), job(2, 'processing')]);
    expect(first.jobs.map((j) => [j.id, j.status])).toEqual([[1, 'completed'], [2, 'processing']]);
    expect(first.gone).toEqual([3]);

    const second = mergeFollowed(first, jobIdsToAsk([1, 2, 3], first), [job(2, 'failed')]);
    expect(second.jobs.map((j) => [j.id, j.status])).toEqual([[1, 'completed'], [2, 'failed']]);
    expect(second.gone).toEqual([3]);
    expect(jobIdsToAsk([1, 2, 3], second)).toEqual([]);
  });

  it('a running job that is no longer returned is gone, not kept as running', () => {
    const next = mergeFollowed(followed([job(1, 'processing')]), [1], []);
    expect(next).toEqual({ jobs: [], gone: [1] });
    expect(anyStillRunning(next)).toBe(false);
  });

  it('goes on while nothing is known or a job is not finished, and stops otherwise', () => {
    expect(anyStillRunning(undefined)).toBe(true);
    expect(anyStillRunning(followed([job(1, 'completed'), job(2, 'queued')]))).toBe(true);
    expect(anyStillRunning(followed([job(1, 'completed'), job(2, 'failed')], [3]))).toBe(false);
    expect(anyStillRunning(followed([], [1]))).toBe(false);
  });
});

describe('what a finish puts out of date', () => {
  it('names the finished and the gone jobs, and the completed among them, in a stable order', () => {
    expect(settledJobs(undefined)).toEqual({ settled: [], completed: [] });
    const reading = followed([job(9, 'completed'), job(2, 'processing'), job(4, 'failed')], [7]);
    expect(settledJobs(reading)).toEqual({ settled: [4, 7, 9], completed: [9] });
    // The same set, answered in another order, is the same list.
    const reordered = followed([job(4, 'failed'), job(9, 'completed'), job(2, 'processing')], [7]);
    expect(settledJobs(reordered)).toEqual(settledJobs(reading));
  });

  it('asks for the result of each completed job that made a scan, once per scan, sorted', () => {
    expect(completedScanIds(undefined)).toEqual([]);
    expect(completedScanIds(followed([
      job(1, 'completed', { scan_id: 8 }), job(2, 'completed', { scan_id: 7 }), job(3, 'completed', { scan_id: null }),
      job(4, 'failed', { scan_id: 5 }), job(5, 'processing', { scan_id: 6 }), job(6, 'completed', { scan_id: 8 }),
    ]))).toEqual([7, 8]);
  });
});
