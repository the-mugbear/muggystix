/**
 * The remediation work list (5.340.0), shared by the project's Remediation
 * page and the cross-project follow-up page: findings on hosts by where they
 * stand against their deadline, or the same rows rolled up by contact.
 *
 * One row is one finding ON ONE HOST.  The deadline is derived on the server
 * (assigned date + the installation's days for the finding's severity); this
 * component never computes a state, it shows the one it is given.
 *
 * Filters live in the URL.  `scope="all"` lists every project the reader
 * administers (archived included) and reads and writes each row through its
 * own project.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { Copy, Download, History, Search, X } from 'lucide-react';

import {
  listRemediation, listRemediationContacts, listRemediationOverview, listRemediationTeams,
  type RemediationContact, type RemediationPage, type RemediationPolicy, type RemediationQuery,
  type RemediationRow, type RemediationState, type RemediationTeam,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { GLOBAL, holdProject, queryErrorText } from '../../lib/query';
import { copyToClipboard } from '../../utils/clipboard';
import { saveBlob } from '../../utils/download';
import { usePagedList } from '../../hooks/usePagedList';
import { useUrlPage } from '../../hooks/useUrlPage';
import { useListCursor } from '../../hooks/useListCursor';
import { formatDate } from '../../utils/relativeTime';
import {
  REMEDIATION_GROUPS, REMEDIATION_GROUP_LABEL, REMEDIATION_PAGE_SIZE, REMEDIATION_PAGE_SIZES,
  OVERDUE_BAND_LABEL, REMEDIATION_FLAG_LABEL, REMEDIATION_STATES, REMEDIATION_STATE_HELP, REMEDIATION_STATE_LABEL,
  REMEDIATION_VERIFICATION_LABEL, REPORTED_FIXED, SEARCH_MAX, deadlineCell, idParam,
  isOverdueBand, isRemediationFlag, isRemediationGroup, isRemediationState, isRemediationVerification, remediationCsv,
  remediationPageSize, remediationSummary, searchParam, severityWord, timelineSummary, verificationNote,
} from '../../utils/remediation';
import RemediationInsights, { RemediationVerificationCounts } from './RemediationInsights';
import {
  BulkBar, FilterChips, ListBody, PagedFooter, filterChipClass, useRowSelection,
} from '../operations/QueueParts';
import RemediationEditDialog from './RemediationEditDialog';
import RemediationContactReportDialog from './RemediationContactReportDialog';
import RemediationFollowUpDialog from './RemediationFollowUpDialog';
import RemediationTimeline from './RemediationTimeline';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { SeverityBadge } from '../ui/SeverityBadge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

const NO_ROWS: RemediationRow[] = [];
const NO_CONTACTS: RemediationContact[] = [];
const NO_TEAMS: RemediationTeam[] = [];
/** The most rows one CSV holds; beyond it the page says to narrow the list. */
export const CSV_MAX_ROWS = 20000;
const LINK = 'rounded text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';
const ALL_PROJECTS = '__all__';
// How far ahead "Remind" looks for a contact with nothing overdue or due soon
// (one of the follow-up dialog's horizons).
const UPCOMING_REMINDER_DAYS = 90;
const ANY_SEVERITY = '__any__';
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];

/** Findings on hosts still open in a project: every state but deferred and reported fixed. */
export const openCount = (states: Record<RemediationState, number>): number =>
  states.overdue + states.due_soon + states.on_track + states.not_assigned + states.no_deadline;

const Dash: React.FC<{ label: string }> = ({ label }) => (
  <span className="text-muted-foreground" title={label}>
    <span aria-hidden>—</span><span className="sr-only">{label}</span>
  </span>
);

const count = (n: number, tone?: string) => (
  n === 0 ? <span className="text-muted-foreground">0</span>
    : <span className={tone ? `font-medium ${tone}` : undefined}>{n.toLocaleString()}</span>
);

export interface RemediationWorkListProps {
  scope: 'project' | 'all';
  canWrite: boolean;
  policy: RemediationPolicy | null;
  /** `scope="all"`: the projects to choose from. */
  projects?: Array<{
    project_id: number; name: string; archived: boolean; states: Record<RemediationState, number>;
  }>;
  /** Told each page that loads, for the page's lead sentence. */
  onLoaded?: (page: RemediationPage, filtered: boolean) => void;
  /** `scope="all"`: open a row's host or finding in ITS project (the page
   *  switches project first).  A row whose project cannot be opened — an
   *  archived one, or one the reader is not in — stays text. */
  canOpen?: (row: RemediationRow) => boolean;
  onOpen?: (row: RemediationRow, path: string) => void;
  /** "in this project" / "across the projects you administer" — for the copied summary. */
  where?: string;
}

