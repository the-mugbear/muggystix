/**
 * The Scanner observations inspector (5.404.0) — one issue, as the host
 * inspector shows one host: what the scanner wrote about it, every host
 * carrying it, and on each host what the scanner printed there.
 *
 * A side panel over the list (style guide §11: master-detail is a non-modal
 * SideSheet), opened by the address (`?issue=<key>`), so it can be linked and
 * Back closes it.  Its body is keyed by the issue: a draft or an opened host
 * never follows the reader to the next one.
 *
 * It makes no judgment of its own.  Ticking hosts, "Promote…" and "Mark false
 * positive…" are the list's selection and the list's dialogs (props), so a
 * promotion from here is the same request as one from the bulk bar.
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Ban, ChevronDown, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';

import {
  getObservationHostOutput,
  getObservationIssueDetail,
  getObservationIssueHosts,
} from '../../services/api';
import type { FindingHostStatus, FindingStatus, ObservationIssue, ObservationIssueHost } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import { agentInstruction } from '../../utils/agentRuns';
import { ENDPOINT_STATUS_LABEL, STATUS_LABEL } from '../../utils/findingStatus';
import AgentTaskButton from '../agent-sessions/AgentTaskButton';
import InspectorSection from '../host-inspector/InspectorSection';
import ScannerReferences from '../host-inspector/ScannerReferences';
import { TimeAgo } from '../TimeAgo';
import { SeverityBadge } from '../ui/SeverityBadge';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { SideSheet, SideSheetBody, SideSheetContent, SideSheetHeader, SideSheetTitle } from '../ui/side-sheet';

/** Hosts listed under an issue; one more is requested to know the list is cut. */
export const HOST_CAP = 100;

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

/** Where testing of the issue stands on one of its hosts, in the words the
 *  Hosts row uses (`testWorkState`): tested, tested with more to do, planned. */
export const issueTestState = (h: Pick<ObservationIssueHost, 'tests_to_do' | 'tests_recorded'>): string => {
  const toDo = h.tests_to_do ?? 0;
  const recorded = h.tests_recorded ?? 0;
  if (recorded > 0) return toDo > 0 ? `tested · ${toDo} to do` : 'tested';
  return toDo > 0 ? `${plural(toDo, 'test')} planned` : 'no test';
};

/** The Hosts query that lists exactly the hosts carrying an issue. */
export const issueHostsPath = (issueKey: string) =>
  `/hosts?q=${encodeURIComponent(`issue:"${issueKey.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`)}`;

const KIND_LABEL: Record<string, string> = {
  misconfiguration: 'Misconfiguration',
  vulnerability: 'Vulnerability',
  informational: 'Informational',
};

export interface ObservationInspectorProps {
  /** The issue open (`?issue=`), or null for a closed panel. */
  issueKey: string | null;
  /** Its row on the page shown — null when a filter or page turn left it out. */
  issue: ObservationIssue | null;
  /** Analyst or above: may tick hosts, promote and dismiss. */
  canManage: boolean;
  /** The hosts ticked under it; undefined = every host. */
  narrowed: Set<number> | undefined;
  onToggleHost: (issue: ObservationIssue, hosts: ObservationIssueHost[], hostId: number, on: boolean) => void;
  onPromote: (issue: ObservationIssue) => void;
  onDismiss: (issue: ObservationIssue) => void;
  /** 1-based place in the whole list, and its length; null when unknown. */
  position: { index: number; total: number } | null;
  hasPrev: boolean;
  hasNext: boolean;
  onStep: (delta: 1 | -1) => void;
  onClose: () => void;
}

