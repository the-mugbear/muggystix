/**
 * The finding's report text (v5.260.0) — what the client report says about
 * the issue: description, impact, recommendation, steps to reproduce,
 * references and CVSS.  Markdown, written by people: seeded once at promotion
 * from the scanner row or source note, then edited here by the finding's
 * author or a project admin (the server's `can_modify`, same as renaming).
 *
 * v5.290.0 — shown rendered, under the report's own rules (SafeMarkdown: no
 * raw HTML, no images, web/mail links only, headings as bold text); before,
 * `**bold**` printed literally.
 *
 * 5.293.0 — each field is a MarkdownField: a formatting toolbar, a preview
 * under the same rules (tables included), a guide to what the report prints,
 * and a fix for a table written straight after text.
 *
 * 5.316.0 — "Draft empty sections" produces PROPOSALS (one per section),
 * reviewed in the finding's Proposals section exactly like an agent's; it no
 * longer fills the editor.  Any analyst may draft; accepting stays the
 * author's or a project admin's.
 *
 * 5.334.0 — pending drafts are reviewed HERE, in the section they would
 * change (`FieldDraftsReview`: the current text beside the draft).  A section
 * with drafts waiting says so instead of "Not written yet", never opens an
 * empty editor by itself, and "Draft empty sections" leaves it alone.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Loader2, Pencil, Sparkles } from 'lucide-react';

import {
  draftFindingText,
  Finding,
  FindingReportText,
  FindingReportTextField,
  FindingReportTextUpdate,
  Proposal,
  updateFinding,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useDiscardGuard } from '../hooks/useDiscardGuard';
import { formatApiError } from '../utils/apiErrors';
import { announceProposalsChanged } from '../utils/proposalEvents';
import type { MarkdownImages } from '../utils/reportImages';
import { Button } from './ui/button';
import { InfoTip } from './ui/info-tip';
import PostureSection from './posture/PostureSection';
import { Input } from './ui/input';
import { Label } from './ui/label';
import MarkdownField from './MarkdownField';
import SafeMarkdown from './SafeMarkdown';
import FieldDraftsReview from './proposals/FieldDraftsReview';

export const REPORT_TEXT_FIELDS: Array<{ key: FindingReportTextField; label: string; hint: string; rows: number }> = [
  { key: 'description', label: 'Description', hint: 'What the issue is, in the client’s terms.', rows: 6 },
  { key: 'impact', label: 'Impact', hint: 'What an attacker gains, on this network.', rows: 3 },
  { key: 'recommendation', label: 'Recommendation', hint: 'What to change to fix it.', rows: 4 },
  { key: 'steps_to_reproduce', label: 'Steps to reproduce', hint: 'How it was demonstrated (a list works well).', rows: 5 },
  { key: 'references', label: 'References', hint: 'Advisories and vendor guidance, one per line.', rows: 3 },
];

type Draft = Record<FindingReportTextField | 'cvss_vector' | 'cvss_score', string>;

const toDraft = (t: FindingReportText | null | undefined): Draft => ({
  description: t?.description ?? '',
  impact: t?.impact ?? '',
  recommendation: t?.recommendation ?? '',
  steps_to_reproduce: t?.steps_to_reproduce ?? '',
  references: t?.references ?? '',
  cvss_vector: t?.cvss_vector ?? '',
  cvss_score: t?.cvss_score != null ? String(t.cvss_score) : '',
});

/** The fields a report would show empty — the Reports page counts the same. */
export const missingReportText = (t: FindingReportText | null | undefined): string[] =>
  REPORT_TEXT_FIELDS
    .filter((f) => f.key !== 'steps_to_reproduce' && f.key !== 'references')
    .filter((f) => !(t?.[f.key] ?? '').trim())
    .map((f) => f.label.toLowerCase());

/** The sections a report needs — the ones an AI draft may fill. */
const DRAFTABLE: FindingReportTextField[] = ['description', 'impact', 'recommendation'];

/** Nothing the report needs has been written — a finding that was just made.
 *  Its page opens ready to write: a wall of "Not written yet" behind an Edit
 *  button hid the one thing there is to do next (5.323.0). */
