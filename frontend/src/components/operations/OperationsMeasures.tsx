/**
 * The Operations measures strip (5.329.0, design review 2026-10-02).
 *
 * "Tested 4 of 419" and "33 untouched hosts carry a critical observation" are
 * the two facts a lead acts on; one was the last thing on the page and the
 * other was inside the terrain.  They lead now, beside the two queues' sizes:
 * at most four measures on one baseline (UI_STYLE_GUIDE §7), each with its
 * (i) and each opening exactly what it counts — a Hosts list, or the section
 * of this page that lists it.
 *
 * A measure that could not be counted says so.  It never shows a zero: "0
 * untouched hosts with a critical observation" is a claim.
 */
import React from 'react';
import { Link } from 'react-router-dom';

import type { OperationsMeasures as Measures, ReviewFollowupsResponse } from '../../services/api';
import { buildHostsUrl } from '../../utils/drilldownLinks';
import { UNTOUCHED_CRITICAL_HREF } from '../../utils/addressTerrain';
import PostureMeasure from '../posture/PostureMeasure';

const LINK = 'rounded text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';
const n = (v: number) => v.toLocaleString();

/** A value the page does not have: loading, or the count failed. */
const Unknown: React.FC<{ loading: boolean }> = ({ loading }) => (
  <span className="text-muted-foreground" aria-label={loading ? 'Loading' : 'Unavailable'}>{loading ? '…' : '—'}</span>
);

const Unavailable: React.FC<{ onRetry?: () => void; what: string }> = ({ onRetry, what }) => (
  <span role="alert" className="text-warning">
    {what} could not be counted — this is not a zero.
    {onRetry && (
      <>
        {' '}
        <button type="button" onClick={onRetry} className={LINK}>Retry</button>
      </>
    )}
  </span>
);

export interface OperationsMeasuresProps {
  /** `GET /workbench/measures`; null while loading or when it failed. */
  measures: Measures | null;
  measuresLoading: boolean;
  measuresUnavailable: boolean;
  onRetryMeasures: () => void;
  /** The workbench's "Changed since review" section. */
  followups: ReviewFollowupsResponse | null;
  followupsUnavailable: boolean;
  /** The reader's queue (`personalWorkCounts`); null until the workbench loads. */
  myQueue: { total: number; available: number } | null;
  workbenchLoading: boolean;
  workbenchFailed: boolean;
  onRetryWorkbench: () => void;
  /** In-page targets: the sections these two measures count. */
  changedHref: string;
  myWorkHref: string;
  toClaimHref: string;
}

export const OperationsMeasures: React.FC<OperationsMeasuresProps> = ({
  measures, measuresLoading, measuresUnavailable, onRetryMeasures,
  followups, followupsUnavailable, myQueue, workbenchLoading, workbenchFailed, onRetryWorkbench,
  changedHref, myWorkHref, toClaimHref,
}) => {
  const untested = measures ? measures.total_hosts - measures.tested_hosts : 0;
  const changedKnown = !followupsUnavailable && !workbenchFailed && followups != null;
  const changed = changedKnown ? (followups.host_total ?? followups.total) : 0;
  const queueKnown = !workbenchFailed && myQueue != null;

  return (
    <div
      aria-label="Where the engagement stands"
      role="group"
      className="grid min-w-0 gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x"
    >
      <PostureMeasure
        label="Tested"
        info="Hosts with recorded evidence of a test that ran — a finding, no finding, or an inconclusive result. A proposed test alone does not count, nor does an attempt that could not run. The line below opens the hosts with none."
        value={measures
          ? <>{n(measures.tested_hosts)} <span className="text-metadata font-normal text-muted-foreground">of {n(measures.total_hosts)} hosts</span></>
          : <Unknown loading={measuresLoading} />}
        to={measures && measures.tested_hosts > 0 ? buildHostsUrl({ q: 'has:tested' }) : undefined}
        toLabel="Tested — view hosts"
      >
        {measuresUnavailable ? <Unavailable what="Tested hosts" onRetry={onRetryMeasures} /> : measures && (
          untested > 0 ? (
            <Link to={buildHostsUrl({ q: 'NOT has:tested' })} className={LINK}>
              {n(untested)} not yet tested
            </Link>
          ) : 'every host is tested'
        )}
      </PostureMeasure>

      <PostureMeasure
        label="Untouched, with a critical observation"
        info="Hosts carrying a critical scanner observation that nobody has touched: no review or assignment, note, test, evidence or finding. Scanner-reported, not confirmed. The same hosts the map marks with a diamond."
        value={measures ? n(measures.untouched_critical_hosts) : <Unknown loading={measuresLoading} />}
        to={measures && measures.untouched_critical_hosts > 0 ? UNTOUCHED_CRITICAL_HREF : undefined}
        toLabel="Untouched hosts with a critical observation — view hosts"
      >
        {measuresUnavailable ? <Unavailable what="Untouched critical exposure" onRetry={onRetryMeasures} /> : measures && (
          measures.untouched_critical_hosts > 0
            ? 'critical exposure nobody has looked at yet'
            : 'every host with a critical observation has been touched'
        )}
      </PostureMeasure>

      <PostureMeasure
        label="Changed since review"
        info="Reviewed hosts that are not done: the host gained open ports or critical / high scanner observations after its review, or the review concluded “needs more evidence”. Counted in hosts; the section below lists them."
        value={changedKnown ? n(changed) : <Unknown loading={workbenchLoading} />}
        to={changedKnown && changed > 0 ? changedHref : undefined}
        toLabel="Changed since review — go to the list"
      >
        {followupsUnavailable || workbenchFailed
          ? <Unavailable what="Reviewed hosts" onRetry={onRetryWorkbench} />
          : changedKnown && (changed > 0
            ? `${n(followups.mine_total)} of ${n(followups.total)} review${followups.total === 1 ? '' : 's'} yours`
            : 'no review to re-check')}
      </PostureMeasure>

      <PostureMeasure
        label="My queue"
        info="What is waiting on you: hosts you have in review, tests assigned to you, tests on the hosts you are reviewing, and findings you own that need something — under investigation, report text missing, or a proposal to decide. A confirmed, written-up finding is not counted."
        value={queueKnown ? n(myQueue.total) : <Unknown loading={workbenchLoading} />}
        to={queueKnown && myQueue.total > 0 ? myWorkHref : undefined}
        toLabel="My queue — go to My work"
      >
        {workbenchFailed
          ? <Unavailable what="Your queue" onRetry={onRetryWorkbench} />
          : queueKnown && (myQueue.available > 0 ? (
            <Link to={toClaimHref} className={LINK}>{n(myQueue.available)} more to claim</Link>
          ) : myQueue.total > 0 ? 'items waiting on you' : 'nothing is waiting on you')}
      </PostureMeasure>
    </div>
  );
};

export default OperationsMeasures;
