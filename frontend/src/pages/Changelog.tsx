/**
 * Changelog — what the project's members, and the agents working for them,
 * did to the project's record, newest first.
 *
 * Read-only: a line is written by the change it describes, so there is
 * nothing here to change.  For project auditors and above (the route says so
 * too).  The filters — who, people or agents, what kind — and the page are
 * the address's (UI_STYLE_GUIDE §39), so a link to "what the agents did" is
 * a link.
 *
 * An agent's action is always said to be an agent's: "Ana's agent", never
 * "Ana" — the person whose authority it used did not do it by hand.
 */
import React, { useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Bot } from 'lucide-react';

import { getChangelog, type ChangelogBy, type ChangelogLine, type ChangelogPage } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import { usePagedList } from '../hooks/usePagedList';
import { useUrlPage } from '../hooks/useUrlPage';
import { PagedFooter } from '../components/operations/QueueParts';
import { ListFilterBar } from '../components/ListFilterBar';
import LastUpdated from '../components/LastUpdated';
import { TableSkeleton } from '../components/PageSkeleton';
import TimeAgo from '../components/TimeAgo';
import { Alert, AlertDescription } from '../components/ui/alert';
import { PostureSection, SectionCount } from '../components/posture/PostureSection';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '../components/ui/table';
import { cn } from '../utils/cn';
import { FORMER_MEMBER, personName } from '../utils/people';

const PAGE_SIZE = 50;
const NO_ROWS: ChangelogLine[] = [];

/** The kind filter's words (the server sends the kinds it can filter by). */
const GROUP_LABEL: Record<string, string> = {
  findings: 'Findings',
  observations: 'Scanner observations',
  tests: 'Tests and evidence',
  hosts: 'Hosts',
  scans: 'Scans and imports',
  scope: 'Scope',
  reports: 'Reports',
  proposals: 'Proposals',
  remediation: 'Remediation',
  project: 'Project and members',
  agents: 'Agent sessions',
};

const BY_OPTIONS: Array<{ value: ChangelogBy | null; label: string }> = [
  { value: null, label: 'Everyone' },
  { value: 'people', label: 'People' },
  { value: 'agents', label: 'Agents' },
];

/** Where a line's subject can be opened, or null: a subject with no page of
 *  its own, or one the line itself says is gone. */
const SUBJECT_GONE = new Set(['finding_deleted', 'name_deleted', 'report_deleted', 'scan_deleted']);

export const subjectPath = (line: ChangelogLine): string | null => {
  // Only the lines whose SUBJECT was deleted: a deleted note or comment is
  // about a host or a finding that is still there.
  if (line.subject_id == null || SUBJECT_GONE.has(line.action)) return null;
  switch (line.subject_type) {
    case 'finding': return `/findings/${line.subject_id}`;
    case 'host': return `/hosts/${line.subject_id}`;
    case 'agent_session': return `/agent-sessions/${line.subject_id}`;
    case 'report': return `/reports/${line.subject_id}`;
    case 'scan': return `/scans/${line.subject_id}`;
    default: return null;
  }
};

/** Who a line names: the person, or — always said — their agent. */
export const actorText = (line: ChangelogLine): string => {
  const who = line.actor ? personName(line.actor.full_name, line.actor.username) : FORMER_MEMBER;
  return line.by_agent ? `${who}’s agent` : who;
};

const chipClass = (active: boolean) => cn(
  'inline-flex items-center whitespace-nowrap rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
  active
    ? 'border-primary bg-primary/10 text-foreground'
    : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
);