const nothingWritten = (t: FindingReportText | null | undefined): boolean =>
  DRAFTABLE.every((k) => !(t?.[k] ?? '').trim());

interface Props {
  finding: Finding;
  /** Analyst+ AND the server's can_modify (author / project admin). */
  canEdit: boolean;
  /** Analyst+: may ask for an AI draft (a proposal changes nothing). */
  canPropose?: boolean;
  onSaved: (finding: Finding) => void;
  /** A draft created proposals: re-read the page's proposals. */
  onDrafted?: () => void;
  /** Open in the editor (the Reports page's "missing report text" links). */
  startEditing?: boolean;
  /** 5.317.0 — "Work on this with your agent" (the page supplies it, so the
   *  card stays free of the session hooks). Shown beside the draft button. */
  agentAction?: React.ReactNode;
  /** The finding's images (the page loads them once, `useFindingImages`):
   *  the editor's "Insert image" and the images placed in the text. */
  images?: MarkdownImages;
  /** Pending report-text drafts by field (`useFindingProposals().textByField`). */
  drafts?: Map<string, Proposal[]>;
  /** Analyst+: may accept or reject a draft (the server still decides). */
  canDecide?: boolean;
  /** A draft was accepted or rejected. */
  onProposalDecided?: (updated: Proposal) => void;
  /** The editor holds text that is not saved (or no longer does) — for the
   *  page's own Back button. */
  onDirtyChange?: (dirty: boolean) => void;
}

const NO_DRAFTS = new Map<string, Proposal[]>();

