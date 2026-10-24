/**
 * The live presentations the reader can join, across their projects (5.426.0;
 * owner, 2026-10-24) — read from what is live, so the bell's glow and its
 * "Presenting now" rows go away by themselves when a presentation ends or
 * goes idle (the stored "is presenting" notification stays as a record).
 *
 * ONE read, `['listLivePresentations']`: the top bar polls it every
 * `LIVE_POLL_MS` (a minute, like the bell's count, would make a glow a minute
 * late); the bell's panel reads the same key when it opens.
 */
import { useQuery } from '@tanstack/react-query';

import { listLivePresentations, type LivePresentation } from '../services/api';
import { pollEvery } from '../lib/query';

export const LIVE_POLL_MS = 20_000;
export const LIVE_PRESENTATIONS_KEY = ['listLivePresentations'] as const;

const NONE: LivePresentation[] = [];

/** "Ana is presenting" — or "You are presenting" when the reader drives it. */
export const whoPresents = (driver: LivePresentation['driver'], readerId: number | undefined): string =>
  driver && readerId != null && driver.id === readerId ? 'You are presenting' : `${driver?.name ?? 'Someone'} is presenting`;

export function useLivePresentations({ enabled = true, poll = false }: { enabled?: boolean; poll?: boolean } = {}) {
  const query = useQuery({
    queryKey: LIVE_PRESENTATIONS_KEY,
    queryFn: ({ signal }) => listLivePresentations(signal),
    enabled,
    ...(poll ? pollEvery(LIVE_POLL_MS) : {}),
  });
  return { live: query.data ?? NONE, failed: query.isError };
}