const Changelog: React.FC = () => {
  const projectId = useProjectId();
  const [searchParams, setSearchParams] = useSearchParams();

  // The filters are read from the address on every render and changed by
  // writing it, once per action, dropping the page.
  const actorParam = searchParams.get('actor');
  const actorId = actorParam && /^\d+$/.test(actorParam) ? Number(actorParam) : null;
  const byParam = searchParams.get('by');
  const by: ChangelogBy | null = byParam === 'people' || byParam === 'agents' ? byParam : null;
  const group = searchParams.get('kind');
  const setFilter = useCallback((name: 'actor' | 'by' | 'kind', value: string | null) => {
    setSearchParams((prev) => {
      const out = new URLSearchParams(prev);
      if (value) out.set(name, value); else out.delete(name);
      out.delete('page');
      return out;
    }, { replace: true });
  }, [setSearchParams]);

  const list = usePagedList<ChangelogLine, ChangelogPage>(
    'getChangelog',
    ({ offset, limit, signal }) => getChangelog(projectId, { actorId, by, group, offset, limit }, signal),
    [projectId, actorId, by, group],
    { pageSize: PAGE_SIZE, errorMessage: 'Failed to load the changelog.', page: useUrlPage(), within: projectId },
  );
  const { page, setPage, loading, error } = list;
  // The rows on screen stay while another page or filter loads; the filter's
  // choices come from the last answer so they do not blink.
  const shown = list.response ?? list.lastResponse;
  const rows = shown?.items ?? NO_ROWS;
  const total = shown?.total ?? 0;
  const people = shown?.people ?? [];
  const groups = shown?.groups ?? [];
  const filtered = actorId != null || by != null || !!group;

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Changelog</h1>
          <p className="text-metadata text-muted-foreground">
            What this project’s members, and the agents working for them, did to its record — newest first.
            A line is written with the change itself; a bulk action is one line.
          </p>
        </div>
        <LastUpdated compact lastFetched={list.loadedAt} onRefresh={() => { void list.reload(); }} isLoading={loading} label="changelog" />
      </div>

      <ListFilterBar className="mb-md">
        <label className="inline-flex items-center gap-xs text-metadata text-muted-foreground">
          By
          <select
            aria-label="Whose actions"
            className="h-8 rounded-control border border-input bg-background px-xs text-metadata text-foreground"
            value={actorId ?? ''}
            onChange={(e) => setFilter('actor', e.target.value || null)}
          >
            <option value="">anyone</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>{personName(p.full_name, p.username)}</option>
            ))}
          </select>
        </label>
        <div className="flex flex-wrap items-center gap-xs" role="group" aria-label="People or agents">
          {BY_OPTIONS.map((opt) => (
            <button key={opt.label} type="button" aria-pressed={by === opt.value}
              className={chipClass(by === opt.value)} onClick={() => setFilter('by', opt.value)}>
              {opt.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-xs" role="group" aria-label="Kind of action">
          <button type="button" aria-pressed={!group} className={chipClass(!group)} onClick={() => setFilter('kind', null)}>
            All kinds
          </button>
          {groups.map((g) => (
            <button key={g} type="button" aria-pressed={group === g}
              className={chipClass(group === g)} onClick={() => setFilter('kind', g)}>
              {GROUP_LABEL[g] ?? g}
            </button>
          ))}
        </div>
      </ListFilterBar>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <PostureSection
        title={
          <>
            {filtered ? 'Matching actions' : 'All actions'}
            {total > 0 && <SectionCount>{total.toLocaleString()}</SectionCount>}
          </>
        }
        description={shown?.trimmed
          ? 'This project holds as many lines as this installation keeps; the oldest have been removed.'
          : undefined}
      >
        {loading && rows.length === 0 ? (
          <TableSkeleton rows={8} />
        ) : rows.length === 0 ? (
          !error && (
            <div className="py-md text-metadata text-muted-foreground" data-testid="changelog-empty">
              {filtered
                ? 'No action matches the current filter.'
                : 'Nothing has been recorded yet. The changelog starts when it was added to this installation; earlier work is on each finding’s, host’s and scan’s own page.'}
            </div>
          )
        ) : (
          <div className="overflow-x-auto">
            <Table style={{ tableLayout: 'fixed' }} className="min-w-[760px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[14%]">When</TableHead>
                  <TableHead className="w-[20%]">Who</TableHead>
                  <TableHead>What</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((line) => {
                  const to = subjectPath(line);
                  const label = line.subject_label;
                  return (
                    <TableRow key={line.id} data-testid={`changelog-line-${line.id}`}>
                      <TableCell className="align-top text-metadata text-muted-foreground">
                        <TimeAgo value={line.created_at} absoluteAfterDays={7} />
                      </TableCell>
                      <TableCell className="align-top">
                        <span className="inline-flex min-w-0 max-w-full items-center gap-xxs text-metadata">
                          {line.by_agent && <Bot className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />}
                          <span className="min-w-0 truncate" title={actorText(line)}>{actorText(line)}</span>
                        </span>
                      </TableCell>
                      <TableCell className="align-top">
                        <div className="min-w-0 break-words text-metadata">
                          {line.words}
                          {label && (
                            <>
                              {' '}
                              {to
                                ? <Link to={to} className="font-medium text-info hover:underline">{label}</Link>
                                : <span className="font-medium text-foreground">{label}</span>}
                            </>
                          )}
                          {line.summary && <span className="text-muted-foreground"> — {line.summary}</span>}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
        {total > PAGE_SIZE && rows.length > 0 && (
          <PagedFooter
            pager={{ page, pageSize: PAGE_SIZE, total, onPage: setPage }}
            shown={Math.max(0, Math.min(rows.length, total - page * PAGE_SIZE))}
            noun="actions"
            busy={loading}
          />
        )}
      </PostureSection>
    </div>
  );
};

export default Changelog;
