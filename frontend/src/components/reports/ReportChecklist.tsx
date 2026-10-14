/**
 * A draft report's ONE "Before issuing" checklist (walkthrough 2026-10-14):
 * every gap the summary names, grouped, each group with its count, and every
 * line an action — a missing detail goes to its field in Report details on
 * the same page (or to Project settings, or to where an administrator sets a
 * full name), a finding missing text opens its report text with a bar that
 * steps through the rest and back, a proposal / an image / a note opens the
 * finding where it is decided.  It replaced five sections (proposals, text
 * the reader cannot follow, images without a caption, the TODO list, notes).
 *
 * Only a draft has one: an issued report no longer changes.
 */
import React, { useState } from 'react';
import { Link } from 'react-router-dom';

import type { ReportSummary } from '../../services/api';
import { NEED_SECTION } from '../../utils/findingNeeds';
import { REPORT_FIELD_LABELS } from '../../utils/reportImages';
import { findingFromReportPath, REPORT_CHECKLIST_ANCHOR } from '../findings/ReportDraftBar';
import PostureSection, { SectionCount } from '../posture/PostureSection';
import { Button } from '../ui/button';

/** A group lists this many lines, then "Show all". */
export const CHECKLIST_PREVIEW = 10;

/** The labels `REQUIRED_DETAILS` sends, for a summary that carries no keys. */
const KEY_OF_LABEL: Record<string, string> = {
  'executive summary': 'executive_summary',
  client: 'client_name',
  author: 'author_name',
  classification: 'classification',
  'engagement type': 'engagement_type',
  'system description': 'system_description',
  'assessment team': 'testers',
  'distribution list': 'distribution',
  'project dates': 'project_dates',
};

/** Each missing detail with its key (`missing_detail_keys`, else read from
 *  its label; a full-name line is the only label that names someone). */
export const missingDetailEntries = (s: ReportSummary): Array<{ key: string; label: string }> => {
  const labels = s.missing_details ?? [];
  const keys = s.missing_detail_keys;
  return labels.map((label, i) => ({
    label,
    key: keys?.[i] ?? KEY_OF_LABEL[label] ?? (label.startsWith('a full name') ? 'account_full_name' : label),
  }));
};

/** Report details: the field each key is written in (`idPrefix` "report"). */
const FIELD_ID: Record<string, string> = {
  executive_summary: 'summary',
  client_name: 'client',
  author_name: 'author',
  classification: 'classification',
  engagement_type: 'type',
  system_description: 'system',
};
/** The two lists: their fieldsets (`EngagementSettingsFields`). */
const FIELDSET_ID: Record<string, string> = {
  testers: 'team',
  distribution: 'distribution',
};

const FOCUSABLE = 'input:not([disabled]), textarea:not([disabled]), button:not([disabled]), [role="combobox"]:not([disabled])';

/**
 * Scroll to the field `key` is written in, within `root` (the Report details
 * form), and put the caret there.  Answers whether there was one.
 */
export const focusReportDetail = (root: HTMLElement | null, key: string, idPrefix = 'report'): boolean => {
  if (!root) return false;
  let target: HTMLElement | null = null;
  let focusable: HTMLElement | null = null;
  const field = FIELD_ID[key];
  if (field) {
    const id = `${idPrefix}-${field}`;
    // A Markdown field shown as its preview has the id on neither element.
    target = root.querySelector<HTMLElement>(`#${id}`) ?? root.querySelector<HTMLElement>(`[data-testid="${id}-preview"]`);
    focusable = target;
  } else if (FIELDSET_ID[key]) {
    target = root.querySelector<HTMLElement>(`#${idPrefix}-${FIELDSET_ID[key]}`);
    focusable = target?.querySelector<HTMLElement>(FOCUSABLE) ?? null;
  }
  if (!target) return false;
  // A draft's detail that follows the project is text, not a field
  // (`data-followed`): the caret goes to its visible action — "Set for the
  // project" when the project has no value, else "Override for this report"
  // — never to the "⋯" menu beside it (`data-more-actions`).
  if (target.matches('[data-followed]') || !focusable || focusable.matches(':disabled')) {
    focusable = root.querySelector<HTMLElement>(
      `#${idPrefix}-${key}-source button:not([disabled]):not([data-more-actions])`) ?? focusable;
  }
  target.scrollIntoView?.({ block: 'center' });
  focusable?.focus?.({ preventScroll: true });
  return true;
};

