/**
 * Engagement details of a client report (v5.261.0): client, author,
 * classification, engagement type, assessment team, distribution list, system
 * description (with the project's own report images, `ReportTextImages`),
 * other targets and disclaimer.  Controlled; three uses:
 *
 * - The project's report defaults (Reports page): plain editing.  An empty
 *   author or disclaimer uses the installation's (`inherited`), said under it.
 * - A draft (`followed` given): every detail FOLLOWS the project unless the
 *   report overrides it (owner, 2026-10-14).  `value` is the override map — a
 *   key present is overridden (a list even when empty), a key absent follows.
 *   A followed detail is TEXT, not a greyed field (owner, 2026-10-14: a
 *   disabled box read as "locked"): the value it prints, where it comes from
 *   (`followedFrom`: the project's defaults, the installation's, the project's
 *   members) and its actions — "Override for this report" (an editable copy,
 *   the report's own), and, given `onSetForProject`, "Set for the project" /
 *   "Edit the project's default" (edited here, saved to the project's
 *   defaults, never as an override).  An overridden detail is the editable
 *   control with "Revert to the project's" and, given `onPromote`, "Make this
 *   the project's default".  Lists are overridden, and pushed up, as a whole.
 *   ONE action per line is a visible link; the others are in a "⋯" menu
 *   beside it ("More actions for <detail>", marked `data-more-actions`, which
 *   a checklist line never focuses).
 * - `readOnly` (an issued report, or someone who may not edit): the values as
 *   they stand (an issued report's as issued), no action.
 *
 * The team and the distribution list print in the order shown; their rows are
 * reordered, and removed, by `ReorderableRows`.
 */
import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Copy, Loader2, MoreHorizontal, Plus, Users } from 'lucide-react';