const FindingReportTextCard: React.FC<Props> = ({
  finding, canEdit, canPropose = canEdit, onSaved, onDrafted, startEditing = false, agentAction, images,
  drafts = NO_DRAFTS, canDecide = false, onProposalDecided, onDirtyChange,
}) => {
  const toast = useToast();
  const text = finding.report_text;
  const draftsFor = (field: string) => drafts.get(field) ?? [];
  const [draft, setDraft] = useState<Draft | null>(() => ((startEditing || nothingWritten(text)) && canEdit ? toDraft(text) : null));
  // Opened by itself because nothing is written (not by the reader): when the
  // proposals arrive and some section has drafts waiting, the drafts are the
  // next thing to do — close the untouched editor so they show.
  const autoOpened = useRef(!startEditing && nothingWritten(text) && canEdit);
  useEffect(() => {
    if (!autoOpened.current || drafts.size === 0) return;
    autoOpened.current = false;
    setDraft((d) => {
      if (!d) return d;
      const untouched = JSON.stringify(d) === JSON.stringify(toDraft(text));
      return untouched ? null : d;
    });
  }, [drafts, text]);
  // Unsaved text asks before a reload, a tab close or Cancel drops it.
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(toDraft(text));
  const { confirmLeave, confirmEl } = useDiscardGuard(
    () => dirty,
    'The report text you changed has not been saved. Discard it?',
  );
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  const cancel = () => {
    if (!dirty) { setDraft(null); return; }
    void confirmLeave().then((ok) => { if (ok) setDraft(null); });
  };
  // Opened from an empty section: put the caret in that section.
  const [focusField, setFocusField] = useState<string | null>(null);
  useEffect(() => {
    if (!draft || !focusField) return;
    document.getElementById(`rt-${focusField}`)?.focus?.();
    setFocusField(null);
  }, [draft, focusField]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  // 5.334.4 — sections the last AI draft declined, with what it needs: an
  // answer, shown until the next draft; never report text.
  const [declined, setDeclined] = useState<Record<string, string>>({});
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (startEditing) cardRef.current?.scrollIntoView?.({ block: 'start' });
  }, [startEditing]);

  const missing = missingReportText(text);
  // Empty sections an AI draft may fill and nobody has drafted yet.
  const undrafted = DRAFTABLE.filter((k) => !(text?.[k] ?? '').trim() && draftsFor(k).length === 0);
  const waiting = [...drafts.values()].reduce((n, list) => n + list.length, 0);

  // Draft the required sections still empty as proposals — the same review
  // path an agent's drafts take; nothing is written until one is accepted.
  const draftEmpty = async () => {
    const empty = undrafted;
    if (empty.length === 0) return;
    setDrafting(true);
    setError(null);
    try {
      const { proposals, declined: notDrafted = {} } = await draftFindingText(finding.id, empty);
      setDeclined(notDrafted);
      const skipped = Object.keys(notDrafted).length;
      if (proposals.length > 0) {
        onDrafted?.();
        announceProposalsChanged();
        toast.success(
          `Drafted ${proposals.length} section${proposals.length === 1 ? '' : 's'} as proposals — review each in its section below.`
          + (skipped ? ` ${skipped} not drafted: not enough information.` : ''),
        );
      } else {
        toast.info('Nothing drafted: the finding does not hold enough information yet. See what is missing below.');
      }
      cardRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    } catch (err) {
      setError(formatApiError(err, 'Could not draft the report text.'));
    } finally {
      setDrafting(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    const before = toDraft(text);
    const payload: FindingReportTextUpdate = {};
    for (const f of REPORT_TEXT_FIELDS) {
      if (draft[f.key] !== before[f.key]) payload[f.key] = draft[f.key].trim() || null;
    }
    if (draft.cvss_vector !== before.cvss_vector) payload.cvss_vector = draft.cvss_vector.trim() || null;
    if (draft.cvss_score !== before.cvss_score) {
      const raw = draft.cvss_score.trim();
      const score = raw === '' ? null : Number(raw);
      if (score !== null && (Number.isNaN(score) || score < 0 || score > 10)) {
        setError('A CVSS score is a number from 0.0 to 10.0.');
        return;
      }
      payload.cvss_score = score;
    }
    if (Object.keys(payload).length === 0) { setDraft(null); return; }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateFinding(finding.id, payload);
      onSaved(updated);
      setDraft(null);
      toast.success('Report text saved.');
    } catch (err) {
      setError(formatApiError(err, 'Could not save the report text.'));
    } finally {
      setSaving(false);
    }
  };

  const cvssLine = text?.cvss_vector || text?.cvss_score != null
    ? `${text?.cvss_score != null ? text.cvss_score.toFixed(1) : '—'}${text?.cvss_vector ? ` · ${text.cvss_vector}` : ''}`
    : null;

  return (
    // v5.294.0 (UX review) — a section over a thin rule, not a bordered card
    // (UI_STYLE_GUIDE §7). The wrapper keeps the ref the ?edit= link scrolls to.
    <div className="mb-md" ref={cardRef}>
      {confirmEl}
      <PostureSection
        title={(
          <>
            <span>Report text</span>
            <InfoTip
              label="About report text"
              text={`What the client report says about this finding. Written in Markdown; shown as the report prints it.${images ? ' An image ticked “In report” can be placed in a section (Insert image); the rest print under Evidence.' : ''}`}
            />
          </>
        )}
        // Only what is specific to THIS finding is said under the heading.
        description={missing.length > 0 || waiting > 0 ? (
          <>
            {missing.length > 0 && (
              <>Still empty: <span className="text-foreground">{missing.join(', ')}</span>.{' '}</>
            )}
            {waiting > 0 && (
              <span className="text-info">{waiting === 1 ? '1 draft is' : `${waiting} drafts are`} waiting for review in the sections below.</span>
            )}
          </>
        ) : undefined}
        actions={canEdit || canPropose ? (
          <>
            {canPropose && agentAction}
            {canPropose && undrafted.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => void draftEmpty()} disabled={drafting || saving}
                title="Draft the empty sections with your LLM provider, as proposals to review; nothing changes until one is accepted">
                {drafting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Sparkles className="size-4" aria-hidden />}
                Draft empty sections
              </Button>
            )}
            {canEdit && !draft && (
              <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(text)); setError(null); }}>
                <Pencil className="size-4" aria-hidden /> Edit
              </Button>
            )}
          </>
        ) : undefined}
      >
        {draft ? (
          <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); void save(); }}>
            {REPORT_TEXT_FIELDS.map((f) => (
              <div key={f.key} className="space-y-xxs">
                <Label htmlFor={`rt-${f.key}`}>{f.label}</Label>
                <p className="text-caption text-muted-foreground">{f.hint}</p>
                {draftsFor(f.key).length > 0 && (
                  <p role="note" className="text-caption text-info">
                    {draftsFor(f.key).length === 1 ? 'A draft is' : `${draftsFor(f.key).length} drafts are`} waiting for this section.
                    {' '}Saving here does not retire {draftsFor(f.key).length === 1 ? 'it' : 'them'}; accepting one later replaces what you save.
                  </p>
                )}
                <MarkdownField
                  id={`rt-${f.key}`}
                  label={f.label}
                  rows={f.rows}
                  maxLength={32768}
                  value={draft[f.key]}
                  onChange={(v) => setDraft((d) => (d ? { ...d, [f.key]: v } : d))}
                  disabled={saving}
                  images={images}
                />
              </div>
            ))}
            <div className="flex flex-wrap gap-md">
              <div className="min-w-0 flex-1 space-y-xxs">
                <Label htmlFor="rt-cvss-vector">CVSS vector</Label>
                <Input
                  id="rt-cvss-vector"
                  value={draft.cvss_vector}
                  maxLength={200}
                  placeholder="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
                  onChange={(e) => setDraft({ ...draft, cvss_vector: e.target.value })}
                  disabled={saving}
                  className="font-mono"
                />
                <p className="text-caption text-muted-foreground">
                  A 3.x or 2.0 vector sets the score; for 4.0 enter the score yourself.
                </p>
              </div>
              <div className="w-32 space-y-xxs">
                <Label htmlFor="rt-cvss-score">Score</Label>
                <Input
                  id="rt-cvss-score"
                  inputMode="decimal"
                  value={draft.cvss_score}
                  onChange={(e) => setDraft({ ...draft, cvss_score: e.target.value })}
                  disabled={saving}
                />
              </div>
            </div>
            {error && <p className="text-caption text-destructive">{error}</p>}
            <div className="flex gap-xs">
              <Button type="submit" size="sm" disabled={saving}>
                {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save report text
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={cancel} disabled={saving}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <>
          {error && <p className="mb-sm break-words text-caption text-destructive">{error}</p>}
          <dl className="space-y-sm">
            {REPORT_TEXT_FIELDS.map((f) => (
              <div key={f.key} className="min-w-0">
                <dt className="text-caption font-medium text-muted-foreground">{f.label}</dt>
                {declined[f.key] && draftsFor(f.key).length === 0 && !(text?.[f.key] ?? '').trim() && (
                  <dd role="note" className="min-w-0 break-words text-caption text-warning" data-testid={`declined-${f.key}`}>
                    Not drafted — not enough information: {declined[f.key]}
                  </dd>
                )}
                <dd className="min-w-0 break-words text-body" data-testid={`report-text-${f.key}`}>
                  {draftsFor(f.key).length > 0 ? (
                    <FieldDraftsReview
                      field={f.key} label={f.label} current={text?.[f.key]} drafts={draftsFor(f.key)}
                      canDecide={canDecide} onDecided={(u) => onProposalDecided?.(u)} evidence={images?.resolver}
                    />
                  ) : text?.[f.key]?.trim()
                    ? <SafeMarkdown text={text[f.key] as string} evidence={images?.resolver} />
                    : canEdit ? (
                      <button
                        type="button"
                        className="text-info hover:underline"
                        onClick={() => { setDraft(toDraft(text)); setError(null); setFocusField(f.key); }}
                      >
                        Not written yet. Write it
                      </button>
                    ) : <span className="text-muted-foreground">Not written yet</span>}
                </dd>
              </div>
            ))}
            <div className="min-w-0">
              <dt className="text-caption font-medium text-muted-foreground">CVSS</dt>
              <dd className="break-all font-mono text-body">
                {cvssLine ?? <span className="font-sans text-muted-foreground">Not scored</span>}
              </dd>
              {draftsFor('cvss_vector').length > 0 && (
                <dd className="mt-xs min-w-0 font-sans">
                  <FieldDraftsReview
                    field="cvss_vector" label="CVSS vector" current={text?.cvss_vector} drafts={draftsFor('cvss_vector')}
                    canDecide={canDecide} onDecided={(u) => onProposalDecided?.(u)}
                  />
                </dd>
              )}
            </div>
          </dl>
          </>
        )}
      </PostureSection>
    </div>
  );
};

export default FindingReportTextCard;
