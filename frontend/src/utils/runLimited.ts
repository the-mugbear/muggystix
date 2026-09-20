/**
 * `Promise.allSettled` with a ceiling on how many tasks run at once (v5.248.0).
 *
 * The upload review staged and started every selected file simultaneously.
 * The browser's old six-connections-per-host limit does not apply over
 * HTTP/2, so a 200-file drop was 200 concurrent uploads against a handful of
 * API workers and one database pool. Results keep the input order; a task
 * that throws or rejects settles as `rejected` and never stops the others.
 */
export async function runLimited<T, R>(
  items: readonly T[],
  limit: number,
  task: (item: T, index: number) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next++;
      try {
        results[index] = { status: 'fulfilled', value: await task(items[index], index) };
      } catch (reason) {
        results[index] = { status: 'rejected', reason };
      }
    }
  };
  const width = Math.max(1, Math.min(Math.floor(limit) || 1, items.length));
  await Promise.all(Array.from({ length: width }, worker));
  return results;
}

/** Files transferred at once while staging. Each is an upload plus a detection. */
export const STAGE_CONCURRENCY = 4;
/** Staged jobs started at once. Small requests, but each takes a row lock. */
export const START_CONCURRENCY = 6;
