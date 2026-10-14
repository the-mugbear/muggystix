/**
 * A report's details as the report workspace shows them (owner, 2026-10-14):
 * five sections in the order the report reads — Summary, Client &
 * engagement, Team & distribution, Scope & targets, Disclaimer.
 *
 * Each section reads as the report's details (formatted text; the rendered
 * preview is what is authoritative for print), with ONE status line on a
 * draft — "Uses the project's defaults" or "N customised for this report" —
 * and the customised fields marked, each with "Use the project's".  "Edit"
 * opens the section's fields; Done keeps the changes pending, Cancel returns
 * to the last Edit, "Discard changes" to the last save (`useDetailEdits`).
 * A value that does not come from the project's defaults (the installation's
 * author, the project's members as the team) names its source.
 *
 * The same component serves the project's report defaults
 * (`/reports/defaults`): no follow status there (`ctx.followed` is null).
 */
import React from 'react';
import { MoreHorizontal } from 'lucide-react';

import type {
  EngagementSettings, ProjectMember, ReportRecipient, ReportTester,
} from '../../../services/api';
import type { ProjectMembersStatus } from '../../../hooks/useProjectMembers';
import type { EvidenceImages } from '../../../utils/evidenceImages';
import type { MarkdownImages } from '../../../utils/reportImages';
import MarkdownField from '../../MarkdownField';
import SafeMarkdown from '../../SafeMarkdown';
import { Button } from '../../ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '../../ui/dropdown-menu';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { Textarea } from '../../ui/textarea';
import {
  DETAIL_LABELS, DETAIL_SECTIONS, type DetailKey, type DetailSection, current, customised, isListKey,
  sectionStatus, sourceCaption,
} from './detailModel';
import { DetailRows, DistributionEditor, TeamEditor } from './DetailEditors';
import type { DetailEdits } from './useDetailEdits';

/** A report detail's name, wherever one is shown: a small muted caption. */
export const DETAIL_NAME = 'block text-caption font-medium uppercase tracking-wide text-muted-foreground';

/** The element id of each detail's field (`${idPrefix}-${suffix}`): the
 *  Review tab's lines open the section and put the caret there. */
export const FIELD_SUFFIX: Record<DetailKey, string> = {
  executive_summary: 'summary', client_name: 'client', author_name: 'author', classification: 'classification',
  engagement_type: 'type', testers: 'team', distribution: 'distribution', system_description: 'system',
  applications: 'applications', thick_clients: 'thick_clients', other_targets: 'other_targets', disclaimer: 'disclaimer',
};

/** Put the caret in a detail's field (its section open), scrolled into view:
 *  the field itself, else — a list — its first control.  Answers whether
 *  there was one.  Called once the section has rendered open. */
export const focusDetailField = (idPrefix: string, key: DetailKey): boolean => {
  const el = document.getElementById(`${idPrefix}-${FIELD_SUFFIX[key]}`);
  if (!el) return false;
  el.scrollIntoView?.({ block: 'center' });
  const target = el.matches('input, textarea, button')
    ? el
    : el.parentElement?.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled]), button:not([disabled])');
  target?.focus?.({ preventScroll: true });
  return true;
};

const MARKDOWN: ReadonlyArray<DetailKey> = [
  'executive_summary', 'system_description', 'applications', 'thick_clients', 'other_targets', 'disclaimer',
];
const MAX: Partial<Record<DetailKey, number>> = {
  executive_summary: 65536, client_name: 255, author_name: 255, classification: 100, engagement_type: 100,
};
const EXAMPLE: Partial<Record<DetailKey, string>> = {
  author_name: 'e.g. Example Security Ltd', classification: 'e.g. Confidential',
  engagement_type: 'e.g. Internal network penetration test',
};
const HELP: Partial<Record<DetailKey, string>> = {
  system_description: 'What was assessed, in the client’s terms. The scope’s networks and domains are listed after it.',
  applications: 'If applicable — printed only when written. One target per line works well.',
  thick_clients: 'If applicable — printed only when written.',
  other_targets: 'If applicable — printed only when written.',
};

