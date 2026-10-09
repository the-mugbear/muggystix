import { useQueries, useQuery, type QueryKey } from '@tanstack/react-query';

import { pollEvery } from '../lib/query';

/**
 * useJobPoll — follow ONE server-side job from the answer of the request that
 * started it until it is finished (lib/query; UI_STYLE_GUIDE §48).
 *
 *   const [started, setStarted] = useState<Job | null>(null);   // the POST's answer
 *   const start = useMutation({ mutationFn: () => prepareThing(), onSuccess: setStarted });
 *   const { job, running, error } = useJobPoll({
 *     queryKey: ['getThingJob', started?.id],                    // the API function, then its arguments
 *     queryFn: (j, signal) => getThingJob(j.id, signal),
 *     job: started,
 *     interval: 2000,
 *     isDone: (j) => j.status !== 'queued' && j.status !== 'processing',
 *   });
 *
 * What the caller gets, beyond a query with `pollEvery`:
 *   - **No request at once.**  The job the POST answered IS the first reading;
 *     the first poll is one `interval` later.
 *   - **One request per `interval` while the job runs** — visible tab only,
 *     half as often while the read is failing (`pollEvery`).
 *   - **A finished job is never asked about again**: not by the interval, not
 *     on return to the tab, not when its key is invalidated.  A job that is
 *     already finished when it is given is never asked about at all.
 *   - **A failed poll keeps the job on screen** (`job` is the last reading,
 *     `running` stays true) and is reported in `error` until a poll answers.
 *   - **On return to the tab**: one read at once when the last reading is
 *     older than `interval`, else the next one on schedule.
 *   - `job == null` follows nothing and returns `job: null`.  A new job is a
 *     new id in `queryKey`, so it starts from its own POST's answer.
 *
 * It does NOT fit a list that is polled while any row in it is running (the
 * inventory download dialog): that has no POST's answer to start from and
 * must read at once — it is a plain `useQuery` with
 * `pollEvery((query) => anyRunning(query.state.data) ? ms : null)`.
 */
export interface JobPoll<TJob> {
  /** The job as last read; the given job until a poll has answered; null with no job. */
  job: TJob | null;
  /** There is a job and it is not finished: it is still being asked about. */
  running: boolean;
  /** Why the last poll failed, or null.  The job above is then the last good reading. */
  error: unknown;
}

interface JobPollShared<TJob> {
  /** The read, given the job as the POST answered it (for its id). */
  queryFn: (job: TJob, signal: AbortSignal) => Promise<TJob>;
  /** Milliseconds between two reads while the job runs. */
  interval: number;
  /** True once nothing more will happen to the job: the poll stops. */
  isDone: (job: TJob) => boolean;
}

export interface UseJobPollOptions<TJob> extends JobPollShared<TJob> {
  /** The API function's name, then its arguments (the job's id among them). */
  queryKey: QueryKey;
  /** The job as the request that started it answered; null / undefined: nothing to follow. */
  job: TJob | null | undefined;
}

export interface UseJobPollsOptions<TJob> extends JobPollShared<TJob> {
  /** The jobs to follow, each as the request that started it answered. */
  jobs: readonly TJob[];
  /** One job's key: the API function's name, then its arguments. */
  queryKey: (job: TJob) => QueryKey;
}

/** The ONE set of query options behind both hooks. */
function follow<TJob>(queryKey: QueryKey, job: TJob | null | undefined, shared: JobPollShared<TJob>) {
  const { queryFn, interval, isDone } = shared;
  // eslint-disable-next-line @tanstack/query/exhaustive-deps -- the caller's key names the job (its id) and its project
  return {
    queryKey,
    queryFn: ({ signal }: { signal: AbortSignal }) => queryFn(job as TJob, signal),
    // The POST's answer is the first reading, and is recent for one interval:
    // mounting asks nothing.
    initialData: job ?? undefined,
    staleTime: interval,
    // The stop.  Disabled rather than an interval of null, so that nothing —
    // an invalidation, a second reader — asks about a finished job either.
    enabled: (query: { state: { data?: TJob } }) =>
      job != null && query.state.data !== undefined && !isDone(query.state.data),
    ...pollEvery(interval),
  };
}

function reading<TJob>(
  given: TJob | null | undefined, data: TJob | undefined, error: unknown, isDone: (job: TJob) => boolean,
): JobPoll<TJob> {
  const job = given != null ? data ?? given : null;
  return { job, running: job != null && !isDone(job), error: given != null ? error ?? null : null };
}

export function useJobPoll<TJob>(options: UseJobPollOptions<TJob>): JobPoll<TJob> {
  const query = useQuery(follow(options.queryKey, options.job, options));
  return reading(options.job, query.data, query.error, options.isDone);
}

/**
 * Several jobs at once, one query each (`useQueries`) — a page whose jobs are
 * started one by one and followed side by side (a draft report's previews).
 * The answer is in the order of `jobs`; each job has `useJobPoll`'s guarantees.
 */
export function useJobPolls<TJob>(options: UseJobPollsOptions<TJob>): Array<JobPoll<TJob>> {
  const { jobs, queryKey, isDone } = options;
  const queries = useQueries({ queries: jobs.map((job) => follow(queryKey(job), job, options)) });
  return jobs.map((job, i) => reading(job, queries[i]?.data as TJob | undefined, queries[i]?.error, isDone));
}