import {
  getProjectReportTeam, type EngagementOverrides, type EngagementSettings, type FollowedSource,
  type InheritedReportDetails, type ProjectMember, type ReportRecipient, type ReportTester,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import type { ProjectMembersStatus } from '../../hooks/useProjectMembers';
import { useReportTextImages } from '../../hooks/useReportTextImages';
import MembersLoadError from '../MembersLoadError';
import { formatApiError } from '../../utils/apiErrors';
import { Button } from '../ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../ui/select';
import { Textarea } from '../ui/textarea';
import MarkdownField from '../MarkdownField';
import SafeMarkdown from '../SafeMarkdown';
import ReorderableRows from './ReorderableRows';
import ReportTextImages from './ReportTextImages';

type DetailKey = keyof EngagementSettings;
type TextKey = Exclude<DetailKey, 'testers' | 'distribution'>;
type ListKey = 'testers' | 'distribution';

interface Props<T extends EngagementOverrides> {
  value: T;
  onChange: (next: T) => void;
  members: ProjectMember[];
  /** How the member list stands (`useProjectRoster`): a failed load is said,
   *  never shown as a project with no members. */
  membersStatus?: ProjectMembersStatus;
  onRetryMembers?: () => void;
  /** Whoever is signed in. A global admin can work on a project without being
   *  a member of it, so the member list alone left them nobody to pick. */
  currentUser?: { id: number; name: string } | null;
  /** Momentarily unavailable (saving): every control stays, greyed. */
  disabled?: boolean;
  /** v5.290.0 — the details as they stand, not a form (an issued report,
   *  or someone who may not edit): the fields show their values and no
   *  add / remove / override control is rendered at all. */
  readOnly?: boolean;
  /** A DRAFT: what each detail prints when the report does not override it
   *  (`ClientReport.followed`).  Given, `value` is the report's overrides. */
  followed?: EngagementSettings | null;
  /** A draft: where each followed value comes from (`ClientReport.followed_from`). */
  followedFrom?: Partial<Record<DetailKey, FollowedSource | null>> | null;
  /** A draft, for someone who may write the project's defaults: save one
   *  detail to them ("Set for the project").  Resolves once saved. */
  onSetForProject?: (key: DetailKey, value: EngagementSettings[DetailKey]) => Promise<unknown>;
  /** A draft, for someone who may write the project's defaults: "Make this
   *  the project's default" for an overridden detail, with the value shown. */
  onPromote?: (key: DetailKey, value: EngagementSettings[DetailKey]) => void;
  /** A write to the project's defaults is running: its actions wait. */
  projectBusy?: boolean;
  /** The project's defaults only: the installation's author and disclaimer,
   *  which an empty one there uses (`ReportProfile.inherited`). */
  inherited?: InheritedReportDetails | null;
  idPrefix: string;
}

const memberName = (m: ProjectMember) => m.full_name || m.username || `User ${m.user_id}`;

export const DETAIL_LABELS: Record<DetailKey, string> = {
  client_name: 'Client', author_name: 'Author', classification: 'Classification', engagement_type: 'Engagement type',
  testers: 'Assessment team', distribution: 'Distribution', system_description: 'System description',
  applications: 'Application URLs and API endpoints', thick_clients: 'Thick client applications',
  other_targets: 'Other targets', disclaimer: 'Disclaimer',
};
const LABELS = DETAIL_LABELS;

/** A report detail's name, wherever one is shown — a field's label, a
 *  followed detail, the team and distribution lists, the defaults' summary on
 *  the Reports page: a small muted caption, so it reads apart from its value
 *  (owner, 2026-10-14). One style: a detail's name used to change with its
 *  state, and the two lists had a heading of their own. */
export const DETAIL_NAME = 'block text-caption font-medium uppercase tracking-wide text-muted-foreground';
const LIST_KEYS: ReadonlyArray<DetailKey> = ['testers', 'distribution'];

/** Is this detail overridden in a draft's override map?  Present = yes (a
 *  list even when empty, a text even while it is being emptied). */
export const isOverridden = (overrides: EngagementOverrides, key: DetailKey): boolean =>
  overrides[key] !== undefined && overrides[key] !== null;

/** Is there anything to push up?  A text with text; a list always (an empty
 *  one is a value: "nobody"). */
export const hasValue = (key: DetailKey, v: unknown): boolean =>
  LIST_KEYS.includes(key) ? Array.isArray(v) : typeof v === 'string' && v.trim() !== '';

/** Rows with no name dropped, names trimmed. */
const cleanRows = <R extends { name: string }>(rows: readonly R[]): R[] =>
  rows.filter((r) => r.name.trim()).map((r) => ({ ...r, name: r.name.trim() }));

/** A detail's value as it is sent to the server: rows cleaned, a text trimmed
 *  (blank → null). */
export const cleanDetail = (key: DetailKey, v: unknown): EngagementSettings[DetailKey] => {
  if (LIST_KEYS.includes(key)) return cleanRows((v ?? []) as Array<{ name: string }>) as EngagementSettings[DetailKey];
  const text = typeof v === 'string' ? v.trim() : '';
  return (text || null) as EngagementSettings[DetailKey];
};

/** Where a followed value comes from, in the words beside it. */
const SOURCE_TEXT: Record<FollowedSource, string> = {
  project: 'From the project’s defaults',
  installation: 'From the installation’s defaults',
  project_team: 'From the project’s analysts and admins',
};

const LINK_BUTTON = 'h-auto p-0 text-caption';

/** A draft's detail: how it stands, and what can be done with it. */
type Mode = 'own' | 'project' | 'followed';

/** One action on a detail's source line: shown as a link, or as an item of
 *  its "⋯" menu.  `aria` is its accessible name either way. */
interface SourceAction { key: string; text: string; aria: string; onClick: () => void; off?: boolean }

const DetailSource: React.FC<{
  id: string; label: string; mode: Mode; source: FollowedSource | null; blank: boolean;
  disabled?: boolean; projectDisabled?: boolean; saving?: boolean;
  /** The project's value being edited is empty: nothing to save yet. */
  saveDisabled?: boolean;
  onOverride: () => void; onRevert: () => void;
  onSetForProject?: () => void; onPromote?: () => void;
  onSaveProject?: () => void; onCancelProject?: () => void;
}> = ({
  id, label, mode, source, blank, disabled, projectDisabled, saving, saveDisabled,
  onOverride, onRevert, onSetForProject, onPromote, onSaveProject, onCancelProject,
}) => {
  // ONE visible action per state; the others in a small "⋯" menu beside it
  // (owner, 2026-10-14: two or three links a line read as a wall of links).
  let primary: SourceAction | null = null;
  const more: SourceAction[] = [];
  let projectEditing: React.ReactNode = null;
  let words: string | null;
  if (mode === 'own') {
    words = blank ? 'Overridden for this report — empty: saved, it follows the project’s again' : 'Overridden for this report';
    primary = { key: 'revert', text: 'Revert to the project’s', aria: `Revert ${label} to the project’s`, onClick: onRevert, off: disabled };
    if (onPromote && !blank) {
      more.push({ key: 'promote', text: 'Make this the project’s default', aria: `Make ${label} the project’s default`,
        onClick: onPromote, off: disabled || projectDisabled });
    }
  } else if (mode === 'project') {
    words = 'Editing the project’s default — every draft that follows it will print it';
    projectEditing = (
      <>
        <Button type="button" size="sm" className="h-7 px-xs text-caption" disabled={projectDisabled || saveDisabled}
          title={saveDisabled ? 'Type a value first' : undefined}
          aria-label={`Save ${label} for the project`} onClick={onSaveProject}>
          {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Save for the project
        </Button>
        <span aria-hidden>·</span>
        <Button type="button" variant="link" size="sm" className={LINK_BUTTON} disabled={projectDisabled}
          aria-label={`Cancel editing the project’s ${label}`} onClick={() => onCancelProject?.()}>
          Cancel
        </Button>
      </>
    );
  } else {
    words = source ? SOURCE_TEXT[source] : null;
    const override: SourceAction = { key: 'override', text: 'Override for this report',
      aria: `Override ${label} for this report`, onClick: onOverride, off: disabled };
    if (!onSetForProject) {
      primary = override;
    } else if (source === 'project') {
      primary = override;
      more.push({ key: 'edit-project', text: 'Edit the project’s default', aria: `Edit ${label} in the project’s defaults`,
        onClick: onSetForProject, off: disabled || projectDisabled });
    } else {
      const set: SourceAction = { key: 'set-project', text: 'Set for the project', aria: `Set ${label} for the project`,
        onClick: onSetForProject, off: disabled || projectDisabled };
      // Empty in the project: setting it there is the common fix (the one a
      // checklist line leads to).  From the installation or the members:
      // overriding it here is.
      if (source == null) { primary = set; more.push(override); } else { primary = override; more.push(set); }
    }
  }
  return (
    <div id={id} className="flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs text-caption text-muted-foreground">
      {words && <span>{words}</span>}
      {words && (primary || projectEditing) && <span aria-hidden>·</span>}
      {projectEditing}
      {primary && (
        <Button type="button" variant="link" size="sm" className={LINK_BUTTON} disabled={primary.off}
          aria-label={primary.aria} onClick={primary.onClick}>
          {primary.text}
        </Button>
      )}
      {more.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="ghost" size="icon" className="size-5 text-muted-foreground"
              data-more-actions="" disabled={disabled} aria-label={`More actions for ${label}`}>
              <MoreHorizontal className="size-4" aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {more.map((a) => (
              <DropdownMenuItem key={a.key} disabled={a.off} aria-label={a.aria}
                // A tick later: a confirmation, or the field taking the
                // caret, must not race the menu handing focus back.
                onSelect={() => { setTimeout(a.onClick, 0); }}>
                {a.text}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
};

/** What "Not set" says for a followed detail nobody wrote. */
const notSet = (key: DetailKey): string =>
  key === 'disclaimer' ? 'The project sets none: the template’s own disclaimer is printed.'
    : 'Not set in the project’s defaults';

/** A followed list as text: one person a line. */
const FollowedRows: React.FC<{ rows: ReadonlyArray<ReportTester | ReportRecipient> }> = ({ rows }) => (
  <ul className="max-h-48 space-y-xxs overflow-y-auto text-body">
    {rows.map((r, i) => (
      <li key={i} className="min-w-0 [overflow-wrap:anywhere]">
        <span className="font-medium">{r.name}</span>
        {'role' in r && r.role && <span className="text-muted-foreground"> — {r.role}</span>}
        {r.email && <span className="text-muted-foreground"> · {r.email}</span>}
      </li>
    ))}
  </ul>
);

function EngagementSettingsFields<T extends EngagementOverrides>({
  value, onChange, members, membersStatus = 'ready', onRetryMembers, currentUser, disabled: busy, readOnly = false,
  followed, followedFrom, onSetForProject, onPromote, projectBusy = false, inherited, idPrefix,
}: Props<T>): React.ReactElement {
  const disabled = busy || readOnly;
  // A draft: each detail is the report's override, the project's being
  // edited here, or the project's as it stands.
  const drafting = !readOnly && followed != null;
  // Keys whose PROJECT default is being edited here ("Set for the project").
  const [projectEdits, setProjectEdits] = useState<Partial<EngagementSettings>>({});
  const [savingKey, setSavingKey] = useState<DetailKey | null>(null);
  const mode = (key: DetailKey): Mode => {
    if (!drafting || isOverridden(value, key)) return 'own';
    return key in projectEdits ? 'project' : 'followed';
  };
  const editable = (key: DetailKey) => mode(key) !== 'followed';
  const source = (key: DetailKey): FollowedSource | null => {
    if (followedFrom && key in followedFrom) return followedFrom[key] ?? null;
    // A read that does not say: a value present is taken as the project's.
    const v = followed?.[key];
    return (Array.isArray(v) ? v.length > 0 : hasValue(key, v)) ? 'project' : null;
  };
  const copyOf = (key: DetailKey) => {
    const from = followed?.[key];
    return (Array.isArray(from) ? from.map((e) => ({ ...e })) : (from ?? '')) as EngagementSettings[typeof key];
  };
  const set = <K extends DetailKey>(key: K, v: EngagementSettings[K]) => {
    if (mode(key) === 'project') setProjectEdits((p) => ({ ...p, [key]: v }));
    else onChange({ ...value, [key]: v });
  };
  const override = (key: DetailKey) => onChange({ ...value, [key]: copyOf(key) });
  const revert = (key: DetailKey) => {
    const next = { ...value };
    delete next[key];
    onChange(next);
  };
  // Opening the field puts the caret in it: it used to open unfocused, so
  // what the reader typed went nowhere (walkthrough, 2026-10-14).  The field
  // is found by the source line it is described by (every editable detail
  // carries it), else the first control under that line's detail.
  const editForProject = (key: DetailKey) => {
    setProjectEdits((p) => ({ ...p, [key]: copyOf(key) }));
    window.requestAnimationFrame(() => {
      const line = document.getElementById(sourceId(key));
      const field = document.querySelector<HTMLElement>(`[aria-describedby~="${sourceId(key)}"]:not([disabled])`)
        ?? line?.parentElement?.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled])');
      field?.focus();
    });
  };
  // Nothing to save: an empty value would be "saved" as nothing.
  const projectBlank = (key: DetailKey): boolean => {
    const v = projectEdits[key];
    return Array.isArray(v) ? v.length === 0 : !(typeof v === 'string' && v.trim());
  };
  const cancelProject = (key: DetailKey) => setProjectEdits((p) => {
    const next = { ...p };
    delete next[key];
    return next;
  });
  const saveProject = (key: DetailKey) => {
    if (!onSetForProject) return;
    setSavingKey(key);
    onSetForProject(key, cleanDetail(key, projectEdits[key]))
      .then(() => cancelProject(key), () => undefined)
      .finally(() => setSavingKey(null));
  };
  // What a field shows: the report's own (or the defaults' own), the
  // project's being edited, else (a draft) what it follows.
  const shown = <K extends DetailKey>(key: K): EngagementSettings[K] | undefined => {
    const m = mode(key);
    if (m === 'project') return projectEdits[key] as EngagementSettings[K];
    return (m === 'own' ? value[key] : followed?.[key]) as EngagementSettings[K] | undefined;
  };
  const text = (key: TextKey) => shown(key) ?? '';
  const projectWriting = projectBusy || savingKey != null;
  const locked = (key: DetailKey) => disabled || !editable(key) || (mode(key) === 'project' && projectWriting);
  // In a draft an emptied override stays one until saved (the server then
  // reads it as following); in the defaults an empty text is null.
  const typed = (key: TextKey, v: string) => set(key, (drafting ? v : (v || null)) as EngagementSettings[TextKey]);
  const onText = (key: TextKey) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => typed(key, e.target.value);
  const sourceId = (key: DetailKey) => `${idPrefix}-${key}-source`;
  const sourceLine = (key: DetailKey) => drafting && (
    <DetailSource id={sourceId(key)} label={LABELS[key]} mode={mode(key)} source={source(key)} disabled={busy}
      projectDisabled={projectWriting} saving={savingKey === key}
      saveDisabled={mode(key) === 'project' && projectBlank(key)}
      blank={mode(key) === 'own' && typeof value[key] === 'string' && !(value[key] as string).trim()}
      onOverride={() => override(key)} onRevert={() => revert(key)}
      onSetForProject={onSetForProject ? () => editForProject(key) : undefined}
      onPromote={onPromote ? () => onPromote(key, cleanDetail(key, value[key])) : undefined}
      onSaveProject={() => saveProject(key)} onCancelProject={() => cancelProject(key)} />
  );
  const describedBy = (key: DetailKey, extra?: string) =>
    [drafting ? sourceId(key) : '', extra ?? ''].filter(Boolean).join(' ') || undefined;
  const placeholder = (example?: string) => (readOnly ? undefined : example);

  // The project's own report images: what the System description may place.
  // One load for the field's picker, its preview and the list under it.
  const reportImages = useReportTextImages({ enabled: !readOnly });

  /** A followed text detail: its label, the value as text (Markdown
   *  rendered), bounded, scrolling inside itself. */
  const followedText = (key: TextKey, id: string, markdown = false) => {
    const v = (followed?.[key] ?? '').trim();
    return (
      <div className="min-w-0 space-y-xxs">
        <span id={`${id}-label`} className={DETAIL_NAME}>
          {LABELS[key]}
        </span>
        <div id={id} role="group" aria-labelledby={`${id}-label`} aria-describedby={describedBy(key)}
          data-followed="true" data-testid={`${id}-followed`}
          className={markdown && v
            ? 'max-h-48 overflow-y-auto rounded-control border border-border px-sm py-xs'
            : 'max-h-24 overflow-y-auto'}>
          {!v ? (
            <p className="text-body italic text-muted-foreground">{notSet(key)}</p>
          ) : markdown ? (
            <SafeMarkdown text={v} evidence={key === 'system_description' ? reportImages.markdown.resolver : undefined} />
          ) : (
            <p className="whitespace-pre-wrap text-body font-medium text-foreground [overflow-wrap:anywhere]">{v}</p>
          )}
        </div>
        {sourceLine(key)}
      </div>
    );
  };

  // The project's defaults: an empty author or disclaimer uses the
  // installation's, said under the field.
  const installationAuthor = !readOnly && !drafting && !text('author_name').trim()
    ? (inherited?.author_name ?? '').trim() : '';
  const disclaimerEmpty = !text('disclaimer').trim();
  const installationDisclaimer = !readOnly && !drafting && disclaimerEmpty ? (inherited?.disclaimer ?? '').trim() : '';

  const testers = shown('testers') ?? [];
  const distribution = shown('distribution') ?? [];
  const editTeam = !readOnly && editable('testers');
  const editDistribution = !readOnly && editable('distribution');
  const available = members.filter((m) => !testers.some((t) => t.user_id === m.user_id));
  // Offered only when the member picker cannot add them (not a member).
  const canAddSelf = !!currentUser
    && !testers.some((t) => t.user_id === currentUser.id)
    && !members.some((m) => m.user_id === currentUser.id);

  // The project's analysts and admins (name, role line, email) — added once
  // each; whoever is already listed keeps what was written for them.
  const toast = useToast();
  const projectId = useProjectId();
  // Asked for by the button, each time: the team as the project has it now.
  const projectTeam = useMutation({
    mutationFn: () => getProjectReportTeam(projectId),
    onSuccess: (team) => {
      const fresh = team.filter((t) => !testers.some((x) => x.user_id != null && x.user_id === t.user_id));
      if (fresh.length === 0) toast.info('Everyone on the project is already listed.');
      else set('testers', [...testers, ...fresh]);
    },
    onError: (err) => toast.error(formatApiError(err, "Could not load the project's members.")),
  });
  const addingTeam = projectTeam.isPending;

  /** A single-line text detail: the field, or (followed) its text. */
  const lineDetail = (key: TextKey, suffix: string, maxLength: number, example?: string, extra?: React.ReactNode,
    extraId?: string, title?: string) => {
    const id = `${idPrefix}-${suffix}`;
    if (!editable(key)) return followedText(key, id);
    return (
      <div className="min-w-0 space-y-xxs">
        <Label htmlFor={id} className={DETAIL_NAME}>{LABELS[key]}</Label>
        <Input id={id} maxLength={maxLength} value={text(key)} title={title}
          placeholder={placeholder(example)} aria-describedby={describedBy(key, extraId)}
          onChange={onText(key)} disabled={locked(key)} />
        {sourceLine(key)}
        {extra}
      </div>
    );
  };

  const teamRows = (
    <ReorderableRows items={testers} noun="team member" editable={editTeam} disabled={locked('testers')}
      onChange={(next) => set('testers', next)}>
      {(t, i) => (
        <>
          <Input aria-label={`Team member ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
            value={t.name} disabled={locked('testers')}
            onChange={(e) => set('testers', testers.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <Input aria-label={`Team member ${i + 1} role`} className="min-w-0 flex-1" maxLength={200}
            placeholder="Role" value={t.role ?? ''} disabled={locked('testers')}
            onChange={(e) => set('testers', testers.map((x, j) => (j === i ? { ...x, role: e.target.value || null } : x)))} />
          <Input aria-label={`Team member ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
            placeholder="Contact" value={t.email ?? ''} disabled={locked('testers')}
            onChange={(e) => set('testers', testers.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
        </>
      )}
    </ReorderableRows>
  );

  const distributionRows = (
    <ReorderableRows items={distribution} noun="recipient" editable={editDistribution}
      disabled={locked('distribution')} onChange={(next) => set('distribution', next)}>
      {(r, i) => (
        <>
          <Input aria-label={`Recipient ${i + 1} name`} className="min-w-0 flex-1" maxLength={200}
            value={r.name} disabled={locked('distribution')}
            onChange={(e) => set('distribution', distribution.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <Input aria-label={`Recipient ${i + 1} contact`} className="min-w-0 flex-1" maxLength={254}
            placeholder="Contact" value={r.email ?? ''} disabled={locked('distribution')}
            onChange={(e) => set('distribution', distribution.map((x, j) => (j === i ? { ...x, email: e.target.value || null } : x)))} />
        </>
      )}
    </ReorderableRows>
  );

  const followedList = (key: ListKey, rows: ReadonlyArray<ReportTester | ReportRecipient>) => (
    <div data-testid={`${idPrefix}-${key}-followed`}>
      {rows.length ? <FollowedRows rows={rows} />
        : <p className="text-body italic text-muted-foreground">{notSet(key)}</p>}
    </div>
  );

  return (
    <div className="space-y-md">
      <div className="grid gap-md md:grid-cols-2 xl:grid-cols-4">
        {lineDetail('client_name', 'client', 255)}
        {lineDetail('author_name', 'author', 255,
          // No example on a report that cannot be edited: in a disabled
          // field it reads as the author the report was issued with.
          'e.g. Example Security Ltd',
          installationAuthor ? (
            <p id={`${idPrefix}-author-inherited`} title={installationAuthor}
              className="line-clamp-2 text-caption text-muted-foreground [overflow-wrap:anywhere]">
              Empty: uses the installation’s — “{installationAuthor}”
            </p>
          ) : null,
          installationAuthor ? `${idPrefix}-author-inherited` : undefined,
          'Who produced the report')}
        {lineDetail('classification', 'classification', 100, 'e.g. Confidential')}
        {lineDetail('engagement_type', 'type', 100, 'e.g. Internal network penetration test')}
      </div>

      <fieldset id={`${idPrefix}-team`} className="min-w-0 space-y-xs" aria-describedby={describedBy('testers')}
        data-followed={editable('testers') ? undefined : 'true'}>
        <legend className={DETAIL_NAME}>Assessment team</legend>
        {sourceLine('testers')}
        {!editable('testers') ? followedList('testers', testers) : (
          <>
            {testers.length === 0 && (
              <p className="text-caption text-muted-foreground">Nobody listed yet.</p>
            )}
            {/* The order here is the order the report prints. */}
            {teamRows}
          </>
        )}
        {editTeam && (
        <div className="flex flex-wrap items-center gap-xs">
          {membersStatus === 'error' && onRetryMembers && <MembersLoadError onRetry={onRetryMembers} />}
          {membersStatus === 'ready' && members.length === 0 && !disabled && (
            <span className="text-caption text-muted-foreground">
              Nobody is a member of this project yet, so there is no one to pick — add people by name, or add members in{' '}
              <Link to="/project-settings" className="text-info hover:underline">Project settings</Link>.
            </span>
          )}
          {canAddSelf && currentUser && (
            <Button type="button" variant="outline" size="sm" disabled={locked('testers')}
              onClick={() => set('testers', [...testers, { user_id: currentUser.id, name: currentUser.name, role: null, email: null }])}>
              <Plus className="size-4" aria-hidden /> Add yourself
            </Button>
          )}
          {members.length > 0 && (
            <Button type="button" variant="outline" size="sm" disabled={locked('testers') || addingTeam}
              onClick={() => projectTeam.mutate()}>
              <Users className="size-4" aria-hidden /> Add the project&apos;s members
            </Button>
          )}
          {available.length > 0 && (
            <Select value="" disabled={locked('testers')}
              onValueChange={(v) => {
                const m = members.find((x) => String(x.user_id) === v);
                if (m) set('testers', [...testers, { user_id: m.user_id, name: memberName(m), role: null, email: null }]);
              }}>
              <SelectTrigger className="h-8 w-[14rem] text-caption" aria-label="Add a project member to the team">
                <SelectValue placeholder="Add a project member…" />
              </SelectTrigger>
              <SelectContent>
                {available.map((m) => (
                  <SelectItem key={m.user_id} value={String(m.user_id)}>{memberName(m)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button type="button" variant="ghost" size="sm" disabled={locked('testers')}
            onClick={() => set('testers', [...testers, { user_id: null, name: '', role: null, email: null }])}>
            <Plus className="size-4" aria-hidden /> Someone else
          </Button>
        </div>
        )}
      </fieldset>

      <fieldset id={`${idPrefix}-distribution`} className="min-w-0 space-y-xs" aria-describedby={describedBy('distribution')}
        data-followed={editable('distribution') ? undefined : 'true'}>
        <legend className={DETAIL_NAME}>Distribution</legend>
        {sourceLine('distribution')}
        {!editable('distribution') ? followedList('distribution', distribution) : (
          <>
            {distribution.length === 0 && (
              <p className="text-caption text-muted-foreground">Nobody listed yet.</p>
            )}
            {distributionRows}
          </>
        )}
        {editDistribution && (
          <Button type="button" variant="ghost" size="sm" disabled={locked('distribution')}
            onClick={() => set('distribution', [...distribution, { name: '', email: null }])}>
            <Plus className="size-4" aria-hidden /> Add recipient
          </Button>
        )}
      </fieldset>

      <div className="min-w-0 space-y-xxs">
        {editable('system_description') ? (
          <>
            <Label htmlFor={`${idPrefix}-system`} className={DETAIL_NAME}>System description</Label>
            <p className="text-caption text-muted-foreground">
              What was assessed, in the client&apos;s terms. Markdown. The scope&apos;s networks and domains are listed after it.
            </p>
            <MarkdownField id={`${idPrefix}-system`} label="System description" rows={5} maxLength={32768}
              value={text('system_description')} onChange={(v) => typed('system_description', v)}
              disabled={locked('system_description')}
              images={locked('system_description') ? undefined : reportImages.markdown} />
            {sourceLine('system_description')}
          </>
        ) : followedText('system_description', `${idPrefix}-system`, true)}
        {!readOnly && <ReportTextImages idPrefix={idPrefix} state={reportImages} disabled={disabled} />}
      </div>

      <fieldset className="min-w-0 space-y-xs">
        <legend className="text-body font-medium">Other targets</legend>
        <p className="text-caption text-muted-foreground">
          If applicable — each list appears in the report only when written. Markdown; one target per line works well.
        </p>
        <div className="grid gap-md md:grid-cols-3">
          {(['applications', 'thick_clients', 'other_targets'] as const).map((key) => (
            <React.Fragment key={key}>
              {editable(key) ? (
                <div className="min-w-0 space-y-xxs">
                  <Label htmlFor={`${idPrefix}-${key}`} className={DETAIL_NAME}>{LABELS[key]}</Label>
                  <Textarea id={`${idPrefix}-${key}`} rows={3} maxLength={32768} value={text(key)}
                    aria-describedby={describedBy(key)} onChange={onText(key)} disabled={locked(key)} />
                  {sourceLine(key)}
                </div>
              ) : followedText(key, `${idPrefix}-${key}`, true)}
            </React.Fragment>
          ))}
        </div>
      </fieldset>

      <div className="min-w-0 space-y-xxs">
        {editable('disclaimer') ? (
          <>
            <Label htmlFor={`${idPrefix}-disclaimer`} className={DETAIL_NAME}>Disclaimer</Label>
            <MarkdownField id={`${idPrefix}-disclaimer`} label="Disclaimer" rows={4} maxLength={32768}
              value={text('disclaimer')} onChange={(v) => typed('disclaimer', v)} disabled={locked('disclaimer')} />
            {sourceLine('disclaimer')}
          </>
        ) : followedText('disclaimer', `${idPrefix}-disclaimer`, true)}
        {installationDisclaimer && (
          // Any length: closed, it is one line; open, it scrolls inside itself.
          <details className="text-caption text-muted-foreground" data-testid={`${idPrefix}-disclaimer-inherited`}>
            <summary className="cursor-pointer">Empty: uses the installation’s disclaimer. Show it</summary>
            <div className="mt-xxs max-h-64 overflow-y-auto rounded-control border border-border px-sm py-xs">
              <SafeMarkdown text={installationDisclaimer} />
            </div>
          </details>
        )}
        {installationDisclaimer && (
          <Button type="button" variant="ghost" size="sm" className="h-7 px-xs text-caption" disabled={disabled}
            onClick={() => set('disclaimer', installationDisclaimer)}>
            <Copy className="size-3.5" aria-hidden /> Edit a copy
          </Button>
        )}
        {readOnly && disclaimerEmpty && (
          <p className="text-caption text-muted-foreground">None was written: the template’s own disclaimer was printed.</p>
        )}
        {!readOnly && !drafting && disclaimerEmpty && !installationDisclaimer && (
          <p className="text-caption text-muted-foreground">Empty: the template’s own disclaimer is printed.</p>
        )}
      </div>
    </div>
  );
}

/** Rows with no name would fail validation; drop them before saving.  A list
 *  a draft does not override stays absent (it follows the project). */
export const cleanSettings = <T extends EngagementOverrides>(s: T): T => ({
  ...s,
  ...(s.testers ? { testers: cleanRows(s.testers) } : {}),
  ...(s.distribution ? { distribution: cleanRows(s.distribution) } : {}),
});

export default EngagementSettingsFields;