const ObservationInspector: React.FC<ObservationInspectorProps> = (props) => {
  const { issueKey, position, hasPrev, hasNext, onStep, onClose } = props;
  return (
    <SideSheet open={issueKey !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SideSheetContent width="xl" aria-describedby={undefined}>
        <SideSheetHeader>
          <div className="flex items-center justify-between gap-sm pr-xl">
            <SideSheetTitle>
              Scanner observation
              {position && (
                <span className="ml-xs text-caption font-normal text-muted-foreground">
                  {position.index.toLocaleString()} of {position.total.toLocaleString()}
                </span>
              )}
            </SideSheetTitle>
            <div className="flex items-center gap-xxs">
              <Button variant="ghost" size="icon" aria-label="Previous observation" disabled={!hasPrev} onClick={() => onStep(-1)}>
                <ChevronLeft className="size-4" aria-hidden />
              </Button>
              <Button variant="ghost" size="icon" aria-label="Next observation" disabled={!hasNext} onClick={() => onStep(1)}>
                <ChevronRight className="size-4" aria-hidden />
              </Button>
            </div>
          </div>
        </SideSheetHeader>
        <SideSheetBody>
          {issueKey !== null && <Body key={issueKey} {...props} issueKey={issueKey} />}
        </SideSheetBody>
      </SideSheetContent>
    </SideSheet>
  );
};

const Body: React.FC<ObservationInspectorProps & { issueKey: string }> = ({
  issueKey, issue, canManage, narrowed, onToggleHost, onPromote, onDismiss,
}) => {
  const projectId = useProjectId();
  const detail = useQuery({
    queryKey: ['getObservationIssueDetail', projectId, issueKey],
    queryFn: ({ signal }) => getObservationIssueDetail(projectId, issueKey, signal),
  });
  const hostsQuery = useQuery({
    queryKey: ['getObservationIssueHosts', projectId, issueKey, HOST_CAP + 1],
    queryFn: ({ signal }) => getObservationIssueHosts(projectId, issueKey, HOST_CAP + 1, signal),
  });
  const title = issue?.title ?? detail.data?.title ?? null;
  const hostCount = issue?.host_count ?? null;

  return (
    <div className="flex min-w-0 flex-col gap-md" data-testid="observation-inspector">
      <div className="flex min-w-0 flex-col gap-xs">
        <h2 className="break-words text-subheading font-semibold text-foreground">
          {title ?? (detail.isError ? 'This observation' : 'Loading…')}
        </h2>
        {issue ? (
          <>
            <div className="flex min-w-0 flex-wrap items-center gap-xs">
              <SeverityBadge severity={issue.severity} />
              {issue.kind && <Badge variant="outline">{KIND_LABEL[issue.kind] ?? issue.kind}</Badge>}
              {issue.exploitable && <Badge variant="destructive-outline">Exploit reported</Badge>}
              {issue.cve_id && <span className="font-mono text-caption text-muted-foreground">{issue.cve_id}</span>}
              {issue.sources.length > 0 && (
                <span className="min-w-0 truncate text-caption text-muted-foreground">from {issue.sources.join(', ')}</span>
              )}
            </div>
            <p className="text-metadata text-muted-foreground" data-testid="observation-inspector-counts">
              {plural(issue.host_count, 'host')}
              {' · '}{issue.judged_host_count.toLocaleString()} judged
              {' · '}{(issue.host_count - issue.judged_host_count).toLocaleString()} not yet judged
              {issue.finding_id != null && (
                <>
                  {' · '}
                  <Link to={`/findings/${issue.finding_id}`} className="text-primary hover:underline">
                    Finding #{issue.finding_id}
                    {issue.finding_status ? ` · ${STATUS_LABEL[issue.finding_status as FindingStatus] ?? issue.finding_status}` : ''}
                  </Link>
                </>
              )}
            </p>
            {canManage && (
              <div className="flex flex-wrap items-center gap-xs">
                <Button size="sm" onClick={() => onPromote(issue)}>
                  {narrowed ? `Promote on ${plural(narrowed.size, 'ticked host')}…` : 'Promote to a finding…'}
                </Button>
                <Button size="sm" variant="outline" onClick={() => onDismiss(issue)}>
                  <Ban className="size-3.5" aria-hidden /> Mark false positive…
                </Button>
              </div>
            )}
          </>
        ) : (
          <p className="text-caption text-warning" data-testid="observation-inspector-outside">
            Not among the rows shown — a filter or page change left it out. Its details and hosts are below;
            to promote or dismiss it, show it in the list again.
          </p>
        )}
      </div>

      <InspectorSection id="observation-scanner" title="What the scanner wrote">
        {detail.isPending ? (
          <p className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading what the scanner wrote…
          </p>
        ) : detail.isError ? (
          <p role="alert" className="text-caption text-destructive">
            {queryErrorText(detail.error, 'Could not load what the scanner wrote.')}{' '}
            <Button variant="ghost" size="sm" onClick={() => void detail.refetch()}>Retry</Button>
          </p>
        ) : (
          <ScannerWrote detail={detail.data} />
        )}
      </InspectorSection>

      <InspectorSection id="observation-hosts" title="Hosts carrying it" count={hostCount}>
        {hostsQuery.isPending ? (
          <p className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading hosts…
          </p>
        ) : hostsQuery.isError ? (
          <p role="alert" className="text-caption text-destructive">
            {queryErrorText(hostsQuery.error, 'Could not load the hosts.')}{' '}
            <Button variant="ghost" size="sm" onClick={() => void hostsQuery.refetch()}>Retry</Button>
          </p>
        ) : (
          <IssueHosts
            issueKey={issueKey}
            issue={issue}
            hosts={hostsQuery.data}
            canManage={canManage}
            narrowed={narrowed}
            onToggleHost={onToggleHost}
            title={title}
          />
        )}
      </InspectorSection>
    </div>
  );
};

type Detail = Awaited<ReturnType<typeof getObservationIssueDetail>>;

const prose = 'max-h-64 overflow-auto whitespace-pre-wrap break-words text-caption text-muted-foreground';

const ScannerWrote: React.FC<{ detail: Detail }> = ({ detail }) => {
  const references = detail.references ?? [];
  const ids = [
    detail.plugin_id ? `Plugin ${detail.plugin_id}` : null,
    detail.plugin_name && detail.plugin_name !== detail.title ? detail.plugin_name : null,
  ].filter(Boolean);
  const nothing = !detail.description && !detail.solution && references.length === 0 && detail.cvss_score == null;
  return (
    <div className="flex min-w-0 flex-col gap-xs" data-testid="observation-detail">
      {nothing && (
        <p className="text-caption text-muted-foreground">No scanner recorded a description or remediation for this.</p>
      )}
      {detail.description && (
        <div className="min-w-0">
          <p className="text-caption font-semibold text-foreground">
            Description <span className="font-normal text-muted-foreground">· as {detail.source} reports it</span>
          </p>
          <p className={prose}>{detail.description}</p>
        </div>
      )}
      {detail.solution && (
        <div className="min-w-0">
          <p className="text-caption font-semibold text-foreground">Remediation</p>
          <p className={prose}>{detail.solution}</p>
        </div>
      )}
      {detail.cvss_score != null && (
        <p className="min-w-0 break-all text-caption text-muted-foreground">
          CVSS {detail.cvss_score}{detail.cvss_vector ? ` · ${detail.cvss_vector}` : ''}
        </p>
      )}
      {ids.length > 0 && (
        <p className="min-w-0 truncate text-caption text-muted-foreground" title={ids.join(' · ')}>
          {detail.source} · {ids.join(' · ')}
        </p>
      )}
      <ScannerReferences references={references} />
    </div>
  );
};

const IssueHosts: React.FC<{
  issueKey: string;
  issue: ObservationIssue | null;
  hosts: ObservationIssueHost[];
  canManage: boolean;
  narrowed: Set<number> | undefined;
  onToggleHost: ObservationInspectorProps['onToggleHost'];
  title: string | null;
}> = ({ issueKey, issue, hosts, canManage, narrowed, onToggleHost, title }) => {
  // A cut list cannot be narrowed: unticking one of the first HOST_CAP would
  // promote those, not the rest.
  const cut = hosts.length > HOST_CAP;
  const shown = cut ? hosts.slice(0, HOST_CAP) : hosts;
  const canTick = canManage && !cut && issue !== null;
  const picked = shown.filter((h) => (narrowed ? narrowed.has(h.host_id) : true));
  const total = issue?.host_count ?? hosts.length;
  if (hosts.length === 0) {
    return <p className="text-caption text-muted-foreground">No host in this project carries it any more.</p>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      {canTick && (
        <p className="text-caption text-muted-foreground">
          Untick the hosts you have not verified; a promotion records the finding on the ticked ones.
        </p>
      )}
      {cut && (
        <p className="text-caption text-muted-foreground" data-testid="observation-hosts-cut">
          The first {HOST_CAP} of {plural(total, 'host')}.{' '}
          {canManage && 'Promoting records it on every one; to narrow, open them on Hosts. '}
          <Link to={issueHostsPath(issueKey)} className="text-primary hover:underline">
            All {plural(total, 'host')} on Hosts
          </Link>
        </p>
      )}
      <ul className="divide-y divide-border">
        {shown.map((h) => (
          <HostRow
            key={h.host_id}
            issueKey={issueKey}
            host={h}
            ticked={narrowed ? narrowed.has(h.host_id) : true}
            onTick={canTick && issue ? (on) => onToggleHost(issue, hosts, h.host_id, on) : undefined}
          />
        ))}
      </ul>
      {picked.length > 0 && title && (
        <div className="flex flex-wrap items-center gap-xs">
          {/* The hosts ticked here, handed to the operator's agent: a fixed
              list, never "every host of the issue" beyond the ones shown. */}
          <AgentTaskButton
            label={`Ask agent to propose tests on ${cut || !canTick ? 'these' : 'the ticked'} ${plural(picked.length, 'host')}`}
            title="Hand these hosts and this issue to your agent, to propose a test that would confirm it on each"
            instruction={agentInstruction.proposeTestsForIssue(picked.map((h) => h.host_id), title)}
          />
        </div>
      )}
    </div>
  );
};

const HostRow: React.FC<{
  issueKey: string;
  host: ObservationIssueHost;
  ticked: boolean;
  onTick?: (on: boolean) => void;
}> = ({ issueKey, host: h, ticked, onTick }) => {
  const [open, setOpen] = useState(false);
  return (
    <li className="min-w-0 py-xxs">
      <div className="flex min-w-0 items-center gap-sm text-metadata">
        {onTick && (
          <Checkbox aria-label={`Include ${h.ip_address}`} checked={ticked} onCheckedChange={(v) => onTick(v === true)} />
        )}
        <Link to={`/hosts/${h.host_id}`} className="w-32 shrink-0 truncate font-mono text-primary hover:underline">
          {h.ip_address}
        </Link>
        <span className="min-w-0 flex-1 truncate text-muted-foreground" title={h.hostname ?? undefined}>
          {h.hostname || '—'}
        </span>
        <span className="w-24 shrink-0 truncate text-caption text-muted-foreground">
          {h.ports.length > 0 ? `port ${h.ports.join(', ')}` : 'host-level'}
        </span>
        <span className="w-36 shrink-0 truncate text-caption">
          {h.judged
            ? `On the finding · ${ENDPOINT_STATUS_LABEL[(h.endpoint_status ?? 'open') as FindingHostStatus] ?? h.endpoint_status}`
            : <span className="text-muted-foreground">not yet judged</span>}
        </span>
        <span
          className="w-28 shrink-0 truncate text-caption text-muted-foreground"
          title="Tests on this host that name this issue: results recorded, and tests proposed or in progress"
        >
          {issueTestState(h)}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0"
          aria-expanded={open}
          aria-label={`${open ? 'Hide' : 'Show'} what the scanner printed on ${h.ip_address}`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
          Output
        </Button>
      </div>
      {open && <HostOutput issueKey={issueKey} hostId={h.host_id} />}
    </li>
  );
};

/** What the scanner printed for this issue on one host — that host's rows, as
 *  the host page shows them.  Read when the row is opened. */
const HostOutput: React.FC<{ issueKey: string; hostId: number }> = ({ issueKey, hostId }) => {
  const projectId = useProjectId();
  const output = useQuery({
    queryKey: ['getObservationHostOutput', projectId, issueKey, hostId],
    queryFn: ({ signal }) => getObservationHostOutput(projectId, issueKey, hostId, signal),
  });
  if (output.isPending) {
    return (
      <p className="ml-lg flex items-center gap-xs py-xxs text-caption text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading what the scanner printed…
      </p>
    );
  }
  if (output.isError) {
    return (
      <p role="alert" className="ml-lg py-xxs text-caption text-destructive">
        {queryErrorText(output.error, 'Could not load what the scanner printed.')}{' '}
        <Button variant="ghost" size="sm" onClick={() => void output.refetch()}>Retry</Button>
      </p>
    );
  }
  if (output.data.length === 0) {
    return <p className="ml-lg py-xxs text-caption text-muted-foreground">The scanner no longer reports it on this host.</p>;
  }
  return (
    <ul className="ml-lg flex min-w-0 flex-col gap-xs py-xxs" data-testid="observation-host-output">
      {output.data.map((row) => (
        <li key={row.id} className="min-w-0">
          <p className="min-w-0 truncate text-caption text-muted-foreground">
            {row.port_number != null
              ? `Port ${row.port_number}${row.protocol ? `/${row.protocol}` : ''}${row.service_name ? ` · ${row.service_name}` : ''}`
              : 'Host-level'}
            {row.source ? ` · ${row.source}` : ''}
            {row.plugin_id ? ` · plugin ${row.plugin_id}` : ''}
            {row.last_seen && <> · last seen <TimeAgo value={row.last_seen} absoluteAfterDays={30} /></>}
          </p>
          {row.plugin_output && row.plugin_output.trim() ? (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-control bg-muted/40 p-xs font-mono text-caption text-muted-foreground">
              {row.plugin_output.trim()}
            </pre>
          ) : (
            <p className="text-caption text-muted-foreground">The scanner printed nothing for this host.</p>
          )}
        </li>
      ))}
    </ul>
  );
};

export default ObservationInspector;
