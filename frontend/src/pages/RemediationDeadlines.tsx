/**
 * Remediation deadlines (5.340.0) — the cross-project follow-up page: every
 * finding on a host that is assigned to someone to fix, across the projects
 * the reader ADMINISTERS (every project for a global administrator), by where
 * it stands against its deadline.  Archived projects are included: testing
 * has ended there, remediation runs until every finding is dispositioned.
 *
 * Lead sentence, a table of the projects (each count opens its list below),
 * then the shared work list — by finding on a host, or by contact, which is
 * where a follow-up is prepared and recorded.
 *
 * It exists only on an installation that turned remediation tracking on.
 */
import React, { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';

import {
  listRemediationProjects, type RemediationPage, type RemediationProjects, type RemediationRow,
  type RemediationState,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { idParam } from '../utils/remediation';
import RemediationTrend from '../components/remediation/RemediationTrend';
import { useRemediationPolicy } from '../hooks/useRemediationPolicy';
import { queryErrorText } from '../lib/query';
import { REPORTED_FIXED, timelineSummary } from '../utils/remediation';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import RemediationWorkList, { VIEW_TITLE } from '../components/remediation/RemediationWorkList';
import { RemediationLead } from './Remediation';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { InfoTip } from '../components/ui/info-tip';
import DefectMetricsSection from '../components/remediation/DefectMetricsSection';

const COLUMNS: Array<{ state: RemediationState; label: string; tone?: string }> = [
  { state: 'overdue', label: 'Overdue', tone: 'text-destructive' },
  { state: 'due_soon', label: 'Due soon', tone: 'text-warning' },
  { state: 'on_track', label: 'On track' },
  { state: 'not_assigned', label: 'Not assigned' },
  { state: 'deferred', label: 'Deferred' },
  { state: 'closed', label: REPORTED_FIXED },
];

/** A count that opens its rows; a zero is plain.  (No dash for "a server
 *  that predates it": backend and frontend ship together — third review
 *  2026-10-15, R5.) */
const countButton = (value: number, label: string, onClick: () => void, tone: string) => (
  value === 0 ? <span className="text-muted-foreground">0</span> : (
    <button type="button" onClick={onClick} aria-label={label} title={label}
      className={`rounded hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${tone}`}>
      {value.toLocaleString()}
    </button>
  )
);

const RemediationDeadlines: React.FC = () => {
  const { policy, enabled, loading, error: policyError, retry: retryPolicy } = useRemediationPolicy();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { projects: mine, selectProject } = useProject();
  // A row opens its host or finding in ITS project: switch, then go.  Only a
  // project the reader can switch to — an archived one is not in that list —
  // so the page never shows one project's page under another's name.
  const canOpen = (row: RemediationRow) => mine.some((p) => p.id === row.project_id);
  const onOpen = (row: RemediationRow, path: string) => {
    const project = mine.find((p) => p.id === row.project_id);
    if (!project) return;
    selectProject(project);
    navigate(path);
  };
  // Re-read after a save by the write itself (`invalidateRemediationReads`).
  const query = useQuery({
    queryKey: ['listRemediationProjects'],
    queryFn: ({ signal }) => listRemediationProjects(signal),
    enabled,
  });
  const projects: RemediationProjects | null = query.data ?? null;
  const error = queryErrorText(query.error, 'The projects could not be loaded.');
  const load = () => { void query.refetch(); };
  const [showIdle, setShowIdle] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  if (!enabled) {
    return (
      <div className="flex flex-col gap-lg p-md md:p-lg">
        <h1 className="text-page-title">Remediation deadlines</h1>
        {policyError ? (
          <p role="alert" className="max-w-4xl break-words text-metadata text-destructive">
            {policyError}{' '}
            <button type="button" className="text-info hover:underline" onClick={retryPolicy}>Retry</button>
          </p>
        ) : (
          <p className="max-w-4xl text-metadata text-muted-foreground">
            {loading ? 'Loading…' : 'Remediation tracking is not turned on for this installation. A global administrator turns it on in System settings.'}
          </p>
        )}
      </div>
    );
  }

  // A count in the projects table or the defect metrics opens exactly its
  // rows in the list below: the filters it was counted under, nothing else.
  const openList = (set: { project?: number | null; state?: RemediationState; defect?: string[];
    severity?: string[] | null; active?: boolean }) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      ['view', 'contact', 'unassigned', 'state', 'defect', 'severity', 'active', 'band', 'stale', 'verification',
        'flag', 'team', 'q', 'page'].forEach((k) => next.delete(k));
      if (set.project != null) next.set('project', String(set.project)); else next.delete('project');
      if (set.state) next.set('state', set.state);
      set.defect?.forEach((d) => next.append('defect', d));
      set.severity?.forEach((sev) => next.append('severity', sev));
      if (set.active) next.set('active', '1');
      return next;
    });
    listRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  };
  const open = (projectId: number, state: RemediationState) => openList({ project: projectId, state });
  // The defect metrics follow the page's project filter: one project, else
  // every administered project that is not archived.
  const metricsProject = idParam(params.get('project'));
  const metricsProjectName = projects?.items.find((p) => p.project_id === metricsProject)?.name;

  const none = projects != null && projects.items.length === 0;

  // What needs someone first; a project where nothing has been assigned or
  // closed has no deadline to follow, and a wall of zero rows hid the two
  // projects that had (seen in the browser).  Those fold into one line.
  const tracked = (p: RemediationProjects['items'][number]) =>
    p.states.overdue + p.states.due_soon + p.states.on_track + p.states.deferred + p.states.closed > 0;
  const ranked = [...(projects?.items ?? [])].sort((a, b) =>
    b.states.overdue - a.states.overdue || b.states.due_soon - a.states.due_soon
    || b.states.on_track - a.states.on_track || a.name.localeCompare(b.name));
  const idle = ranked.filter((p) => !tracked(p));
  const shown = showIdle ? ranked : ranked.filter(tracked);
  const idleUnassigned = idle.reduce((sum, p) => sum + p.states.not_assigned, 0);

  // The page around the work list, drawn with what the list has on screen:
  // the lead reads the list's answer where it is shown (the list hands it to
  // its `children`), never from a copy kept in this page's state.
  const layout = ({ page, filtered, list, title }: {
    page: RemediationPage | null; filtered: boolean; list: React.ReactNode; title: string;
  }) => (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      <div className="min-w-0">
        <h1 className="text-page-title">Remediation deadlines</h1>
        <p className="mt-xxs max-w-4xl text-metadata text-muted-foreground">
          {none
            ? 'You administer no project, so there is nothing to follow up here. A project’s administrators follow its remediation.'
            : <RemediationLead page={page} filtered={filtered} dueSoonDays={policy?.due_soon_days ?? 7}
                where="across the projects you administer" />}
        </p>
      </div>

      {!none && (
        <>
          <PostureSection
            // The count is of the rows listed: the projects folded into the
            // line under the table are counted there ("Not shown: 8 projects…").
            title={<>Projects{projects && <SectionCount>{shown.length}</SectionCount>}</>}
            description={policy ? `${timelineSummary(policy)}, counted from the day a finding is assigned.` : undefined}
          >
            {error && (
              <p role="alert" className="text-caption text-destructive">
                {error} <button type="button" className="text-info hover:underline" onClick={load}>Retry</button>
              </p>
            )}
            {!projects && !error && <p className="text-caption text-muted-foreground">Loading…</p>}
            {projects && (
              <div className="min-w-0 overflow-x-auto">
                <Table aria-label="Remediation deadlines by project" className="min-w-[54rem] table-fixed">
                  <TableHeader>
                    <TableRow className="hover:bg-transparent hover:shadow-none">
                      <TableHead>Project</TableHead>
                      <TableHead className="w-[6rem] text-right">Total</TableHead>
                      {COLUMNS.map((c) => <TableHead key={c.state} className="w-[7rem] text-right">{c.label}</TableHead>)}
                      <TableHead className="w-[8rem] text-right">
                        <span className="inline-flex items-center justify-end gap-xxs">
                          Remediated
                          <InfoTip label="What Remediated counts"
                            text={`Findings on hosts whose endpoint was marked Remediated after the retest — the assessment's conclusion, not the contact's "${REPORTED_FIXED}".`} />
                        </span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {shown.map((p) => (
                      <TableRow key={p.project_id}>
                        <TableCell className="min-w-0 align-middle">
                          <span className="block truncate font-medium" title={p.name}>{p.name}</span>
                          {p.archived && <span className="block text-caption text-muted-foreground">Archived</span>}
                        </TableCell>
                        <TableCell className="text-right align-middle tabular-nums">
                          {countButton(p.total, `${p.name}: show every finding on a host (${p.total.toLocaleString()})`,
                            () => openList({ project: p.project_id }), 'font-medium text-foreground')}
                        </TableCell>
                        {COLUMNS.map((c) => {
                          const value = p.states[c.state];
                          return (
                            <TableCell key={c.state} className="text-right align-middle tabular-nums">
                              {value === 0 ? <span className="text-muted-foreground">0</span> : (
                                <button type="button" onClick={() => open(p.project_id, c.state)}
                                  title={`${p.name}: show ${c.label.toLowerCase()}`}
                                  aria-label={`${p.name}: show ${c.label.toLowerCase()} (${value.toLocaleString()})`}
                                  className={`rounded hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${c.tone ? `font-medium ${c.tone}` : 'text-foreground'}`}>
                                  {value.toLocaleString()}
                                </button>
                              )}
                            </TableCell>
                          );
                        })}
                        <TableCell className="text-right align-middle tabular-nums">
                          {countButton(p.remediated, `${p.name}: show remediated (${p.remediated.toLocaleString()})`,
                            () => openList({ project: p.project_id, defect: ['remediated'] }), 'text-foreground')}
                        </TableCell>
                      </TableRow>
                    ))}
                    {projects.items.length > 1 && (
                      <TableRow className="hover:bg-transparent hover:shadow-none">
                        <TableCell className="align-middle font-medium">All</TableCell>
                        {/* Every count opens its list (review 2026-10-14, R14): the
                            list of every project above, archived ones included, as
                            the row adds them up. */}
                        <TableCell className="text-right align-middle font-medium tabular-nums">
                          {countButton(projects.defect_totals.total,
                            `All projects: show every finding on a host (${projects.defect_totals.total.toLocaleString()})`,
                            () => openList({}), 'font-medium text-foreground')}
                        </TableCell>
                        {COLUMNS.map((c) => (
                          <TableCell key={c.state} className="text-right align-middle font-medium tabular-nums">
                            {countButton(projects.totals[c.state],
                              `All projects: show ${c.label.toLowerCase()} (${projects.totals[c.state].toLocaleString()})`,
                              () => openList({ state: c.state }), c.tone ? `font-medium ${c.tone}` : 'font-medium text-foreground')}
                          </TableCell>
                        ))}
                        <TableCell className="text-right align-middle font-medium tabular-nums">
                          {countButton(projects.defect_totals.remediated,
                            `All projects: show remediated (${projects.defect_totals.remediated.toLocaleString()})`,
                            () => openList({ defect: ['remediated'] }), 'font-medium text-foreground')}
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
                {idle.length > 0 && (
                  <p className="mt-xs text-caption text-muted-foreground">
                    {showIdle ? 'Showing' : 'Not shown:'} {idle.length.toLocaleString()}{' '}
                    {idle.length === 1 ? 'project' : 'projects'} where nothing has been assigned yet
                    {idleUnassigned > 0 && ` (${idleUnassigned.toLocaleString()} ${idleUnassigned === 1 ? 'finding on a host' : 'findings on hosts'} not assigned)`}.{' '}
                    <button type="button" className="text-info hover:underline" aria-expanded={showIdle}
                      onClick={() => setShowIdle((v) => !v)}>
                      {showIdle ? 'Hide them' : 'Show them'}
                    </button>
                  </p>
                )}
              </div>
            )}
          </PostureSection>

          <PostureSection
            title="Defect metrics"
            description={metricsProject != null
              ? `${metricsProjectName ?? 'The chosen project'}: each finding on a host is a defect, false positives left out.`
              : 'Every project you administer that is not archived: each finding on a host is a defect, false positives left out. Choose a project in the list to see one.'}
          >
            <DefectMetricsSection projectId={metricsProject}
              onOpen={({ defect, severity }) => openList({
                project: metricsProject, defect, severity, active: metricsProject == null,
              })} />
          </PostureSection>

          <div ref={listRef} className="scroll-mt-16">
            <PostureSection title={title}>{list}</PostureSection>
          </div>

          <PostureSection title="Over time">
            <RemediationTrend scope="all" projectId={idParam(params.get('project')) ?? undefined} />
          </PostureSection>
        </>
      )}
    </div>
  );

  // With no project to administer there is no list to read (and none is asked for).
  if (none) return layout({ page: null, filtered: false, list: null, title: VIEW_TITLE.rows });
  return (
    <RemediationWorkList
      scope="all"
      canWrite
      policy={policy}
      projects={projects?.items}
      canOpen={canOpen}
      onOpen={onOpen}
      where="across the projects you administer"
    >
      {layout}
    </RemediationWorkList>
  );
};

export default RemediationDeadlines;