const ROW = 'flex min-w-0 flex-wrap items-baseline gap-x-xs text-body';
const LINK = 'min-w-0 truncate text-info hover:underline';
const TODO = (
  <span className="rounded bg-warning/20 px-xxs text-caption font-semibold text-foreground">TODO</span>
);

/** One group: its heading and count, a line on what it is, then its lines —
 *  the first `CHECKLIST_PREVIEW`, then all of them on request. */
const Group: React.FC<{
  id: string;
  title: string;
  count: number;
  todo?: boolean;
  note: React.ReactNode;
  /** Keyed `<li>`s. */
  items: React.ReactElement[];
  listTestId?: string;
  listClassName?: string;
}> = ({ id, title, count, todo = false, note, items, listTestId, listClassName = 'space-y-xxs' }) => {
  const [all, setAll] = useState(false);
  const headingId = `checklist-${id}-title`;
  return (
    <div className="min-w-0 space-y-xxs" data-testid={`checklist-${id}`} role="group" aria-labelledby={headingId}>
      <h3 id={headingId} className="flex min-w-0 flex-wrap items-center gap-x-xs text-body font-semibold text-foreground">
        {todo && TODO}
        <span>{title}</span>
        <SectionCount>{count.toLocaleString()}</SectionCount>
      </h3>
      <p className="max-w-3xl text-caption text-muted-foreground">{note}</p>
      <ul className={listClassName} data-testid={listTestId}>
        {all ? items : items.slice(0, CHECKLIST_PREVIEW)}
      </ul>
      {items.length > CHECKLIST_PREVIEW && (
        <Button variant="ghost" size="sm" onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer' : `Show all ${items.length.toLocaleString()}`}
        </Button>
      )}
    </div>
  );
};

interface Props {
  reportId: number;
  summary: ReportSummary;
  /** Go to a report detail's field on the page (Report details). */
  onDetail: (key: string) => void;
  /** A global administrator: the one who sets someone else's full name. */
  isAdmin: boolean;
}

/** Whether a summary leaves anything for the checklist. */
export const hasChecklist = (s: ReportSummary): boolean =>
  !!(s.missing_details?.length || s.missing_text?.length || s.pending_proposals?.length
    || s.uncaptioned_images?.length || s.internal_references?.length || s.evidence_note_findings?.length);

