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
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import {
  listRemediationProjects, type RemediationPage, type RemediationProjects, type RemediationRow,
  type RemediationState,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { idParam } from '../utils/remediation';
import RemediationTrend from '../components/remediation/RemediationTrend';
import { useRemediationPolicy } from '../hooks/useRemediationPolicy';
import { formatApiError } from '../utils/apiErrors';
import { timelineSummary } from '../utils/remediation';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import RemediationWorkList from '../components/remediation/RemediationWorkList';
import { RemediationLead } from './Remediation';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';

const COLUMNS: Array<{ state: RemediationState; label: string; tone?: string }> = [
  { state: 'overdue', label: 'Overdue', tone: 'text-destructive' },
  { state: 'due_soon', label: 'Due soon', tone: 'text-warning' },
  { state: 'on_track', label: 'On track' },
  { state: 'not_assigned', label: 'Not assigned' },
  { state: 'deferred', label: 'Deferred' },
  { state: 'closed', label: 'Closed' },
];

const RemediationDeadlines: React.FC = () => {
  const { policy, enabled, loading } = useRemediationPolicy();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { projects: mine, selectProject } = useProject();
  const [changes, setChanges] = useState(0);
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
  const [projects, setProjects] = useState<RemediationProjects | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<RemediationPage | null>(null);
  const [filtered, setFiltered] = useState(false);
  const [showIdle, setShowIdle] = useState(false);
  const generation = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);

  const load = () => {
    const mine = ++generation.current;
    listRemediationProjects()
      .then((result) => { if (mine === generation.current) { setProjects(result); setError(null); } })
      .catch((err) => { if (mine === generation.current) setError(formatApiError(err, 'The projects could not be loaded.')); });
  };
  useEffect(() => {
    if (enabled) load();
    return () => { generation.current += 1; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  if (!enabled) {
    return (
      <div className="flex flex-col gap-lg p-md md:p-lg">
        <h1 className="text-page-title">Remediation deadlines</h1>
        <p className="max-w-4xl text-metadata text-muted-foreground">
          {loading ? 'Loading…' : 'Remediation tracking is not turned on for this installation. A global administrator turns it on in System settings.'}
        </p>
      </div>
    );
  }

  // A count in the projects table opens exactly its rows in the list below.
  const open = (projectId: number, state: RemediationState) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('view'); next.delete('contact'); next.delete('unassigned');
      next.set('project', String(projectId));
      next.set('state', state);
      return next;
    });
    listRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  };

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

  return (
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
            title={<>Projects{projects && <SectionCount>{projects.items.length}</SectionCount>}</>}
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
                      {COLUMNS.map((c) => <TableHead key={c.state} className="w-[7rem] text-right">{c.label}</TableHead>)}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {shown.map((p) => (
                      <TableRow key={p.project_id}>
                        <TableCell className="min-w-0 align-middle">
                          <span className="block truncate font-medium" title={p.name}>{p.name}</span>
                          {p.archived && <span className="block text-caption text-muted-foreground">Archived</span>}
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
                      </TableRow>
                    ))}
                    {projects.items.length > 1 && (
                      <TableRow className="hover:bg-transparent hover:shadow-none">
                        <TableCell className="align-middle font-medium">All</TableCell>
                        {COLUMNS.map((c) => (
                          <TableCell key={c.state} className="text-right align-middle font-medium tabular-nums">
                            {projects.totals[c.state].toLocaleString()}
                          </TableCell>
                        ))}
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

          <div ref={listRef} className="scroll-mt-16">
            <PostureSection title="Findings on hosts">
              <RemediationWorkList
                scope="all"
                canWrite
                policy={policy}
                projects={projects?.items}
                onLoaded={(next, isFiltered) => { setPage(next); setFiltered(isFiltered); }}
                onChanged={() => { load(); setChanges((n) => n + 1); }}
                canOpen={canOpen}
                onOpen={onOpen}
                where="across the projects you administer"
              />
            </PostureSection>
          </div>

          <PostureSection title="Over time">
            <RemediationTrend scope="all" projectId={idParam(params.get('project')) ?? undefined} reloadKey={changes} />
          </PostureSection>
        </>
      )}
    </div>
  );
};

export default RemediationDeadlines;
