import { useEffect, useState } from 'react';

/**
 * Shared "tick every N ms" clock for relative-time displays.
 *
 * Returns the current Date.now(), updated on each tick. Multiple
 * consumers on one page reuse the same React render cycle naturally —
 * but to avoid each call site spawning its own setInterval, prefer
 * pulling from a single shared module-level subscription registry.
 *
 * The implementation below is intentionally simple — one interval per
 * unique `intervalMs`, shared across all subscribers. Skips ticks when
 * the document is hidden so hidden tabs don't keep waking the JS event
 * loop.
 */

interface Bucket {
  subscribers: Set<(now: number) => void>;
  timer: ReturnType<typeof setInterval> | null;
  intervalMs: number;
}

const buckets = new Map<number, Bucket>();

function getOrCreateBucket(intervalMs: number): Bucket {
  let bucket = buckets.get(intervalMs);
  if (bucket) return bucket;
  bucket = { subscribers: new Set(), timer: null, intervalMs };
  buckets.set(intervalMs, bucket);
  return bucket;
}

function startBucket(bucket: Bucket): void {
  if (bucket.timer != null) return;
  bucket.timer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return;
    const now = Date.now();
    bucket.subscribers.forEach((fn) => fn(now));
  }, bucket.intervalMs);
}

function stopBucket(bucket: Bucket): void {
  if (bucket.timer != null) {
    clearInterval(bucket.timer);
    bucket.timer = null;
  }
}

export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const bucket = getOrCreateBucket(intervalMs);
    bucket.subscribers.add(setNow);
    startBucket(bucket);
    return () => {
      bucket.subscribers.delete(setNow);
      if (bucket.subscribers.size === 0) {
        stopBucket(bucket);
        buckets.delete(intervalMs);
      }
    };
  }, [intervalMs]);

  return now;
}
