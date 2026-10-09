import { describe, it, expect } from 'vitest';
import { ProjectChanged, getQueryScope, setQueryScope } from '../../lib/query';
import { runLimited } from '../../utils/runLimited';

const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => { resolve = r; });
  return { promise, resolve };
};

describe('runLimited', () => {
  // The defect: a 200-file drop started 200 uploads at once.
  it('never runs more than the limit at once, for a large selection', async () => {
    let running = 0;
    let peak = 0;
    const gates = Array.from({ length: 200 }, deferred);
    const all = runLimited(gates, 4, async (gate) => {
      running += 1;
      peak = Math.max(peak, running);
      await gate.promise;
      running -= 1;
    });
    // Release them one at a time; each release lets exactly one more start.
    for (const gate of gates) {
      await Promise.resolve();
      gate.resolve();
      await Promise.resolve();
    }
    const results = await all;
    expect(peak).toBe(4);
    expect(results).toHaveLength(200);
    expect(results.every((r) => r.status === 'fulfilled')).toBe(true);
  });

  it('keeps input order and lets a failure settle without stopping the rest', async () => {
    const results = await runLimited([1, 2, 3, 4], 2, async (n) => {
      if (n === 2) throw new Error('boom');
      return n * 10;
    });
    expect(results.map((r) => r.status)).toEqual(['fulfilled', 'rejected', 'fulfilled', 'fulfilled']);
    expect(results.map((r) => (r.status === 'fulfilled' ? r.value : null))).toEqual([10, null, 30, 40]);
  });

  // Code review 2026-10-09: an API function builds its address from the
  // project that is current when it is called, so the rest of a selection
  // must not be started once the reader has switched project.
  it('starts nothing more once the project changes: what is left settles as rejected', async () => {
    const before = getQueryScope();
    setQueryScope({ userId: 1, projectId: 1 });
    const started: number[] = [];
    const first = deferred();
    const all = runLimited([1, 2, 3], 1, async (n) => {
      started.push(n);
      if (n === 1) await first.promise;
      return n;
    });
    await Promise.resolve();
    setQueryScope({ userId: 1, projectId: 2 });
    first.resolve();
    const results = await all;
    setQueryScope(before);

    expect(started).toEqual([1]);
    expect(results.map((r) => r.status)).toEqual(['fulfilled', 'rejected', 'rejected']);
    expect((results[1] as PromiseRejectedResult).reason).toBeInstanceOf(ProjectChanged);
  });

  it('handles nothing to do, and a limit below one', async () => {
    expect(await runLimited([], 4, async () => 1)).toEqual([]);
    const results = await runLimited(['a', 'b'], 0, async (s) => s);
    expect(results.map((r) => r.status)).toEqual(['fulfilled', 'fulfilled']);
  });
});