export interface DetailSectionsProps {
  edits: DetailEdits;
  /** May the reader change these details (a draft's `can_edit`, the
   *  defaults' writer)?  Without it nothing editable is rendered. */
  canEdit: boolean;
  idPrefix: string;
  /** Saving: every control stays, greyed. */
  busy?: boolean;
  /** The summary's name ("Summary of changes" on an addendum). */
  summaryLabel?: string;
  /** Beside the summary while it is open ("Draft with AI…"). */
  summaryAction?: React.ReactNode;
  /** The project's report images: the system description's picker, paste
   *  and preview. */
  images?: MarkdownImages;
  /** Under the Scope & targets section ("Project images…"). */
  scopeAction?: React.ReactNode;
  /** Said under a field (the defaults page: "Empty: the installation's …"). */
  notes?: Partial<Record<DetailKey, React.ReactNode>>;
  members: ProjectMember[];
  membersStatus?: ProjectMembersStatus;
  onRetryMembers?: () => void;
  currentUser?: { id: number; name: string } | null;
}

const DetailSections: React.FC<DetailSectionsProps> = (props) => (
  <div className="space-y-lg">
    {DETAIL_SECTIONS.map((section) => <SectionBlock key={section.id} section={section} {...props} />)}
  </div>
);

const SectionBlock: React.FC<DetailSectionsProps & { section: DetailSection }> = ({
  section, edits, canEdit, idPrefix, busy = false, summaryLabel, summaryAction, images, scopeAction, notes,
  members, membersStatus, onRetryMembers, currentUser,
}) => {
  const { ctx } = edits;
  const open = canEdit && edits.isOpen(section.id);
  const status = sectionStatus(ctx, section);
  const sectionDirty = edits.dirtySections.includes(section.id);
  const title = section.id === 'summary' && summaryLabel ? summaryLabel : section.title;
  const label = (key: DetailKey) => (key === 'executive_summary' && summaryLabel ? summaryLabel : DETAIL_LABELS[key]);
  const resolver: EvidenceImages | undefined = images?.resolver;

  const field = (key: DetailKey) => {
    const id = `${idPrefix}-${FIELD_SUFFIX[key]}`;
    const value = current(ctx, key);
    const own = customised(ctx, key);
    const caption = ctx.followed && !own && !(key in ctx.pending) ? sourceCaption(ctx.sources?.[key]) : null;
    // A section of one field says "Customised for this report" in its status
    // line already: the field keeps only its action (walkthrough 2026-10-14 —
    // the Executive summary said it twice).
    const single = section.keys.length === 1 && status != null;
    const mark = own && !(single && !canEdit) ? (
      <p className="flex flex-wrap items-center gap-x-xs text-caption text-muted-foreground" data-testid={`${id}-customised`}>
        {!single && <span className="font-medium text-foreground">Customised for this report</span>}
        {canEdit && (
          <>
            {!single && <span aria-hidden>·</span>}
            <Button type="button" variant="link" size="sm" className="h-auto p-0 text-caption" disabled={busy}
              aria-label={`Use the project’s ${label(key)}`} onClick={() => edits.set(key, null)}>
              Use the project’s
            </Button>
          </>
        )}
      </p>
    ) : caption ? <p className="text-caption text-muted-foreground">{caption}</p> : null;
    const note = notes?.[key];

    if (!open) {
      // Read view: the value as the report's details read.
      let body: React.ReactNode;
      if (isListKey(key)) {
        body = <DetailRows rows={(value as Array<ReportTester | ReportRecipient> | undefined) ?? []} empty="Nobody listed" />;
      } else {
        const text = typeof value === 'string' ? value.trim() : '';
        body = !text
          ? <p className="text-body italic text-muted-foreground">{key === 'disclaimer' ? 'None written: the template’s own disclaimer is printed' : 'Not written'}</p>
          : MARKDOWN.includes(key)
            ? <div className="max-w-4xl text-body"><SafeMarkdown text={text} evidence={key === 'system_description' ? resolver : undefined} /></div>
            : <p className="whitespace-pre-wrap text-body font-medium [overflow-wrap:anywhere]">{text}</p>;
      }
      return (
        <div key={key} className="min-w-0 space-y-xxs" id={id} data-testid={`${id}-read`}>
          <span className={DETAIL_NAME}>{label(key)}</span>
          {body}
          {mark}
          {note}
        </div>
      );
    }

    // Edit view.
    const set = (v: EngagementSettings[DetailKey] | null) => edits.set(key, v);
    let control: React.ReactNode;
    if (key === 'testers') {
      control = (
        <TeamEditor value={(value as ReportTester[] | undefined) ?? []} onChange={set} members={members}
          membersStatus={membersStatus} onRetryMembers={onRetryMembers} currentUser={currentUser} disabled={busy} />
      );
    } else if (key === 'distribution') {
      control = <DistributionEditor value={(value as ReportRecipient[] | undefined) ?? []} onChange={set} disabled={busy} />;
    } else if (key === 'executive_summary' || key === 'system_description' || key === 'disclaimer') {
      control = (
        <MarkdownField id={id} label={label(key)} rows={key === 'disclaimer' ? 4 : 7} maxLength={MAX[key] ?? 32768}
          value={(value as string | null | undefined) ?? ''} onChange={set} disabled={busy}
          images={key === 'system_description' ? images : undefined} />
      );
    } else if (MARKDOWN.includes(key)) {
      control = (
        <Textarea id={id} rows={3} maxLength={32768} value={(value as string | null | undefined) ?? ''}
          onChange={(e) => set(e.target.value)} disabled={busy} />
      );
    } else {
      control = (
        <Input id={id} maxLength={MAX[key] ?? 255} value={(value as string | null | undefined) ?? ''}
          placeholder={EXAMPLE[key]} onChange={(e) => set(e.target.value)} disabled={busy} />
      );
    }
    const listEmptied = isListKey(key) && Array.isArray(value) && value.length === 0 && own;
    return (
      <div key={key} className="min-w-0 space-y-xxs">
        {isListKey(key)
          ? <span id={id} className={DETAIL_NAME}>{label(key)}</span>
          : <Label htmlFor={id} className={DETAIL_NAME}>{label(key)}</Label>}
        {HELP[key] && <p className="text-caption text-muted-foreground">{HELP[key]}</p>}
        {key === 'system_description' && images?.upload && (
          // A paste crosses the draft's Save (second review): said before it.
          <p className="text-caption text-muted-foreground" data-testid={`${id}-paste-note`}>
            A pasted screenshot is added to the project’s images at once. Cancel takes it out of this text, not out
            of the images.
          </p>
        )}
        {control}
        {listEmptied && (
          // Never created silently: an emptied list is said as a choice.
          <p className="text-caption text-warning" data-testid={`${id}-nobody`}>
            This report will list nobody here.
          </p>
        )}
        {mark}
        {note}
      </div>
    );
  };

  const people = section.id === 'people' || section.id === 'engagement';
  return (
    <section className="min-w-0 space-y-sm border-t border-border pt-md first:border-t-0 first:pt-0"
      aria-labelledby={`${idPrefix}-section-${section.id}`} data-testid={`${idPrefix}-section-${section.id}`}>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-xs">
        <div className="min-w-0">
          <h3 id={`${idPrefix}-section-${section.id}`} className="text-subheading font-medium">{title}</h3>
          {status && (
            <p className="text-caption text-muted-foreground" data-testid={`${idPrefix}-section-${section.id}-status`}>{status}</p>
          )}
        </div>
        {canEdit && (
          <div className="flex shrink-0 items-center gap-xs">
            {open && section.id === 'summary' && summaryAction}
            {open ? (
              <>
                <Button type="button" size="sm" disabled={busy} onClick={() => edits.done(section.id)}>Done</Button>
                <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => edits.cancel(section.id)}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button type="button" variant="outline" size="sm" disabled={busy}
                aria-label={`Edit ${title}`} onClick={() => edits.edit(section.id)}>
                Edit
              </Button>
            )}
            {sectionDirty && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button type="button" variant="ghost" size="icon" className="size-7" disabled={busy}
                    aria-label={`More actions for ${title}`}>
                    <MoreHorizontal className="size-4" aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => { setTimeout(() => edits.discardSection(section.id), 0); }}>
                    Discard changes in this section
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        )}
      </div>
      <div className={people && !open ? 'grid gap-md md:grid-cols-2' : 'space-y-md'}>
        {section.keys.map(field)}
      </div>
      {section.id === 'scope' && scopeAction}
      {sectionDirty && !open && (
        <p className="text-caption text-warning" data-testid={`${idPrefix}-section-${section.id}-unsaved`}>Unsaved changes</p>
      )}
    </section>
  );
};

export default DetailSections;