export const RemediationWorkList: React.FC<RemediationWorkListProps> = ({
  scope, canWrite, policy, projects, onLoaded, canOpen, onOpen, where = 'in this project',
}) => {
  const toast = useToast();
  const across = scope === 'all';
  const [params, setParams] = useSearchParams();

  // A value the page does not know (a typo, an old link) is the default,
  // never a request the API refuses.
  const stateParam = params.get('state');
  const state: RemediationState | null = isRemediationState(stateParam) ? stateParam : null;
  const groupParam = params.get('group');
  const group = isRemediationGroup(groupParam) ? groupParam : 'due';
  const contact = params.get('contact') ?? '';
  const unassigned = params.get('unassigned') === '1';
  const severityParam = params.get('severity');
  const severity = severityParam && SEVERITIES.includes(severityParam) ? severityParam : null;
  const viewParam = params.get('view');
  const view: 'rows' | 'contacts' | 'teams' = viewParam === 'contacts' || viewParam === 'teams' ? viewParam : 'rows';
  const team = params.get('team') ?? '';
  const bandParam = params.get('band');
  const band = isOverdueBand(bandParam) ? bandParam : null;
  // Overdue or due soon, and nobody followed up within the warning window.
  const stale = params.get('stale') === '1';
  // Where the contact's record and the assessment disagree (the server derives it).
  const verificationParam = params.get('verification');
  const verification = isRemediationVerification(verificationParam) ? verificationParam : null;
  // Deferrals to review, or due dates set by hand (the server derives both).
  const flagParam = params.get('flag');
  const flag = isRemediationFlag(flagParam) ? flagParam : null;
  // A finding's title, or a host's address or name.
  const q = searchParam(params.get('q'));
  // A link from a host or a finding narrows the list to it (`?host=`, `?finding=`).
  const hostId = across ? null : idParam(params.get('host'));
  const findingId = across ? null : idParam(params.get('finding'));
  const projectId = across ? idParam(params.get('project')) : null;
  const pageSize = remediationPageSize(params.get('per'));

  const setParam = (key: string, value: string | undefined) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value == null || value === '') next.delete(key); else next.set(key, value);
      return next;
    }, { replace: key === 'contact' || key === 'q' });
  };

  // The search box is typed into; the address (and the request) follow a
  // moment later.
  const [typed, setTyped] = useState(contact);
  useEffect(() => { setTyped(contact); }, [contact]);
  useEffect(() => {
    if (typed.trim() === contact) return undefined;
    const timer = window.setTimeout(() => setParam('contact', typed.trim() || undefined), 300);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typed]);

  // The same for "Finding or host": one character searches nothing, so the
  // address only ever holds a search the server takes.
  const [sought, setSought] = useState(q);
  // Follows the address (Back, a cleared chip) without wiping a single
  // character the reader has typed so far.
  useEffect(() => { setSought((s) => (searchParam(s) === q ? s : q)); }, [q]);
  useEffect(() => {
    if (searchParam(sought) === q) return undefined;
    const timer = window.setTimeout(() => setParam('q', searchParam(sought) || undefined), 300);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sought]);

  // The project chooser lists only projects with something still open (owner,
  // 2026-10-08), each with its count; the one in the address stays listed
  // even at zero, so the control never shows a project it does not offer.
  const choices = useMemo(
    () => (projects ?? []).filter((p) => openCount(p.states) > 0 || p.project_id === projectId),
    [projects, projectId],
  );

  const filtered = !!contact || unassigned || hostId != null || findingId != null || projectId != null
    || severity != null || !!team || band != null || stale || verification != null || flag != null || !!q;

  // The filters of the list, as the API takes them — the list, the CSV and
  // nothing else read them, so the file holds exactly the rows on screen.
  const filters = useMemo((): RemediationQuery => ({
    state: state ? [state] : undefined, contact: contact || undefined, unassigned: unassigned || undefined,
    host_id: hostId ?? undefined, finding_id: findingId ?? undefined, severity: severity ?? undefined,
    team: team || undefined, overdue_band: band ?? undefined,
    no_follow_up_days: stale ? (policy?.due_soon_days || 7) : undefined,
    verification: verification ?? undefined,
    // Left out entirely when unset, so the request reads as it always did.
    ...(flag ? { flag } : {}),
    ...(q ? { q } : {}),
    project_id: projectId ?? undefined,
  }), [state, contact, unassigned, hostId, findingId, severity, team, band, stale, verification, flag, q, projectId, policy]);

  // The rows' page is in the address (`?page=`): a reload, or Back from a
  // host or a finding, returns to the rows that were on screen.
  const urlPage = useUrlPage();
  const list = usePagedList<RemediationRow, RemediationPage>(
    across ? 'listRemediationOverview' : 'listRemediation',
    ({ offset, limit, signal }) => {
      const query = { ...filters, group, offset, limit };
      return across ? listRemediationOverview(query, signal) : listRemediation(query, signal);
    },
    [filters, group, pageSize, view === 'rows'],
    // Across projects the rows are not one project's: a GLOBAL key, like the
    // contacts and the teams below.
    { pageSize, errorMessage: 'The remediation list could not be loaded.', page: urlPage, global: across },
  );
  const rows = list.rows ?? NO_ROWS;
  const counts = list.lastResponse?.state_counts ?? null;
  const all = counts ? REMEDIATION_STATES.reduce((sum, s) => sum + counts[s], 0) : null;

  useEffect(() => {
    if (list.lastResponse) onLoaded?.(list.lastResponse, filtered);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.lastResponse]);

  // The contacts and the teams, each read only while its view is open.
  const people = useQuery({
    queryKey: across
      ? [GLOBAL, 'listRemediationContacts', 'all', projectId ?? undefined]
      : ['listRemediationContacts', undefined, projectId ?? undefined],
    queryFn: ({ signal }) => listRemediationContacts(across ? 'all' : undefined, projectId ?? undefined, signal),
    enabled: view === 'contacts',
  });
  const contacts = people.data ?? NO_CONTACTS;

  const groups = useQuery({
    queryKey: across
      ? [GLOBAL, 'listRemediationTeams', 'all', projectId ?? undefined]
      : ['listRemediationTeams', undefined, projectId ?? undefined],
    queryFn: ({ signal }) => listRemediationTeams(across ? 'all' : undefined, projectId ?? undefined, signal),
    enabled: view === 'teams',
  });
  const teams = groups.data ?? NO_TEAMS;

  // Open the rows view on a set of filters, dropping the ones that would
  // contradict them.
  const openRows = (set: Record<string, string>) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    ['view', 'state', 'severity', 'band', 'stale', 'team', 'contact', 'unassigned'].forEach((k) => next.delete(k));
    Object.entries(set).forEach(([k, v]) => next.set(k, v));
    return next;
  });
  // A gap count is taken over the selection before the state filters, so
  // opening it drops those and keeps the rest (contact, team, severity…).
  const openVerification = (value: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    ['view', 'state', 'band', 'stale', 'flag'].forEach((k) => next.delete(k));
    next.set('verification', value);
    return next;
  });
  // The flag counts follow the same rule, and are taken before the gap filter too.
  const openFlag = (value: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    ['view', 'state', 'band', 'stale', 'verification'].forEach((k) => next.delete(k));
    next.set('flag', value);
    return next;
  });

  // --- handing it to someone else -----------------------------------------
  const csv = useMutation({
    mutationFn: async (): Promise<{ all: RemediationRow[]; total: number }> => {
      const all: RemediationRow[] = [];
      let total = Infinity;
      // One project's rows only: a page asked for after the reader switched
      // project would be the other project's (the cross-project list names
      // no project in its address and is not held).
      const stillHere = across ? () => {} : holdProject();
      // The same filters, 200 rows a call, one call at a time.
      while (all.length < Math.min(total, CSV_MAX_ROWS)) {
        const query = { ...filters, group, offset: all.length, limit: 200 };
        const next = await (across ? listRemediationOverview(query) : listRemediation(query));
        stillHere();
        total = next.total;
        if (next.items.length === 0) break;
        all.push(...next.items);
      }
      saveBlob(new Blob([`\uFEFF${remediationCsv(all)}`], { type: 'text/csv;charset=utf-8' }),
        `remediation-${list.lastResponse?.as_of ?? 'export'}.csv`);
      return { all, total };
    },
    onSuccess: ({ all, total }) => {
      if (total > all.length) {
        toast.warning(`The file holds the first ${all.length.toLocaleString()} of ${total.toLocaleString()} rows. Narrow the list to get the rest.`);
      } else {
        toast.success(`${all.length.toLocaleString()} ${all.length === 1 ? 'row' : 'rows'} saved as CSV.`);
      }
    },
    onError: () => toast.error('The CSV could not be built. Nothing was saved.'),
  });
  const exporting = csv.isPending;
  const copySummary = async () => {
    if (!list.lastResponse) return;
    const text = remediationSummary(list.lastResponse, {
      where: filtered ? `${where} (filtered)` : where,
      dueSoonDays: policy?.due_soon_days ?? 7, timeline: policy ? timelineSummary(policy) : undefined,
    });
    if (await copyToClipboard(text)) toast.success('Summary copied.');
    else toast.error('The summary could not be copied.');
  };

  const keys = useMemo(() => rows.map((r) => r.finding_host_id), [rows]);
  const selection = useRowSelection(keys);
  const selectedRows = useMemo(
    () => rows.filter((r) => selection.selected.includes(r.finding_host_id)),
    [rows, selection.selected],
  );

  const [editing, setEditing] = useState<RemediationRow[] | null>(null);
  const [timeline, setTimeline] = useState<RemediationRow | null>(null);
  const [followUp, setFollowUp] = useState<string | null>(null);
  // A contact with nothing overdue or due soon is reminded of what is coming.
  const [followUpAhead, setFollowUpAhead] = useState(0);
  const [report, setReport] = useState<RemediationContact | null>(null);
  // A contact's list is ONE project's document: on the cross-project page it
  // needs the project chosen first.
  const reportProject = across ? projects?.find((p) => p.project_id === projectId) ?? null : null;
  const canReport = canWrite && (!across || reportProject != null);


  // After a save.  The write itself has every remediation read on screen
  // re-read in place (`invalidateRemediationReads`): the reader keeps their
  // page.  What is left for the list is its own selection.
  const changed = () => selection.clear();

  // Every row of a narrowed list is that host's (or that finding's), so the
  // first one names it; the id stands in until a row has loaded.
  const narrowed: Array<{ key: 'host' | 'finding'; label: string }> = [];
  if (hostId != null) {
    narrowed.push({ key: 'host', label: `Host ${rows.find((r) => r.host_id === hostId)?.ip_address ?? `#${hostId}`}` });
  }
  if (findingId != null) {
    narrowed.push({
      key: 'finding',
      label: `Finding ${rows.find((r) => r.finding_id === findingId)?.finding_title ?? `#${findingId}`}`,
    });
  }

  const extra: Array<{ key: string; label: string }> = [];
  if (team) extra.push({ key: 'team', label: `Team ${team}` });
  if (band) extra.push({ key: 'band', label: `Overdue by ${OVERDUE_BAND_LABEL[band].toLowerCase()}` });
  if (stale) extra.push({ key: 'stale', label: `Overdue or due soon, no follow-up in ${policy?.due_soon_days || 7} days` });
  if (verification) extra.push({ key: 'verification', label: REMEDIATION_VERIFICATION_LABEL[verification] });
  if (flag) extra.push({ key: 'flag', label: REMEDIATION_FLAG_LABEL[flag] });

  // j / k move, Enter opens the row: the editor for an admin, the timeline otherwise.
  const { cursorRowProps } = useListCursor(
    rows.length,
    (i) => { if (canWrite) setEditing([rows[i]]); else setTimeline(rows[i]); },
    {
      enabled: view === 'rows' && editing === null && timeline === null && followUp === null && report === null,
      resetKey: list.page, getId: (i) => rows[i]?.finding_host_id,
    },
  );

  // In this app's own project a host or a finding is a link; across projects
  // it is text — following it would land in whichever project is selected.
  const hostCell = (r: RemediationRow) => {
    const title = r.hostname ? `${r.ip_address} · ${r.hostname}` : r.ip_address;
    if (across && canOpen?.(r) && onOpen) {
      return (
        <button type="button" className={`${LINK} block max-w-full truncate text-left font-mono`}
          title={`${title} — open in ${r.project_name}`} onClick={() => onOpen(r, `/hosts/${r.host_id}`)}>
          {r.ip_address}
        </button>
      );
    }
    return across
      ? <span className="block truncate font-mono" title={title}>{r.ip_address}</span>
      : <Link to={`/hosts/${r.host_id}`} className={`${LINK} block truncate font-mono`} title={title}>{r.ip_address}</Link>;
  };
  const findingCell = (r: RemediationRow) => (across && canOpen?.(r) && onOpen
    ? (
      <button type="button" className={`${LINK} line-clamp-2 min-w-0 break-words text-left`}
        title={`${r.finding_title} — open in ${r.project_name}`}
        onClick={() => onOpen(r, `/findings/${r.finding_id}?endpoint=${r.finding_host_id}#endpoints`)}>
        {r.finding_title}
      </button>
    ) : across
    ? <span className="line-clamp-2 min-w-0 break-words" title={r.finding_title}>{r.finding_title}</span>
    : (
      <Link to={`/findings/${r.finding_id}?endpoint=${r.finding_host_id}#endpoints`}
        className={`${LINK} line-clamp-2 min-w-0 break-words`} title={r.finding_title}>
        {r.finding_title}
      </Link>
    ));

  const viewButton = (key: 'rows' | 'contacts' | 'teams', label: string) => (
    <button type="button" role="tab" aria-selected={view === key}
      className={filterChipClass(view === key)}
      onClick={() => setParam('view', key === 'rows' ? undefined : key)}>
      {label}
    </button>
  );

  return (
    <div className="min-w-0">
      <div className="mb-sm flex min-w-0 flex-wrap items-center gap-sm">
        <div role="tablist" aria-label="How the list is shown" className="flex items-center gap-xs">
          {viewButton('rows', 'Findings on hosts')}
          {viewButton('contacts', 'By contact')}
          {viewButton('teams', 'By team')}
        </div>
        {across && choices.length > 1 && (
          <>
            <Label htmlFor="rem-project" className="shrink-0">Project</Label>
            <Select value={projectId != null ? String(projectId) : ALL_PROJECTS}
              onValueChange={(v) => setParam('project', v === ALL_PROJECTS ? undefined : v)}>
              <SelectTrigger id="rem-project" className="h-8 w-80 max-w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_PROJECTS}>
                  All projects with open findings ({choices.filter((p) => openCount(p.states) > 0).length})
                </SelectItem>
                {choices.map((p) => (
                  <SelectItem key={p.project_id} value={String(p.project_id)}>
                    {p.name}{p.archived ? ' (archived)' : ''} · {openCount(p.states).toLocaleString()} open
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        )}
      </div>

      {view === 'teams' ? (
        <ListBody
          rows={groups.data ?? null}
          state={{
            loading: groups.isFetching, error: queryErrorText(groups.error, 'The teams could not be loaded.'),
            onRetry: () => void groups.refetch(),
          }}
          what="the teams"
          empty="No finding has a team yet. Set a team on a row — or on a selection — and it shows here."
        >
          {() => (
            <div className="min-w-0 overflow-x-auto">
              <Table aria-label="Teams and their remediation deadlines" className="min-w-[50rem] table-fixed">
                <TableHeader>
                  <TableRow className="hover:bg-transparent hover:shadow-none">
                    <TableHead>Team</TableHead>
                    <TableHead className="w-[6rem] text-right">Overdue</TableHead>
                    <TableHead className="w-[6rem] text-right">Due soon</TableHead>
                    <TableHead className="w-[6rem] text-right">On track</TableHead>
                    <TableHead className="w-[6rem] text-right">All open</TableHead>
                    <TableHead className="w-[8rem] text-right">{REPORTED_FIXED}</TableHead>
                    <TableHead className="w-[6rem] text-right">Contacts</TableHead>
                    {across && <TableHead className="w-[6rem] text-right">Projects</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {teams.map((t) => (
                    <TableRow key={t.team ?? '\u0000none'}>
                      <TableCell className="min-w-0 align-middle">
                        {t.team ? (
                          <button type="button" className={`${LINK} block max-w-full truncate text-left`}
                            title={`Show the findings on hosts of ${t.team}`} onClick={() => openRows({ team: t.team as string })}>
                            {t.team}
                          </button>
                        ) : <span className="text-muted-foreground">A contact, no team</span>}
                      </TableCell>
                      <TableCell className="text-right align-middle tabular-nums">{count(t.overdue, 'text-destructive')}</TableCell>
                      <TableCell className="text-right align-middle tabular-nums">{count(t.due_soon, 'text-warning')}</TableCell>
                      <TableCell className="text-right align-middle tabular-nums">{count(t.on_track)}</TableCell>
                      <TableCell className="text-right align-middle tabular-nums">{count(t.open)}</TableCell>
                      <TableCell className="text-right align-middle tabular-nums">{count(t.closed)}</TableCell>
                      <TableCell className="text-right align-middle tabular-nums">{count(t.contacts)}</TableCell>
                      {across && <TableCell className="text-right align-middle tabular-nums">{t.projects}</TableCell>}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </ListBody>
      ) : view === 'contacts' ? (
        <ListBody
          rows={people.data ?? null}
          state={{
            loading: people.isFetching, error: queryErrorText(people.error, 'The contacts could not be loaded.'),
            onRetry: () => void people.refetch(),
          }}
          what="the contacts"
          empty="Nobody has been assigned a finding yet. Name a contact on a row and they show here."
        >
          {() => (
            <div className="min-w-0 overflow-x-auto">
              {across && canWrite && reportProject == null && (
                <p className="mb-xs text-caption text-muted-foreground">
                  Follow up covers every project. A contact’s list as a document is one project’s: choose a project above to prepare one.
                </p>
              )}
              <Table aria-label="Remediation contacts and their deadlines" className="min-w-[57rem] table-fixed">
                <TableHeader>
                  <TableRow className="hover:bg-transparent hover:shadow-none">
                    <TableHead>Contact</TableHead>
                    <TableHead className="w-[6rem] text-right">Overdue</TableHead>
                    <TableHead className="w-[6rem] text-right">Due soon</TableHead>
                    <TableHead className="w-[6rem] text-right">On track</TableHead>
                    <TableHead className="w-[6rem] text-right">All open</TableHead>
                    {across && <TableHead className="w-[6rem] text-right">Projects</TableHead>}
                    <TableHead className="w-[9rem]">Last followed up</TableHead>
                    <TableHead className="w-[12.5rem]"><span className="sr-only">Actions</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {contacts.map((c) => {
                    const atRisk = c.overdue + c.due_soon;
                    return (
                      <TableRow key={c.contact_email}>
                        <TableCell className="min-w-0 align-middle">
                          <button type="button" className={`${LINK} block max-w-full truncate text-left`}
                            title={`Show ${c.contact_email}’s findings on hosts`}
                            onClick={() => setParams((prev) => {
                              const next = new URLSearchParams(prev);
                              next.delete('view'); next.delete('unassigned'); next.delete('state');
                              next.set('contact', c.contact_email);
                              return next;
                            })}>
                            {c.contact_name ?? c.contact_email}
                          </button>
                          {c.contact_name && (
                            <span className="block truncate text-caption text-muted-foreground" title={c.contact_email}>
                              {c.contact_email}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="text-right align-middle tabular-nums">{count(c.overdue, 'text-destructive')}</TableCell>
                        <TableCell className="text-right align-middle tabular-nums">{count(c.due_soon, 'text-warning')}</TableCell>
                        <TableCell className="text-right align-middle tabular-nums">{count(c.on_track)}</TableCell>
                        <TableCell className="text-right align-middle tabular-nums">{count(c.open)}</TableCell>
                        {across && <TableCell className="text-right align-middle tabular-nums">{c.projects}</TableCell>}
                        <TableCell className="truncate align-middle tabular-nums">
                          {c.last_follow_up_on ? formatDate(c.last_follow_up_on)
                            : atRisk > 0 ? <span className="text-muted-foreground">Not yet</span> : <Dash label="Nothing to follow up" />}
                        </TableCell>
                        <TableCell className="align-middle">
                          <div className="flex justify-end gap-xs">
                            {canReport && c.open > 0 && (
                              <Button size="sm" variant="ghost" className="h-7"
                                title="Prepare this contact’s remediation list as a Word or HTML document"
                                onClick={() => setReport(c)}>Document</Button>
                            )}
                            {atRisk > 0 ? (
                              <Button size="sm" variant="outline" className="h-7"
                                onClick={() => { setFollowUpAhead(0); setFollowUp(c.contact_email); }}>Follow up</Button>
                            ) : c.on_track > 0 && (
                              <Button size="sm" variant="ghost" className="h-7"
                                title="Nothing is overdue or due soon: remind this contact of the deadlines coming up"
                                onClick={() => { setFollowUpAhead(UPCOMING_REMINDER_DAYS); setFollowUp(c.contact_email); }}>Remind</Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </ListBody>
      ) : (
        <>
          {list.lastResponse && (
            <RemediationInsights
              page={list.lastResponse}
              onSeverity={(sev, st) => openRows({ severity: sev, state: st })}
              onBand={(b) => openRows({ band: b })}
              onNotFollowedUp={() => openRows({ stale: '1' })}
            />
          )}
          <RemediationVerificationCounts counts={list.lastResponse?.verification_counts}
            selected={verification} onSelect={openVerification}
            flagCounts={list.lastResponse?.flag_counts} selectedFlag={flag} onSelectFlag={openFlag} />
          <FilterChips<RemediationState>
            label="Filter by deadline state"
            allLabel="All"
            allCount={all}
            chips={REMEDIATION_STATES.map((s) => ({
              key: s, label: REMEDIATION_STATE_LABEL[s], count: counts ? counts[s] : null,
              title: REMEDIATION_STATE_HELP[s], strong: s === 'overdue' && !!counts && counts.overdue > 0,
            }))}
            selected={state}
            onSelect={(s) => setParam('state', s ?? undefined)}
          />

          <div className="mb-sm flex min-w-0 flex-wrap items-center gap-sm">
            <div className="relative min-w-0">
              <Search className="pointer-events-none absolute left-2 top-2 size-4 text-muted-foreground" aria-hidden />
              <Input id="rem-search" type="search" aria-label="Search by finding or host"
                className="h-8 w-64 max-w-full pl-8 pr-8 [&::-webkit-search-cancel-button]:hidden"
                value={sought} maxLength={SEARCH_MAX} placeholder="Finding or host…"
                title="A finding’s title, or a host’s address or name (two characters or more)"
                onChange={(e) => setSought(e.target.value)} />
              {sought && (
                <button type="button" aria-label="Clear the search"
                  className="absolute right-1 top-1 rounded p-1 text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => { setSought(''); setParam('q', undefined); }}>
                  <X className="size-4" aria-hidden />
                </button>
              )}
            </div>
            <Label htmlFor="rem-contact" className="shrink-0">Contact</Label>
            <div className="relative min-w-0">
              <Input id="rem-contact" className="h-8 w-64 max-w-full pr-8" value={typed} maxLength={254}
                placeholder="Name or address" disabled={unassigned}
                onChange={(e) => setTyped(e.target.value)} />
              {typed && (
                <button type="button" aria-label="Clear the contact filter"
                  className="absolute right-1 top-1 rounded p-1 text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => { setTyped(''); setParam('contact', undefined); }}>
                  <X className="size-4" aria-hidden />
                </button>
              )}
            </div>
            <button type="button" aria-pressed={unassigned} className={filterChipClass(unassigned)}
              title="Findings on hosts that nobody has been named for"
              onClick={() => {
                if (!unassigned) { setTyped(''); }
                setParams((prev) => {
                  const next = new URLSearchParams(prev);
                  if (unassigned) next.delete('unassigned'); else { next.set('unassigned', '1'); next.delete('contact'); }
                  return next;
                });
              }}>
              No contact yet
            </button>
            <Label htmlFor="rem-severity" className="shrink-0">Severity</Label>
            <Select value={severity ?? ANY_SEVERITY}
              onValueChange={(v) => setParam('severity', v === ANY_SEVERITY ? undefined : v)}>
              <SelectTrigger id="rem-severity" className="h-8 w-36"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY_SEVERITY}>Any</SelectItem>
                {SEVERITIES.map((s) => <SelectItem key={s} value={s}>{severityWord(s)}</SelectItem>)}
              </SelectContent>
            </Select>
            <Label htmlFor="rem-group" className="shrink-0">Ordered by</Label>
            <Select value={group} onValueChange={(v) => setParam('group', v === 'due' ? undefined : v)}>
              <SelectTrigger id="rem-group" className="h-8 w-36"><SelectValue /></SelectTrigger>
              <SelectContent>
                {REMEDIATION_GROUPS.map((g) => <SelectItem key={g} value={g}>{REMEDIATION_GROUP_LABEL[g]}</SelectItem>)}
              </SelectContent>
            </Select>
            <span className="ml-auto flex shrink-0 items-center gap-xs">
              <Button size="sm" variant="outline" className="h-8" disabled={!list.lastResponse}
                title="Copy these counts as text, for a status mail" onClick={() => void copySummary()}>
                <Copy className="size-4" aria-hidden /> Copy summary
              </Button>
              <Button size="sm" variant="outline" className="h-8" disabled={exporting || list.total === 0}
                title="Save the rows matching these filters as a CSV file" onClick={() => csv.mutate()}>
                <Download className="size-4" aria-hidden /> {exporting ? 'Building…' : 'CSV'}
              </Button>
            </span>
          </div>

          {narrowed.length + extra.length > 0 && (
            <div className="mb-sm flex min-w-0 flex-wrap items-center gap-xs">
              <span className="shrink-0 text-caption text-muted-foreground">Only</span>
              {[...narrowed, ...extra].map((n) => (
                <button key={n.key} type="button" title={`${n.label} — remove this filter`}
                  aria-label={`${n.label}: remove this filter`}
                  className={`${filterChipClass(true)} inline-flex min-w-0 max-w-full items-center gap-xxs`}
                  onClick={() => setParam(n.key, undefined)}>
                  <span className="min-w-0 max-w-[24rem] truncate">{n.label}</span>
                  <X className="size-3 shrink-0" aria-hidden />
                </button>
              ))}
            </div>
          )}

          {/* The bar's place is kept while nothing is selected, so ticking the
              first row does not move the rows under the pointer. */}
          {canWrite && selection.selected.length === 0 && list.rows !== null && rows.length > 0 && (
            <p className="mb-xs flex h-7 items-center border-l-2 border-l-transparent pl-sm text-caption text-muted-foreground">
              Tick rows to assign a contact, or set dates or status, on several at once.
            </p>
          )}
          {canWrite && (
            <BulkBar count={selection.selected.length} noun="row" onClear={selection.clear}>
              <Button size="sm" variant="outline" className="h-7" onClick={() => setEditing(selectedRows)}>
                Assign or update
              </Button>
            </BulkBar>
          )}

          <ListBody
            rows={list.rows}
            state={{ loading: list.loading, error: list.error, onRetry: () => void list.reload() }}
            what="the remediation list"
            empty={q
              ? (
                <span className="break-words">
                  No finding or host matches “{q}”{(state || contact || unassigned || severity || team || band || stale || verification || flag || projectId != null) ? ' with these filters' : ''}.
                </span>
              )
              : filtered || state
              ? 'Nothing matches these filters.'
              : 'Nothing here — a finding shows once per host when it is confirmed, accepted as a risk or remediated.'}
          >
            {() => (
              <div>
                <div className="min-w-0 overflow-x-auto">
                  {/* The fixed columns leave Finding — the one column with no
                      width — at least 10rem at the table's minimum. */}
                  <Table aria-label="Findings on hosts and their remediation deadlines"
                    className={`${across ? 'min-w-[71.5rem]' : 'min-w-[62.5rem]'} table-fixed`}>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent hover:shadow-none">
                        {canWrite && (
                          <TableHead className="w-10">
                            <Checkbox aria-label="Select every row on this page" checked={selection.allState}
                              onCheckedChange={(v) => selection.toggleAll(v === true)} />
                          </TableHead>
                        )}
                        {/* Wide enough for "Deferred · review <date>" on one line. */}
                        <TableHead className="w-[14rem]">Deadline</TableHead>
                        <TableHead className="w-[9rem]">Host</TableHead>
                        <TableHead>Finding</TableHead>
                        {across && <TableHead className="w-[9rem]">Project</TableHead>}
                        <TableHead className="w-[12rem]">Contact</TableHead>
                        <TableHead className="w-[6.5rem]">Assigned</TableHead>
                        <TableHead className="w-[6rem]"><span className="sr-only">Actions</span></TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.map((r, i) => {
                        const picked = selection.isSelected(r.finding_host_id);
                        const deadline = deadlineCell(r);
                        const verified = verificationNote(r);
                        return (
                          <TableRow key={r.finding_host_id} data-state={picked ? 'selected' : undefined}
                            aria-selected={canWrite ? picked : undefined} {...cursorRowProps(i)}>
                            {canWrite && (
                              <TableCell className="align-middle">
                                <Checkbox aria-label={`Select ${r.finding_title} on ${r.ip_address}`} checked={picked}
                                  onCheckedChange={() => selection.toggle(r.finding_host_id)} />
                              </TableCell>
                            )}
                            <TableCell className="min-w-0 align-middle tabular-nums">
                              <span className={`block truncate ${deadline.tone}`}
                                // A deferral's phrase carries its review date: it is on the tooltip too, should it be cut.
                                title={r.state === 'deferred' && deadline.primary !== REMEDIATION_STATE_LABEL.deferred
                                  ? `${deadline.primary}. ${REMEDIATION_STATE_HELP.deferred}` : REMEDIATION_STATE_HELP[r.state]}>
                                {deadline.primary}
                              </span>
                              {deadline.date && (
                                <span className="block truncate text-caption text-muted-foreground">
                                  {formatDate(deadline.date)}
                                </span>
                              )}
                              {/* A due date somebody set by hand, with the policy's. */}
                              {deadline.source && (
                                <span className="block truncate text-caption text-muted-foreground" title={deadline.source.title}>
                                  {deadline.source.text}
                                </span>
                              )}
                              {/* The assessment's side of the same finding on this host. */}
                              {verified && (
                                <span className={`block truncate text-caption ${verified.tone}`} title={verified.title}>
                                  {verified.text}
                                </span>
                              )}
                            </TableCell>
                            <TableCell className="min-w-0 align-middle">
                              {hostCell(r)}
                              {r.hostname && (
                                <span className="block truncate text-caption text-muted-foreground" title={r.hostname}>
                                  {r.hostname}
                                </span>
                              )}
                            </TableCell>
                            <TableCell className="min-w-0 align-middle">
                              <div className="flex min-w-0 items-center gap-xs">
                                <SeverityBadge severity={r.severity} className="shrink-0" />
                                {findingCell(r)}
                              </div>
                            </TableCell>
                            {across && (
                              <TableCell className="min-w-0 align-middle">
                                <span className="block truncate" title={r.project_name}>{r.project_name}</span>
                              </TableCell>
                            )}
                            <TableCell className="min-w-0 align-middle">
                              {r.contact_email || r.contact_name ? (
                                <>
                                  <span className="block truncate" title={r.contact_email ?? r.contact_name ?? undefined}>
                                    {r.contact_name ?? r.contact_email}
                                  </span>
                                  {r.team && (
                                    <span className="block truncate text-caption text-muted-foreground" title={`Team: ${r.team}`}>
                                      {r.team}
                                    </span>
                                  )}
                                  {r.last_follow_up_on ? (
                                    <span className="block truncate text-caption text-muted-foreground">
                                      followed up {formatDate(r.last_follow_up_on)}
                                    </span>
                                  ) : r.contact_name && r.contact_email && (
                                    <span className="block truncate text-caption text-muted-foreground" title={r.contact_email}>
                                      {r.contact_email}
                                    </span>
                                  )}
                                </>
                              ) : r.team ? <span className="block truncate" title={`Team: ${r.team}`}>{r.team}</span>
                                : <Dash label="No contact yet" />}
                            </TableCell>
                            <TableCell className="truncate align-middle tabular-nums">
                              {r.notified_on ? formatDate(r.notified_on) : <Dash label="Not assigned" />}
                            </TableCell>
                            <TableCell className="align-middle">
                              <div className="flex items-center justify-end gap-xxs">
                                {canWrite && (
                                  <Button size="sm" variant="ghost" className="h-7" onClick={() => setEditing([r])}>
                                    {r.state === 'not_assigned' ? 'Assign' : 'Edit'}
                                  </Button>
                                )}
                                <Button size="sm" variant="ghost" className="h-7 px-xs"
                                  aria-label={`Timeline for ${r.ip_address}`} title="This host’s remediation timeline"
                                  onClick={() => setTimeline(r)}>
                                  <History className="size-4" aria-hidden />
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                <PagedFooter
                  pager={{ page: list.page, pageSize: list.pageSize, total: list.total, onPage: list.setPage }}
                  shown={rows.length}
                  noun="rows"
                >
                  <span className="inline-flex items-center gap-xs">
                    <Label htmlFor="rem-per" className="text-caption text-muted-foreground">Rows per page</Label>
                    <Select value={String(pageSize)}
                      onValueChange={(v) => setParam('per', Number(v) === REMEDIATION_PAGE_SIZE ? undefined : v)}>
                      <SelectTrigger id="rem-per" className="h-7 w-20"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {REMEDIATION_PAGE_SIZES.map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </span>
                </PagedFooter>
              </div>
            )}
          </ListBody>
        </>
      )}

      {editing && editing.length > 0 && (
        <RemediationEditDialog
          rows={editing}
          policy={policy}
          today={list.lastResponse?.as_of}
          acrossProjects={across}
          onClose={() => setEditing(null)}
          // The page on screen is re-read in place: the reader keeps their place.
          onSaved={changed}
        />
      )}
      {followUp && (
        <RemediationFollowUpDialog
          contactEmail={followUp}
          scope={scope}
          projectId={projectId ?? undefined}
          canWrite={canWrite}
          initialAhead={followUpAhead}
          onClose={() => setFollowUp(null)}
          onRecorded={changed}
        />
      )}
      {report && (
        <RemediationContactReportDialog
          contactEmail={report.contact_email}
          contactName={report.contact_name}
          projectId={reportProject?.project_id}
          projectName={reportProject?.name}
          onClose={() => setReport(null)}
        />
      )}
      <RemediationTimeline host={timeline} canWrite={canWrite} onClose={() => setTimeline(null)}
        projectId={across ? timeline?.project_id : undefined} />
    </div>
  );
};

export default RemediationWorkList;