const ReportChecklist: React.FC<Props> = ({ reportId, summary: s, onDetail, isAdmin }) => {
  if (!hasChecklist(s)) return null;
  const details = missingDetailEntries(s);
  const missingText = s.missing_text ?? [];
  const proposals = s.pending_proposals ?? [];
  const uncaptioned = s.uncaptioned_images ?? [];
  const references = s.internal_references ?? [];
  const notes = s.evidence_note_findings ?? [];
  const images = uncaptioned.reduce((n, m) => n + m.count, 0);
  const noteCount = notes.reduce((n, m) => n + m.notes.length, 0);

  const detailItem = ({ key, label }: { key: string; label: string }) => {
    if (key === 'project_dates') {
      return (
        <li key={label} className={ROW}>
          <Link to="/project-settings" className="text-info hover:underline">{label}</Link>
          <span className="text-caption text-muted-foreground">set in Project settings</span>
        </li>
      );
    }
    if (key === 'account_full_name') {
      return (
        <li key={label} className={ROW}>
          <span className="min-w-0 break-words">{label}</span>
          {isAdmin ? (
            <Link to="/system-settings?tab=users" className="text-caption text-info hover:underline">
              Set it in System settings › Users
            </Link>
          ) : (
            <span className="text-caption text-muted-foreground">
              each person sets their own in Profile, or a global administrator does
            </span>
          )}
        </li>
      );
    }
    return (
      <li key={label} className={ROW}>
        <button type="button" className="text-left text-info hover:underline" onClick={() => onDetail(key)}
          aria-label={`Go to ${label} in Report details`}>
          {label}
        </button>
      </li>
    );
  };

  return (
    <div id={REPORT_CHECKLIST_ANCHOR} data-testid="report-checklist" className="scroll-mt-24">
      <PostureSection title="Before issuing"
        description="Each line opens where it is fixed. Empty details and findings without text print as a highlighted TODO; the rest are warnings, never blocks.">
        <div className="space-y-md">
          {details.length > 0 && (
            <Group id="details" title="Report details to fill" count={details.length} todo
              note="Written in Report details, below."
              items={details.map(detailItem)} />
          )}
          {missingText.length > 0 && (
            <Group id="missing-text" title="Findings missing report text" count={missingText.length} todo
              note="Open one to write or draft it; its page steps on to the next."
              items={missingText.map((m) => (
                <li key={m.id} className={ROW}>
                  <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                  <Link to={findingFromReportPath(m.id, reportId)} className={LINK}
                    title={`${m.title} — open its report text to write or draft it`}>{m.title}</Link>
                  <span className="text-caption text-muted-foreground">missing {m.missing.join(', ')}</span>
                </li>
              ))} />
          )}
          {proposals.length > 0 && (
            <Group id="proposals" title="Findings with proposals to decide" count={proposals.length}
              note="Changes an agent or an AI draft proposed. The report prints the findings as they stand."
              items={proposals.map((m) => (
                <li key={m.id} className={ROW}>
                  <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                  <Link to={`/findings/${m.id}#proposals`} className={LINK} title={m.title}>{m.title}</Link>
                  <span className="text-caption text-muted-foreground">{m.count} proposal{m.count === 1 ? '' : 's'}</span>
                </li>
              ))} />
          )}
          {uncaptioned.length > 0 && (
            <Group id="uncaptioned" title="Images without a caption" count={images}
              note="Each prints under its file’s name. A caption is written on the image, in the finding’s comments."
              items={uncaptioned.map((m) => (
                <li key={m.id} className={ROW}>
                  <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                  <Link to={`/findings/${m.id}#${NEED_SECTION.uncaptioned_images}`} className={LINK} title={m.title}>{m.title}</Link>
                  <span className="text-caption tabular-nums text-muted-foreground">
                    {m.count.toLocaleString()} image{m.count === 1 ? '' : 's'}
                  </span>
                </li>
              ))} />
          )}
          {references.length > 0 && (
            <Group id="references" title="Text the reader cannot follow" count={references.length}
              note="These sections name a BlueStick record, which the report’s reader has never seen: name a finding by its title and a system by its address."
              items={references.map((m) => (
                <li key={m.id} className={ROW}>
                  <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                  <Link to={`/findings/${m.id}?edit=report-text`} className={LINK} title={m.title}>{m.title}</Link>
                  <span className="min-w-0 break-words text-caption text-muted-foreground">
                    {m.fields.map((f) => `${REPORT_FIELD_LABELS[f.field] ?? f.field}: ${f.phrases.map((p) => `“${p}”`).join(', ')}`).join(' · ')}
                  </span>
                </li>
              ))} />
          )}
          {notes.length > 0 && (
            <Group id="notes" title="Notes the client will read" count={noteCount}
              note="Each is printed word for word under Evidence, before the image attached to it. To keep one out, place its image in a written section or untick “In report”."
              listTestId="report-evidence-notes" listClassName="space-y-xs"
              items={notes.map((m) => (
                <li key={m.id} className="min-w-0 text-body">
                  <span className={ROW}>
                    <span className="tabular-nums text-muted-foreground">{m.ref}</span>
                    <Link to={`/findings/${m.id}#${NEED_SECTION.uncaptioned_images}`} className={LINK} title={m.title}>{m.title}</Link>
                  </span>
                  <ul className="mt-xxs space-y-xxs">
                    {m.notes.map((note, i) => (
                      <li key={i} className="line-clamp-3 whitespace-pre-wrap border-l-2 border-border pl-xs text-caption text-muted-foreground [overflow-wrap:anywhere]">
                        {note}
                      </li>
                    ))}
                  </ul>
                </li>
              ))} />
          )}
        </div>
      </PostureSection>
    </div>
  );
};

export default ReportChecklist;
